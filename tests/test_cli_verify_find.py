import copy
import io
import json
import re

import httpx
import pytest
import typer
from conftest import load_fixture, sent_json, serve
from typer.testing import CliRunner

from mailtester_cli.cli import app
from mailtester_cli.client import BASE_URL
from mailtester_cli.commands._shared import confirm, fmt_int, handle_errors

VERIFY = f"{BASE_URL}/verify"
FINDER = f"{BASE_URL}/finder/email"
CREDITS = f"{BASE_URL}/credits"
VERIFY_COST = "This will use 1 credit.\n"
README_ORDER = ["verify", "batch", "find", "find-domain", "find-company", "lists", "credits"]

runner = CliRunner()


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_test")


def fixture_data(name: str) -> dict:
    return copy.deepcopy(load_fixture(name)[2]["data"])


def listed_commands(help_text: str) -> list[str]:
    section = help_text.split("Commands", 1)[1]
    return re.findall(r"^│ ([\w-]+)\s", section, flags=re.MULTILINE)


# app


def test_help_lists_commands_in_readme_order():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--json" in result.stdout
    assert "--version" in result.stdout
    assert listed_commands(result.stdout) == README_ORDER


# verify


def test_verify_human_output(respx_mock, api_key):
    serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "batch1@example.com"])
    assert result.exit_code == 0
    assert result.stdout == (
        "batch1@example.com  risky  score=50\n"
        "  reason      CATCH_ALL\n"
        "  provider    -\n"
        "  flags       accept_all\n"
    )
    assert result.stderr == VERIFY_COST


def test_verify_json_still_notes_cost(respx_mock, api_key):
    serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = runner.invoke(app, ["--json", "verify", "batch1@example.com"])
    assert result.exit_code == 0
    assert result.stderr == VERIFY_COST


def test_verify_shows_suggestion_and_flags(respx_mock, api_key):
    data = fixture_data("verify_single")
    data.update(did_you_mean="batch1@gmail.com", free=True, role=True, smtp_provider="Google")
    respx_mock.post(VERIFY).respond(200, json={"success": True, "data": data})
    result = runner.invoke(app, ["verify", "batch1@example.com"])
    assert result.exit_code == 0
    assert result.stdout.splitlines()[2:] == [
        "  provider    Google",
        "  flags       role, free, accept_all",
        "  suggestion  batch1@gmail.com",
    ]


def test_verify_json(respx_mock, api_key):
    serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = runner.invoke(app, ["--json", "verify", "batch1@example.com"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data("verify_single")


def test_verify_sends_options(respx_mock, api_key):
    route = serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "x@example.com", "--timeout", "10", "--no-smtp"])
    assert result.exit_code == 0
    assert sent_json(route) == {"email": "x@example.com", "timeout": 10, "smtp_check": False}


def test_verify_omits_unset_options(respx_mock, api_key):
    route = serve(respx_mock, "verify_single", VERIFY, method="POST")
    runner.invoke(app, ["verify", "x@example.com"])
    assert sent_json(route) == {"email": "x@example.com"}


def test_verify_rejects_out_of_range_timeout(api_key):
    result = runner.invoke(app, ["verify", "x@example.com", "--timeout", "61"])
    assert result.exit_code == 2


# find


def test_find_human_output(respx_mock, api_key):
    serve(respx_mock, "finder_email", FINDER, method="POST")
    result = runner.invoke(app, ["find", "Jane", "Doe", "example.com"])
    assert result.exit_code == 0
    assert result.stdout == "jane.doe@example.com  risky  confidence=40  pattern={first}.{last}\n"
    assert result.stderr == "This will use 3 credits.\n"


def test_find_omits_missing_pattern(respx_mock, api_key):
    data = fixture_data("finder_email") | {"pattern": None}
    respx_mock.post(FINDER).respond(200, json={"success": True, "data": data})
    result = runner.invoke(app, ["find", "Jane", "Doe", "example.com"])
    assert result.stdout == "jane.doe@example.com  risky  confidence=40\n"


def _not_found(respx_mock):
    data = fixture_data("finder_email") | {"email": None, "pattern": None, "candidates": []}
    respx_mock.post(FINDER).respond(200, json={"success": True, "data": data})
    return data


def test_find_not_found(respx_mock, api_key):
    _not_found(respx_mock)
    result = runner.invoke(app, ["find", "Jane", "Doe", "example.com"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "This will use 3 credits.\nNo address found for Jane Doe at example.com.\n"
    )


def test_find_not_found_json_still_prints_payload(respx_mock, api_key):
    data = _not_found(respx_mock)
    result = runner.invoke(app, ["--json", "find", "Jane", "Doe", "example.com"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == data


def test_find_json(respx_mock, api_key):
    serve(respx_mock, "finder_email", FINDER, method="POST")
    result = runner.invoke(app, ["--json", "find", "Jane", "Doe", "example.com"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data("finder_email")


# errors


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("error_401", "Error: Invalid API key\n"),
        ("error_402", "Error: Insufficient credits (available credits: 0)\n"),
        (
            "error_429",
            "Rate limited, retrying in 30s...\n" * 2
            + "Error: Rate limit exceeded. Retry in 30s.\n",
        ),
        ("error_404", None),
    ],
)
def test_api_errors(respx_mock, api_key, clock, fixture, expected):
    serve(respx_mock, fixture, VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "x@example.com"])
    assert result.exit_code == 1
    assert result.stdout == ""
    if expected is None:
        expected = f"Error: {load_fixture(fixture)[2]['error']}\n"
    assert result.stderr == VERIFY_COST + expected
    assert "Traceback" not in result.stdout + result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_missing_key(monkeypatch):
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    result = runner.invoke(app, ["verify", "x@example.com"])
    assert result.exit_code == 1
    assert result.stderr.startswith(VERIFY_COST + "Error: ")
    assert "MAILTESTER_API_KEY" in result.stderr


def test_transport_error(respx_mock, api_key, clock):
    respx_mock.post(VERIFY).mock(side_effect=httpx.ConnectError)
    result = runner.invoke(app, ["verify", "x@example.com"])
    assert result.exit_code == 1
    assert result.stderr.startswith(
        VERIFY_COST
        + "Could not reach MailTester, retrying in 1s...\n"
        + "Could not reach MailTester, retrying in 2s...\n"
        + "Error: Could not reach MailTester"
    )


# retries


def fixture_response(name: str) -> httpx.Response:
    status, headers, body = load_fixture(name)
    return httpx.Response(status, headers=headers, json=body)


@pytest.mark.parametrize("args", [["verify"], ["--json", "verify"]])
def test_verify_prints_a_retry_line_then_the_result(respx_mock, api_key, clock, args):
    respx_mock.post(VERIFY).mock(
        side_effect=[fixture_response("error_429"), fixture_response("verify_single")]
    )
    result = runner.invoke(app, [*args, "batch1@example.com"])
    assert result.exit_code == 0
    assert result.stderr == VERIFY_COST + "Rate limited, retrying in 30s...\n"
    if "--json" in args:
        assert json.loads(result.stdout) == fixture_data("verify_single")
    else:
        assert result.stdout.startswith("batch1@example.com  risky  score=50\n")
    assert clock.sleeps == [30]


@pytest.mark.parametrize(
    ("failure", "line"),
    [
        (fixture_response("error_502"), "MailTester returned 502, retrying in 1s..."),
        (httpx.ConnectError("refused"), "Could not reach MailTester, retrying in 1s..."),
    ],
)
def test_credits_prints_a_retry_line_for_5xx_and_connection_errors(
    respx_mock, api_key, clock, failure, line
):
    respx_mock.get(CREDITS).mock(side_effect=[failure, fixture_response("credits_before")])
    result = runner.invoke(app, ["credits"])
    assert result.exit_code == 0
    assert result.stderr == f"{line}\n"


def test_verify_does_not_replay_a_5xx(respx_mock, api_key, clock):
    route = serve(respx_mock, "error_500", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "x@example.com"])
    assert result.exit_code == 1
    assert result.stderr == VERIFY_COST + f"Error: {load_fixture('error_500')[2]['error']}\n"
    assert route.call_count == 1


def test_keyboard_interrupt_exits_130(capsys):
    with pytest.raises(typer.Exit) as exc_info, handle_errors():
        raise KeyboardInterrupt
    assert exc_info.value.exit_code == 130
    assert capsys.readouterr().err == "Interrupted.\n"


def test_other_exceptions_propagate():
    with pytest.raises(RuntimeError), handle_errors():
        raise RuntimeError("bug")


# helpers


def test_fmt_int():
    assert fmt_int(1000) == "1,000"
    assert fmt_int(0) == "0"
    assert fmt_int(None) == "-"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("y\n", True),
        ("Y\n", True),
        ("yes\n", True),
        (" yes \n", True),
        ("n\n", False),
        ("\n", False),
        ("maybe\n", False),
    ],
)
def test_confirm_answers(monkeypatch, capsys, answer, expected):
    monkeypatch.setattr("sys.stdin", io.StringIO(answer))
    assert confirm("Continue?") is expected
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Continue? [y/N]: "


def test_confirm_eof_declines_and_ends_the_line(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert confirm("Continue?") is False
    assert capsys.readouterr().err == "Continue? [y/N]: \n"


def test_find_domain_without_mx(respx_mock, api_key):
    serve(respx_mock, "finder_no_mx", FINDER, method="POST")
    result = runner.invoke(app, ["find", "Jane", "Doe", "nonexistent-domain-x7q2.invalid"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"This will use 3 credits.\nError: {load_fixture('finder_no_mx')[2]['error']}\n"
    )
