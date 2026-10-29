"""The README's Python examples, run against mocked responses.

Where the README shows illustrative values the fixtures don't have, these tests use inline
fixture copies with those values, or check types. The README stays as written.
"""

import httpx
import pytest
from conftest import load_fixture, serve

from mailtester_cli import MailTester
from mailtester_cli.client import BASE_URL
from mailtester_cli.models import BatchJob, FinderCandidate, ListJob, VerifyResult


@pytest.fixture
def mt(monkeypatch):
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_test")
    with MailTester() as client:  # reads MAILTESTER_API_KEY, as in the README
        yield client


def route_with(respx_mock, method: str, url: str, name: str, **data):
    """Route to a fixture, overriding keys in its `data`."""
    status, headers, body = load_fixture(name)
    return respx_mock.request(method, url).respond(
        status, headers=headers, json={**body, "data": {**body["data"], **data}}
    )


def test_verify(respx_mock, mt):
    route_with(respx_mock, "POST", f"{BASE_URL}/verify", "verify_single", email="jane@acme.com")
    result = mt.verify("jane@acme.com")

    assert result.email == "jane@acme.com"
    assert result.result in {"valid", "risky", "invalid", "unknown"}
    assert result.score == 50
    assert result.reason == "CATCH_ALL"
    assert result.is_valid is False
    assert result.accept_all is True
    assert result.disposable is False
    assert result.did_you_mean is None


def test_batch(respx_mock, mt):
    serve(respx_mock, "batch_submit", f"{BASE_URL}/verify/batch", method="POST")
    respx_mock.get(f"{BASE_URL}/verify/batch/1457").mock(
        side_effect=[
            httpx.Response(200, json=load_fixture(f"batch_status_{s}")[2])
            for s in ("processing", "completed")
        ]
    )
    results_url = f"{BASE_URL}/verify/batch/1457/results"
    for name, params in [
        ("batch_results_json_all", {"format": "json", "filter": "all"}),
        ("batch_results_json_risky", {"format": "json", "filter": "risky"}),
        ("batch_results_csv_all", {"format": "csv", "filter": "all"}),
    ]:
        serve(respx_mock, name, results_url, params__eq=params)
    mt._sleep = lambda seconds: pytest.fail("wait_for_batch slept")

    emails = ["user1@example.com", "user2@example.com", "user3@example.com"]

    job = mt.submit_batch(emails, name="Q3 leads")
    assert isinstance(job, BatchJob)
    assert isinstance(job.id, int)

    job = mt.batch_status(job.id)
    assert job.status == "processing"
    assert isinstance(job.progress, int)

    job = mt.wait_for_batch(job.id)
    assert job.status == "completed"
    results = mt.batch_results(job.id)
    assert isinstance(results, list)
    assert all(isinstance(r, VerifyResult) for r in results)
    risky = mt.batch_results(job.id, filter="risky")
    assert [r.result for r in risky] == ["risky", "risky"]
    csv_text = mt.batch_results_csv(job.id)
    assert isinstance(csv_text, str)
    assert csv_text.splitlines()[0] == "email,result,reason"

    valid = [r.email for r in results if r.is_valid]
    assert valid == []


def test_find_lists_credits(respx_mock, mt):
    route_with(
        respx_mock,
        "POST",
        f"{BASE_URL}/finder/email",
        "finder_email",
        email="jane.doe@acme.com",
        status="valid",
        confidence=95,
        domain="acme.com",
    )
    serve(respx_mock, "lists_after_batch", f"{BASE_URL}/lists")
    route_with(respx_mock, "GET", f"{BASE_URL}/credits", "credits_after", balance=5000)

    match = mt.find_email("Jane", "Doe", "acme.com")
    assert match.email == "jane.doe@acme.com"
    assert match.status == "valid"
    assert match.confidence == 95
    assert match.candidates
    assert all(isinstance(c, FinderCandidate) for c in match.candidates)

    jobs = mt.lists()
    assert jobs
    for job in jobs:
        assert isinstance(job, ListJob)
        assert isinstance(job.id, int)
        assert isinstance(job.name, str | None)
        assert isinstance(job.status, str)

    assert mt.credits().balance == 5000


def test_errors(respx_mock, mt):
    from mailtester_cli.errors import (
        AuthenticationError,
        InsufficientCreditsError,
        MailTesterError,
        RateLimitError,
    )

    def readme_example() -> str:
        try:
            mt.verify("jane@acme.com")
        except InsufficientCreditsError as e:
            return f"Out of credits ({e.available_credits} left)"
        except RateLimitError as e:
            return f"Slow down, retry in {e.retry_after}s"
        except MailTesterError as e:
            return f"MailTester error: {e}"
        return "no error"

    route = respx_mock.post(f"{BASE_URL}/verify")
    expected = {
        "error_402": "Out of credits (0 left)",
        "error_429": "Slow down, retry in 30s",
        "error_401": f"MailTester error: {load_fixture('error_401')[2]['error']}",
    }
    for name, message in expected.items():
        status, headers, body = load_fixture(name)
        route.respond(status, headers=headers, json=body)
        assert readme_example() == message

    # The last route is still the 401.
    with pytest.raises(AuthenticationError):
        mt.verify("jane@acme.com")
