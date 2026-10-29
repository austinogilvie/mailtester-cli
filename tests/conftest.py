import json
from pathlib import Path

import pytest

import mailtester_cli.client
from mailtester_cli import MailTester

FIXTURES = Path(__file__).parent / "fixtures" / "api"


def _record(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def load_fixture(name: str) -> tuple[int, dict[str, str], dict | str]:
    """Return (status, headers, body) for tests/fixtures/api/<name>.json.

    body is the parsed JSON dict, or, when the record has body_file
    (e.g. batch_results_csv_all.json), the text of that sibling file.
    """
    record = _record(name)
    if "body_file" in record:
        return record["status"], record["headers"], (FIXTURES / record["body_file"]).read_text()
    return record["status"], record["headers"], record["body"]


def load_request(name: str) -> dict:
    """Return the recorded request ({method, path, params, json}) for <name>.json."""
    return _record(name)["request"]


def serve(respx_mock, name: str, url: str, method: str = "GET", **patterns):
    """Route method+url on respx_mock to the recorded response in <name>.json.

    Extra respx patterns pass through, e.g. params__eq={...} to match the exact query.
    """
    status, headers, body = load_fixture(name)
    kwargs = {"text": body} if isinstance(body, str) else {"json": body}
    return respx_mock.request(method, url, **patterns).respond(status, headers=headers, **kwargs)


def sent_json(route) -> dict:
    """Return the JSON body of the last request a respx route received."""
    return json.loads(route.calls.last.request.content)


@pytest.fixture
def api_fixture():
    return load_fixture


@pytest.fixture
def mt():
    with MailTester(api_key="evp_test") as client:
        yield client


@pytest.fixture(autouse=True)
def _isolate_api_key(monkeypatch):
    """Keep the real key (environment or .env) out of every test."""
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    monkeypatch.setattr(mailtester_cli.client, "dotenv_values", lambda *args, **kwargs: {})
