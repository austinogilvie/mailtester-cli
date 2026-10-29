import pytest
from conftest import load_fixture, load_request, sent_json, serve

from mailtester_cli.client import BASE_URL
from mailtester_cli.errors import ConflictError, MailTesterError, NotFoundError
from mailtester_cli.models import (
    Credits,
    FinderCandidate,
    FinderResult,
    ListCounts,
    ListJob,
    ListPage,
)

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


def page_body(items: list[dict], **meta) -> dict:
    base = {"current_page": 1, "last_page": 1, "per_page": 100, "total": len(items)}
    return {"success": True, "data": items, "meta": {**base, **meta}}


def item(list_id: int) -> dict:
    return {**load_fixture("list_show")[2]["data"], "id": list_id}


def test_lists_page_sends_page_and_per_page(respx_mock, mt):
    route = serve(respx_mock, "lists_page_2", LISTS, params__eq={"per_page": "1", "page": "2"})
    page = mt.lists_page(page=2, per_page=1)
    assert route.called
    params = dict(route.calls.last.request.url.params)
    assert params == {k: str(v) for k, v in load_request("lists_page_2")["params"].items()}
    assert isinstance(page, ListPage)
    assert (page.current_page, page.last_page, page.per_page, page.total) == (2, 2, 1, 2)
    assert [j.id for j in page.items] == [1457]


def test_lists_page_without_params_sends_no_query(respx_mock, mt):
    route = serve(respx_mock, "lists_after_batch", LISTS)
    mt.lists_page()
    assert route.calls.last.request.url.query == b""


def test_lists_sends_page(respx_mock, mt):
    route = serve(respx_mock, "lists_page_2", LISTS, params__eq={"per_page": "1", "page": "2"})
    jobs = mt.lists(page=2, per_page=1)
    assert isinstance(jobs, list)
    assert all(isinstance(j, ListJob) for j in jobs)
    assert [j.id for j in jobs] == [1457]
    assert dict(route.calls.last.request.url.params) == {"per_page": "1", "page": "2"}


def test_lists_page_without_meta_raises(respx_mock, mt):
    respx_mock.get(LISTS).respond(200, json={"success": True, "data": []})
    with pytest.raises(MailTesterError) as info:
        mt.lists_page()
    assert str(info.value) == "Unexpected response from MailTester: missing 'meta'"
    assert info.value.body == {"success": True, "data": []}


def test_lists_past_last_page(respx_mock, mt):
    serve(respx_mock, "lists_past_last_page", LISTS, params__eq={"per_page": "1", "page": "3"})
    page = mt.lists_page(page=3, per_page=1)
    assert page.items == []
    assert page.current_page == 3
    assert page.last_page == 2
    assert page.total == 2


def test_iter_lists_walks_every_page(respx_mock, mt):
    page1 = respx_mock.get(LISTS, params__eq={"per_page": "1", "page": "1"}).respond(
        200, json=page_body([item(3)], last_page=3, total=3, per_page=1)
    )
    page2 = respx_mock.get(LISTS, params__eq={"per_page": "1", "page": "2"}).respond(
        200, json=page_body([item(2)], current_page=2, last_page=3, total=3, per_page=1)
    )
    page3 = respx_mock.get(LISTS, params__eq={"per_page": "1", "page": "3"}).respond(
        200, json=page_body([item(1)], current_page=3, last_page=3, total=3, per_page=1)
    )
    assert [j.id for j in mt.iter_lists(per_page=1)] == [3, 2, 1]
    assert page1.call_count == page2.call_count == page3.call_count == 1


def test_iter_lists_default_per_page_100(respx_mock, mt):
    route = serve(
        respx_mock, "lists_after_batch", LISTS, params__eq={"per_page": "100", "page": "1"}
    )
    assert [j.id for j in mt.iter_lists()] == [1457]
    assert route.call_count == 1


def test_iter_lists_stops_on_empty_page(respx_mock, mt):
    respx_mock.get(LISTS, params__eq={"per_page": "100", "page": "1"}).respond(
        200, json=page_body([item(3)], last_page=3, total=3)
    )
    respx_mock.get(LISTS, params__eq={"per_page": "100", "page": "2"}).respond(
        200, json=page_body([], current_page=2, last_page=3, total=3)
    )
    page3 = respx_mock.get(LISTS, params__eq={"per_page": "100", "page": "3"}).respond(
        200, json=page_body([item(1)], current_page=3, last_page=3, total=3)
    )
    assert [j.id for j in mt.iter_lists()] == [3]
    assert page3.call_count == 0


def test_iter_lists_is_lazy(respx_mock, mt):
    route = respx_mock.get(LISTS, params__eq={"per_page": "100", "page": "1"}).respond(
        200, json=page_body([item(3)])
    )
    it = mt.iter_lists()
    assert route.call_count == 0
    next(it)
    assert route.call_count == 1


def test_iter_lists_uses_page_one_last_page(respx_mock, mt):
    respx_mock.get(LISTS, params__eq={"per_page": "100", "page": "1"}).respond(
        200, json=page_body([item(3)], last_page=2, total=2)
    )
    respx_mock.get(LISTS, params__eq={"per_page": "100", "page": "2"}).respond(
        200, json=page_body([item(2)], current_page=2, last_page=3, total=3)
    )
    page3 = respx_mock.get(LISTS, params__eq={"per_page": "100", "page": "3"}).respond(
        200, json=page_body([item(1)], current_page=3, last_page=3, total=3)
    )
    assert [j.id for j in mt.iter_lists()] == [3, 2]
    assert page3.call_count == 0


def test_get_list_parses_item(respx_mock, mt):
    route = serve(respx_mock, "list_show", f"{LISTS}/1457")
    job = mt.get_list(1457)
    assert isinstance(job, ListJob)
    assert job.id == 1457
    assert job.name == "discovery"
    assert job.results.risky == 2
    assert job.credits_used == 3
    assert job.completed_at.tzinfo is not None
    request = route.calls.last.request
    assert request.method == "GET"
    assert request.url.query == b""
    assert request.content == b""


def test_get_list_pending_has_null_timestamps(respx_mock, mt):
    serve(respx_mock, "list_show_pending", f"{LISTS}/1492")
    job = mt.get_list(1492)
    assert job.status == "pending"
    assert job.progress == 0
    assert job.started_at is None
    assert job.completed_at is None
    assert job.credits_used == 1
    assert job.name is None


def test_get_list_not_found(respx_mock, mt):
    serve(respx_mock, "error_404_list", f"{LISTS}/999999999")
    with pytest.raises(NotFoundError) as info:
        mt.get_list(999999999)
    assert str(info.value) == load_fixture("error_404_list")[2]["error"]


def test_get_list_malformed_raises_mailtester_error(respx_mock, mt):
    respx_mock.get(f"{LISTS}/1").respond(200, json={"success": True, "data": {"id": 1}})
    with pytest.raises(MailTesterError) as info:
        mt.get_list(1)
    assert type(info.value) is MailTesterError
    assert str(info.value).startswith("Unexpected response from MailTester: missing ")


def test_delete_list_returns_message(respx_mock, mt):
    route = serve(respx_mock, "list_delete", f"{LISTS}/1492", method="DELETE")
    assert mt.delete_list(1492) == "List deleted"
    request = route.calls.last.request
    assert request.method == "DELETE"
    assert request.content == b""
    assert request.url.query == b""


def test_delete_list_without_message_returns_empty(respx_mock, mt):
    respx_mock.delete(f"{LISTS}/1492").respond(200, json={"success": True})
    assert mt.delete_list(1492) == ""


def test_delete_list_no_content_is_success(respx_mock, mt):
    respx_mock.delete(f"{LISTS}/1492").respond(204)
    assert mt.delete_list(1492) == ""


def test_delete_list_non_json_body_is_success(respx_mock, mt):
    respx_mock.delete(f"{LISTS}/1492").respond(200, text="ok")
    assert mt.delete_list(1492) == ""


def test_delete_list_not_found(respx_mock, mt):
    serve(respx_mock, "error_404_list", f"{LISTS}/999999999", method="DELETE")
    with pytest.raises(NotFoundError) as info:
        mt.delete_list(999999999)
    assert str(info.value) == load_fixture("error_404_list")[2]["error"]


def test_delete_list_conflict_while_processing(respx_mock, mt):
    serve(respx_mock, "error_409_list_processing", f"{LISTS}/1457", method="DELETE")
    with pytest.raises(ConflictError) as info:
        mt.delete_list(1457)
    assert str(info.value) == load_fixture("error_409_list_processing")[2]["error"]
    assert info.value.status_code == 409


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
