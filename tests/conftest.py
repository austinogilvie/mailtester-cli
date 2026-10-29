import json
from pathlib import Path

import pytest

import mailtester_cli.client

FIXTURES = Path(__file__).parent / "fixtures" / "api"


def load_fixture(name: str) -> tuple[int, dict[str, str], dict | str]:
    """Return (status, headers, body) for tests/fixtures/api/<name>.json.

    body is the parsed JSON dict, or, when the record has body_file
    (e.g. batch_results_csv_all.json), the text of that sibling file.
    """
    record = json.loads((FIXTURES / f"{name}.json").read_text())
    if "body_file" in record:
        return record["status"], record["headers"], (FIXTURES / record["body_file"]).read_text()
    return record["status"], record["headers"], record["body"]


@pytest.fixture
def api_fixture():
    return load_fixture


@pytest.fixture(autouse=True)
def _isolate_api_key(monkeypatch):
    """Keep the real key (environment or .env) out of every test."""
    monkeypatch.delenv("MAILTESTER_API_KEY", raising=False)
    monkeypatch.setattr(mailtester_cli.client, "load_dotenv", lambda *args, **kwargs: None)
