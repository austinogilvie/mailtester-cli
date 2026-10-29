"""Typed response models for the MailTester API.

Each model is a frozen dataclass built from the API's `data` payload with `from_dict`
(`ListPage` takes the whole body with `from_body`). Required keys raise `KeyError` when
missing, optional keys default to `None`, unknown keys are ignored, and `raw` keeps the
untouched payload. Values are passed through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp to an aware datetime, assuming UTC when naive."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class VerifyResult:
    """A single verify result, or one row of batch results.

    `mx_records`, `spamtrap`, and `did_you_mean` are `None` when not returned (batch rows).
    """

    email: str
    result: str
    score: int
    reason: str
    disposable: bool
    role: bool
    free: bool
    accept_all: bool
    smtp_provider: str | None
    mx_records: list[str] | None
    spamtrap: bool | None
    did_you_mean: str | None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_valid(self) -> bool:
        return self.result == "valid"

    @classmethod
    def from_dict(cls, data: dict) -> VerifyResult:
        return cls(
            email=data["email"],
            result=data["result"],
            score=data["score"],
            reason=data["reason"],
            disposable=data["disposable"],
            role=data["role"],
            free=data["free"],
            accept_all=data["accept_all"],
            smtp_provider=data["smtp_provider"],
            mx_records=data.get("mx_records"),
            spamtrap=data.get("spamtrap"),
            did_you_mean=data.get("did_you_mean"),
            raw=data,
        )


@dataclass(frozen=True)
class FinderCandidate:
    """One address a finder checked: a name pattern for a person, or a role mailbox."""

    email: str
    status: str
    confidence: int
    is_role: bool
    is_catch_all: bool
    reason: str | None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> FinderCandidate:
        return cls(
            email=data["email"],
            status=data["status"],
            confidence=data["confidence"],
            is_role=data["is_role"],
            is_catch_all=data["is_catch_all"],
            reason=data.get("reason"),
            raw=data,
        )


@dataclass(frozen=True)
class FinderResult:
    """The finder's best address for a person at a domain, plus every candidate tried."""

    email: str | None
    status: str
    confidence: int
    pattern: str | None
    first_name: str
    last_name: str
    domain: str
    candidates: list[FinderCandidate]
    credits_used: int
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> FinderResult:
        return cls(
            email=data.get("email"),
            status=data["status"],
            confidence=data["confidence"],
            pattern=data.get("pattern"),
            first_name=data["first_name"],
            last_name=data["last_name"],
            domain=data["domain"],
            candidates=[FinderCandidate.from_dict(c) for c in data.get("candidates") or []],
            credits_used=data["credits_used"],
            raw=data,
        )


@dataclass(frozen=True)
class RoleFinderResult:
    """Role mailboxes at a domain, from `POST /finder/domain` or `POST /finder/company`.

    `company` is the echoed name from the company finder and `None` from the domain finder;
    `offset` is `None` from the company finder, which doesn't return one. `total` can exceed
    `len(emails)`: the API returns at most `limit` (10) mailboxes per call. A null or missing
    `emails` reads as an empty list, like `FinderResult.candidates`.
    """

    domain: str
    company: str | None
    emails: list[FinderCandidate]
    total: int
    limit: int
    offset: int | None
    credits_used: int
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> RoleFinderResult:
        return cls(
            domain=data["domain"],
            company=data.get("company"),
            emails=[FinderCandidate.from_dict(item) for item in data.get("emails") or []],
            total=data["total"],
            limit=data["limit"],
            offset=data.get("offset"),
            credits_used=data["credits_used"],
            raw=data,
        )


@dataclass(frozen=True)
class Credits:
    """The account's credit balance and usage."""

    balance: int
    monthly_credits: int
    monthly_credit_amount: int
    rollover_credits: int
    reserved_credits: int
    total_purchased: int
    total_used: int
    next_renewal: datetime | None
    account_type: str
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> Credits:
        return cls(
            balance=data["balance"],
            monthly_credits=data["monthly_credits"],
            monthly_credit_amount=data["monthly_credit_amount"],
            rollover_credits=data["rollover_credits"],
            reserved_credits=data["reserved_credits"],
            total_purchased=data["total_purchased"],
            total_used=data["total_used"],
            next_renewal=_parse_datetime(data.get("next_renewal")),
            account_type=data["account_type"],
            raw=data,
        )


@dataclass(frozen=True)
class ListCounts:
    """Per-verdict counts nested in a list item's `results`, shown as the API returns them."""

    valid: int
    invalid: int
    unknown: int
    catch_all: int
    risky: int
    disposable: int
    role: int
    free: int
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> ListCounts:
        return cls(
            valid=data["valid"],
            invalid=data["invalid"],
            unknown=data["unknown"],
            catch_all=data["catch_all"],
            risky=data["risky"],
            disposable=data["disposable"],
            role=data["role"],
            free=data["free"],
            raw=data,
        )


@dataclass(frozen=True)
class ListJob:
    """One item from `GET /lists`. Its `id` is the batch id."""

    id: int
    name: str | None
    filename: str | None
    status: str
    progress: float
    total_emails: int
    processed_emails: int
    results: ListCounts
    credits_used: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> ListJob:
        return cls(
            id=data["id"],
            name=data.get("name"),
            filename=data.get("filename"),
            status=data["status"],
            progress=data["progress"],
            total_emails=data["total_emails"],
            processed_emails=data["processed_emails"],
            results=ListCounts.from_dict(data["results"]),
            credits_used=data["credits_used"],
            created_at=_parse_datetime(data["created_at"]),
            started_at=_parse_datetime(data.get("started_at")),
            completed_at=_parse_datetime(data.get("completed_at")),
            raw=data,
        )


@dataclass(frozen=True)
class ListPage:
    """One page of `GET /lists`: its items plus the `meta` pagination block. `raw` is the body."""

    items: list[ListJob]
    current_page: int
    last_page: int
    per_page: int
    total: int
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_body(cls, body: dict) -> ListPage:
        meta = body["meta"]
        return cls(
            items=[ListJob.from_dict(item) for item in body["data"]],
            current_page=meta["current_page"],
            last_page=meta["last_page"],
            per_page=meta["per_page"],
            total=meta["total"],
            raw=body,
        )


@dataclass(frozen=True)
class BatchJob:
    """A bulk verification job, from `POST /verify/batch` or `GET /verify/batch/{id}`.

    The submit response has only `id`, `status`, `total_emails`, and `created_at`, so every
    other field is `None` there. There's no risky count because the API doesn't return one.
    `progress` is a percentage and can be fractional (12.5 for 1 of 8).
    """

    id: int
    status: str
    total_emails: int
    created_at: datetime
    progress: float | None = None
    processed_emails: int | None = None
    valid_emails: int | None = None
    invalid_emails: int | None = None
    unknown_emails: int | None = None
    completed_at: datetime | None = None
    retrieving_results: bool | None = None
    notice: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @classmethod
    def from_dict(cls, data: dict) -> BatchJob:
        return cls(
            id=data["id"],
            status=data["status"],
            total_emails=data["total_emails"],
            created_at=_parse_datetime(data["created_at"]),
            progress=data.get("progress"),
            processed_emails=data.get("processed_emails"),
            valid_emails=data.get("valid_emails"),
            invalid_emails=data.get("invalid_emails"),
            unknown_emails=data.get("unknown_emails"),
            completed_at=_parse_datetime(data.get("completed_at")),
            retrieving_results=data.get("retrieving_results"),
            notice=data.get("notice"),
            raw=data,
        )
