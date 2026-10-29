"""Helpers shared by every command module: state, client, errors, and output."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, NoReturn

import typer

from mailtester_cli.client import MailTester
from mailtester_cli.errors import InsufficientCreditsError, MailTesterError, RateLimitError


@dataclass(frozen=True)
class State:
    """Global options, stored in `ctx.obj` by the app callback."""

    json: bool = False


def get_state(ctx: typer.Context) -> State:
    """Return the global options, or the defaults when the callback didn't run."""
    return ctx.find_object(State) or State()


def client() -> MailTester:
    """Build a client from `MAILTESTER_API_KEY` (environment or `.env`)."""
    return MailTester()


def fail(message: str, code: int = 1) -> NoReturn:
    """Print `Error: <message>` to stderr and exit with `code`."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


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
        fail(error_message(exc))
    except KeyboardInterrupt:
        typer.echo("Interrupted.", err=True)
        raise typer.Exit(130) from None


def print_json(data: Any) -> None:
    """Write `data` to stdout as indented JSON."""
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def fmt_int(n: int | None) -> str:
    """Format with thousands separators; `None` becomes `-`."""
    return "-" if n is None else f"{n:,}"
