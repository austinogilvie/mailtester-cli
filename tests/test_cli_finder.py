import copy
import json

import pytest
from conftest import load_fixture, load_request, sent_json, serve
from typer.testing import CliRunner

from mailtester_cli.cli import app
from mailtester_cli.client import BASE_URL

FINDER_DOMAIN = f"{BASE_URL}/finder/domain"
FINDER_COMPANY = f"{BASE_URL}/finder/company"
COST = "This will use 3 credits.\n"
ROWS_EXAMPLE_COM = (
    "info@example.com     risky  confidence=55\n"
    "contact@example.com  risky  confidence=55\n"
    "support@example.com  risky  confidence=55\n"
    "sales@example.com    risky  confidence=55\n"
    "team@example.com     risky  confidence=55\n"
)
ROWS_CATCH_ALL = (
    "info@example.net     risky  confidence=40\n"
    "contact@example.net  risky  confidence=40\n"
    "hello@example.net    risky  confidence=40\n"
    "support@example.net  risky  confidence=40\n"
    "sales@example.net    risky  confidence=40\n"
    "admin@example.net    risky  confidence=40\n"
    "team@example.net     risky  confidence=40\n"
    "office@example.net   risky  confidence=40\n"
    "help@example.net     risky  confidence=40\n"
    "service@example.net  risky  confidence=40\n"
)
CATCH_ALL_NOTES = (
    "Showing 10 of 20 role mailboxes.\n"
    "Note: example.net accepts all mail, so these results are low confidence.\n"
)

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


def respond(respx_mock, url: str, data: dict):
    return respx_mock.post(url).respond(200, json={"success": True, "data": data})


def mailbox(email: str, status: str = "risky", confidence: int = 55, **extra) -> dict:
    base = {"is_role": True, "is_catch_all": False, "reason": "ROLE_ACCOUNT"}
    return {"email": email, "status": status, "confidence": confidence, **base, **extra}


# find-domain


def test_find_domain_human_output(respx_mock, api_key):
    serve(respx_mock, "finder_domain", FINDER_DOMAIN, method="POST")
    result = runner.invoke(app, ["find-domain", "example.com"])
    assert result.exit_code == 0
    assert result.stdout == ROWS_EXAMPLE_COM
    assert result.stderr == COST


def test_find_domain_sends_request_body(respx_mock, api_key):
    route = serve(respx_mock, "finder_domain", FINDER_DOMAIN, method="POST")
    runner.invoke(app, ["find-domain", "example.com"])
    assert sent_json(route) == load_request("finder_domain")["json"]
    assert sent_json(route) == {"domain": "example.com"}
    assert route.call_count == 1


def test_find_domain_json(respx_mock, api_key):
    serve(respx_mock, "finder_domain", FINDER_DOMAIN, method="POST")
    result = runner.invoke(app, ["--json", "find-domain", "example.com"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data("finder_domain")
    assert result.stderr == COST


def test_find_domain_catch_all_notes(respx_mock, api_key):
    serve(respx_mock, "finder_domain_catch_all", FINDER_DOMAIN, method="POST")
    result = runner.invoke(app, ["find-domain", "example.net"])
    assert result.exit_code == 0
    assert result.stdout == ROWS_CATCH_ALL
    assert result.stderr == COST + CATCH_ALL_NOTES


def test_find_domain_catch_all_json_has_no_notes(respx_mock, api_key):
    serve(respx_mock, "finder_domain_catch_all", FINDER_DOMAIN, method="POST")
    result = runner.invoke(app, ["--json", "find-domain", "example.net"])
    assert json.loads(result.stdout) == fixture_data("finder_domain_catch_all")
    assert result.stderr == COST


def test_find_domain_partial_catch_all_has_no_note(respx_mock, api_key):
    data = fixture_data("finder_domain_catch_all")
    data["emails"][0]["is_catch_all"] = False
    respond(respx_mock, FINDER_DOMAIN, data)
    result = runner.invoke(app, ["find-domain", "example.net"])
    assert result.stdout == ROWS_CATCH_ALL
    assert result.stderr == COST + "Showing 10 of 20 role mailboxes.\n"


def test_find_domain_truncation_note_without_catch_all(respx_mock, api_key):
    data = fixture_data("finder_domain") | {"total": 12}
    respond(respx_mock, FINDER_DOMAIN, data)
    result = runner.invoke(app, ["find-domain", "example.com"])
    assert result.stdout == ROWS_EXAMPLE_COM
    assert result.stderr == COST + "Showing 5 of 12 role mailboxes.\n"


def test_find_domain_pads_mixed_statuses(respx_mock, api_key):
    data = fixture_data("finder_domain")
    data["emails"] = [
        mailbox("info@example.com", "valid", 95),
        mailbox("support@example.com", "invalid", 0),
        mailbox("hr@example.com", "risky", 40),
    ]
    respond(respx_mock, FINDER_DOMAIN, data)
    result = runner.invoke(app, ["find-domain", "example.com"])
    assert result.stdout == (
        "info@example.com     valid    confidence=95\n"
        "support@example.com  invalid  confidence=0\n"
        "hr@example.com       risky    confidence=40\n"
    )


def test_find_domain_not_found(respx_mock, api_key):
    data = fixture_data("finder_domain") | {"emails": [], "total": 0}
    respond(respx_mock, FINDER_DOMAIN, data)
    result = runner.invoke(app, ["find-domain", "example.com"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == COST + "No role mailboxes found at example.com.\n"


def test_find_domain_not_found_json_still_prints_payload(respx_mock, api_key):
    data = fixture_data("finder_domain") | {"emails": [], "total": 0}
    respond(respx_mock, FINDER_DOMAIN, data)
    result = runner.invoke(app, ["--json", "find-domain", "example.com"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == data
    assert result.stderr == COST + "No role mailboxes found at example.com.\n"


def test_find_domain_no_mx(respx_mock, api_key):
    serve(respx_mock, "finder_domain_no_mx", FINDER_DOMAIN, method="POST")
    result = runner.invoke(app, ["find-domain", "nonexistent-domain-x7q2.invalid"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == COST + f"Error: {load_fixture('finder_domain_no_mx')[2]['error']}\n"
    assert "Traceback" not in result.stdout + result.stderr


def test_find_domain_empty_argument_is_a_422(respx_mock, api_key):
    serve(respx_mock, "finder_domain_422", FINDER_DOMAIN, method="POST")
    result = runner.invoke(app, ["find-domain", ""])
    assert result.exit_code == 1
    message = load_fixture("finder_domain_422")[2]["errors"]["domain"][0]
    assert result.stderr == COST + f"Error: domain: {message}\n"


# find-company


def test_find_company_human_output(respx_mock, api_key):
    serve(respx_mock, "finder_company", FINDER_COMPANY, method="POST")
    result = runner.invoke(app, ["find-company", "Acme"])
    assert result.exit_code == 0
    assert result.stdout == ROWS_EXAMPLE_COM
    assert result.stderr == COST + "Resolved Acme to example.com.\n"


def test_find_company_sends_request_body(respx_mock, api_key):
    route = serve(respx_mock, "finder_company", FINDER_COMPANY, method="POST")
    runner.invoke(app, ["find-company", "Acme"])
    assert sent_json(route) == load_request("finder_company")["json"]
    assert sent_json(route) == {"company": "Acme"}


def test_find_company_json_has_no_resolved_line(respx_mock, api_key):
    serve(respx_mock, "finder_company", FINDER_COMPANY, method="POST")
    result = runner.invoke(app, ["--json", "find-company", "Acme"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data("finder_company")
    assert result.stderr == COST


def test_find_company_multiword_name(respx_mock, api_key):
    data = fixture_data("finder_company") | {"company": "Acme Widgets Ltd"}
    route = respond(respx_mock, FINDER_COMPANY, data)
    result = runner.invoke(app, ["find-company", "Acme Widgets Ltd"])
    assert sent_json(route) == {"company": "Acme Widgets Ltd"}
    assert result.stderr == COST + "Resolved Acme Widgets Ltd to example.com.\n"


def test_find_company_missing(respx_mock, api_key):
    serve(respx_mock, "finder_company_missing", FINDER_COMPANY, method="POST")
    result = runner.invoke(app, ["find-company", "Nowhere"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == COST + "No role mailboxes found for Nowhere (tried example.org).\n"


def test_find_company_missing_json_still_prints_payload(respx_mock, api_key):
    serve(respx_mock, "finder_company_missing", FINDER_COMPANY, method="POST")
    result = runner.invoke(app, ["--json", "find-company", "Nowhere"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == fixture_data("finder_company_missing")
    assert result.stderr == COST + "No role mailboxes found for Nowhere (tried example.org).\n"


def test_find_company_catch_all_names_resolved_domain(respx_mock, api_key):
    data = fixture_data("finder_domain_catch_all") | {"company": "Acme"}
    del data["offset"]
    respond(respx_mock, FINDER_COMPANY, data)
    result = runner.invoke(app, ["find-company", "Acme"])
    assert result.stdout == ROWS_CATCH_ALL
    assert result.stderr == COST + "Resolved Acme to example.net.\n" + CATCH_ALL_NOTES


# errors and usage


@pytest.mark.parametrize(
    ("argv", "url"),
    [
        (["find-domain", "example.com"], FINDER_DOMAIN),
        (["find-company", "Acme"], FINDER_COMPANY),
    ],
)
def test_role_finders_map_api_errors(respx_mock, api_key, argv, url):
    serve(respx_mock, "error_401", url, method="POST")
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == COST + f"Error: {load_fixture('error_401')[2]['error']}\n"
    assert result.exception is None or isinstance(result.exception, SystemExit)


@pytest.mark.parametrize(
    "argv",
    [
        ["find-domain"],
        ["find-domain", "a.com", "b.com"],
        ["find-company"],
        ["find-company", "Acme", "Inc"],
    ],
)
def test_role_finders_require_one_argument(guard, api_key, argv):
    result = runner.invoke(app, argv)
    assert result.exit_code == 2
    assert not guard.called
    assert "This will use" not in result.stderr


# help


def test_help_lists_seven_commands_with_costs():
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    assert "find-domain   Find role mailboxes at a domain. (3 credits)" in result.stdout
    assert "find-company  Find a company's domain and role mailboxes. (3 credits)" in result.stdout
    assert (
        result.stdout.index("find-domain")
        < result.stdout.index("find-company")
        < result.stdout.index("│ lists")
    )


@pytest.mark.parametrize(
    ("command", "text"),
    [
        ("find-domain", "Domain, e.g. acme.com."),
        ("find-company", 'Company name, e.g. "Acme Inc".'),
    ],
)
def test_finder_help_arguments(command, text):
    result = runner.invoke(app, [command, "--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    assert text in result.stdout
