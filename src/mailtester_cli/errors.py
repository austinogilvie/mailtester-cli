"""Errors raised by the MailTester client."""

from __future__ import annotations

from typing import Any

import httpx


class MailTesterError(Exception):
    """Base class for every error the client raises. `str(e)` is the API's message."""

    def __init__(
        self, message: str, *, status_code: int | None = None, body: dict | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body


class MissingAPIKeyError(MailTesterError):
    """No API key was found."""


class AuthenticationError(MailTesterError):
    """401: the API key was rejected."""


class InsufficientCreditsError(MailTesterError):
    """402: not enough credits for the request."""

    def __init__(self, message: str, *, available_credits: int | None = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.available_credits = available_credits


class NotFoundError(MailTesterError):
    """404: the resource doesn't exist."""


class ConflictError(MailTesterError):
    """409: the request conflicts with the resource's current state."""


class ValidationError(MailTesterError):
    """422: the request failed validation. `errors` maps each field to its messages."""

    def __init__(self, message: str, *, errors: dict[str, list[str]] | None = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.errors = errors or {}


class RateLimitError(MailTesterError):
    """429: rate limited. `retry_after` is in seconds when the API gave one."""

    def __init__(self, message: str, *, retry_after: int | None = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(MailTesterError):
    """5xx: the API or its upstream failed."""


class APIConnectionError(MailTesterError):
    """The request never got a response (connection failure or timeout)."""


_BY_STATUS: dict[int, type[MailTesterError]] = {
    401: AuthenticationError,
    402: InsufficientCreditsError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _field_errors(body: dict | None) -> dict[str, list[str]]:
    errors = body.get("errors") if body else None
    if not isinstance(errors, dict):
        return {}
    return {
        str(field): [msgs] if isinstance(msgs, str) else [str(m) for m in msgs]
        for field, msgs in errors.items()
        if isinstance(msgs, str | list)
    }


def _message(status: int, body: dict | None, errors: dict[str, list[str]]) -> str:
    if body and isinstance(body.get("error"), str) and body["error"]:
        return body["error"]
    if errors:
        return "; ".join(f"{field}: {msg}" for field, msgs in errors.items() for msg in msgs)
    return f"HTTP {status}"


def error_from_response(response: httpx.Response) -> MailTesterError:
    """Map a non-2xx response to the matching MailTesterError subclass."""
    try:
        parsed = response.json()
    except ValueError:
        parsed = None
    body = parsed if isinstance(parsed, dict) else None
    status = response.status_code
    errors = _field_errors(body)
    message = _message(status, body, errors)
    kwargs: dict[str, Any] = {"status_code": status, "body": body}

    if 500 <= status < 600:
        return ServerError(message, **kwargs)
    cls = _BY_STATUS.get(status, MailTesterError)
    if cls is InsufficientCreditsError:
        kwargs["available_credits"] = _as_int(body.get("available_credits")) if body else None
    elif cls is ValidationError:
        kwargs["errors"] = errors
    elif cls is RateLimitError:
        kwargs["retry_after"] = _as_int(body.get("retry_after")) if body else None
    return cls(message, **kwargs)
