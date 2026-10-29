import copy
import json
import re

import httpx
import pytest
import typer
from conftest import load_fixture, sent_json, serve
from typer.testing import CliRunner

from mailtester_cli.cli import app
from mailtester_cli.client import BASE_URL
from mailtester_cli.commands._shared import fmt_int, handle_errors

VERIFY = f"{BASE_URL}/verify"
FINDER = f"{BASE_URL}/finder/email"
README_ORDER = ["verify", "batch", "find", "lists", "credits"]

runner = CliRunner()


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_test")


def fixture_data(name: str) -> dict:
    return copy.deepcopy(load_fixture(name)[2]["data"])


def listed_commands(help_text: str) -> list[str]:
    section = help_text.split("Commands", 1)[1]
    return re.findall(r"^│ (\w+)\s", section, flags=re.MULTILINE)


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
    assert result.stderr == ""


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
    assert result.stderr == "No address found for Jane Doe at example.com.\n"


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
        ("error_429", "Error: Rate limit exceeded. Retry in 30s.\n"),
        ("error_404", None),
    ],
)
def test_api_errors(respx_mock, api_key, fixture, expected):
    serve(respx_mock, fixture, VERIFY, method="POST")
    result = runner.invoke(app, ["verify", "x@example.com"])
    assert result.exit_code == 1
    assert result.stdout == ""
    if expected is None:
        expected = f"Error: {load_fixture(fixture)[2]['error']}\n"
    assert result.stderr == expected
    assert "Traceback" not in result.stdout + result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_missing_key(monkeypatch):
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    result = runner.invoke(app, ["verify", "x@example.com"])
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: ")
    assert "MAILTESTER_API_KEY" in result.stderr


def test_transport_error(respx_mock, api_key):
    respx_mock.post(VERIFY).mock(side_effect=httpx.ConnectError)
    result = runner.invoke(app, ["verify", "x@example.com"])
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: Could not reach MailTester")


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


def test_find_domain_without_mx(respx_mock, api_key):
    serve(respx_mock, "finder_no_mx", FINDER, method="POST")
    result = runner.invoke(app, ["find", "Jane", "Doe", "nonexistent-domain-x7q2.invalid"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Error: Domain 'nonexistent-domain-x7q2.invalid' has no mail (MX) records.\n"
    )
