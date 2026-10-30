"""Helpers shared by every command module: state, client, errors, and output."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, NoReturn

import typer

from mailtester_cli.client import MailTester, RetryAttempt
from mailtester_cli.errors import (
    InsufficientCreditsError,
    MailTesterError,
    RateLimitError,
    ServerError,
    ValidationError,
)


@dataclass(frozen=True)
class State:
    """Global options, stored in `ctx.obj` by the app callback."""

    json: bool = False
    api_key: str | None = field(default=None, repr=False)


# The running command's options, so helpers such as `fail` can read --json without every
# caller passing `ctx` along. None outside a command.
_current: ContextVar[State | None] = ContextVar("mailtester_state", default=None)


def set_state(ctx: typer.Context, state: State) -> None:
    """Store the global options on `ctx` and as the current state until the command ends."""
    ctx.obj = state
    token = _current.set(state)
    ctx.call_on_close(lambda: _current.reset(token))


def get_state(ctx: typer.Context | None = None) -> State:
    """Return the global options, or the defaults when the callback didn't run."""
    found = ctx.find_object(State) if ctx is not None else None
    return found or _current.get() or State()


def client() -> MailTester:
    """Build a client from `--api-key`, else `MAILTESTER_API_KEY` (environment or `.env`).

    Each retry prints a line on stderr.
    """
    return MailTester(api_key=get_state().api_key, on_retry=_print_retry)


def _print_retry(retry: RetryAttempt) -> None:
    """One stderr line per retry, under --json too: a retry is progress, not a result."""
    error = retry.error
    if isinstance(error, RateLimitError):
        what = "Rate limited"
    elif isinstance(error, ServerError):
        what = f"MailTester returned {error.status_code}"
    else:
        what = "Could not reach MailTester"
    typer.echo(f"{what}, retrying in {retry.delay:g}s...", err=True)


def fail(
    message: str,
    code: int = 1,
    *,
    error: MailTesterError | None = None,
    hint: str | None = None,
) -> NoReturn:
    """Print the error on stderr and exit with `code`.

    Text mode prints `Error: <message>. <hint>`, with `error_message`'s suffixes when `error`
    is given. Under --json it prints one `error_object` line instead.
    """
    _print_error(message, code, error=error, hint=hint)
    raise typer.Exit(code)


def report_error(exc: MailTesterError, *, prefix: str = "", **fields: Any) -> None:
    """Print an error about one item of many, without exiting.

    Text mode prints `Error: <prefix><message>`; under --json the error object gains `fields`
    (for example `id=1492` or `email=...`) instead of the prefix.
    """
    _print_error(str(exc), 1, error=exc, prefix=prefix, fields=fields)


def error_object(
    message: str, code: int, error: MailTesterError | None, hint: str | None
) -> dict[str, Any]:
    """The --json form of an error: `error`, `type`, `status_code`, then the fields present.

    `error` is the bare message; the numbers `error_message` appends for humans are their own
    keys. A local error with no exception is a `UsageError` (exit 2) or a `CLIError`.
    """
    local = "UsageError" if code == 2 else "CLIError"
    obj: dict[str, Any] = {
        "error": message,
        "type": type(error).__name__ if error is not None else local,
        "status_code": error.status_code if error is not None else None,
    }
    if isinstance(error, ValidationError) and error.errors:
        obj["errors"] = error.errors
    if isinstance(error, InsufficientCreditsError) and error.available_credits is not None:
        obj["available_credits"] = error.available_credits
    if isinstance(error, RateLimitError) and error.retry_after is not None:
        obj["retry_after"] = error.retry_after
    if hint:
        obj["hint"] = hint
    return obj


def _print_error(
    message: str,
    code: int,
    *,
    error: MailTesterError | None = None,
    hint: str | None = None,
    prefix: str = "",
    fields: dict[str, Any] | None = None,
) -> None:
    if get_state().json:
        obj = error_object(message, code, error, hint) | (fields or {})
        typer.echo(json.dumps(obj, ensure_ascii=False), err=True)
        return
    text = error_message(error) if error is not None else message
    if hint:
        text = f"{text.rstrip('.')}. {hint}"
    typer.echo(f"Error: {prefix}{text}", err=True)


def confirm(question: str) -> bool:
    """Ask `question` on stderr and read one line from stdin; EOF and anything but y/yes is no.

    typer.confirm prints the prompt's trailing space to stdout; stdout is for results only.
    """
    typer.echo(f"{question} [y/N]: ", err=True, nl=False)
    answer = sys.stdin.readline()
    if not answer:
        typer.echo(err=True)
    return answer.strip().lower() in {"y", "yes"}


def error_message(exc: MailTesterError) -> str:
    """The API's message, plus the available-credits or retry hint when there is one."""
    message = str(exc)
    if isinstance(exc, InsufficientCreditsError) and exc.available_credits is not None:
        message += f" (available credits: {fmt_int(exc.available_credits)})"
    elif isinstance(exc, RateLimitError) and exc.retry_after is not None:
        if not message.endswith((".", "!", "?")):
            message += "."
        message += f" Retry in {exc.retry_after}s."
    return message


@contextmanager
def handle_errors() -> Iterator[None]:
    """Turn client errors into `Error: <message>` on stderr and exit 1; Ctrl-C exits 130."""
    try:
        yield
    except MailTesterError as exc:
        fail(str(exc), error=exc)
    except KeyboardInterrupt:
        typer.echo("Interrupted.", err=True)
        raise typer.Exit(130) from None


def print_json(data: Any) -> None:
    """Write `data` to stdout as indented JSON."""
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def fmt_int(n: int | None) -> str:
    """Format with thousands separators; `None` becomes `-`."""
    return "-" if n is None else f"{n:,}"
