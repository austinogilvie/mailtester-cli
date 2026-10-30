import copy
import json

import pytest
from conftest import load_fixture, serve
from typer.testing import CliRunner

from mailtester_cli.cli import app
from mailtester_cli.client import BASE_URL

CREDITS = f"{BASE_URL}/credits"

runner = CliRunner()


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_test")


def fixture_data(name: str):
    return copy.deepcopy(load_fixture(name)[2]["data"])


def respond(respx_mock, url: str, data) -> None:
    respx_mock.get(url).respond(200, json={"success": True, "data": data})


# credits


def test_credits(respx_mock, api_key):
    serve(respx_mock, "credits_after", CREDITS)
    result = runner.invoke(app, ["credits"])
    assert result.exit_code == 0
    assert result.stdout == (
        "Balance    93 credits\n"
        "Account    pay_as_you_go\n"
        "Rate limit 35 of 60 left, resets 2026-10-29 00:43:00 UTC\n"
    )


def test_credits_rate_limit_without_reset(respx_mock, api_key):
    headers = {"x-ratelimit-limit": "60", "x-ratelimit-remaining": "35"}
    respx_mock.get(CREDITS).respond(
        200, json={"success": True, "data": fixture_data("credits_after")}, headers=headers
    )
    result = runner.invoke(app, ["credits"])
    assert result.stdout.splitlines()[-1] == "Rate limit 35 of 60 left"


def test_credits_without_rate_limit_headers(respx_mock, api_key):
    respond(respx_mock, CREDITS, fixture_data("credits_after"))
    result = runner.invoke(app, ["credits"])
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
