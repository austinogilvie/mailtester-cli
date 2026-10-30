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


# verify many

THREE = ["batch1@example.com", "nobody@freemail.example", "nobody@nomx.invalid"]
THREE_LINES = [
    "batch1@example.com       risky    score=50   CATCH_ALL",
    "nobody@freemail.example  invalid  score=0    INVALID",
    "nobody@nomx.invalid      invalid  score=0    NO_MX_RECORD",
]


def fixture_response(name: str) -> httpx.Response:
    status, headers, body = load_fixture(name)
    return httpx.Response(status, headers=headers, json=body)


def verdict(email: str, name: str = "verify_single") -> httpx.Response:
    """A 200 for `email`: the fixture's data with the address swapped in."""
    data = fixture_data(name) | {"email": email}
    return httpx.Response(200, json={"success": True, "data": data})


def three_verdicts() -> list[httpx.Response]:
    names = ["verify_single", "verify_invalid", "verify_no_mx"]
    return [verdict(email, name) for email, name in zip(THREE, names, strict=True)]


def many(n: int) -> list[str]:
    return [f"user{i}@example.com" for i in range(n)]


def write_emails(tmp_path, lines: list[str]) -> str:
    path = tmp_path / "emails.txt"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


@pytest.fixture
def guard(respx_mock):
    """A catch-all route: any request at all shows up as guard.called."""
    return respx_mock.route().respond(500)


def test_verify_single_prints_cost_note(respx_mock, api_key):
    serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "batch1@example.com"])
    assert result.stdout.startswith("batch1@example.com  risky  score=50\n")
    assert result.stderr == "This will use 1 credit.\n"


def test_verify_many_lines(respx_mock, api_key):
    route = respx_mock.post(VERIFY).mock(side_effect=three_verdicts())
    result = runner.invoke(app, ["verify", *THREE])
    assert result.exit_code == 0
    assert result.stdout.splitlines() == THREE_LINES
    assert result.stderr == "This will use 3 credits.\n"
    assert [json.loads(c.request.content)["email"] for c in route.calls] == THREE


def test_verify_many_json_lines(respx_mock, api_key):
    respx_mock.post(VERIFY).mock(side_effect=three_verdicts())
    result = runner.invoke(app, ["--json", "verify", *THREE])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["result"] for line in lines] == ["risky", "invalid", "invalid"]
    assert json.loads(lines[1]) == fixture_data("verify_invalid")
    assert all(line.startswith('{"email": ') for line in lines)  # one object per line


def test_verify_file_and_args_combine(respx_mock, api_key, tmp_path):
    route = respx_mock.post(VERIFY).mock(side_effect=three_verdicts())
    path = write_emails(tmp_path, ["", f"  {THREE[1]}", THREE[2]])
    result = runner.invoke(app, ["verify", THREE[0], "--file", path])
    assert result.exit_code == 0
    assert [json.loads(c.request.content)["email"] for c in route.calls] == THREE
    assert result.stdout.splitlines() == THREE_LINES


def test_verify_file_stdin(respx_mock, api_key):
    route = respx_mock.post(VERIFY).mock(side_effect=three_verdicts())
    stdin = ("﻿" + "\n".join(THREE) + "\n").encode("utf-8")
    result = runner.invoke(app, ["verify", "--file", "-"], input=stdin)
    assert result.exit_code == 0
    assert [json.loads(c.request.content)["email"] for c in route.calls] == THREE


def test_verify_one_line_file_prints_the_full_result(respx_mock, api_key, tmp_path):
    serve(respx_mock, "verify_single", VERIFY, method="POST")
    path = write_emails(tmp_path, ["batch1@example.com"])
    result = runner.invoke(app, ["--json", "verify", "--file", path])
    assert result.exit_code == 0
    assert result.stdout.startswith("{\n")
    assert json.loads(result.stdout) == fixture_data("verify_single")


def test_verify_dedupes_before_cost(respx_mock, api_key):
    route = serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "a@x.example", "A@X.EXAMPLE"])
    assert result.exit_code == 0
    assert route.call_count == 1
    assert sent_json(route) == {"email": "a@x.example"}
    assert result.stderr == "Skipped 1 duplicate.\nThis will use 1 credit.\n"
    assert result.stdout.endswith("  flags       accept_all\n")  # the one-address output


def test_verify_no_input_is_usage_error(respx_mock, guard, api_key):
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 2
    assert result.stderr == "Error: Give at least one address or --file.\n"
    assert not guard.called


def test_verify_empty_file(respx_mock, guard, api_key, tmp_path):
    path = write_emails(tmp_path, ["", "  "])
    result = runner.invoke(app, ["verify", "--file", path])
    assert result.exit_code == 1
    assert result.stderr == f"Error: No email addresses in {path}.\n"
    assert not guard.called


def test_verify_missing_file(respx_mock, guard, api_key, tmp_path):
    path = tmp_path / "nope.txt"
    result = runner.invoke(app, ["verify", "--file", str(path)])
    assert result.exit_code == 1
    assert result.stderr == f"Error: File not found: {path}\n"
    assert not guard.called


TIP = (
    "Tip: 100 addresses take about 2 minutes one at a time; "
    "batch submit is faster for a list this size.\n"
)


def test_verify_confirm_at_threshold(respx_mock, guard, api_key, tmp_path):
    path = write_emails(tmp_path, many(100))
    result = runner.invoke(app, ["verify", "--file", path], input="n\n")
    assert result.exit_code == 1
    assert result.stderr.startswith("This will use 100 credits.\n" + TIP + "Continue? [y/N]:")
    assert result.stderr.endswith("Aborted.\n")
    assert result.stdout == ""
    assert not guard.called


def test_verify_confirm_accepted(respx_mock, api_key, tmp_path):
    route = respx_mock.post(VERIFY).mock(side_effect=[verdict(e) for e in many(100)])
    path = write_emails(tmp_path, many(100))
    result = runner.invoke(app, ["verify", "--file", path], input="y\n")
    assert result.exit_code == 0
    assert route.call_count == 100
    assert len(result.stdout.splitlines()) == 100


@pytest.mark.parametrize("flag", ["--yes", "-y"])
def test_verify_yes_skips_prompt_and_tip(respx_mock, api_key, tmp_path, flag):
    respx_mock.post(VERIFY).mock(side_effect=[verdict(e) for e in many(100)])
    result = runner.invoke(app, ["verify", "--file", write_emails(tmp_path, many(100)), flag])
    assert result.exit_code == 0
    assert result.stderr == "This will use 100 credits.\n"


def test_verify_below_threshold_has_no_prompt_or_tip(respx_mock, api_key):
    respx_mock.post(VERIFY).mock(side_effect=[verdict(e) for e in many(99)])
    result = runner.invoke(app, ["verify", *many(99)])
    assert result.exit_code == 0
    assert result.stderr == "This will use 99 credits.\n"


def test_verify_stdin_requires_yes(respx_mock, guard, api_key):
    result = runner.invoke(app, ["verify", "--file", "-"], input="\n".join(many(100)))
    assert result.exit_code == 1
    assert result.stderr == (
        "This will use 100 credits.\n"
        + TIP
        + "Error: Pass --yes to confirm spending 100 credits when reading from stdin.\n"
    )
    assert not guard.called


def test_verify_422_continues(respx_mock, api_key):
    addresses = [THREE[0], "nobody@", THREE[2]]
    respx_mock.post(VERIFY).mock(
        side_effect=[
            verdict(THREE[0]),
            fixture_response("error_422_email"),
            verdict(THREE[2], "verify_no_mx"),
        ]
    )
    result = runner.invoke(app, ["verify", *addresses])
    assert result.exit_code == 1
    assert result.stdout.splitlines() == [
        "batch1@example.com   risky    score=50   CATCH_ALL",
        "nobody@nomx.invalid  invalid  score=0    NO_MX_RECORD",
    ]
    assert result.stderr == (
        "This will use 3 credits.\n"
        "Error: nobody@: email: The email field must be a valid email address.\n"
    )


def test_verify_422_single_has_no_prefix(respx_mock, api_key):
    serve(respx_mock, "error_422_email", VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "nobody@"])
    assert result.exit_code == 1
    assert result.stderr == (
        VERIFY_COST + "Error: email: The email field must be a valid email address.\n"
    )


def test_verify_402_stops(respx_mock, api_key):
    route = respx_mock.post(VERIFY).mock(
        side_effect=[verdict(THREE[0]), fixture_response("error_402"), verdict(THREE[2])]
    )
    result = runner.invoke(app, ["verify", *THREE])
    assert result.exit_code == 1
    assert result.stdout == THREE_LINES[0] + "\n"
    assert result.stderr.endswith(
        "Error: Insufficient credits (available credits: 0)\n"
        "Verified 1 of 3 addresses; 2 were not sent.\n"
    )
    assert route.call_count == 2


def test_verify_stop_after_a_422_counts_only_verdicts(respx_mock, api_key):
    respx_mock.post(VERIFY).mock(
        side_effect=[fixture_response("error_422_email"), fixture_response("error_401")]
    )
    result = runner.invoke(app, ["verify", "nobody@", *THREE])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.endswith(
        "Error: Invalid API key\nVerified 0 of 4 addresses; 3 were not sent.\n"
    )


def test_verify_429_stops_after_the_client_retries(respx_mock, api_key, clock):
    respx_mock.post(VERIFY).mock(
        side_effect=[verdict(THREE[0])] + [fixture_response("error_429")] * 3
    )
    result = runner.invoke(app, ["verify", *THREE])
    assert result.exit_code == 1
    assert result.stderr == (
        "This will use 3 credits.\n"
        + "Rate limited, retrying in 30s...\n" * 2
        + "Error: Rate limit exceeded. Retry in 30s.\n"
        + "Verified 1 of 3 addresses; 2 were not sent.\n"
    )
    assert clock.sleeps == [30, 30]


def test_verify_many_rides_out_a_429(respx_mock, api_key, clock):
    responses = three_verdicts()
    responses.insert(1, fixture_response("error_429"))
    respx_mock.post(VERIFY).mock(side_effect=responses)
    result = runner.invoke(app, ["verify", *THREE])
    assert result.exit_code == 0
    assert result.stdout.splitlines() == THREE_LINES
    assert result.stderr == "This will use 3 credits.\nRate limited, retrying in 30s...\n"


def test_verify_interrupt_reports_count(respx_mock, api_key):
    responses = iter([verdict(THREE[0])])

    def answer(request):
        # respx only raises Exception subclasses from a list, so Ctrl-C comes from a function.
        response = next(responses, None)
        if response is None:
            raise KeyboardInterrupt
        return response

    respx_mock.post(VERIFY).mock(side_effect=answer)
    result = runner.invoke(app, ["verify", *THREE])
    assert result.exit_code == 130
    assert result.stdout == THREE_LINES[0] + "\n"
    assert result.stderr.endswith("Interrupted. Verified 1 of 3 addresses.\n")


def test_verify_options_apply_to_every_request(respx_mock, api_key):
    route = respx_mock.post(VERIFY).mock(side_effect=three_verdicts()[:2])
    result = runner.invoke(app, ["verify", *THREE[:2], "--timeout", "10", "--no-smtp"])
    assert result.exit_code == 0
    assert [json.loads(c.request.content) for c in route.calls] == [
        {"email": email, "timeout": 10, "smtp_check": False} for email in THREE[:2]
    ]


def test_verify_many_missing_key(monkeypatch):
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    result = runner.invoke(app, ["verify", *THREE])
    assert result.exit_code == 1
    assert result.stderr.startswith("This will use 3 credits.\nError: ")
    assert "MAILTESTER_API_KEY" in result.stderr


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
