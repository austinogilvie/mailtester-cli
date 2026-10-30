import importlib.util
import json
from pathlib import Path

import httpx
import pytest
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
        200,
        json={
            "success": True,
            "data": [
                {"id": 1, "name": "Client leads", "status": "processing"},
                {"id": 2, "name": "Old leads", "status": "completed"},
            ],
            "meta": {"current_page": 1, "last_page": 2, "per_page": 5, "total": 7},
        },
    )
    respx_mock.get("/lists/2").respond(
        200, json={"success": True, "data": {"id": 2, "name": "Old leads", "status": "completed"}}
    )
    respx_mock.get("/verify/batch/999999999").respond(
        404, json={"success": False, "error": "List not found"}
    )
    respx_mock.get("/lists/999999999").respond(
        404, json={"success": False, "error": "List not found"}
    )
    respx_mock.post("/verify/batch").respond(
        422, json={"success": False, "errors": {"emails": ["The emails field is required."]}}
    )
    probe_routes(respx_mock)

    assert discover.main(["--out", str(tmp_path)]) == 0

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "credits_before.json",
        "error_401.json",
        "error_404.json",
        "error_404_list.json",
        "error_422.json",
        "list_show.json",
        "lists.json",
        "lists_page_2.json",
        "lists_past_last_page.json",
    ]
    shown = json.loads((tmp_path / "list_show.json").read_text())
    assert shown["request"]["path"] == "/lists/2"
    assert shown["body"]["data"]["name"] == "redacted list"
    page_2 = json.loads((tmp_path / "lists_page_2.json").read_text())
    assert page_2["request"]["params"] == {"per_page": 1, "page": 2}
    past = json.loads((tmp_path / "lists_past_last_page.json").read_text())
    assert past["request"]["params"] == {"per_page": 1, "page": 8}
    not_found = json.loads((tmp_path / "error_404_list.json").read_text())
    assert not_found["status"] == 404
    assert not_found["request"]["path"] == "/lists/999999999"
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
    respx_mock.get("/lists/999999999").respond(
        404, json={"success": False, "error": "List not found"}
    )
    respx_mock.post("/verify/batch").respond(422, json={"success": False, "errors": {}})
    probe_routes(respx_mock)

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


# --upload, --no-start, --callback-url (018)

UPLOAD_CSV = "name,email\nJonathan Smithers,jsmithers@acmecorp.io\nAda Lovelace,ada@acmecorp.io\n"
XLSX_HEAD = b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00\xff\xfe\x9c\xe3"


def upload_body(**data) -> dict:
    base = {"id": 1498, "status": "processing", "total_emails": 2, "created_at": "2026-10-29"}
    return {"success": True, "data": {**base, **data}}


def free_routes(respx_mock) -> None:
    respx_mock.get("/credits").respond(200, json={"success": True, "data": {"balance": 10}})
    respx_mock.get("/lists").respond(200, json={"success": True, "data": []})
    respx_mock.get("/verify/batch/999999999").respond(404, json={"success": False})
    respx_mock.get("/lists/999999999").respond(
        404, json={"success": False, "error": "List not found"}
    )
    respx_mock.post("/verify/batch").respond(422, json={"success": False, "errors": {}})
    probe_routes(respx_mock)


def probe_routes(respx_mock, company_field: str = "company"):
    """The empty-body 422 probes for the two role finders (steps 4dp and 4cp)."""
    required = "The {} field is required."
    domain = respx_mock.post("/finder/domain", json__eq={}).respond(
        422, json={"success": False, "errors": {"domain": [required.format("domain")]}}
    )
    company = respx_mock.post("/finder/company", json__eq={}).respond(
        422, json={"success": False, "errors": {company_field: [required.format(company_field)]}}
    )
    return domain, company


def throwaway_routes(respx_mock, list_id: int):
    """GET and DELETE /lists/{id} for the pending batch discover.py creates and removes."""
    show = respx_mock.get(f"/lists/{list_id}").respond(
        200, json={"success": True, "data": {"id": list_id, "status": "pending"}}
    )
    delete = respx_mock.delete(f"/lists/{list_id}").respond(
        200, json={"success": True, "message": "List deleted"}
    )
    return show, delete


def test_save_describes_multipart_request_instead_of_json(tmp_path):
    rec = discover.Recorder(httpx.Client(), discover.Redactor(), tmp_path)
    request = httpx.Request("POST", f"{discover.BASE_URL}/verify/batch/upload")
    resp = httpx.Response(201, json=upload_body(), request=request)
    rec.save("5u", "batch_upload_pending", resp, form={"auto_start": "0"}, file_name="one.csv")
    record = json.loads((tmp_path / "batch_upload_pending.json").read_text())
    assert record["request"] == {
        "method": "POST",
        "path": "/verify/batch/upload",
        "params": {},
        "form": {"auto_start": "0"},
        "file": "one.csv",
    }
    assert record["status"] == 201


def test_save_records_empty_form_for_a_lone_file_part(tmp_path):
    rec = discover.Recorder(httpx.Client(), discover.Redactor(), tmp_path)
    request = httpx.Request("POST", f"{discover.BASE_URL}/verify/batch/upload")
    resp = httpx.Response(201, json=upload_body(), request=request)
    rec.save("5u", "batch_upload", resp, form=None, file_name="leads.csv")
    record = json.loads((tmp_path / "batch_upload.json").read_text())
    assert record["request"]["form"] == {}
    assert record["request"]["file"] == "leads.csv"
    assert "json" not in record["request"]


@respx.mock(base_url=discover.BASE_URL)
def test_call_passes_data_and_files_to_httpx(respx_mock, tmp_path):
    route = respx_mock.post("/verify/batch/upload").respond(201, json=upload_body())
    with httpx.Client(base_url=discover.BASE_URL) as client:
        rec = discover.Recorder(client, discover.Redactor(), tmp_path)
        files = {"file": ("leads.csv", b"a@example.com\n")}
        rec.call(
            "5u",
            "batch_upload",
            "POST",
            "/verify/batch/upload",
            data={"name": "x"},
            files=files,
            file_name="leads.csv",
        )
    request = route.calls.last.request
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="name"\r\n\r\nx' in request.content
    assert b'filename="leads.csv"' in request.content
    record = json.loads((tmp_path / "batch_upload.json").read_text())
    assert record["request"]["form"] == {"name": "x"}


@pytest.mark.parametrize(
    ("name", "no_start", "status", "expected"),
    [
        ("leads.csv", False, 201, "batch_upload"),
        ("one.csv", True, 201, "batch_upload_pending"),
        ("leads.xlsx", True, 201, "batch_upload_xlsx_pending"),
        ("leads.XLS", False, 201, "batch_upload_xlsx"),
        ("leads.json", True, 422, "batch_upload_bad_type"),
    ],
)
def test_upload_fixture_name(name, no_start, status, expected):
    assert discover._upload_name(Path(name), no_start, status) == expected


def test_upload_cost_counts_non_blank_lines_or_none_for_binary(tmp_path):
    csv = tmp_path / "leads.csv"
    csv.write_text("name,email\n\njane@example.com\n   \njohn@example.com\n")
    xlsx = tmp_path / "leads.xlsx"
    xlsx.write_bytes(XLSX_HEAD)
    assert discover.upload_count(csv) == 3
    assert discover.upload_count(xlsx) is None
    assert discover.planned_cost(discover.parse_args(["--upload", str(csv)])) == 3
    assert discover.planned_cost(discover.parse_args(["--upload", str(xlsx)])) == 0
    assert discover.planned_cost(discover.parse_args(["--upload", str(csv), "--no-start"])) == 0


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_refuses_started_binary_upload_up_front(respx_mock, monkeypatch, tmp_path, capsys):
    # A binary file can't be counted locally, so --spend can't vouch for its cost.
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    anything = respx_mock.route().respond(500)
    xlsx = tmp_path / "leads.xlsx"
    xlsx.write_bytes(XLSX_HEAD)

    assert discover.main(["--spend", "--upload", str(xlsx), "--out", str(tmp_path)]) == 2

    err = capsys.readouterr().err
    assert "can't be counted locally" in err
    assert "--no-start" in err
    assert not anything.called
    assert list(tmp_path.iterdir()) == [xlsx]


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_binary_upload_with_no_start_is_still_allowed(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    free_routes(respx_mock)
    upload = respx_mock.post("/verify/batch/upload").respond(
        201, json=upload_body(id=1497, status="pending")
    )
    _, delete = throwaway_routes(respx_mock, 1497)
    xlsx = tmp_path / "leads.xlsx"
    xlsx.write_bytes(XLSX_HEAD)

    assert discover.main(["--upload", str(xlsx), "--no-start", "--out", str(tmp_path)]) == 0

    assert upload.called and delete.called
    assert b'name="auto_start"\r\n\r\n0' in upload.calls.last.request.content


def test_started_upload_requires_spend(tmp_path, capsys):
    csv = tmp_path / "leads.csv"
    csv.write_text("a@example.com\n")
    assert discover.main(["--upload", str(csv), "--out", str(tmp_path)]) == 2
    assert "--spend" in capsys.readouterr().err


def test_refuses_started_upload_with_more_than_3_lines(tmp_path, capsys):
    csv = tmp_path / "leads.csv"
    csv.write_text("".join(f"u{i}@example.com\n" for i in range(4)))
    assert discover.main(["--spend", "--upload", str(csv), "--out", str(tmp_path)]) == 2
    assert "at most 3 lines" in capsys.readouterr().err


def test_missing_upload_file_exits_with_message(tmp_path, capsys):
    missing = tmp_path / "nope.csv"
    assert discover.main(["--upload", str(missing), "--no-start"]) == 2
    assert "not a file" in capsys.readouterr().err


def test_redactor_registers_upload_addresses_and_names(tmp_path):
    csv = tmp_path / "leads.csv"
    csv.write_text(UPLOAD_CSV)
    args = discover.parse_args(["--upload", str(csv), "--no-start"])
    r = discover.build_redactor(args, "evp_realkey123")
    out = r.text("jsmithers@acmecorp.io ada@acmecorp.io Jonathan Smithers Ada Lovelace name,email")
    assert out == "upload1@example.com upload2@example.com Person1 Person2 name,email"
    assert "jonathan smithers" in r.leaks("Jonathan Smithers wrote in")


def test_redactor_skips_upload_binary_and_address_first_columns(tmp_path):
    xlsx = tmp_path / "leads.xlsx"
    xlsx.write_bytes(XLSX_HEAD)
    r = discover.build_redactor(discover.parse_args(["--upload", str(xlsx)]), "k")
    assert r.emails == {} and r.names == {}
    txt = tmp_path / "leads.txt"
    txt.write_text("one@acmecorp.io\n")
    r = discover.build_redactor(discover.parse_args(["--upload", str(txt)]), "k")
    assert r.emails == {"one@acmecorp.io": "upload1@example.com"}
    assert r.names == {}


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_no_start_upload_is_free_recorded_and_deleted(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    free_routes(respx_mock)
    upload = respx_mock.post("/verify/batch/upload").respond(
        201, json=upload_body(id=1495, status="pending", total_emails=1)
    )
    show, delete = throwaway_routes(respx_mock, 1495)
    status = respx_mock.get("/verify/batch/1495").respond(200, json=upload_body(id=1495))
    csv = tmp_path / "one.csv"
    csv.write_text("alice@acmecorp.io\n")  # not "one@": its local part would "leak" via one.csv

    assert discover.main(["--upload", str(csv), "--no-start", "--out", str(tmp_path)]) == 0

    assert upload.called and show.called and delete.called and not status.called
    body = upload.calls.last.request.content
    assert b'name="auto_start"\r\n\r\n0' in body
    assert b'filename="one.csv"' in body
    record = json.loads((tmp_path / "batch_upload_pending.json").read_text())
    assert record["request"]["form"] == {"auto_start": "0"}
    assert record["request"]["file"] == "one.csv"
    assert record["body"]["data"]["status"] == "pending"
    names = {p.name for p in tmp_path.iterdir()}
    assert "credits_after.json" not in names  # nothing was spent
    assert not any(n.startswith("batch_status_") for n in names)
    assert {"list_show_pending.json", "list_delete.json"} <= names
    show_record = json.loads((tmp_path / "list_show_pending.json").read_text())
    assert show_record["request"] == {
        "method": "GET",
        "path": "/lists/1495",
        "params": {},
        "json": None,
    }


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_started_upload_polls_unrecorded_and_records_results(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setattr(discover.time, "sleep", lambda s: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    free_routes(respx_mock)
    upload = respx_mock.post("/verify/batch/upload").respond(201, json=upload_body())
    status = respx_mock.get("/verify/batch/1498").mock(
        side_effect=[
            httpx.Response(200, json=upload_body(status="processing")),
            httpx.Response(200, json=upload_body(status="completed")),
        ]
    )
    rows = [{"email": "jsmithers@acmecorp.io", "result": "invalid"}]
    results = respx_mock.get("/verify/batch/1498/results").respond(
        200, json={"success": True, "data": rows}
    )
    delete = respx_mock.delete("/lists/1498").respond(200, json={"success": True})
    csv = tmp_path / "leads.csv"
    csv.write_text(UPLOAD_CSV)

    argv = ["--spend", "--upload", str(csv), "--callback-url", "https://hooks.example.com/mt"]
    assert discover.main([*argv, "--out", str(tmp_path)]) == 0

    assert upload.called and status.call_count == 2 and results.called and not delete.called
    assert b'name="auto_start"' not in upload.calls.last.request.content
    assert results.calls.last.request.url.params["format"] == "json"
    assert results.calls.last.request.url.params["filter"] == "all"
    names = {p.name for p in tmp_path.iterdir()}
    assert "batch_upload.json" in names
    assert "batch_results_json_upload.json" in names
    assert "credits_after.json" in names
    assert not any(n.startswith("batch_status_") for n in names)
    record = json.loads((tmp_path / "batch_upload.json").read_text())
    assert record["request"]["form"] == {"callback_url": "https://hooks.example.com/mt"}
    results_record = json.loads((tmp_path / "batch_results_json_upload.json").read_text())
    assert results_record["body"]["data"] == [{"email": "upload1@example.com", "result": "invalid"}]
    assert results_record["request"]["params"] == {"format": "json", "filter": "all"}


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_bad_type_upload_is_recorded_and_not_a_stop(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    free_routes(respx_mock)
    errors = {"file": ["The file field must be a file of type: csv, txt, xlsx, xls."]}
    respx_mock.post("/verify/batch/upload").respond(422, json={"success": False, "errors": errors})
    bad = tmp_path / "leads.json"
    bad.write_text('["a@example.com"]\n')

    assert discover.main(["--upload", str(bad), "--no-start", "--out", str(tmp_path)]) == 0

    record = json.loads((tmp_path / "batch_upload_bad_type.json").read_text())
    assert record["status"] == 422
    assert record["request"]["file"] == "leads.json"
    assert "error_401.json" in {p.name for p in tmp_path.iterdir()}  # the run carried on


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_xlsx_upload_takes_the_xlsx_name(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    free_routes(respx_mock)
    pending = upload_body(id=1497, status="pending")
    respx_mock.post("/verify/batch/upload").respond(201, json=pending)
    _, delete = throwaway_routes(respx_mock, 1497)
    xlsx = tmp_path / "leads.xlsx"
    xlsx.write_bytes(XLSX_HEAD)

    assert discover.main(["--upload", str(xlsx), "--no-start", "--out", str(tmp_path)]) == 0

    assert delete.called
    record = json.loads((tmp_path / "batch_upload_xlsx_pending.json").read_text())
    assert record["request"]["file"] == "leads.xlsx"


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_no_start_and_callback_url_apply_to_batch_submit(respx_mock, monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    respx_mock.get("/credits").respond(200, json={"success": True, "data": {}})
    respx_mock.get("/lists").respond(200, json={"success": True, "data": []})
    respx_mock.get("/verify/batch/999999999").respond(404, json={"success": False})
    respx_mock.get("/lists/999999999").respond(
        404, json={"success": False, "error": "List not found"}
    )
    submit = respx_mock.post("/verify/batch").mock(
        side_effect=[
            httpx.Response(201, json=upload_body(id=1492, status="pending", total_emails=1)),
            httpx.Response(422, json={"success": False, "errors": {}}),
        ]
    )
    show, delete = throwaway_routes(respx_mock, 1492)
    status = respx_mock.get("/verify/batch/1492").respond(200, json=upload_body(id=1492))
    probe_routes(respx_mock)

    argv = ["--batch", "me@personal.dev", "--no-start"]
    argv += ["--callback-url", "https://hooks.example.com/mt", "--out", str(tmp_path)]
    assert discover.main(argv) == 0

    sent = json.loads(submit.calls[0].request.content)
    assert sent == {
        "emails": ["me@personal.dev"],
        "name": "discovery",
        "auto_start": False,
        "callback_url": "https://hooks.example.com/mt",
    }
    assert show.called
    assert delete.called and not status.called
    names = {p.name for p in tmp_path.iterdir()}
    assert "batch_submit_pending.json" in names
    assert "batch_submit.json" not in names
    assert "credits_after.json" not in names
    assert {"list_show_pending.json", "list_delete.json"} <= names
    assert not any(n.startswith("batch_status_") for n in names)


# lists fixtures (020)


def test_planned_cost_skips_no_start_batch():
    assert discover.planned_cost(discover.parse_args(["--batch", "a@b.co", "--no-start"])) == 0
    assert discover.planned_cost(discover.parse_args(["--batch", "a@b.co"])) == 1


def test_spend_with_only_a_no_start_batch_is_an_error():
    assert discover.main(["--spend", "--batch", "a@b.co", "--no-start"]) == 2


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_list_pages_skipped_without_meta(respx_mock, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    free_routes(respx_mock)  # its /lists body has no meta

    assert discover.main(["--out", str(tmp_path)]) == 0

    assert not (tmp_path / "lists_page_2.json").exists()
    assert not (tmp_path / "lists_past_last_page.json").exists()
    assert "[ 2b] skipped lists_past_last_page: step 2 had no meta.total" in capsys.readouterr().out


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_list_show_skipped_without_a_completed_list(respx_mock, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    free_routes(respx_mock)  # its /lists body has no rows

    assert discover.main(["--out", str(tmp_path)]) == 0

    assert not (tmp_path / "list_show.json").exists()
    assert "[ 2c] skipped list_show: step 2 had no completed list" in capsys.readouterr().out


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_save_registers_response_domains_for_every_step(respx_mock, monkeypatch, tmp_path):
    # A domain the API volunteers in any step, here nested in a /lists row, is redacted too.
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    free_routes(respx_mock)
    row = {"id": 1, "name": "discovery", "results": {"top": {"domain": "surprise.io", "n": 2}}}
    respx_mock.get("/lists").respond(200, json={"success": True, "data": [row]})

    assert discover.main(["--out", str(tmp_path)]) == 0

    lists = (tmp_path / "lists.json").read_text()
    assert "surprise.io" not in lists
    assert json.loads(lists)["body"]["data"][0]["results"]["top"]["domain"] == "example.com"


# role-mailbox finders (023)

# The made-up company shares no word with the no-MX domain: a word of it would be redacted
# inside `nonexistent-domain-x7q2.invalid`, which the redactor otherwise keeps as it is.
MISSING_COMPANY = "Zxqv Imaginary Holdings"
FINDER_ARGV = [
    "--find-domain",
    "acmecorp.io",
    "--find-company",
    "Acme Widgets Ltd",
    "--find-company-missing",
    MISSING_COMPANY,
    "--find-domain-catchall",
    "catchall.io",
    "--find-domain-nomx",
    "nonexistent-domain-x7q2.invalid",
]
FINDER_FLAGS = [
    ("--find-domain", "acmecorp.io"),
    ("--find-company", "Acme Widgets Ltd"),
    ("--find-company-missing", MISSING_COMPANY),
    ("--find-domain-catchall", "catchall.io"),
    ("--find-domain-nomx", "nonexistent-domain-x7q2.invalid"),
]
NO_MX_ERROR = "Domain 'nonexistent-domain-x7q2.invalid' has no mail (MX) records."


def role_body(domain: str, *locals_: str, **extra) -> dict:
    """A `POST /finder/domain` or `/finder/company` body in the shape 023 recorded."""
    emails = [
        {"email": f"{local}@{domain}", "status": "valid", "confidence": 95} for local in locals_
    ]
    data = {"domain": domain, "emails": emails, "total": len(emails), "limit": 10}
    return {"success": True, "data": {**data, "credits_used": 3, **extra}}


def missing_company_body() -> dict:
    """The 023 no-match shape: the name resolved to a domain that holds no role mailboxes."""
    return role_body("zxqvimaginaryholdings.com", total=0, company=MISSING_COMPANY)


def credits_sequence(respx_mock, *balances: int):
    """GET /credits answers with each balance in turn: credits_before, the Budget baseline, one
    per paid finder step. The last balance repeats, for credits_after and any step past the list.
    """
    bodies = [{"success": True, "data": {"balance": b}} for b in balances]
    responses = [httpx.Response(200, json=body) for body in bodies]
    return respx_mock.get("/credits").mock(side_effect=responses + [responses[-1]] * 4)


def finder_run_routes(respx_mock, *balances: int, company_field: str = "company"):
    """Everything a full --spend finder run touches, with the paid finder routes returned."""
    respx_mock.get("/lists").respond(200, json={"success": True, "data": []})
    respx_mock.get("/verify/batch/999999999").respond(404, json={"success": False})
    respx_mock.get("/lists/999999999").respond(404, json={"success": False})
    respx_mock.post("/verify/batch").respond(422, json={"success": False, "errors": {}})
    respx_mock.get("/credits", headers={"Authorization": "Bearer evp_invalid"}).respond(
        401, json={"success": False, "error": "Invalid API key"}
    )
    credits_sequence(respx_mock, *balances)
    probe_routes(respx_mock, company_field)
    domain = respx_mock.post("/finder/domain").mock(
        side_effect=[
            httpx.Response(200, json=role_body("acmecorp.io", "info", "sales")),
            httpx.Response(200, json=role_body("catchall.io", "info", "sales", "support")),
            httpx.Response(422, json={"success": False, "error": NO_MX_ERROR}),
        ]
    )
    company = respx_mock.post("/finder/company").mock(
        side_effect=[
            httpx.Response(200, json=role_body("acmecorp.io", "info", company="Acme Widgets Ltd")),
            httpx.Response(200, json=missing_company_body()),
        ]
    )
    return domain, company


def run_finders(monkeypatch, tmp_path, *argv: str) -> int:
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    return discover.main([*argv, "--out", str(tmp_path)])


def record(tmp_path, name: str) -> dict:
    return json.loads((tmp_path / f"{name}.json").read_text())


@pytest.mark.parametrize(("flag", "value"), FINDER_FLAGS)
def test_planned_cost_counts_each_finder_flag(flag, value):
    assert discover.planned_cost(discover.parse_args([flag, value])) == 3


def test_planned_cost_sums_the_finder_flags():
    assert discover.planned_cost(discover.parse_args(FINDER_ARGV)) == 15
    both = [*FINDER_ARGV, "--verify-email", "me@personal.dev"]
    assert discover.planned_cost(discover.parse_args(both)) == 16


def test_finder_flags_require_spend(capsys):
    assert discover.main(FINDER_ARGV) == 2
    assert "15 credits" in capsys.readouterr().err
    assert discover.main(["--find-domain", "acmecorp.io"]) == 2
    assert "3 credits" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (422, {"success": False, "errors": {"name": ["The name field is required."]}}, "name"),
        (422, {"success": False, "errors": {"company": ["x"], "country": ["y"]}}, "company"),
        (422, {"success": False, "error": "nope"}, "company"),
        (200, {"success": True, "data": {}}, "company"),
    ],
)
def test_required_field_reads_the_one_errors_key(status, body, expected):
    resp = httpx.Response(status, json=body)
    assert discover._required_field(resp, "company") == expected


def test_required_field_without_json_falls_back():
    assert discover._required_field(httpx.Response(500, text="boom"), "domain") == "domain"


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_probe_status_other_than_422_is_not_saved(respx_mock, monkeypatch, tmp_path, capsys):
    domain, _ = finder_run_routes(respx_mock, 71, 71, 68)
    # Same pattern as the probe route in finder_run_routes, so respx replaces that route in place
    # and the probe still answers before the paid route below it.
    respx_mock.post("/finder/domain", json__eq={}).respond(
        500, json={"success": False, "error": "Verification failed"}
    )

    assert run_finders(monkeypatch, tmp_path, "--spend", "--find-domain", "acmecorp.io") == 0

    assert not (tmp_path / "finder_domain_422.json").exists()
    assert (tmp_path / "finder_company_422.json").exists()
    assert "[4dp] expected 422 from an empty body, got 500" in capsys.readouterr().out
    # Step 4d still ran (the probe hit its own route), with the default field.
    assert domain.call_count == 1
    assert json.loads(domain.calls.last.request.content) == {"domain": "acmecorp.io"}
    assert record(tmp_path, "finder_domain")["request"]["json"] == {"domain": "example.com"}


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_probes_skipped_without_finder_flags(respx_mock, monkeypatch, tmp_path):
    free_routes(respx_mock)
    domain, company = probe_routes(respx_mock)  # the same routes, returned so they can be checked

    assert run_finders(monkeypatch, tmp_path) == 0

    assert not domain.called and not company.called
    names = {p.name for p in tmp_path.iterdir()}
    assert "finder_domain_422.json" not in names and "finder_company_422.json" not in names


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_paid_finder_run_records_all_seven_fixtures(respx_mock, monkeypatch, tmp_path, capsys):
    domain, company = finder_run_routes(respx_mock, 71, 71, 68, 65, 65, 62, 62)

    assert run_finders(monkeypatch, tmp_path, "--spend", *FINDER_ARGV) == 0

    assert domain.call_count == 3 and company.call_count == 2
    names = {p.name for p in tmp_path.iterdir()}
    assert {
        "finder_domain_422.json",
        "finder_company_422.json",
        "finder_domain.json",
        "finder_company.json",
        "finder_company_missing.json",
        "finder_domain_catch_all.json",
        "finder_domain_no_mx.json",
        "credits_before.json",
        "credits_after.json",
    } <= names
    assert record(tmp_path, "finder_domain")["request"]["json"] == {"domain": "example.com"}
    assert record(tmp_path, "finder_company")["request"]["json"] == {"company": "Acme"}
    missing = record(tmp_path, "finder_company_missing")
    assert missing["request"]["json"] == {"company": "Nowhere"}
    assert missing["status"] == 200 and missing["body"]["data"]["emails"] == []
    catch_all = record(tmp_path, "finder_domain_catch_all")
    assert catch_all["request"]["json"] == {"domain": "example.net"}
    no_mx = record(tmp_path, "finder_domain_no_mx")
    # A reserved .invalid name isn't redacted, so the fixture matches 012's finder_no_mx.
    assert no_mx["request"]["json"] == {"domain": "nonexistent-domain-x7q2.invalid"}
    assert no_mx["status"] == 422 and no_mx["body"]["error"] == NO_MX_ERROR
    assert record(tmp_path, "finder_domain")["body"]["data"]["emails"][0] == {
        "email": "info@example.com",
        "status": "valid",
        "confidence": 95,
    }
    out = capsys.readouterr().out
    assert "[ 4d] GET /credits -> balance 68 (step 3, run total 3)" in out
    assert "[4dn] GET /credits -> balance 62 (step 0, run total 9)" in out
    # The order the spec gives: 4d, 4c, 4cm, 4dc, 4dn.
    steps = [line[1:4].strip() for line in out.splitlines() if " /finder/" in line]
    assert steps == ["4dp", "4cp", "4d", "4c", "4cm", "4dc", "4dn"]


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_finder_fixtures_redact_inputs_and_the_resolved_domain(respx_mock, monkeypatch, tmp_path):
    finder_run_routes(respx_mock, 71, 71, 68, 65, 65, 65, 65)
    resolved = role_body("acme-widgets.co", "info", "press", company="Acme Widgets Ltd")
    respx_mock.post("/finder/company").mock(
        side_effect=[
            httpx.Response(200, json=resolved),
            httpx.Response(200, json=missing_company_body()),
        ]
    )
    argv = ["--spend", "--find-domain", "acmecorp.io", "--find-company", "Acme Widgets Ltd"]
    argv += ["--find-company-missing", MISSING_COMPANY]

    assert run_finders(monkeypatch, tmp_path, *argv) == 0

    data = record(tmp_path, "finder_company")["body"]["data"]
    assert data["domain"] == "example.net"  # resolved, never an input, still redacted
    assert data["company"] == "Acme"
    assert [m["email"] for m in data["emails"]] == ["info@example.net", "press@example.net"]
    missing = record(tmp_path, "finder_company_missing")["body"]["data"]
    assert missing["domain"] == "example.org"  # resolved from the made-up name, redacted too
    assert missing["company"] == "Nowhere"
    text = "".join(p.read_text() for p in tmp_path.iterdir())
    for real in ("acmecorp", "acme-widgets", "Widgets", "Zxqv", "zxqvimaginaryholdings", "evp_"):
        assert real not in text


def test_redactor_registers_finder_inputs():
    r = discover.build_redactor(discover.parse_args(FINDER_ARGV), "evp_realkey123")
    assert r.domains == {"acmecorp.io": "example.com", "catchall.io": "example.net"}
    # Each name whole, plus its distinctive words. "acme" is the placeholder itself, so it's out.
    assert r.names == {
        "acme widgets ltd": "Acme",
        "widgets": "Acme",
        "zxqv imaginary holdings": "Nowhere",
        "zxqv": "Nowhere",
        "imaginary": "Nowhere",
    }
    assert r.text("info@acmecorp.io sales@catchall.io Acme Widgets Ltd") == (
        "info@example.com sales@example.net Acme"
    )
    assert r.text("Acme Widgets is real") == "Acme Acme is real"
    assert r.text("Zxqv Imaginary Holdings, or Zxqv") == "Nowhere, or Nowhere"
    assert "acme widgets ltd" in r.leaks("Acme Widgets Ltd is real")
    assert "widgets" in r.leaks("Widgets rebranded")
    both = discover.parse_args(["--find", "Jo", "Smith", "a.io", "--find-domain", "b.io"])
    r = discover.build_redactor(both, "k")
    assert r.domains == {"a.io": "example.com", "b.io": "example.net"}


def test_company_generic_words_are_not_registered():
    args = discover.parse_args(["--find-company", "Zxqv Holdings Inc"])
    r = discover.build_redactor(args, "k")
    assert r.names == {"zxqv holdings inc": "Acme", "zxqv": "Acme"}
    assert r.text("Zxqv Holdings Inc, Zxqv, Holdings Inc") == "Acme, Acme, Holdings Inc"
    assert r.leaks("Holdings Inc") == []


def test_invalid_tld_is_not_redacted():
    # .invalid is reserved (RFC 2606), so there's nothing to hide and the fixture keeps the name.
    args = discover.parse_args(["--find-domain-nomx", "nonexistent-domain-x7q2.invalid"])
    r = discover.build_redactor(args, "k")
    assert r.domains == {}
    assert r.text("Domain 'nonexistent-domain-x7q2.invalid' has no MX") == (
        "Domain 'nonexistent-domain-x7q2.invalid' has no MX"
    )
    discover._register_domains(r, [{"domain": "x.invalid"}, {"domain": "Real.io"}])
    assert r.domains == {"real.io": "example.com"}


def test_register_domains_walks_nested_data():
    r = discover.Redactor()
    r.domain("known.io")
    data = {
        "domain": "known.io",
        "emails": [{"email": "info@a.io", "domain": "a.io"}, {"domain": ""}, {"domain": 3}],
        "nested": {"domain": "B.io", "other": "c.io"},
    }
    discover._register_domains(r, data)
    assert r.domains == {"known.io": "example.com", "a.io": "example.net", "b.io": "example.org"}


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_skips_catch_all_step_when_first_call_costs_more_than_3(
    respx_mock, monkeypatch, tmp_path, capsys
):
    # 4d costs 4, 4c costs 4, 4cm and 4dn are free: 8 spent, under the 15 planned.
    domain, company = finder_run_routes(respx_mock, 71, 71, 67, 63, 63, 63)
    domain.side_effect = [
        httpx.Response(200, json=role_body("acmecorp.io", "info", "sales", "support", "hr")),
        httpx.Response(422, json={"success": False, "error": "Domain 'x' has no MX records."}),
    ]

    assert run_finders(monkeypatch, tmp_path, "--spend", *FINDER_ARGV) == 0

    assert domain.call_count == 2 and company.call_count == 2
    assert not (tmp_path / "finder_domain_catch_all.json").exists()
    assert record(tmp_path, "finder_domain_no_mx")["status"] == 422
    out = capsys.readouterr().out
    assert "[ 4d] cost 4, not 3: per-mailbox pricing? skipping step 4dc" in out
    assert "[4dc] skipped finder_domain_catch_all: step 4d showed a per-mailbox cost" in out


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_stops_before_a_call_that_could_exceed_the_plan(respx_mock, monkeypatch, tmp_path, capsys):
    # 4d costs 9: another 9 would pass 15, so 4c never runs.
    domain, company = finder_run_routes(respx_mock, 71, 71, 62, 62)

    assert run_finders(monkeypatch, tmp_path, "--spend", *FINDER_ARGV) == 1

    assert domain.call_count == 1 and not company.called
    err = capsys.readouterr().err
    assert "STOPPED: step 4c could cost 9 and push the spend past 15" in err
    assert (tmp_path / "credits_after.json").exists()


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_stops_when_the_spend_passes_the_plan(respx_mock, monkeypatch, tmp_path, capsys):
    domain, _ = finder_run_routes(respx_mock, 71, 71, 66)

    assert run_finders(monkeypatch, tmp_path, "--spend", "--find-domain", "acmecorp.io") == 1

    assert domain.call_count == 1
    assert "STOPPED: spent 5 credits, more than the 3 planned" in capsys.readouterr().err


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_finder_5xx_at_the_first_step_stops_the_rest(respx_mock, monkeypatch, tmp_path, capsys):
    domain, company = finder_run_routes(respx_mock, 71, 71, 71)
    domain.side_effect = [httpx.Response(502, json={"success": False, "error": "unavailable"})]

    assert run_finders(monkeypatch, tmp_path, "--spend", *FINDER_ARGV) == 1

    assert domain.call_count == 1 and not company.called
    assert record(tmp_path, "finder_domain")["status"] == 502
    names = {p.name for p in tmp_path.iterdir()}
    assert "credits_after.json" in names and "error_401.json" in names
    assert "STOPPED: step 4d returned 502" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, missing_company_body()),
        (404, {"success": False, "error": "Company not found"}),
        (422, {"success": False, "error": "Could not resolve a domain for 'x'."}),
    ],
)
@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_no_match_outcomes_are_recorded_not_fatal(respx_mock, monkeypatch, tmp_path, status, body):
    domain, company = finder_run_routes(respx_mock, 71, 71, 68, 65, 65, 62, 62)
    company.side_effect = [
        httpx.Response(200, json=role_body("acmecorp.io", "info")),
        httpx.Response(status, json=body),
    ]

    assert run_finders(monkeypatch, tmp_path, "--spend", *FINDER_ARGV) == 0

    assert domain.call_count == 3  # 4dc and 4dn still ran after the no-match
    missing = record(tmp_path, "finder_company_missing")
    assert missing["status"] == status
    if "data" in body:
        # The resolved domain is registered on save, so it's a placeholder in the record.
        redacted = {**body["data"], "domain": "example.org", "company": "Nowhere"}
        assert missing["body"]["data"] == redacted
    else:
        assert missing["body"] == body
    assert record(tmp_path, "finder_domain_no_mx")["status"] == 422


@pytest.mark.parametrize("status", [401, 402, 429, 500])
@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_other_statuses_at_a_no_match_step_stop(respx_mock, monkeypatch, tmp_path, status):
    domain, company = finder_run_routes(respx_mock, 71, 71, 68, 65, 65)
    company.side_effect = [
        httpx.Response(200, json=role_body("acmecorp.io", "info")),
        httpx.Response(status, json={"success": False, "error": "x"}),
    ]

    assert run_finders(monkeypatch, tmp_path, "--spend", *FINDER_ARGV) == 1

    assert domain.call_count == 1  # neither 4dc nor 4dn ran
    assert record(tmp_path, "finder_company_missing")["status"] == status


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_company_field_follows_the_probe(respx_mock, monkeypatch, tmp_path, capsys):
    _, company = finder_run_routes(respx_mock, 71, 71, 68, company_field="name")
    argv = ["--spend", "--find-company", "Acme Widgets Ltd"]

    assert run_finders(monkeypatch, tmp_path, *argv) == 0

    assert json.loads(company.calls.last.request.content) == {"name": "Acme Widgets Ltd"}
    assert record(tmp_path, "finder_company")["request"]["json"] == {"name": "Acme"}
    assert "[4cp] /finder/company takes `name`, not `company`" in capsys.readouterr().out


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_budget_without_a_balance_runs_every_step(respx_mock, monkeypatch, tmp_path, capsys):
    domain, company = finder_run_routes(respx_mock, 71)
    respx_mock.get("/credits").respond(200, json={"success": True, "data": {}})

    assert run_finders(monkeypatch, tmp_path, "--spend", *FINDER_ARGV) == 0

    assert domain.call_count == 3 and company.call_count == 2
    out = capsys.readouterr().out
    assert "[ 4d] GET /credits -> balance unknown; the cost isn't checked" in out


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_unreadable_balance_mid_run_stops(respx_mock, monkeypatch, tmp_path, capsys):
    # The baseline was readable, so a read that fails later can't be waved through: the spend
    # arithmetic would drift. Here the read after 4c comes back without a balance.
    domain, company = finder_run_routes(respx_mock, 71)

    def balance(value: int) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"balance": value}})

    blank = httpx.Response(200, json={"success": True, "data": {}})
    # credits_before, the Budget baseline, after 4d, after 4c, then credits_after.
    respx_mock.get("/credits").mock(
        side_effect=[balance(71), balance(71), balance(68), blank, balance(65), balance(65)]
    )

    assert run_finders(monkeypatch, tmp_path, "--spend", *FINDER_ARGV) == 1

    assert domain.call_count == 1 and company.call_count == 1
    assert "STOPPED: step 4c: the balance couldn't be read" in capsys.readouterr().err
    assert (tmp_path / "credits_after.json").exists()


def verify_probe_routes(respx_mock, probe_status: int = 422):
    """The malformed-address probe (step 3p) and the paid verify (step 3) behind it."""
    errors = {"email": ["The email field must be a valid email address."]}
    probe = respx_mock.post("/verify", json__eq=discover.VERIFY_PROBE).respond(
        probe_status, json={"success": False, "errors": errors}
    )
    data = {"email": "me@personal.dev", "result": "risky", "reason": "CATCH_ALL", "score": 50}
    paid = respx_mock.post("/verify").respond(200, json={"success": True, "data": data})
    return probe, paid


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_verify_probe_records_error_422_email(respx_mock, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    respx_mock.get("/credits").respond(200, json={"success": True, "data": {"balance": 59}})
    respx_mock.get("/lists").respond(200, json={"success": True, "data": []})
    respx_mock.get(url__regex=r".*/999999999$").respond(404, json={"success": False})
    respx_mock.post("/verify/batch").respond(422, json={"success": False, "errors": {}})
    probe, paid = verify_probe_routes(respx_mock)

    argv = ["--spend", "--verify-email", "me@personal.dev", "--out", str(tmp_path)]
    assert discover.main(argv) == 0
    assert probe.call_count == 1 and paid.call_count == 1
    recorded = json.loads((tmp_path / "error_422_email.json").read_text())
    assert recorded["status"] == 422
    assert recorded["request"]["json"] == discover.VERIFY_PROBE
    assert list(recorded["body"]["errors"]) == ["email"]
    assert "[ 3p] GET /credits -> balance 59 (step 0)" in capsys.readouterr().out


@respx.mock(base_url=discover.BASE_URL, assert_all_called=False)
def test_verify_probe_other_status_is_not_saved(respx_mock, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(discover, "load_dotenv", lambda: None)
    monkeypatch.setenv("MAILTESTER_API_KEY", "evp_realkey123")
    respx_mock.get("/credits").respond(200, json={"success": True, "data": {}})
    respx_mock.get("/lists").respond(200, json={"success": True, "data": []})
    respx_mock.get(url__regex=r".*/999999999$").respond(404, json={"success": False})
    respx_mock.post("/verify/batch").respond(422, json={"success": False, "errors": {}})
    verify_probe_routes(respx_mock, probe_status=200)

    argv = ["--spend", "--verify-email", "me@personal.dev", "--out", str(tmp_path)]
    assert discover.main(argv) == 0
    assert not (tmp_path / "error_422_email.json").exists()
    out = capsys.readouterr().out
    assert "[ 3p] expected 422 from a malformed address, got 200; not saved" in out
    assert "[ 3p] GET /credits -> balance unknown; the cost isn't checked" in out
