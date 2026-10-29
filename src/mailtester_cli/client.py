"""HTTP client for the MailTester API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

import httpx
from dotenv import find_dotenv, load_dotenv

from mailtester_cli import __version__
from mailtester_cli.errors import (
    APIConnectionError,
    MailTesterError,
    MissingAPIKeyError,
    error_from_response,
)
from mailtester_cli.models import Credits, FinderResult, ListJob, VerifyResult

BASE_URL = "https://app.mailtester.com/api/v1"
ENV_VAR = "MAILTESTER_API_KEY"


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
            reset=datetime.fromtimestamp(reset, tz=UTC) if reset is not None else None,
        )


def _int_header(headers: httpx.Headers, name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, ValueError):
        return None


def _env_key() -> str | None:
    value = os.environ.get(ENV_VAR, "").strip()
    return value or None


def _resolve_key(api_key: str | None) -> str:
    key = (api_key or "").strip() or _env_key()
    if key is None:
        load_dotenv(find_dotenv(usecwd=True))
        key = _env_key()
    if key is None:
        raise MissingAPIKeyError(
            f"{ENV_VAR} is not set. Set it in your environment or in a .env file."
        )
    return key


class MailTester:
    """Client for the MailTester REST API."""

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
        self, method: str, path: str, *, params: dict | None = None, json: Any = None
    ) -> httpx.Response:
        try:
            response = self._client.request(
                method, self.base_url + path, params=params, json=json, headers=self._headers
            )
        except httpx.TransportError as exc:
            detail = str(exc) or type(exc).__name__
            raise APIConnectionError(f"Could not reach MailTester: {detail}") from exc
        self.rate_limit = RateLimit.from_headers(response.headers)
        if not response.is_success:
            raise error_from_response(response)
        return response

    def _data(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        response = self._request(method, path, params=params, json=json)
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
        return VerifyResult.from_dict(self._data("POST", "/verify", json=body))

    def find_email(self, first_name: str, last_name: str, domain: str) -> FinderResult:
        """Find a person's address at a domain. 3 credits."""
        body = {"first_name": first_name, "last_name": last_name, "domain": domain}
        return FinderResult.from_dict(self._data("POST", "/finder/email", json=body))

    def lists(self, *, per_page: int | None = None) -> list[ListJob]:
        """Return the first page of verification lists."""
        params = {"per_page": per_page} if per_page is not None else None
        return [ListJob.from_dict(item) for item in self._data("GET", "/lists", params=params)]

    def credits(self) -> Credits:
        """Return the account's credit balance."""
        return Credits.from_dict(self._data("GET", "/credits"))
