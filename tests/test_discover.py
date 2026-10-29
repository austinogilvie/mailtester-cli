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
    respx_mock.post("/verify/batch").respond(422, json={"success": False, "errors": {}})


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
    respx_mock.delete("/lists/1497").respond(200, json={"success": True})
    xlsx = tmp_path / "leads.xlsx"
    xlsx.write_bytes(XLSX_HEAD)

    assert discover.main(["--upload", str(xlsx), "--no-start", "--out", str(tmp_path)]) == 0

    assert upload.called
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
    delete = respx_mock.delete("/lists/1495").respond(
        200, json={"success": True, "message": "List deleted"}
    )
    status = respx_mock.get("/verify/batch/1495").respond(200, json=upload_body(id=1495))
    csv = tmp_path / "one.csv"
    csv.write_text("alice@acmecorp.io\n")  # not "one@": its local part would "leak" via one.csv

    assert discover.main(["--upload", str(csv), "--no-start", "--out", str(tmp_path)]) == 0

    assert upload.called and delete.called and not status.called
    body = upload.calls.last.request.content
    assert b'name="auto_start"\r\n\r\n0' in body
    assert b'filename="one.csv"' in body
    record = json.loads((tmp_path / "batch_upload_pending.json").read_text())
    assert record["request"]["form"] == {"auto_start": "0"}
    assert record["request"]["file"] == "one.csv"
    assert record["body"]["data"]["status"] == "pending"
    names = {p.name for p in tmp_path.iterdir()}
    assert "credits_after.json" not in names  # nothing was spent
    assert not any(n.startswith(("batch_status_", "list_delete")) for n in names)


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
    delete = respx_mock.delete("/lists/1497").respond(200, json={"success": True})
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
    submit = respx_mock.post("/verify/batch").mock(
        side_effect=[
            httpx.Response(201, json=upload_body(id=1492, status="pending", total_emails=1)),
            httpx.Response(422, json={"success": False, "errors": {}}),
        ]
    )
    delete = respx_mock.delete("/lists/1492").respond(200, json={"success": True})
    status = respx_mock.get("/verify/batch/1492").respond(200, json=upload_body(id=1492))

    argv = ["--spend", "--batch", "me@personal.dev", "--no-start"]
    argv += ["--callback-url", "https://hooks.example.com/mt", "--out", str(tmp_path)]
    assert discover.main(argv) == 0

    sent = json.loads(submit.calls[0].request.content)
    assert sent == {
        "emails": ["me@personal.dev"],
        "name": "discovery",
        "auto_start": False,
        "callback_url": "https://hooks.example.com/mt",
    }
    assert delete.called and not status.called
    names = {p.name for p in tmp_path.iterdir()}
    assert "batch_submit_pending.json" in names
    assert "batch_submit.json" not in names
    assert not any(n.startswith("batch_status_") for n in names)
