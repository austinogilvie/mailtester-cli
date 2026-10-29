import copy
import json
import re

import pytest
from conftest import load_fixture, serve
from typer.testing import CliRunner

from mailtester_cli.cli import app
from mailtester_cli.client import BASE_URL

LISTS = f"{BASE_URL}/lists"
CREDITS = f"{BASE_URL}/credits"
HEADER = "ID    NAME       STATUS     EMAILS  VALID  INVALID  RISKY  CREATED"

runner = CliRunner()


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_test")


def fixture_data(name: str):
    return copy.deepcopy(load_fixture(name)[2]["data"])


def respond(respx_mock, url: str, data) -> None:
    respx_mock.get(url).respond(200, json={"success": True, "data": data})


# lists


def test_lists_table(respx_mock, api_key):
    route = serve(respx_mock, "lists_after_batch", LISTS, params__eq={"per_page": "5"})
    result = runner.invoke(app, ["lists", "--per-page", "5"])
    assert result.exit_code == 0
    assert result.stdout == (
        f"{HEADER}\n1457  discovery  completed       3      0        1      2  2026-10-29\n"
    )
    assert route.called


def test_lists_sends_no_query_by_default(respx_mock, api_key):
    route = serve(respx_mock, "lists_after_batch", LISTS)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 0
    assert route.calls.last.request.url.query == b""


def test_lists_empty(respx_mock, api_key):
    serve(respx_mock, "lists", LISTS)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 0
    assert result.stdout == "No lists yet.\n"


@pytest.mark.parametrize("name", ["lists_after_batch", "lists"])
def test_lists_json(respx_mock, api_key, name):
    serve(respx_mock, name, LISTS)
    result = runner.invoke(app, ["--json", "lists"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data(name)


def test_lists_missing_name_and_large_counts(respx_mock, api_key):
    data = fixture_data("lists_after_batch")
    data[0].update(name=None, total_emails=1000, created_at="2026-10-01T23:30:00-02:00")
    respond(respx_mock, LISTS, data)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ID    NAME  STATUS     EMAILS  VALID  INVALID  RISKY  CREATED",
        "1457  -     completed   1,000      0        1      2  2026-10-02",
    ]


def test_lists_rejects_out_of_range_per_page(api_key):
    assert runner.invoke(app, ["lists", "--per-page", "0"]).exit_code == 2
    assert runner.invoke(app, ["lists", "--per-page", "101"]).exit_code == 2


def test_lists_api_error(respx_mock, api_key):
    serve(respx_mock, "error_401", LISTS)
    result = runner.invoke(app, ["lists"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: Invalid API key\n"


# credits


def test_credits(respx_mock, api_key):
    serve(respx_mock, "credits_after", CREDITS)
    result = runner.invoke(app, ["credits"])
    assert result.exit_code == 0
    assert result.stdout == "Balance    93 credits\nAccount    pay_as_you_go\n"


def test_credits_monthly_allowance(respx_mock, api_key):
    data = fixture_data("credits_after")
    data.update(monthly_credit_amount=1000, next_renewal="2026-11-01T00:00:00+00:00")
    respond(respx_mock, CREDITS, data)
    result = runner.invoke(app, ["credits"])
    assert result.exit_code == 0
    assert result.stdout.splitlines()[-1] == "Monthly    1,000 (renews 2026-11-01)"


def test_credits_monthly_without_renewal(respx_mock, api_key):
    data = fixture_data("credits_after") | {"monthly_credit_amount": 1000, "balance": 12345}
    respond(respx_mock, CREDITS, data)
    result = runner.invoke(app, ["credits"])
    assert result.stdout.splitlines() == [
        "Balance    12,345 credits",
        "Account    pay_as_you_go",
        "Monthly    1,000",
    ]


def test_credits_json(respx_mock, api_key):
    serve(respx_mock, "credits_after", CREDITS)
    result = runner.invoke(app, ["--json", "credits"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == fixture_data("credits_after")


# help


def test_help_lists_account_commands_after_find():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    section = result.stdout.split("Commands", 1)[1]
    listed = re.findall(r"^│ (\w+)\s", section, flags=re.MULTILINE)
    assert listed.index("find") < listed.index("lists") < listed.index("credits")
    assert "List your bulk verification jobs." in result.stdout
    assert "Show your credit balance." in result.stdout
