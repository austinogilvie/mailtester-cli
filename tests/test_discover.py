import importlib.util
import json
from pathlib import Path

import httpx
import respx

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "discover.py"
_spec = importlib.util.spec_from_file_location("discover", SCRIPT)
discover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(discover)


def make_redactor():
    r = discover.Redactor()
    r.domain("acmecorp.io")
    r.name("Jonathan", "Jane")
    r.name("Smithers", "Doe")
    r.email("me@personal.dev", "verify")
    r.secret("evp_realkey123", "API key")
    return r


def test_redacts_input_email_and_its_domain():
    r = make_redactor()
    out = r.text("me@personal.dev via mx1.personal.dev")
    assert out == "verify@example.net via mx1.example.net"


def test_redacts_names_inside_local_parts_and_preserves_case():
    r = make_redactor()
    out = r.text("jsmithers@acmecorp.io, Jonathan.Smithers@acmecorp.io, JONATHAN")
    assert out == "jdoe@example.com, Jane.Doe@example.com, JANE"


def test_input_email_does_not_match_inside_a_longer_address():
    r = discover.Redactor()
    r.domain("lf.org")
    r.name("Linus", "Jane")
    r.name("Torvalds", "Doe")
    r.email("torvalds@lf.org", "verify")
    out = r.text("torvalds@lf.org linus.torvalds@lf.org ltorvalds@lf.org")
    assert out == "verify@example.com jane.doe@example.com ldoe@example.com"


def test_does_not_touch_names_inside_other_words():
    r = make_redactor()
    assert r.text("Smithersville") == "Smithersville"


def test_sweeps_unknown_emails_consistently():
    r = make_redactor()
    out = r.text("owner@account.com and OWNER@account.com and other@account.com")
    assert out == (
        "redacted1@example.invalid and redacted1@example.invalid and redacted2@example.invalid"
    )


def test_redacts_nested_structures():
    r = make_redactor()
    out = r.obj({"data": {"email": "me@personal.dev", "candidates": [{"email": "x@acmecorp.io"}]}})
    assert out == {
        "data": {"email": "verify@example.net", "candidates": [{"email": "x@example.com"}]}
    }


def test_leak_check_reports_labels_not_secret_values():
    r = make_redactor()
    hits = r.leaks("acmecorp something evp_realkey123")
    assert "acmecorp" in hits
    assert "API key" in hits
    assert "evp_realkey123" not in hits


def test_scrubs_list_names_except_our_batch():
    body = {"data": [{"name": "Client leads"}, {"name": "discovery"}]}
    assert discover._scrub_list_names(body) == {
        "data": [{"name": "redacted list"}, {"name": "discovery"}]
    }


def test_refuses_paid_inputs_without_spend(capsys):
    assert discover.main(["--verify-email", "me@personal.dev"]) == 2
    assert "--spend" in capsys.readouterr().err


def test_spend_without_paid_inputs_is_an_error():
    assert discover.main(["--spend"]) == 2


def test_missing_key_exits_with_message(monkeypatch, capsys):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    assert discover.main([]) == 2
    assert "MAILTESTER_API_KEY" in capsys.readouterr().err


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_free_run_writes_redacted_fixtures(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    headers = {"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "59", "X-Other": "drop"}
    respx_mock.get("/credits", headers={"Authorization": "Bearer evp_invalid"}).respond(
        401, json={"success": False, "error": "Invalid API key"}
    )
    respx_mock.get("/credits").respond(
        200,
        json={"success": True, "data": {"balance": 10, "email": "owner@account.com"}},
        headers=headers,
    )
    respx_mock.get("/lists").respond(
        200, json={"success": True, "data": [{"id": 1, "name": "Client leads"}]}
    )
    respx_mock.get("/verify/batch/999999999").respond(
        404, json={"success": False, "error": "List not found"}
    )
    respx_mock.post("/verify/batch").respond(
        422, json={"success": False, "errors": {"emails": ["The emails field is required."]}}
    )

    assert discover.main(["--out", str(tmp_path)]) == 0

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "credits_before.json",
        "error_401.json",
        "error_404.json",
        "error_422.json",
        "lists.json",
    ]
    credits = json.loads((tmp_path / "credits_before.json").read_text())
    assert credits["status"] == 200
    assert credits["request"] == {"method": "GET", "path": "/credits", "params": {}, "json": None}
    assert credits["headers"] == {
        "x-ratelimit-limit": "60",
        "x-ratelimit-remaining": "59",
        "content-type": "application/json",
    }
    assert credits["body"]["data"]["email"] == "redacted1@example.invalid"
    lists = json.loads((tmp_path / "lists.json").read_text())
    assert lists["body"]["data"][0]["name"] == "redacted list"
    assert "evp_" not in "".join(p.read_text() for p in tmp_path.iterdir())


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_paid_step_failure_stops_further_spending(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    respx_mock.get("/credits").respond(200, json={"success": True, "data": {}})
    respx_mock.get("/lists").respond(200, json={"success": True, "data": []})
    respx_mock.post("/verify").respond(500, json={"success": False, "error": "Verification failed"})
    finder = respx_mock.post("/finder/email").respond(200, json={"success": True, "data": {}})
    respx_mock.get("/verify/batch/999999999").respond(404, json={"success": False})
    respx_mock.post("/verify/batch").respond(422, json={"success": False, "errors": {}})

    argv = [
        "--spend",
        "--verify-email",
        "me@personal.dev",
        "--find",
        "Jo",
        "Smith",
        "a.io",
        "--out",
        str(tmp_path),
    ]
    assert discover.main(argv) == 1
    assert not finder.called
    verify = json.loads((tmp_path / "verify_single.json").read_text())
    assert verify["request"]["json"]["email"] == "verify@example.net"


def test_records_non_json_body_beside_fixture(tmp_path):
    rec = discover.Recorder(httpx.Client(), discover.Redactor(), tmp_path)
    request = httpx.Request("GET", f"{discover.BASE_URL}/verify/batch/1/results")
    resp = httpx.Response(
        200,
        text="email,result\nx@y.com,valid\n",
        headers={"content-type": "text/csv"},
        request=request,
    )
    rec.save("8", "batch_results_csv_all", resp, params={"format": "csv"})
    record = json.loads((tmp_path / "batch_results_csv_all.json").read_text())
    assert record["body_file"] == "batch_results_csv_all.csv"
    assert "body" not in record
    csv = (tmp_path / "batch_results_csv_all.csv").read_text()
    assert csv == "email,result\nredacted1@example.invalid,valid\n"
