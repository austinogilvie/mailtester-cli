from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from conftest import load_fixture

from mailtester_cli.models import (
    Credits,
    FinderResult,
    ListCounts,
    ListJob,
    VerifyResult,
    _parse_datetime,
)


def data(name):
    return load_fixture(name)[2]["data"]


# VerifyResult


def test_verify_result_is_valid_for_valid_verdict():
    # Assumed verdict: 002 observed no "valid" result, so this edits a copy of verify_single.
    payload = {**data("verify_single"), "result": "valid", "score": 100}
    assert VerifyResult.from_dict(payload).is_valid is True


def test_verify_result_parses_batch_rows():
    rows = [VerifyResult.from_dict(row) for row in data("batch_results_json_all")]
    assert [r.email for r in rows] == [
        "batch1@example.com",
        "batch2@example.com",
        "batch3@example.net",
    ]
    for r in rows:
        assert r.mx_records is None
        assert r.spamtrap is None
        assert r.did_you_mean is None
        assert r.smtp_provider == "Other"
    risky = [r for r in rows if r.result == "risky"]
    assert len(risky) == 2
    for r in risky:
        assert r.score == 5
        assert r.reason == "CATCHALL"
        assert r.is_valid is False
    disposable = rows[2]
    assert disposable.result == "invalid"
    assert disposable.reason == "DISPOSABLE"
    assert disposable.disposable is True


def test_verify_result_missing_required_key():
    payload = dict(data("verify_single"))
    del payload["result"]
    with pytest.raises(KeyError):
        VerifyResult.from_dict(payload)


# FinderResult


@pytest.mark.parametrize("candidates", [{"candidates": []}, {"candidates": None}, {}])
def test_finder_result_not_found_shape(candidates):
    # Assumed not-found shape: 002 step 4b was not run.
    payload = {
        "email": None,
        "status": "unknown",
        "confidence": 0,
        "pattern": None,
        "first_name": "Jane",
        "last_name": "Doe",
        "domain": "example.com",
        "credits_used": 3,
        **candidates,
    }
    result = FinderResult.from_dict(payload)
    assert result.email is None
    assert result.pattern is None
    assert result.candidates == []


# Credits


def test_credits_next_renewal_is_aware():
    payload = {**data("credits_after"), "next_renewal": "2026-11-01T00:00:00+00:00"}
    credits = Credits.from_dict(payload)
    assert credits.next_renewal == datetime(2026, 11, 1, tzinfo=UTC)
    assert credits.next_renewal.tzinfo is not None


# ListJob


def test_list_job_datetimes_are_aware():
    job = ListJob.from_dict(data("lists_after_batch")[0])
    assert job.created_at == datetime(2026, 10, 29, 0, 41, 49, tzinfo=UTC)
    assert job.started_at == datetime(2026, 10, 29, 0, 41, 49, tzinfo=UTC)
    assert job.completed_at == datetime(2026, 10, 29, 0, 42, 16, tzinfo=UTC)
    for value in (job.created_at, job.started_at, job.completed_at):
        assert value.tzinfo is not None


def test_list_job_unstarted():
    payload = {**data("lists_after_batch")[0], "started_at": None, "completed_at": None}
    job = ListJob.from_dict(payload)
    assert job.started_at is None
    assert job.completed_at is None


def test_list_counts_pass_through():
    job = ListJob.from_dict(data("lists_after_batch")[0])
    assert isinstance(job.results, ListCounts)
    assert job.results.raw == data("lists_after_batch")[0]["results"]


# Base pattern

MODELS = [
    (VerifyResult, lambda: data("verify_single")),
    (FinderResult, lambda: data("finder_email")),
    (Credits, lambda: data("credits_after")),
    (ListJob, lambda: data("lists_after_batch")[0]),
]


@pytest.mark.parametrize("cls, payload", MODELS)
def test_from_dict_ignores_unknown_keys(cls, payload):
    extended = {**payload(), "new_field": "surprise"}
    model = cls.from_dict(extended)
    assert model.raw["new_field"] == "surprise"
    assert not hasattr(model, "new_field")


@pytest.mark.parametrize("cls, payload", MODELS)
def test_raw_is_payload_and_hidden_from_repr(cls, payload):
    model = cls.from_dict(payload())
    assert model.raw == payload()
    assert "raw=" not in repr(model)


def test_nested_models_keep_raw():
    finder = FinderResult.from_dict(data("finder_email"))
    assert finder.candidates[0].raw == data("finder_email")["candidates"][0]
    assert "raw=" not in repr(finder.candidates[0])


def test_parse_datetime():
    assert _parse_datetime(None) is None
    assert _parse_datetime("2026-10-29T00:41:49") == datetime(2026, 10, 29, 0, 41, 49, tzinfo=UTC)
    assert _parse_datetime("2026-10-29T00:41:49+00:00").tzinfo is not None


def test_models_are_frozen():
    result = VerifyResult.from_dict(data("verify_single"))
    with pytest.raises(FrozenInstanceError):
        result.score = 100
