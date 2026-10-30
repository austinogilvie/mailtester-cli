"""The scripting contract: JSON errors under --json and verify --fail-on."""

import copy
import json

import dotenv
import httpx
import pytest
import typer
from conftest import load_fixture, serve
from typer.testing import CliRunner

import mailtester_cli.client
from mailtester_cli import MailTester
from mailtester_cli.cli import app
from mailtester_cli.client import BASE_URL
from mailtester_cli.commands._shared import State, fail

VERIFY = f"{BASE_URL}/verify"
FINDER = f"{BASE_URL}/finder/email"
SUBMIT = f"{BASE_URL}/verify/batch"
STATUS = f"{BASE_URL}/verify/batch/1457"
RESULTS = f"{BASE_URL}/verify/batch/1457/results"
LISTS = f"{BASE_URL}/lists"
COST = "This will use 1 credit.\n"

runner = CliRunner()


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_test")


@pytest.fixture
def guard(respx_mock):
    """A catch-all route: any request at all shows up as guard.called."""
    return respx_mock.route().respond(500)


def fixture_data(name: str) -> dict:
    return copy.deepcopy(load_fixture(name)[2]["data"])


def fixture_response(name: str) -> httpx.Response:
    status, headers, body = load_fixture(name)
    return httpx.Response(status, headers=headers, json=body)


def verdict(email: str, name: str = "verify_single") -> httpx.Response:
    data = fixture_data(name) | {"email": email}
    return httpx.Response(200, json={"success": True, "data": data})


def last_error(result) -> dict:
    """The JSON object on the last stderr line, after checking nothing reached stdout."""
    assert result.stdout == ""
    return json.loads(result.stderr.splitlines()[-1])


# errors under --json


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (
            "error_401",
            {"error": "Invalid API key", "type": "AuthenticationError", "status_code": 401},
        ),
        (
            "error_402",
            {
                "error": "Insufficient credits",
                "type": "InsufficientCreditsError",
                "status_code": 402,
                "available_credits": 0,
            },
        ),
        (
            "error_429",
            {
                "error": "Rate limit exceeded",
                "type": "RateLimitError",
                "status_code": 429,
                "retry_after": 30,
            },
        ),
        (
            "error_502",
            {
                "error": "The deliverability engine is currently unavailable.",
                "type": "ServerError",
                "status_code": 502,
            },
        ),
    ],
)
def test_verify_api_error_is_one_json_line(respx_mock, api_key, clock, fixture, expected):
    serve(respx_mock, fixture, VERIFY, method="POST")
    result = runner.invoke(app, ["--json", "verify", "x@example.com"])
    assert result.exit_code == 1
    obj = last_error(result)
    assert obj == expected
    assert list(obj) == list(expected)  # the documented key order


def test_404_error_object(respx_mock, api_key):
    serve(respx_mock, "error_404_list", f"{LISTS}/999999999")
    result = runner.invoke(app, ["--json", "lists", "show", "999999999"])
    assert result.exit_code == 1
    assert last_error(result) == {
        "error": "List not found",
        "type": "NotFoundError",
        "status_code": 404,
    }
    assert result.stderr.count("\n") == 1


def test_422_carries_the_errors_map(respx_mock, api_key, tmp_path):
    serve(respx_mock, "error_422", SUBMIT, method="POST")
    path = tmp_path / "emails.txt"
    path.write_text("a@example.com\n")
    result = runner.invoke(app, ["--json", "batch", "submit", str(path)])
    assert result.exit_code == 1
    obj = last_error(result)
    assert obj["type"] == "ValidationError"
    assert obj["status_code"] == 422
    assert obj["errors"] == {"emails": ["The emails field is required."]}


def test_422_without_a_map_has_no_errors_key(respx_mock, api_key):
    serve(respx_mock, "finder_no_mx", FINDER, method="POST")
    result = runner.invoke(app, ["--json", "find", "Jane", "Doe", "nonexistent-domain.invalid"])
    assert result.exit_code == 1
    obj = last_error(result)
    assert obj["type"] == "ValidationError"
    assert "errors" not in obj
    assert result.stderr.startswith("This will use 3 credits.\n")  # notes stay text


def test_hint_is_its_own_key(respx_mock, api_key):
    serve(respx_mock, "batch_results_not_complete", RESULTS)
    result = runner.invoke(app, ["--json", "batch", "results", "1457"])
    assert result.exit_code == 1
    assert last_error(result) == {
        "error": "Bulk verification is not complete yet",
        "type": "MailTesterError",
        "status_code": 400,
        "hint": "Check it with: mailtester batch status 1457",
    }


def test_wait_hint_after_status_lines(respx_mock, api_key, tmp_path, clock):
    serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    serve(respx_mock, "error_502", STATUS)
    path = tmp_path / "emails.txt"
    path.write_text("a@example.com\n")
    result = runner.invoke(app, ["--json", "batch", "submit", str(path), "--wait"])
    assert result.exit_code == 1
    obj = last_error(result)
    assert obj["type"] == "ServerError"
    assert obj["hint"] == (
        "Batch 1457 may still be running. Check it with: mailtester batch status 1457"
    )


def test_not_started_batch_hint(respx_mock, api_key, clock):
    serve(respx_mock, "batch_status_pending", STATUS)
    result = runner.invoke(app, ["--json", "batch", "wait", "1457"])
    assert result.exit_code == 1
    obj = last_error(result)
    assert obj["type"] == "BatchNotStartedError"
    assert obj["status_code"] is None
    assert obj["hint"].startswith("Start it in the MailTester app, then resume with:")


def test_transport_error_has_no_status(respx_mock, api_key, clock):
    respx_mock.post(VERIFY).mock(side_effect=httpx.ConnectError("refused"))
    result = runner.invoke(app, ["--json", "verify", "x@example.com"])
    assert result.exit_code == 1
    obj = last_error(result)
    assert obj["type"] == "APIConnectionError"
    assert obj["status_code"] is None
    assert "retrying in" in result.stderr  # retry lines stay text


def test_missing_key(monkeypatch):
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    result = runner.invoke(app, ["--json", "credits"])
    assert result.exit_code == 1
    obj = last_error(result)
    assert obj["type"] == "MissingAPIKeyError"
    assert obj["status_code"] is None
    assert "MAILTESTER_API_KEY" in obj["error"]


def test_missing_key_hint_names_api_key_option(monkeypatch):
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    result = runner.invoke(app, ["credits"])
    assert result.exit_code == 1
    assert result.stderr == (
        "Error: MAILTESTER_API_KEY is not set. Set it in your environment or in a .env file. "
        "Or pass --api-key before the command.\n"
    )


def test_missing_key_hint_json(monkeypatch):
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    result = runner.invoke(app, ["--json", "credits"])
    obj = last_error(result)
    assert obj["error"] == (
        "MAILTESTER_API_KEY is not set. Set it in your environment or in a .env file."
    )
    assert obj["hint"] == "Or pass --api-key before the command."


@pytest.mark.parametrize("args", [["credits"], ["--json", "credits"]])
def test_malformed_key_gets_no_api_key_hint(monkeypatch, args):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_abc def")
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "invalid characters" in result.stderr
    assert "--api-key" not in result.stderr
    assert "evp_abc" not in result.stderr


def test_usage_error(respx_mock, guard, api_key, tmp_path):
    path = tmp_path / "emails.txt"
    path.write_text("a@example.com\n")
    args = ["--json", "batch", "submit", str(path), "--callback-url", "nope"]
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert last_error(result) == {
        "error": "--callback-url must be an http(s) URL.",
        "type": "UsageError",
        "status_code": None,
    }
    assert not guard.called


def test_local_error(respx_mock, guard, api_key, tmp_path):
    path = tmp_path / "nope.txt"
    result = runner.invoke(app, ["--json", "verify", "--file", str(path)])
    assert result.exit_code == 1
    assert last_error(result) == {
        "error": f"File not found: {path}",
        "type": "CLIError",
        "status_code": None,
    }


def test_lists_delete_reports_each_id(respx_mock, api_key):
    serve(respx_mock, "error_404_list", f"{LISTS}/1492", method="DELETE")
    respx_mock.delete(f"{LISTS}/1493").respond(
        200, json={"success": True, "message": "List deleted"}
    )
    result = runner.invoke(app, ["--json", "lists", "delete", "1492", "1493", "--yes"])
    assert result.exit_code == 1
    assert result.stdout == "Deleted list 1493\n"
    assert json.loads(result.stderr) == {
        "error": "List not found",
        "type": "NotFoundError",
        "status_code": 404,
        "id": 1492,
    }


def test_verify_many_422_and_stop_under_json(respx_mock, api_key):
    respx_mock.post(VERIFY).mock(
        side_effect=[
            verdict("a@example.com"),
            fixture_response("error_422_email"),
            fixture_response("error_402"),
        ]
    )
    args = ["--json", "verify", "a@example.com", "nobody@", "b@example.com", "c@example.com"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert [json.loads(line)["email"] for line in result.stdout.splitlines()] == ["a@example.com"]
    lines = result.stderr.splitlines()
    assert lines[0] == "This will use 4 credits."
    skipped = json.loads(lines[1])
    assert skipped["type"] == "ValidationError" and skipped["email"] == "nobody@"
    assert lines[2] == "Verified 1 of 4 addresses; stopped at b@example.com, 1 not sent."
    stopped = json.loads(lines[3])
    assert stopped["type"] == "InsufficientCreditsError"
    assert stopped["email"] == "b@example.com"
    assert len(lines) == 4


def test_text_mode_is_unchanged(respx_mock, api_key):
    serve(respx_mock, "error_402", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "x@example.com"])
    assert result.stderr == COST + "Error: Insufficient credits (available credits: 0)\n"


# verify --fail-on


def test_fail_on_match_exits_3(respx_mock, api_key):
    serve(respx_mock, "verify_invalid", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "nobody@example.com", "--fail-on", "invalid,unknown"])
    assert result.exit_code == 3
    assert result.stdout.startswith("nobody@freemail.example  invalid  score=0\n")
    assert result.stderr == COST


def test_fail_on_no_match_exits_0(respx_mock, api_key):
    serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "x@example.com", "--fail-on", "invalid,unknown"])
    assert result.exit_code == 0


def test_fail_on_trims_and_allows_repeats(respx_mock, api_key):
    serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "x@example.com", "--fail-on", " risky , risky,"])
    assert result.exit_code == 3


def test_fail_on_json_prints_the_payload_then_exits_3(respx_mock, api_key):
    serve(respx_mock, "verify_invalid", VERIFY, method="POST")
    result = runner.invoke(app, ["--json", "verify", "x@example.com", "--fail-on", "invalid"])
    assert result.exit_code == 3
    assert json.loads(result.stdout) == fixture_data("verify_invalid")


@pytest.mark.parametrize("value", ["bogus", "invalid,bogus", "", "Invalid"])
def test_fail_on_bad_value_is_usage_error(respx_mock, guard, api_key, value):
    result = runner.invoke(app, ["verify", "x@example.com", "--fail-on", value])
    assert result.exit_code == 2
    assert result.stderr.startswith("Error: --fail-on accepts valid, risky, invalid, unknown")
    assert not guard.called


def test_fail_on_api_error_exits_1(respx_mock, api_key):
    serve(respx_mock, "error_401", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "x@example.com", "--fail-on", "invalid"])
    assert result.exit_code == 1


def test_fail_on_any_of_many(respx_mock, api_key):
    respx_mock.post(VERIFY).mock(
        side_effect=[verdict("a@example.com", "verify_invalid"), verdict("b@example.com")]
    )
    args = ["verify", "a@example.com", "b@example.com", "--fail-on", "invalid"]
    result = runner.invoke(app, args)
    assert result.exit_code == 3
    assert len(result.stdout.splitlines()) == 2  # everything printed first


def test_fail_on_many_error_outranks_a_match(respx_mock, api_key):
    respx_mock.post(VERIFY).mock(
        side_effect=[
            verdict("a@example.com", "verify_invalid"),
            fixture_response("error_422_email"),
        ]
    )
    result = runner.invoke(app, ["verify", "a@example.com", "nobody@", "--fail-on", "invalid"])
    assert result.exit_code == 1


def test_fail_on_in_help():
    result = runner.invoke(app, ["verify", "--help"])
    assert "--fail-on" in result.stdout


def test_json_mode_ends_with_the_command(respx_mock, api_key, capsys):
    serve(respx_mock, "error_401", VERIFY, method="POST")
    runner.invoke(app, ["--json", "verify", "x@example.com"])
    with pytest.raises(typer.Exit):
        fail("outside a command")
    assert capsys.readouterr().err == "Error: outside a command\n"


# --api-key and MAILTESTER_BASE_URL

CREDITS = f"{BASE_URL}/credits"


def test_api_key_option_wins_over_the_environment(respx_mock, monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_env")
    route = serve(respx_mock, "credits_after", CREDITS)
    result = runner.invoke(app, ["--api-key", "evp_flag", "credits"])
    assert result.exit_code == 0
    assert route.calls.last.request.headers["Authorization"] == "Bearer evp_flag"


def test_api_key_option_wins_over_dotenv(respx_mock, monkeypatch):
    values = {"MAILTESTER_API_KEY": "evp_dotenv"}
    monkeypatch.setattr(mailtester_cli.client, "dotenv_values", lambda *a, **k: values)
    route = serve(respx_mock, "credits_after", CREDITS)
    runner.invoke(app, ["--api-key", "evp_flag", "credits"])
    assert route.calls.last.request.headers["Authorization"] == "Bearer evp_flag"


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_api_key_falls_through(respx_mock, monkeypatch, value):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_env")
    route = serve(respx_mock, "credits_after", CREDITS)
    result = runner.invoke(app, ["--api-key", value, "credits"])
    assert result.exit_code == 0
    assert route.calls.last.request.headers["Authorization"] == "Bearer evp_env"


def test_api_key_never_shows(respx_mock, clock):
    respx_mock.get(CREDITS).mock(side_effect=httpx.ConnectError("refused evp_secret123"))
    result = runner.invoke(app, ["--api-key", "evp_secret123", "credits"])
    assert result.exit_code == 1
    assert "evp_secret123" not in result.stdout + result.stderr
    assert "evp_secret123" not in repr(State(api_key="evp_secret123"))
    help_text = runner.invoke(app, ["--help"]).stdout
    assert "--api-key" in help_text
    assert "evp_" not in help_text


def test_base_url_from_the_environment(respx_mock, api_key, monkeypatch):
    monkeypatch.setenv("MAILTESTER_BASE_URL", "http://localhost:8080/api/v1/")
    route = respx_mock.get("http://localhost:8080/api/v1/credits").respond(
        200, json={"success": True, "data": fixture_data("credits_after")}
    )
    result = runner.invoke(app, ["credits"])
    assert result.exit_code == 0
    assert route.called


def test_base_url_from_dotenv(respx_mock, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "MAILTESTER_API_KEY=evp_dotenv\nMAILTESTER_BASE_URL=https://mock.example/api/v1\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mailtester_cli.client, "dotenv_values", dotenv.dotenv_values)
    route = respx_mock.get("https://mock.example/api/v1/credits").respond(
        200, json={"success": True, "data": fixture_data("credits_after")}
    )
    result = runner.invoke(app, ["credits"])
    assert result.exit_code == 0
    assert route.calls.last.request.headers["Authorization"] == "Bearer evp_dotenv"


def test_base_url_argument_wins(monkeypatch):
    monkeypatch.setenv("MAILTESTER_BASE_URL", "https://mock.example/api/v1")
    with MailTester(api_key="evp_test", base_url="https://other.example/api/v1/") as mt:
        assert mt.base_url == "https://other.example/api/v1"
    with MailTester(api_key="evp_test") as mt:
        assert mt.base_url == "https://mock.example/api/v1"


def test_overridden_base_url_is_named_on_connection_error(respx_mock, api_key, monkeypatch, clock):
    monkeypatch.setenv("MAILTESTER_BASE_URL", "http://localhost:8080/api/v1")
    respx_mock.get("http://localhost:8080/api/v1/credits").mock(
        side_effect=httpx.ConnectError("refused")
    )
    result = runner.invoke(app, ["credits"])
    assert result.stderr.splitlines()[-1] == (
        "Error: Could not reach MailTester at http://localhost:8080/api/v1: refused"
    )


def mock_dotenv(monkeypatch, tmp_path, lines: list[str]):
    (tmp_path / ".env").write_text("\n".join(lines) + "\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mailtester_cli.client, "dotenv_values", dotenv.dotenv_values)


@pytest.mark.parametrize("key_from", ["environment", "--api-key"])
def test_dotenv_base_url_cannot_redirect_a_key_from_elsewhere(
    respx_mock, monkeypatch, tmp_path, key_from
):
    mock_dotenv(
        monkeypatch,
        tmp_path,
        ["MAILTESTER_API_KEY=evp_dotenv", "MAILTESTER_BASE_URL=http://collector.example/api/v1"],
    )
    args = ["credits"]
    if key_from == "environment":
        monkeypatch.setenv("MAILTESTER_API_KEY", "evp_real")
    else:
        args = ["--api-key", "evp_real", "credits"]
    collector = respx_mock.get("http://collector.example/api/v1/credits").respond(500)
    real = serve(respx_mock, "credits_after", CREDITS)
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert not collector.called
    assert real.calls.last.request.headers["Authorization"] == "Bearer evp_real"


def test_dotenv_base_url_without_a_dotenv_key_is_ignored(respx_mock, monkeypatch, tmp_path):
    mock_dotenv(monkeypatch, tmp_path, ["MAILTESTER_BASE_URL=http://collector.example/api/v1"])
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_real")
    real = serve(respx_mock, "credits_after", CREDITS)
    assert runner.invoke(app, ["credits"]).exit_code == 0
    assert real.called


def test_environment_base_url_applies_to_any_key(respx_mock, monkeypatch):
    monkeypatch.setenv("MAILTESTER_BASE_URL", "http://localhost:8080/api/v1")
    route = respx_mock.get("http://localhost:8080/api/v1/credits").respond(
        200, json={"success": True, "data": fixture_data("credits_after")}
    )
    assert runner.invoke(app, ["--api-key", "evp_flag", "credits"]).exit_code == 0
    assert route.calls.last.request.headers["Authorization"] == "Bearer evp_flag"


def test_base_url_credentials_are_not_shown(respx_mock, api_key, monkeypatch, clock):
    monkeypatch.setenv("MAILTESTER_BASE_URL", "http://user:hunter2@proxy.example/api/v1")
    respx_mock.get(url__regex=r".*/credits$").mock(side_effect=httpx.ConnectError("refused"))
    result = runner.invoke(app, ["credits"])
    assert result.stderr.splitlines()[-1] == (
        "Error: Could not reach MailTester at http://proxy.example/api/v1: refused"
    )
    with MailTester(api_key="evp_test") as mt:
        assert "hunter2" not in repr(mt)


def test_declined_prompt_stays_text_under_json(respx_mock, guard, api_key, tmp_path):
    path = tmp_path / "emails.txt"
    path.write_text("\n".join(f"user{i}@example.com" for i in range(100)) + "\n")
    result = runner.invoke(app, ["--json", "verify", "--file", str(path)], input="n\n")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.endswith("Aborted.\n")  # not an error, so no JSON object
    assert not guard.called
