import os
from datetime import UTC, datetime
from pathlib import Path

import dotenv
import httpx
import pytest
from conftest import load_fixture, serve

import mailtester_cli.client
from mailtester_cli import MailTester, __version__
from mailtester_cli.client import BASE_URL, RateLimit
from mailtester_cli.errors import (
    APIConnectionError,
    AuthenticationError,
    ConflictError,
    InsufficientCreditsError,
    MailTesterError,
    MissingAPIKeyError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "api"
CREDITS = f"{BASE_URL}/credits"


def raised(respx_mock, name, api_key="evp_test"):
    serve(respx_mock, name, CREDITS)
    mt = MailTester(api_key=api_key)
    with pytest.raises(MailTesterError) as info:
        mt._data("GET", "/credits")
    return mt, info.value


def raised_inline(respx_mock, status, **respond):
    respx_mock.get(CREDITS).respond(status, **respond)
    with pytest.raises(MailTesterError) as info:
        MailTester(api_key="evp_test")._data("GET", "/credits")
    return info.value


# Key resolution and setup


def test_key_from_argument_beats_env(respx_mock, monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_env")
    route = serve(respx_mock, "credits_before", CREDITS)
    MailTester(api_key="evp_arg")._data("GET", "/credits")
    assert route.calls.last.request.headers["Authorization"] == "Bearer evp_arg"


def test_key_from_env(respx_mock, monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_env")
    route = serve(respx_mock, "credits_before", CREDITS)
    MailTester()._data("GET", "/credits")
    assert route.calls.last.request.headers["Authorization"] == "Bearer evp_env"


def test_key_from_dotenv_in_working_directory(respx_mock, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MAILTESTER_API_KEY=evp_dotenv\nOTHER_SETTING=x\n")
    calls = []

    def fake_find_dotenv(**kwargs):
        calls.append(kwargs)
        return str(env_file)

    monkeypatch.setattr(mailtester_cli.client, "find_dotenv", fake_find_dotenv)
    monkeypatch.setattr(mailtester_cli.client, "dotenv_values", dotenv.dotenv_values)
    route = serve(respx_mock, "credits_before", CREDITS)
    MailTester()._data("GET", "/credits")
    assert calls == [{"usecwd": True}]
    assert route.calls.last.request.headers["Authorization"] == "Bearer evp_dotenv"
    # Only our variable is read; the rest of .env stays out of the process environment.
    assert "OTHER_SETTING" not in os.environ
    assert "MAILTESTER_API_KEY" not in os.environ


def test_dotenv_not_read_when_env_set(monkeypatch):
    calls = []
    monkeypatch.setattr(mailtester_cli.client, "dotenv_values", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_env")
    MailTester()
    assert calls == []


@pytest.mark.parametrize("key", ["evp_abc\ndef", "evp_abc def", "evp_abc\x00", "evp_abc\u00a0def"])
def test_malformed_key_raises_without_echoing_it(key):
    with pytest.raises(MissingAPIKeyError) as info:
        MailTester(api_key=key)
    assert "evp_abc" not in str(info.value)
    assert "MAILTESTER_API_KEY" in str(info.value)


def test_transport_error_message_masks_the_key(respx_mock):
    respx_mock.get(CREDITS).mock(side_effect=httpx.LocalProtocolError("bad header Bearer evp_k"))
    with pytest.raises(APIConnectionError) as info:
        MailTester(api_key="evp_k")._data("GET", "/credits")
    assert "evp_k" not in str(info.value)
    assert "***" in str(info.value)


def test_unparseable_reset_header_is_ignored():
    headers = httpx.Headers({"x-ratelimit-limit": "60", "x-ratelimit-reset": "1793234520000"})
    assert RateLimit.from_headers(headers) == RateLimit(60, None, None)


@pytest.mark.parametrize(
    ("body", "headers", "expected"),
    [
        ({"retry_after": 30.0}, {}, 30),
        ({"retry_after": "30"}, {}, 30),
        ({"retry_after": 2.5}, {}, 3),
        ({}, {"Retry-After": "45"}, 45),
        ({"retry_after": True}, {}, None),
    ],
)
def test_retry_after_accepts_numbers_strings_and_header(respx_mock, body, headers, expected):
    respx_mock.get(CREDITS).respond(429, json={"success": False, **body}, headers=headers)
    with pytest.raises(RateLimitError) as info:
        MailTester(api_key="evp_test")._data("GET", "/credits")
    assert info.value.retry_after == expected


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_missing_key(api_key):
    with pytest.raises(MissingAPIKeyError) as info:
        MailTester(api_key=api_key)
    assert "MAILTESTER_API_KEY" in str(info.value)
    assert info.value.status_code is None
    assert info.value.body is None


def test_request_headers(respx_mock):
    route = serve(respx_mock, "credits_before", CREDITS)
    MailTester(api_key="evp_test")._data("GET", "/credits")
    headers = route.calls.last.request.headers
    assert headers["Authorization"] == "Bearer evp_test"
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"] == f"mailtester-cli/{__version__}"


def test_base_url_override(respx_mock):
    route = serve(respx_mock, "credits_before", "http://test.local/api/v1/credits")
    mt = MailTester(api_key="evp_test", base_url="http://test.local/api/v1/")
    assert mt.base_url == "http://test.local/api/v1"
    mt._data("GET", "/credits")
    assert route.called


def test_default_timeout():
    mt = MailTester(api_key="evp_test")
    assert mt.timeout == 90.0
    assert mt._client.timeout.read == 90.0


# Success


def test_200_unwraps_data(respx_mock):
    serve(respx_mock, "credits_before", CREDITS)
    data = MailTester(api_key="evp_test")._data("GET", "/credits")
    assert data["balance"] == 100
    assert data["account_type"] == "pay_as_you_go"
    assert "success" not in data


def test_201_unwraps_data_and_sends_json(respx_mock):
    route = serve(respx_mock, "batch_submit", f"{BASE_URL}/verify/batch", method="POST")
    payload = {"emails": ["batch1@example.com"], "name": "discovery"}
    data = MailTester(api_key="evp_test")._data("POST", "/verify/batch", json=payload)
    assert data == {
        "id": 1457,
        "status": "processing",
        "total_emails": 3,
        "created_at": "2026-10-29T00:41:49+00:00",
    }
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    assert httpx.Response(200, content=request.content).json() == payload


def test_request_returns_raw_csv_response(respx_mock):
    url = f"{BASE_URL}/verify/batch/1457/results"
    route = serve(respx_mock, "batch_results_csv_all", url)
    response = MailTester(api_key="evp_test")._request(
        "GET", "/verify/batch/1457/results", params={"format": "csv", "filter": "all"}
    )
    assert response.text == (FIXTURES / "batch_results_csv_all.csv").read_text()
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert dict(route.calls.last.request.url.params) == {"format": "csv", "filter": "all"}


def test_2xx_without_data_raises(respx_mock):
    e = raised_inline(respx_mock, 200, json={"success": True})
    assert type(e) is MailTesterError
    assert str(e) == "Unexpected response from MailTester"
    assert e.status_code == 200
    assert e.body == {"success": True}


def test_2xx_non_json_raises(respx_mock):
    e = raised_inline(respx_mock, 200, text="not json")
    assert type(e) is MailTesterError
    assert e.body is None


# _body


def test_body_returns_whole_json_object(respx_mock, mt):
    serve(respx_mock, "list_delete", f"{BASE_URL}/lists/1492", method="DELETE")
    body = mt._body("DELETE", "/lists/1492")
    assert body == {"success": True, "message": "List deleted"}
    assert body == load_fixture("list_delete")[2]


def test_body_non_json_raises(respx_mock, mt):
    respx_mock.delete(f"{BASE_URL}/lists/1492").respond(200, text="not json")
    with pytest.raises(MailTesterError) as info:
        mt._body("DELETE", "/lists/1492")
    e = info.value
    assert type(e) is MailTesterError
    assert str(e) == "Unexpected response from MailTester"
    assert e.status_code == 200
    assert e.body is None


def test_body_json_array_raises(respx_mock, mt):
    respx_mock.delete(f"{BASE_URL}/lists/1492").respond(200, json=[1, 2])
    with pytest.raises(MailTesterError) as info:
        mt._body("DELETE", "/lists/1492")
    e = info.value
    assert type(e) is MailTesterError
    assert e.body is None
    assert e.status_code == 200


def test_body_maps_errors_like_data(respx_mock, mt):
    serve(respx_mock, "error_404_list", f"{BASE_URL}/lists/999999999", method="DELETE")
    with pytest.raises(NotFoundError) as info:
        mt._body("DELETE", "/lists/999999999")
    assert str(info.value) == load_fixture("error_404_list")[2]["error"]
    assert info.value.status_code == 404


# Error mapping


def test_401(respx_mock):
    _, e = raised(respx_mock, "error_401")
    assert isinstance(e, AuthenticationError)
    assert str(e) == "Invalid API key"
    assert e.status_code == 401
    assert e.body["success"] is False


def test_402(respx_mock):
    _, e = raised(respx_mock, "error_402")
    assert isinstance(e, InsufficientCreditsError)
    assert e.available_credits == 0
    assert str(e) == "Insufficient credits"


def test_402_without_available_credits(respx_mock):
    e = raised_inline(respx_mock, 402, json={"success": False, "error": "Insufficient credits"})
    assert isinstance(e, InsufficientCreditsError)
    assert e.available_credits is None


def test_404(respx_mock):
    _, e = raised(respx_mock, "error_404")
    assert isinstance(e, NotFoundError)
    assert str(e) == "Bulk verification not found"


def test_409(respx_mock):
    e = raised_inline(respx_mock, 409, json={"success": False, "error": "List is still processing"})
    assert isinstance(e, ConflictError)
    assert str(e) == "List is still processing"
    assert e.status_code == 409


def test_422(respx_mock):
    _, e = raised(respx_mock, "error_422")
    assert isinstance(e, ValidationError)
    assert e.errors == {"emails": ["The emails field is required."]}
    assert str(e) == "emails: The emails field is required."


def test_422_joins_fields(respx_mock):
    errors = {
        "format": ["The selected format is invalid."],
        "filter": ["The selected filter is invalid.", "The filter must be a string."],
    }
    e = raised_inline(respx_mock, 422, json={"success": False, "errors": errors})
    assert isinstance(e, ValidationError)
    assert e.errors == errors
    assert str(e) == (
        "format: The selected format is invalid.; "
        "filter: The selected filter is invalid.; "
        "filter: The filter must be a string."
    )


def test_422_wraps_bare_string(respx_mock):
    body = {"success": False, "errors": {"emails": "The emails field is required."}}
    e = raised_inline(respx_mock, 422, json=body)
    assert e.errors == {"emails": ["The emails field is required."]}
    assert str(e) == "emails: The emails field is required."


def test_429(respx_mock):
    _, e = raised(respx_mock, "error_429")
    assert isinstance(e, RateLimitError)
    assert e.retry_after == 30
    assert str(e) == "Rate limit exceeded"


def test_429_without_retry_after(respx_mock):
    e = raised_inline(respx_mock, 429, json={"success": False, "error": "Rate limit exceeded"})
    assert isinstance(e, RateLimitError)
    assert e.retry_after is None


def test_500(respx_mock):
    _, e = raised(respx_mock, "error_500")
    assert isinstance(e, ServerError)
    assert str(e) == "Verification failed: SMTP connection error"


def test_502(respx_mock):
    _, e = raised(respx_mock, "error_502")
    assert isinstance(e, ServerError)
    assert str(e) == "The deliverability engine is currently unavailable."


@pytest.mark.parametrize(
    "name, cls",
    [
        ("error_401", AuthenticationError),
        ("error_402", InsufficientCreditsError),
        ("error_404", NotFoundError),
        ("error_422", ValidationError),
        ("error_429", RateLimitError),
        ("error_500", ServerError),
        ("error_502", ServerError),
    ],
)
def test_subclasses_are_mailtester_errors(respx_mock, api_fixture, name, cls):
    _, e = raised(respx_mock, name)
    assert type(e) is cls
    assert isinstance(e, MailTesterError)
    assert e.status_code == api_fixture(name)[0]


def test_unknown_status_raises_base(respx_mock):
    e = raised_inline(respx_mock, 418, json={"success": False, "error": "I'm a teapot"})
    assert type(e) is MailTesterError
    assert str(e) == "I'm a teapot"
    assert e.status_code == 418


def test_non_json_502(respx_mock):
    e = raised_inline(
        respx_mock, 502, text="<html>Bad Gateway</html>", headers={"content-type": "text/html"}
    )
    assert isinstance(e, ServerError)
    assert str(e) == "HTTP 502"
    assert e.body is None


def test_json_without_error_falls_back_to_status(respx_mock):
    e = raised_inline(respx_mock, 400, json={"success": False})
    assert type(e) is MailTesterError
    assert str(e) == "HTTP 400"
    assert e.body == {"success": False}


# Rate limits


def test_rate_limit_before_any_request():
    assert MailTester(api_key="evp_test").rate_limit == RateLimit(None, None, None)


def test_rate_limit_after_success(respx_mock):
    serve(respx_mock, "credits_before", CREDITS)
    mt = MailTester(api_key="evp_test")
    mt._data("GET", "/credits")
    assert mt.rate_limit == RateLimit(60, 54, datetime(2026, 10, 29, 0, 42, tzinfo=UTC))


def test_rate_limit_updated_on_error(respx_mock):
    mt, e = raised(respx_mock, "error_404")
    assert isinstance(e, NotFoundError)
    assert mt.rate_limit.remaining == 33
    assert mt.rate_limit.reset == datetime(2026, 10, 29, 0, 43, tzinfo=UTC)


def test_rate_limit_missing_reset_on_401(respx_mock):
    mt, _ = raised(respx_mock, "error_401")
    assert mt.rate_limit == RateLimit(60, 34, None)


def test_rate_limit_without_headers(respx_mock):
    mt, _ = raised(respx_mock, "error_500")
    assert mt.rate_limit == RateLimit(None, None, None)


def test_rate_limit_ignores_non_integer_headers(respx_mock):
    headers = {"X-RateLimit-Limit": "lots", "X-RateLimit-Remaining": "5"}
    respx_mock.get(CREDITS).respond(200, headers=headers, json={"success": True, "data": {}})
    mt = MailTester(api_key="evp_test")
    mt._data("GET", "/credits")
    assert mt.rate_limit == RateLimit(None, 5, None)


# Transport


def test_connect_error(respx_mock):
    respx_mock.get(CREDITS).mock(side_effect=httpx.ConnectError("connection refused"))
    mt = MailTester(api_key="evp_secret")
    with pytest.raises(APIConnectionError) as info:
        mt._data("GET", "/credits")
    e = info.value
    assert e.status_code is None
    assert e.body is None
    assert isinstance(e.__cause__, httpx.ConnectError)
    assert str(e) == "Could not reach MailTester: connection refused"
    assert "evp_secret" not in str(e)
    assert mt.rate_limit == RateLimit(None, None, None)


def test_read_timeout(respx_mock):
    respx_mock.get(CREDITS).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(APIConnectionError) as info:
        MailTester(api_key="evp_test")._data("GET", "/credits")
    assert isinstance(info.value.__cause__, httpx.ReadTimeout)


def test_empty_transport_message_uses_class_name(respx_mock):
    respx_mock.get(CREDITS).mock(side_effect=httpx.ReadTimeout(""))
    with pytest.raises(APIConnectionError) as info:
        MailTester(api_key="evp_test")._data("GET", "/credits")
    assert str(info.value) == "Could not reach MailTester: ReadTimeout"


# Secrecy


def test_repr_hides_key():
    mt = MailTester(api_key="evp_secret")
    assert repr(mt) == f"MailTester(base_url={BASE_URL!r})"
    assert "evp_secret" not in repr(mt)


def test_auth_error_hides_key(respx_mock):
    _, e = raised(respx_mock, "error_401", api_key="evp_secret")
    assert isinstance(e, AuthenticationError)
    for text in (str(e), repr(e), repr(e.body)):
        assert "evp_secret" not in text


# Ownership


def test_close_owned_client_twice():
    mt = MailTester(api_key="evp_test")
    mt.close()
    assert mt._client.is_closed
    mt.close()


def test_context_manager_closes_owned_client():
    with MailTester(api_key="evp_test") as mt:
        assert not mt._client.is_closed
    assert mt._client.is_closed


def test_injected_client_left_open(respx_mock):
    route = serve(respx_mock, "credits_before", CREDITS)
    client = httpx.Client()
    with MailTester(api_key="evp_test", http_client=client) as mt:
        assert mt._client is client
        mt._data("GET", "/credits")
    assert route.called
    assert not client.is_closed
    assert "Authorization" not in client.headers
    client.close()


# 012 discovery: error shapes seen live


def test_422_with_error_string(respx_mock):
    _, e = raised(respx_mock, "finder_no_mx")
    assert isinstance(e, ValidationError)
    assert e.errors == {}
    assert str(e) == "Domain 'nonexistent-domain-x7q2.invalid' has no mail (MX) records."


def test_422_per_item_keys(respx_mock):
    _, e = raised(respx_mock, "error_422_bad_emails")
    assert isinstance(e, ValidationError)
    assert list(e.errors) == ["emails.0", "emails.1"]
    assert str(e).startswith("emails.0: The emails.0 field must be a valid email address.; ")


def test_400_uses_api_message(respx_mock):
    _, e = raised(respx_mock, "batch_results_not_complete")
    assert type(e) is MailTesterError
    assert e.status_code == 400
    assert str(e) == "Bulk verification is not complete yet"
