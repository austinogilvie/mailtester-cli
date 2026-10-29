"""HTTP client for the MailTester API."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar

import httpx
from dotenv import dotenv_values, find_dotenv

from mailtester_cli import __version__
from mailtester_cli.errors import (
    APIConnectionError,
    BatchFailedError,
    BatchNotStartedError,
    BatchTimeoutError,
    MailTesterError,
    MissingAPIKeyError,
    RateLimitError,
    error_from_response,
)
from mailtester_cli.models import BatchJob, Credits, FinderResult, ListJob, VerifyResult

BASE_URL = "https://app.mailtester.com/api/v1"
ENV_VAR = "MAILTESTER_API_KEY"
MAX_BATCH_EMAILS = 10_000
# the docs say "max 10 MB"; decimal, so the message and the check agree
MAX_UPLOAD_BYTES = 10_000_000
# Python's built-in mimetypes map has no .xlsx entry, so don't let httpx guess from the name.
_UPLOAD_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# Assumed names: only "pending" (auto_start=false), "processing", and "completed" have been
# observed. A "pending" batch never starts on its own, so wait_for_batch raises
# BatchNotStartedError on it instead of timing out.
FAILURE_STATUSES = frozenset({"failed", "cancelled", "canceled", "error"})

T = TypeVar("T")


@dataclass(frozen=True)
class RateLimit:
    """Rate-limit state from the most recent response's X-RateLimit-* headers."""

    limit: int | None
    remaining: int | None
    reset: datetime | None

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> RateLimit:
        reset = _int_header(headers, "x-ratelimit-reset")
        return cls(
            limit=_int_header(headers, "x-ratelimit-limit"),
            remaining=_int_header(headers, "x-ratelimit-remaining"),
            reset=_timestamp(reset),
        )


def _timestamp(value: int | None) -> datetime | None:
    try:
        return datetime.fromtimestamp(value, tz=UTC) if value is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _int_header(headers: httpx.Headers, name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, ValueError):
        return None


def _env_key() -> str | None:
    value = os.environ.get(ENV_VAR, "").strip()
    return value or None


def _dotenv_key() -> str | None:
    # Read only our variable; usecwd finds the user's .env after `uv tool install`.
    value = (dotenv_values(find_dotenv(usecwd=True)).get(ENV_VAR) or "").strip()
    return value or None


def _resolve_key(api_key: str | None) -> str:
    key = (api_key or "").strip() or _env_key() or _dotenv_key()
    if key is None:
        raise MissingAPIKeyError(
            f"{ENV_VAR} is not set. Set it in your environment or in a .env file."
        )
    # httpx echoes a rejected header value in its error, so never send a malformed key.
    if not (key.isascii() and key.isprintable()) or any(c.isspace() for c in key):
        raise MissingAPIKeyError(
            f"{ENV_VAR} contains spaces, line breaks, or other invalid characters."
        )
    return key


def _parse(build: Callable[[Any], T], data: Any) -> T:
    """Build a model from `data`, turning a shape mismatch into a MailTesterError."""
    try:
        return build(data)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        detail = f"missing {exc}" if isinstance(exc, KeyError) else str(exc)
        raise MailTesterError(
            f"Unexpected response from MailTester: {detail}", body={"data": data}
        ) from exc


class MailTester:
    """Client for the MailTester REST API."""

    # wait_for_batch uses only these, so tests can swap in a fake clock.
    _sleep = staticmethod(time.sleep)
    _clock = staticmethod(time.monotonic)

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 90.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = _resolve_key(api_key)
        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": f"mailtester-cli/{__version__}",
        }
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.rate_limit = RateLimit(None, None, None)
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return f"MailTester(base_url={self.base_url!r})"

    def __enter__(self) -> MailTester:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying httpx client if this instance created it."""
        if self._owns_client:
            self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> httpx.Response:
        try:
            response = self._client.request(
                method,
                self.base_url + path,
                params=params,
                json=json,
                data=data,
                files=files,
                headers=self._headers,
            )
        except httpx.TransportError as exc:
            detail = (str(exc) or type(exc).__name__).replace(self._api_key, "***")
            raise APIConnectionError(f"Could not reach MailTester: {detail}") from exc
        self.rate_limit = RateLimit.from_headers(response.headers)
        if not response.is_success:
            raise error_from_response(response)
        return response

    def _data(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> Any:
        response = self._request(method, path, params=params, json=json, data=data, files=files)
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or "data" not in body:
            raise MailTesterError(
                "Unexpected response from MailTester",
                status_code=response.status_code,
                body=body if isinstance(body, dict) else None,
            )
        return body["data"]

    def verify(
        self, email: str, *, timeout: int | None = None, smtp_check: bool | None = None
    ) -> VerifyResult:
        """Verify one address. `timeout` is the server's SMTP timeout in seconds. 1 credit."""
        body: dict[str, Any] = {"email": email}
        if timeout is not None:
            body["timeout"] = timeout
        if smtp_check is not None:
            body["smtp_check"] = smtp_check
        return _parse(VerifyResult.from_dict, self._data("POST", "/verify", json=body))

    def find_email(self, first_name: str, last_name: str, domain: str) -> FinderResult:
        """Find a person's address at a domain. 3 credits."""
        body = {"first_name": first_name, "last_name": last_name, "domain": domain}
        return _parse(FinderResult.from_dict, self._data("POST", "/finder/email", json=body))

    def lists(self, *, per_page: int | None = None) -> list[ListJob]:
        """Return the first page of verification lists."""
        params = {"per_page": per_page} if per_page is not None else None
        data = self._data("GET", "/lists", params=params)
        return _parse(lambda items: [ListJob.from_dict(item) for item in items], data)

    def credits(self) -> Credits:
        """Return the account's credit balance."""
        return _parse(Credits.from_dict, self._data("GET", "/credits"))

    def submit_batch(
        self,
        emails: Sequence[str],
        *,
        name: str | None = None,
        callback_url: str | None = None,
        auto_start: bool | None = None,
    ) -> BatchJob:
        """Submit addresses for bulk verification. 1 credit per address."""
        if isinstance(emails, str):
            raise TypeError("emails must be a sequence of addresses, not a str")
        if not 1 <= len(emails) <= MAX_BATCH_EMAILS:
            raise ValueError(
                f"emails must hold 1 to {MAX_BATCH_EMAILS:,} addresses (got {len(emails):,})"
            )
        body: dict[str, Any] = {"emails": list(emails)}
        if name is not None:
            body["name"] = name
        if callback_url is not None:
            body["callback_url"] = callback_url
        if auto_start is not None:
            body["auto_start"] = auto_start
        return _parse(BatchJob.from_dict, self._data("POST", "/verify/batch", json=body))

    def upload_batch(
        self,
        path: str | os.PathLike[str],
        *,
        name: str | None = None,
        callback_url: str | None = None,
        auto_start: bool | None = None,
    ) -> BatchJob:
        """Upload a CSV, TXT, or XLSX file for bulk verification. 1 credit per address.

        The file goes to the API unchanged, up to MAX_UPLOAD_BYTES. Raises ValueError for an
        empty or oversize file before any request. FileNotFoundError and other OSErrors from
        reading the file propagate.
        """
        file = Path(path)
        size = file.stat().st_size
        if size == 0:
            raise ValueError(f"{file.name} is empty")
        if size > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"{file.name} is {size:,} bytes; the upload limit is {MAX_UPLOAD_BYTES:,} bytes"
            )
        form: dict[str, str] = {}
        if name is not None:
            form["name"] = name
        if callback_url is not None:
            form["callback_url"] = callback_url
        if auto_start is not None:
            form["auto_start"] = "1" if auto_start else "0"
        content_type = _UPLOAD_CONTENT_TYPES.get(file.suffix.lower(), "application/octet-stream")
        with file.open("rb") as fh:
            data = self._data(
                "POST",
                "/verify/batch/upload",
                data=form or None,
                files={"file": (file.name, fh, content_type)},
            )
        return _parse(BatchJob.from_dict, data)

    def batch_status(self, batch_id: int | str) -> BatchJob:
        """Return a batch's current status and counts."""
        return _parse(BatchJob.from_dict, self._data("GET", f"/verify/batch/{batch_id}"))

    def wait_for_batch(
        self,
        batch_id: int | str,
        *,
        timeout: float = 600,
        interval: float = 3.0,
        max_interval: float = 10.0,
        on_poll: Callable[[BatchJob], None] | None = None,
    ) -> BatchJob:
        """Poll until the batch is completed, backing off from `interval` to `max_interval`.

        Raises BatchNotStartedError on "pending", BatchFailedError on a failure status, and
        BatchTimeoutError after `timeout` seconds. A 429 waits for `retry_after` (or
        `max_interval`); if that wait would pass the deadline, the RateLimitError propagates.
        Other errors propagate too.
        """
        if interval <= 0 or max_interval <= 0 or timeout < 0:
            raise ValueError("interval and max_interval must be positive, timeout non-negative")
        deadline = self._clock() + timeout
        delay = interval
        while True:
            try:
                job = self.batch_status(batch_id)
            except RateLimitError as exc:
                wait = exc.retry_after if exc.retry_after and exc.retry_after > 0 else max_interval
                if self._clock() + wait > deadline:
                    raise
                self._sleep(wait)
                continue
            if job.status == "pending":
                raise BatchNotStartedError(job)
            if on_poll is not None:
                on_poll(job)
            if job.status == "completed":
                return job
            if job.status in FAILURE_STATUSES:
                raise BatchFailedError(job)
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise BatchTimeoutError(job, timeout)
            self._sleep(min(delay, remaining))
            delay = min(delay * 1.5, max_interval)

    def batch_results(self, batch_id: int | str, *, filter: str = "all") -> list[VerifyResult]:
        """Return a batch's result rows. `filter` is all, valid, invalid, risky, or unknown."""
        params = {"format": "json", "filter": filter}
        data = self._data("GET", f"/verify/batch/{batch_id}/results", params=params)
        return _parse(lambda rows: [VerifyResult.from_dict(row) for row in rows], data)

    def batch_results_csv(self, batch_id: int | str, *, filter: str = "all") -> str:
        """Return a batch's results as CSV text with columns email,result,reason."""
        params = {"format": "csv", "filter": filter}
        return self._request("GET", f"/verify/batch/{batch_id}/results", params=params).text
