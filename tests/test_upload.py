from pathlib import Path

import httpx
import pytest
from conftest import load_fixture, load_request, sent_form, serve

from mailtester_cli.client import BASE_URL, MAX_UPLOAD_BYTES
from mailtester_cli.errors import MailTesterError, ValidationError
from mailtester_cli.models import BatchJob

UPLOAD = f"{BASE_URL}/verify/batch/upload"
CSV = b"name,email\nJane Doe,jane@example.com\nJohn Doe,john@example.net\n"
XLSX_HEAD = b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00\xff\xfe\x9c\xe3"  # not UTF-8
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_upload_batch_sends_file_part_only(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload", UPLOAD, method="POST")
    job = mt.upload_batch(str(write(tmp_path, "leads.csv", CSV)))
    content_type = route.calls.last.request.headers["content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert sent_form(route) == ({}, {"file": ("leads.csv", CSV, "text/csv")})
    assert isinstance(job, BatchJob)
    data = load_fixture("batch_upload")[2]["data"]
    assert (job.id, job.status, job.total_emails) == (
        data["id"],
        data["status"],
        data["total_emails"],
    )
    assert job.created_at.tzinfo is not None


def test_upload_batch_request_matches_fixture(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload_pending", UPLOAD, method="POST")
    request = load_request("batch_upload_pending")
    assert request["form"] == {"auto_start": "0"}
    mt.upload_batch(write(tmp_path, request["file"], b"one@example.com\n"), auto_start=False)
    fields, files = sent_form(route)
    assert fields == request["form"]
    assert files["file"][0] == request["file"]


def test_upload_batch_sends_optional_fields(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload_pending", UPLOAD, method="POST")
    mt.upload_batch(
        write(tmp_path, "leads.csv", CSV),
        name="Q3 leads",
        callback_url="https://hooks.example.com/mt",
        auto_start=False,
    )
    assert sent_form(route)[0] == {
        "name": "Q3 leads",
        "callback_url": "https://hooks.example.com/mt",
        "auto_start": "0",
    }


def test_upload_batch_auto_start_true_sends_1(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload", UPLOAD, method="POST")
    mt.upload_batch(write(tmp_path, "leads.csv", CSV), auto_start=True)
    assert sent_form(route)[0] == {"auto_start": "1"}


def test_upload_batch_accepts_pathlike(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload", UPLOAD, method="POST")
    path = write(tmp_path, "leads.csv", CSV)
    assert isinstance(path, Path)
    from_path = mt.upload_batch(path)
    from_str = mt.upload_batch(str(path))
    assert from_path == from_str
    assert sent_form(route) == ({}, {"file": ("leads.csv", CSV, "text/csv")})


@pytest.mark.parametrize(
    ("name", "data", "content_type"),
    [
        ("leads.txt", b"a@example.com\n", "text/plain"),
        ("leads.xlsx", XLSX_HEAD, XLSX_TYPE),
    ],
)
def test_upload_batch_txt_and_xlsx_content_types(
    respx_mock, mt, tmp_path, name, data, content_type
):
    route = serve(respx_mock, "batch_upload_xlsx_pending", UPLOAD, method="POST")
    mt.upload_batch(write(tmp_path, name, data), auto_start=False)
    assert sent_form(route) == ({"auto_start": "0"}, {"file": (name, data, content_type)})


def test_upload_batch_rejects_empty_file_without_request(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload", UPLOAD, method="POST")
    with pytest.raises(ValueError, match="is empty"):
        mt.upload_batch(write(tmp_path, "empty.csv", b""))
    assert route.call_count == 0


def test_upload_batch_rejects_oversize_file_without_request(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload", UPLOAD, method="POST")
    path = tmp_path / "big.csv"
    with path.open("wb") as fh:  # sparse: one byte past the limit
        fh.seek(MAX_UPLOAD_BYTES)
        fh.write(b"x")
    assert path.stat().st_size == MAX_UPLOAD_BYTES + 1
    with pytest.raises(ValueError, match="upload limit"):
        mt.upload_batch(path)
    assert route.call_count == 0


def test_upload_batch_accepts_exactly_max_bytes(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload", UPLOAD, method="POST")
    path = tmp_path / "max.csv"
    with path.open("wb") as fh:
        fh.truncate(MAX_UPLOAD_BYTES)
    mt.upload_batch(path)
    assert route.call_count == 1
    assert len(sent_form(route)[1]["file"][1]) == MAX_UPLOAD_BYTES


def test_upload_batch_missing_file_raises_without_request(respx_mock, mt, tmp_path):
    route = serve(respx_mock, "batch_upload", UPLOAD, method="POST")
    with pytest.raises(FileNotFoundError):
        mt.upload_batch(tmp_path / "missing.csv")
    assert route.call_count == 0


@pytest.mark.parametrize("status", [200, 201])
def test_upload_batch_parses_200_and_201(respx_mock, mt, tmp_path, status):
    body = load_fixture("batch_upload")[2]
    respx_mock.post(UPLOAD).mock(return_value=httpx.Response(status, json=body))
    job = mt.upload_batch(write(tmp_path, "leads.csv", CSV))
    assert job == BatchJob.from_dict(body["data"])


def test_upload_batch_pending(respx_mock, mt, tmp_path):
    serve(respx_mock, "batch_upload_pending", UPLOAD, method="POST")
    job = mt.upload_batch(write(tmp_path, "one.csv", b"one@example.com\n"), auto_start=False)
    assert job.status == "pending"
    assert job.is_complete is False


def test_upload_batch_bad_type_raises_validation_error(respx_mock, mt, tmp_path):
    serve(respx_mock, "batch_upload_bad_type", UPLOAD, method="POST")
    path = write(tmp_path, "leads.json", b'["a@example.com"]')
    with pytest.raises(ValidationError) as info:
        mt.upload_batch(path, auto_start=False)
    assert "file" in info.value.errors
    assert str(info.value).startswith("file: ")


def test_upload_batch_updates_rate_limit(respx_mock, mt, tmp_path):
    serve(respx_mock, "batch_upload", UPLOAD, method="POST")
    headers = load_fixture("batch_upload")[1]
    assert "x-ratelimit-limit" in headers
    mt.upload_batch(write(tmp_path, "leads.csv", CSV))
    assert mt.rate_limit.limit == int(headers["x-ratelimit-limit"])


def test_upload_batch_malformed_body_raises_mailtester_error(respx_mock, mt, tmp_path):
    respx_mock.post(UPLOAD).respond(201, json={"success": True, "data": {"id": 1}})
    with pytest.raises(MailTesterError, match="missing 'status'"):
        mt.upload_batch(write(tmp_path, "leads.csv", CSV))
