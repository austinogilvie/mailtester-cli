"""The client's retry policy: _retry_delay on its own, then through real requests."""

from datetime import UTC, datetime

import httpx
import pytest
from conftest import load_fixture, serve

from mailtester_cli import MailTester, RetryAttempt
from mailtester_cli.client import BASE_URL, RateLimit, _retry_delay
from mailtester_cli.errors import (
    APIConnectionError,
    AuthenticationError,
    ConflictError,
    InsufficientCreditsError,
    MailTesterError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)
from mailtester_cli.models import Credits

CREDITS = f"{BASE_URL}/credits"
VERIFY = f"{BASE_URL}/verify"
LISTS = f"{BASE_URL}/lists"
UPLOAD = f"{BASE_URL}/verify/batch/upload"


def response(name: str) -> httpx.Response:
    status, headers, body = load_fixture(name)
    return httpx.Response(status, headers=headers, json=body)


def limited(retry_after=None, headers=None) -> httpx.Response:
    body = {"success": False, "error": "Rate limit exceeded"}
    if retry_after is not None:
        body["retry_after"] = retry_after
    return httpx.Response(429, json=body, headers=headers)


def server(status: int) -> httpx.Response:
    return httpx.Response(status, json={"success": False, "error": "engine down"})


def transport(cls: type[httpx.TransportError]) -> APIConnectionError:
    """An APIConnectionError caused by `cls`, as _send raises it."""
    try:
        raise APIConnectionError("Could not reach MailTester") from cls("boom")
    except APIConnectionError as exc:
        return exc


def delay(method, error, attempt=1, *, slept=0.0, budget=60.0, max_retries=2, reset_in=None):
    return _retry_delay(
        method,
        error,
        attempt,
        max_retries=max_retries,
        slept=slept,
        budget=budget,
        reset_in=reset_in,
    )


@pytest.fixture
def calls():
    """An on_retry callback that records every RetryAttempt."""
    return []


def client(calls=None, **kwargs) -> MailTester:
    return MailTester(
        api_key="evp_test", on_retry=calls.append if calls is not None else None, **kwargs
    )


# _retry_delay


RATE = RateLimitError("Rate limit exceeded", status_code=429, retry_after=30)
RATE_BARE = RateLimitError("Rate limit exceeded", status_code=429)
RATE_ZERO = RateLimitError("Rate limit exceeded", status_code=429, retry_after=0)


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_429_waits_retry_after_on_every_method(method):
    assert delay(method, RATE) == 30


@pytest.mark.parametrize("error", [RATE_BARE, RATE_ZERO])
def test_429_without_usable_retry_after_backs_off(error):
    assert [delay("POST", error, n, budget=100, max_retries=5) for n in (1, 2, 3)] == [1, 2, 4]


def test_429_falls_back_to_the_reset_header_before_backoff():
    assert delay("GET", RATE_BARE, reset_in=4.2) == 5
    assert delay("GET", RATE_BARE, reset_in=-3) == 1  # already past: back off
    assert delay("GET", RATE, reset_in=4.2) == 30  # retry_after wins


def test_backoff_doubles_and_caps_at_10():
    error = ServerError("down", status_code=502)
    got = [delay("GET", error, n, budget=1000, max_retries=7) for n in range(1, 8)]
    assert got == [1, 2, 4, 8, 10, 10, 10]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.parametrize(("method", "retried"), [("GET", True), ("DELETE", True), ("POST", False)])
def test_5xx_retried_only_on_idempotent_methods(status, method, retried):
    error = ServerError("down", status_code=status)
    assert delay(method, error) == (1 if retried else None)


@pytest.mark.parametrize("status", [501, 505, 599])
def test_other_5xx_not_retried(status):
    assert delay("GET", ServerError("no", status_code=status)) is None


@pytest.mark.parametrize("cls", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_connection_failures_retried_on_every_method(cls, method):
    assert delay(method, transport(cls)) == 1


@pytest.mark.parametrize(
    "cls",
    [
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
        httpx.LocalProtocolError,
    ],
)
@pytest.mark.parametrize(("method", "retried"), [("GET", True), ("POST", False), ("DELETE", False)])
def test_ambiguous_transport_errors_retried_on_get_only(cls, method, retried):
    assert delay(method, transport(cls)) == (1 if retried else None)


@pytest.mark.parametrize(
    "error",
    [
        MailTesterError("Bad request", status_code=400),
        AuthenticationError("Invalid API key", status_code=401),
        InsufficientCreditsError("Insufficient credits", status_code=402),
        NotFoundError("Not found", status_code=404),
        ConflictError("Conflict", status_code=409),
        ValidationError("Invalid", status_code=422),
        MailTesterError("I'm a teapot", status_code=418),
        MailTesterError("Unexpected response from MailTester"),
    ],
)
def test_client_errors_and_bad_bodies_not_retried(error):
    assert delay("GET", error) is None


def test_attempts_past_max_retries_raise():
    assert delay("GET", RATE, 2) == 30
    assert delay("GET", RATE, 3) is None
    assert delay("GET", RATE, 1, max_retries=0) is None


def test_a_delay_past_the_budget_raises_without_waiting():
    assert delay("GET", RATE, 2, slept=30) == 30  # 30 + 30 is exactly the budget
    assert delay("GET", RATE, 2, slept=30.5) is None
    assert delay("GET", RateLimitError("x", status_code=429, retry_after=61)) is None
    # A retry_after given in milliseconds by mistake can't put a call to sleep for hours.
    assert delay("GET", RateLimitError("x", status_code=429, retry_after=30000)) is None


# Through requests


def test_429_then_success(respx_mock, clock, calls):
    route = respx_mock.get(CREDITS).mock(
        side_effect=[response("error_429"), response("credits_before")]
    )
    assert isinstance(client(calls).credits(), Credits)
    assert clock.sleeps == [30]
    assert route.call_count == 2
    assert len(calls) == 1
    retry = calls[0]
    assert (retry.attempt, retry.max_retries, retry.delay) == (1, 2, 30)
    assert (retry.method, retry.path) == ("GET", "/credits")
    assert isinstance(retry.error, RateLimitError)


def test_429_three_times_raises_the_last_error_unchanged(respx_mock, clock, calls):
    route = serve(respx_mock, "error_429", CREDITS)
    with pytest.raises(RateLimitError) as info:
        client(calls).credits()
    assert clock.sleeps == [30, 30]
    assert route.call_count == 3
    assert [c.attempt for c in calls] == [1, 2]
    e = info.value
    assert (str(e), e.status_code, e.retry_after) == ("Rate limit exceeded", 429, 30)
    assert e.body == load_fixture("error_429")[2]


def test_429_over_budget_raises_at_once(respx_mock, clock):
    route = respx_mock.get(CREDITS).mock(return_value=limited(retry_after=61))
    with pytest.raises(RateLimitError):
        client().credits()
    assert clock.sleeps == []
    assert route.call_count == 1


def test_429_without_retry_after_backs_off(respx_mock, clock):
    respx_mock.get(CREDITS).mock(side_effect=[limited(), response("credits_before")])
    client().credits()
    assert clock.sleeps == [1.0]


def test_429_without_retry_after_waits_for_the_reset_header(respx_mock, clock, monkeypatch):
    monkeypatch.setattr(MailTester, "_wall", staticmethod(lambda: 1_793_382_000.0))
    headers = {"X-RateLimit-Reset": "1793382005"}
    respx_mock.get(CREDITS).mock(side_effect=[limited(headers=headers), response("credits_before")])
    client().credits()
    assert clock.sleeps == [5.0]


def test_post_retries_a_429(respx_mock, clock):
    route = respx_mock.post(VERIFY).mock(
        side_effect=[response("error_429"), response("verify_single")]
    )
    assert client().verify("x@example.com").result == "risky"
    assert route.call_count == 2
    assert clock.sleeps == [30]


@pytest.mark.parametrize("name", ["error_500", "error_502"])
def test_post_never_replays_a_5xx(respx_mock, clock, name):
    route = serve(respx_mock, name, VERIFY, method="POST")
    with pytest.raises(ServerError):
        client().verify("x@example.com")
    assert route.call_count == 1
    assert clock.sleeps == []


def test_get_retries_5xx_with_backoff(respx_mock, clock):
    route = respx_mock.get(CREDITS).mock(
        side_effect=[response("error_502"), response("error_500"), response("credits_before")]
    )
    client().credits()
    assert clock.sleeps == [1.0, 2.0]
    assert route.call_count == 3


@pytest.mark.parametrize(("status", "retried"), [(503, True), (504, True), (501, False)])
def test_get_5xx_statuses(respx_mock, clock, status, retried):
    route = respx_mock.get(CREDITS).mock(side_effect=[server(status), response("credits_before")])
    if retried:
        client().credits()
    else:
        with pytest.raises(ServerError):
            client().credits()
    assert route.call_count == (2 if retried else 1)


def test_delete_retries_a_5xx(respx_mock, clock):
    url = f"{LISTS}/1558"
    route = respx_mock.delete(url).mock(
        side_effect=[response("error_500"), response("list_delete")]
    )
    assert client().delete_list(1558) == "List deleted"
    assert route.call_count == 2


def test_delete_does_not_replay_a_read_timeout(respx_mock, clock):
    route = respx_mock.delete(f"{LISTS}/1558").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(APIConnectionError):
        client().delete_list(1558)
    assert route.call_count == 1
    assert clock.sleeps == []


def test_post_retries_a_connect_error(respx_mock, clock):
    route = respx_mock.post(VERIFY).mock(
        side_effect=[httpx.ConnectError("refused"), response("verify_single")]
    )
    client().verify("x@example.com")
    assert route.call_count == 2
    assert clock.sleeps == [1.0]


def test_post_does_not_replay_a_read_timeout(respx_mock, clock):
    route = respx_mock.post(VERIFY).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(APIConnectionError):
        client().verify("x@example.com")
    assert route.call_count == 1


def test_get_retries_a_read_timeout(respx_mock, clock):
    respx_mock.get(CREDITS).mock(
        side_effect=[httpx.ReadTimeout("slow"), response("credits_before")]
    )
    client().credits()
    assert clock.sleeps == [1.0]


def test_connect_error_three_times_raises_with_the_key_scrubbed(respx_mock, clock):
    respx_mock.get(CREDITS).mock(side_effect=httpx.ConnectError("refused by evp_secret"))
    with pytest.raises(APIConnectionError) as info:
        MailTester(api_key="evp_secret").credits()
    assert clock.sleeps == [1.0, 2.0]
    assert "evp_secret" not in str(info.value)
    assert isinstance(info.value.__cause__, httpx.ConnectError)


def test_upload_retry_resends_the_whole_file(respx_mock, clock, tmp_path):
    path = tmp_path / "leads.txt"
    path.write_text("a@example.com\nb@example.com\n")
    route = respx_mock.post(UPLOAD).mock(
        side_effect=[response("error_429"), response("batch_upload")]
    )
    client().upload_batch(path)
    assert route.call_count == 2
    # httpx seeks the file back to 0 each time it builds the body, so the resend isn't empty.
    assert b"a@example.com\nb@example.com\n" in route.calls.last.request.read()


def test_max_retries_zero_raises_the_first_error(respx_mock, clock):
    route = serve(respx_mock, "error_429", CREDITS)
    with pytest.raises(RateLimitError):
        client(max_retries=0).credits()
    assert route.call_count == 1
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1},
        {"max_retries": 1.5},
        {"max_retries": True},
        {"max_retry_wait": -1},
        {"max_retry_wait": float("nan")},
        {"max_retry_wait": "60"},
    ],
)
def test_bad_knobs_raise_before_any_request(respx_mock, kwargs):
    guard = respx_mock.route().respond(500)
    with pytest.raises(ValueError):
        MailTester(api_key="evp_test", **kwargs)
    assert not guard.called


def test_knobs_are_public(respx_mock):
    mt = MailTester(api_key="evp_test", max_retries=5, max_retry_wait=300)
    assert (mt.max_retries, mt.max_retry_wait) == (5, 300.0)


def test_on_retry_exception_propagates_before_sleeping(respx_mock, clock):
    serve(respx_mock, "error_429", CREDITS)

    def boom(retry: RetryAttempt) -> None:
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        MailTester(api_key="evp_test", on_retry=boom).credits()
    assert clock.sleeps == []


def test_rate_limit_reflects_the_final_response(respx_mock, clock):
    respx_mock.get(CREDITS).mock(side_effect=[response("error_429"), response("credits_before")])
    mt = client()
    mt.credits()
    assert mt.rate_limit == RateLimit(60, 54, datetime(2026, 10, 29, 0, 42, tzinfo=UTC))


def test_iter_lists_retries_a_page(respx_mock, clock):
    page_1 = {
        "success": True,
        "data": [{**load_fixture("list_show")[2]["data"]}],
        "meta": {"current_page": 1, "last_page": 2, "per_page": 1, "total": 2},
    }
    respx_mock.get(LISTS, params__eq={"per_page": "1", "page": "1"}).respond(200, json=page_1)
    respx_mock.get(LISTS, params__eq={"per_page": "1", "page": "2"}).mock(
        side_effect=[response("error_429"), response("lists_page_2")]
    )
    ids = [job.id for job in client().iter_lists(per_page=1)]
    assert ids == [1498, 1491]
    assert clock.sleeps == [30]
