import json
from pathlib import Path

import pytest

import mailtester_cli.client

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


def serve(respx_mock, name: str, url: str, method: str = "GET"):
    """Route method+url on respx_mock to the recorded response in <name>.json."""
    status, headers, body = load_fixture(name)
    kwargs = {"text": body} if isinstance(body, str) else {"json": body}
    return respx_mock.request(method, url).respond(status, headers=headers, **kwargs)


@pytest.fixture
def api_fixture():
    return load_fixture


@pytest.fixture(autouse=True)
def _isolate_api_key(monkeypatch):
    """Keep the real key (environment or .env) out of every test."""
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    monkeypatch.setattr(mailtester_cli.client, "load_dotenv", lambda *args, **kwargs: None)
