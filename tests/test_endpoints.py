import pytest
from conftest import load_request, sent_json, serve

from mailtester_cli.client import BASE_URL
from mailtester_cli.errors import NotFoundError
from mailtester_cli.models import Credits, FinderCandidate, FinderResult, ListCounts, ListJob

VERIFY = f"{BASE_URL}/verify"
FINDER = f"{BASE_URL}/finder/email"
LISTS = f"{BASE_URL}/lists"
CREDITS = f"{BASE_URL}/credits"


# verify


def test_verify_request_matches_fixture(respx_mock, mt):
    route = serve(respx_mock, "verify_single", VERIFY, method="POST")
    mt.verify("batch1@example.com", smtp_check=True)
    assert sent_json(route) == load_request("verify_single")["json"]
    assert sent_json(route) == {"email": "batch1@example.com", "smtp_check": True}


def test_verify_omits_unset_options(respx_mock, mt):
    route = serve(respx_mock, "verify_single", VERIFY, method="POST")
    mt.verify("batch1@example.com")
    assert sent_json(route) == {"email": "batch1@example.com"}


def test_verify_sends_false_smtp_check(respx_mock, mt):
    route = serve(respx_mock, "verify_single", VERIFY, method="POST")
    mt.verify("batch1@example.com", timeout=10, smtp_check=False)
    assert sent_json(route) == {"email": "batch1@example.com", "timeout": 10, "smtp_check": False}


def test_verify_parses_result(respx_mock, mt):
    serve(respx_mock, "verify_single", VERIFY, method="POST")
    result = mt.verify("batch1@example.com")
    assert result.email == "batch1@example.com"
    assert result.result == "risky"
    assert result.score == 50
    assert result.reason == "CATCH_ALL"
    assert result.accept_all is True
    assert result.smtp_provider is None
    assert len(result.mx_records) == 5
    assert result.spamtrap is False
    assert result.did_you_mean is None
    assert result.is_valid is False


# find_email


def test_find_email_request_matches_fixture(respx_mock, mt):
    route = serve(respx_mock, "finder_email", FINDER, method="POST")
    mt.find_email("Jane", "Doe", "example.com")
    assert sent_json(route) == load_request("finder_email")["json"]


def test_find_email_parses_result(respx_mock, mt):
    serve(respx_mock, "finder_email", FINDER, method="POST")
    result = mt.find_email("Jane", "Doe", "example.com")
    assert isinstance(result, FinderResult)
    assert result.email == "jane.doe@example.com"
    assert result.status == "risky"
    assert result.confidence == 40
    assert result.pattern == "{first}.{last}"
    assert (result.first_name, result.last_name, result.domain) == ("Jane", "Doe", "example.com")
    assert result.credits_used == 3
    assert len(result.candidates) == 15
    assert all(isinstance(c, FinderCandidate) for c in result.candidates)
    assert [c.email for c in result.candidates] == [c["email"] for c in result.raw["candidates"]]
    first = result.candidates[0]
    assert first.email == result.email
    assert first.is_catch_all is True
    assert first.is_role is False
    assert first.reason == "CATCH_ALL"


def test_find_email_404_raises_not_found(respx_mock, mt):
    serve(respx_mock, "error_404", FINDER, method="POST")
    with pytest.raises(NotFoundError) as info:
        mt.find_email("Jane", "Doe", "example.com")
    assert str(info.value) == "Bulk verification not found"
    assert info.value.status_code == 404


# lists


def test_lists_sends_per_page(respx_mock, mt):
    route = serve(respx_mock, "lists_after_batch", LISTS)
    mt.lists(per_page=5)
    params = dict(route.calls.last.request.url.params)
    expected = {k: str(v) for k, v in load_request("lists_after_batch")["params"].items()}
    assert params == expected == {"per_page": "5"}


def test_lists_without_per_page_sends_no_query(respx_mock, mt):
    route = serve(respx_mock, "lists_after_batch", LISTS)
    mt.lists()
    assert route.calls.last.request.url.query == b""


def test_lists_parses_jobs(respx_mock, mt):
    serve(respx_mock, "lists_after_batch", LISTS)
    jobs = mt.lists(per_page=5)
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job, ListJob)
    assert job.id == 1457
    assert job.name == "discovery"
    assert job.filename == "api_upload_1793234509.csv"
    assert job.status == "completed"
    assert job.progress == 100
    assert job.credits_used == 3
    assert isinstance(job.results, ListCounts)
    assert job.results.risky == 2
    assert job.results.catch_all == 2
    assert job.results.invalid == 1
    assert job.results.disposable == 0


def test_lists_empty(respx_mock, mt):
    serve(respx_mock, "lists", LISTS)
    assert mt.lists() == []


# credits


def test_credits_request(respx_mock, mt):
    route = serve(respx_mock, "credits_after", CREDITS)
    mt.credits()
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.query == b""
    assert request.content == b""


def test_credits_parses_balance(respx_mock, mt):
    serve(respx_mock, "credits_after", CREDITS)
    credits = mt.credits()
    assert isinstance(credits, Credits)
    assert credits.balance == 93
    assert credits.total_used == 7
    assert credits.next_renewal is None
    assert credits.account_type == "pay_as_you_go"
    assert not hasattr(credits, "plan")


def test_verify_parses_invalid(respx_mock, mt):
    serve(respx_mock, "verify_invalid", VERIFY, method="POST")
    result = mt.verify("nobody@freemail.example")
    assert (result.result, result.reason, result.score) == ("invalid", "INVALID", 0)
    assert result.free is True
    assert len(result.mx_records) == 5


def test_verify_parses_no_mx(respx_mock, mt):
    serve(respx_mock, "verify_no_mx", VERIFY, method="POST")
    result = mt.verify("test@nonexistent-domain-x7q2.invalid")
    assert (result.result, result.reason) == ("invalid", "NO_MX_RECORD")
    assert result.mx_records == []
