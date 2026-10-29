import httpx
import pytest
from conftest import load_fixture, load_request, sent_json, serve

from mailtester_cli.client import BASE_URL
from mailtester_cli.errors import (
    BatchFailedError,
    BatchTimeoutError,
    MailTesterError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from mailtester_cli.models import BatchJob, VerifyResult

SUBMIT = f"{BASE_URL}/verify/batch"
STATUS = f"{BASE_URL}/verify/batch/1457"
RESULTS = f"{BASE_URL}/verify/batch/1457/results"
EMAILS = ["batch1@example.com", "batch2@example.com", "batch3@example.net"]


class FakeClock:
    """Stands in for time.monotonic/time.sleep: sleep records the delay and advances now."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def clock(mt):
    fake = FakeClock()
    mt._sleep = fake.sleep
    mt._clock = fake.now
    return fake


def response(name: str, **data) -> httpx.Response:
    """Build a response from a fixture, overriding keys in its `data`."""
    status, headers, body = load_fixture(name)
    if data:
        body = {**body, "data": {**body["data"], **data}}
    return httpx.Response(status, headers=headers, json=body)


PROCESSING = "batch_status_processing"
COMPLETED = "batch_status_completed"


def status_route(respx_mock, *responses: httpx.Response):
    return respx_mock.get(STATUS).mock(side_effect=list(responses))


# submit_batch


def test_submit_batch_sends_only_emails_and_parses_job(respx_mock, mt):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    job = mt.submit_batch(EMAILS)
    assert sent_json(route) == {"emails": EMAILS}
    assert isinstance(job, BatchJob)
    assert (job.id, job.status, job.total_emails) == (1457, "processing", 3)
    assert job.created_at.tzinfo is not None
    optional = ("progress", "processed_emails", "valid_emails", "invalid_emails")
    optional += ("unknown_emails", "completed_at", "retrieving_results", "notice")
    assert all(getattr(job, name) is None for name in optional)
    assert job.is_complete is False


def test_submit_batch_request_matches_fixture(respx_mock, mt):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    mt.submit_batch(EMAILS, name="discovery", auto_start=True)
    assert sent_json(route) == load_request("batch_submit")["json"]


def test_submit_batch_sends_false_auto_start_and_callback(respx_mock, mt):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    mt.submit_batch(EMAILS, callback_url="https://hooks.example.com/mt", auto_start=False)
    assert sent_json(route) == {
        "emails": EMAILS,
        "callback_url": "https://hooks.example.com/mt",
        "auto_start": False,
    }


def test_submit_batch_sends_duplicates_as_given(respx_mock, mt):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    emails = ["a@example.com", "a@example.com", " A@example.com "]
    mt.submit_batch(emails)
    assert sent_json(route)["emails"] == emails


@pytest.mark.parametrize("count", [0, 10_001])
def test_submit_batch_rejects_bad_size_without_request(respx_mock, mt, count):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    with pytest.raises(ValueError, match="1 to 10,000 addresses"):
        mt.submit_batch([f"u{i}@example.com" for i in range(count)])
    assert route.call_count == 0


def test_submit_batch_accepts_exactly_10000(respx_mock, mt):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    mt.submit_batch([f"u{i}@example.com" for i in range(10_000)])
    assert len(sent_json(route)["emails"]) == 10_000


def test_submit_batch_rejects_bare_string(respx_mock, mt):
    route = serve(respx_mock, "batch_submit", SUBMIT, method="POST")
    with pytest.raises(TypeError):
        mt.submit_batch("batch1@example.com")
    assert route.call_count == 0


# batch_status


def test_batch_status_processing(respx_mock, mt):
    serve(respx_mock, PROCESSING, STATUS)
    job = mt.batch_status(1457)
    assert job.progress == 0
    assert job.completed_at is None
    assert job.retrieving_results is False
    assert job.notice is None
    assert job.is_complete is False


def test_batch_status_completed(respx_mock, mt):
    serve(respx_mock, COMPLETED, STATUS)
    job = mt.batch_status("1457")
    assert job.progress == 100
    assert job.processed_emails == 3
    assert job.invalid_emails == 1
    assert job.completed_at.tzinfo is not None
    assert job.is_complete is True
    assert job.raw == load_fixture(COMPLETED)[2]["data"]


def test_batch_status_unknown_id_raises_not_found(respx_mock, mt):
    serve(respx_mock, "error_404", f"{BASE_URL}/verify/batch/999999999")
    with pytest.raises(NotFoundError) as info:
        mt.batch_status(999999999)
    assert str(info.value) == "Bulk verification not found"


# wait_for_batch


def test_wait_backs_off_until_completed(respx_mock, mt, clock):
    route = status_route(respx_mock, *(response(PROCESSING) for _ in range(5)), response(COMPLETED))
    job = mt.wait_for_batch(1457)
    assert job.status == "completed"
    assert clock.sleeps == [3.0, 4.5, 6.75, 10.0, 10.0]
    assert route.call_count == 6


def test_wait_returns_immediately_when_complete(respx_mock, mt, clock):
    status_route(respx_mock, response(COMPLETED))
    assert mt.wait_for_batch(1457).is_complete
    assert clock.sleeps == []


def test_wait_honors_retry_after(respx_mock, mt, clock):
    status_route(respx_mock, response("error_429"), response(COMPLETED))
    assert mt.wait_for_batch(1457).is_complete
    assert clock.sleeps == [30]


def test_wait_uses_max_interval_without_retry_after(respx_mock, mt, clock):
    limited = httpx.Response(429, json={"success": False, "error": "Rate limit exceeded"})
    status_route(respx_mock, limited, response(COMPLETED))
    assert mt.wait_for_batch(1457).is_complete
    assert clock.sleeps == [10.0]


def test_wait_keeps_polling_unknown_status(respx_mock, mt, clock):
    route = status_route(respx_mock, response(PROCESSING, status="queued"), response(COMPLETED))
    assert mt.wait_for_batch(1457).is_complete
    assert route.call_count == 2


def test_wait_times_out_with_last_job(respx_mock, mt, clock):
    route = respx_mock.get(STATUS).mock(side_effect=lambda request: response(PROCESSING))
    with pytest.raises(BatchTimeoutError) as info:
        mt.wait_for_batch(1457, timeout=10)
    assert clock.sleeps == [3.0, 4.5, 2.5]
    assert route.call_count == 4
    assert info.value.job.status == "processing"
    assert info.value.job.raw == load_fixture(PROCESSING)[2]["data"]
    assert info.value.timeout == 10
    assert info.value.status_code is None
    assert str(info.value) == 'Batch 1457 still "processing" after 10s'


def test_wait_reraises_429_when_its_wait_passes_deadline(respx_mock, mt, clock):
    status_route(respx_mock, response("error_429"))
    with pytest.raises(RateLimitError) as info:
        mt.wait_for_batch(1457, timeout=10)
    assert clock.sleeps == []
    assert info.value.retry_after == 30


@pytest.mark.parametrize(
    "kwargs", [{"interval": 0}, {"interval": -1}, {"max_interval": 0}, {"timeout": -1}]
)
def test_wait_rejects_bad_timing_before_polling(respx_mock, mt, kwargs):
    route = respx_mock.get(STATUS)
    with pytest.raises(ValueError):
        mt.wait_for_batch(1457, **kwargs)
    assert route.call_count == 0


def test_malformed_row_raises_mailtester_error(respx_mock, mt):
    row = dict(load_fixture("batch_results_json_all")[2]["data"][0])
    del row["reason"]
    respx_mock.get(f"{STATUS}/results").respond(200, json={"success": True, "data": [row]})
    with pytest.raises(MailTesterError) as info:
        mt.batch_results(1457)
    assert "missing 'reason'" in str(info.value)
    assert type(info.value) is MailTesterError


@pytest.mark.parametrize("status", ["failed", "cancelled", "canceled", "error"])
def test_wait_raises_on_failure_status(respx_mock, mt, clock, status):
    # Assumed shape: no failure status has been observed, so this edits a processing fixture.
    status_route(respx_mock, response(PROCESSING, status=status))
    with pytest.raises(BatchFailedError) as info:
        mt.wait_for_batch(1457)
    assert isinstance(info.value, MailTesterError)
    assert info.value.job.status == status
    assert info.value.body == info.value.job.raw
    assert str(info.value) == f'Batch 1457 ended with status "{status}"'


def test_wait_calls_on_poll_per_successful_poll(respx_mock, mt, clock):
    status_route(
        respx_mock,
        response(PROCESSING),
        response("error_429"),
        response(PROCESSING),
        response(COMPLETED),
    )
    seen: list[BatchJob] = []
    mt.wait_for_batch(1457, on_poll=seen.append)
    assert [job.status for job in seen] == ["processing", "processing", "completed"]


def test_wait_propagates_404_without_retry(respx_mock, mt, clock):
    route = status_route(respx_mock, response("error_404"), response(COMPLETED))
    with pytest.raises(NotFoundError):
        mt.wait_for_batch(1457)
    assert route.call_count == 1
    assert clock.sleeps == []


# batch_results


def results_route(respx_mock, name: str):
    return serve(respx_mock, name, RESULTS, params__eq=load_request(name)["params"])


def test_batch_results_all(respx_mock, mt):
    results_route(respx_mock, "batch_results_json_all")
    rows = mt.batch_results(1457)
    assert len(rows) == 3
    assert all(isinstance(r, VerifyResult) for r in rows)
    first, _, last = rows
    assert (first.result, first.score, first.reason) == ("risky", 5, "CATCHALL")
    assert first.accept_all is True
    assert first.smtp_provider == "Other"
    assert (first.mx_records, first.spamtrap, first.did_you_mean) == (None, None, None)
    assert (last.result, last.reason, last.disposable) == ("invalid", "DISPOSABLE", True)


@pytest.mark.parametrize(
    ("filter_", "count"), [("risky", 2), ("invalid", 1), ("valid", 0), ("unknown", 0)]
)
def test_batch_results_filters(respx_mock, mt, filter_, count):
    results_route(respx_mock, f"batch_results_json_{filter_}")
    rows = mt.batch_results(1457, filter=filter_)
    assert len(rows) == count
    assert rows == [] or all(r.result == filter_ for r in rows)


def test_batch_results_bogus_filter_raises_validation_error(respx_mock, mt):
    results_route(respx_mock, "batch_results_bogus_filter")
    with pytest.raises(ValidationError) as info:
        mt.batch_results(1457, filter="bogus")
    assert info.value.errors == {"filter": ["The selected filter is invalid."]}


# batch_results_csv


def test_batch_results_csv_returns_body_unchanged(respx_mock, mt):
    results_route(respx_mock, "batch_results_csv_all")
    assert mt.batch_results_csv(1457) == load_fixture("batch_results_csv_all")[2]


def test_batch_results_csv_sends_filter(respx_mock, mt):
    route = respx_mock.get(RESULTS, params__eq={"format": "csv", "filter": "risky"})
    route.respond(200, text="email,result,reason\n", headers={"content-type": "text/csv"})
    assert mt.batch_results_csv(1457, filter="risky") == "email,result,reason\n"
    assert route.call_count == 1
