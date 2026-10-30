"""`verify` command: one address, or many in turn."""

import json
import math

import typer

from mailtester_cli.client import MailTester
from mailtester_cli.commands._input import (
    CONFIRM_THRESHOLD,
    confirm_spend,
    credits,
    read_addresses,
    source_name,
)
from mailtester_cli.commands._shared import (
    client,
    error_message,
    fail,
    fmt_int,
    get_state,
    handle_errors,
    print_json,
)
from mailtester_cli.errors import MailTesterError, ValidationError
from mailtester_cli.models import VerifyResult

FLAGS = ("disposable", "role", "free", "accept_all", "spamtrap")
VERIFY_COST = 1  # per address, charged even with --no-smtp
RATE_PER_MINUTE = 60  # the key's default limit, for the time estimate in the tip

# Module-level because ruff's B008 flags a call in the default of a list-typed parameter.
EMAILS_ARGUMENT = typer.Argument(None, metavar="[EMAIL]...", help="Addresses to verify.")


def _line(label: str, value: str) -> str:
    return f"  {label:<12}{value}"


def _format_verify(result: VerifyResult) -> str:
    flags = [name for name in FLAGS if getattr(result, name)]
    lines = [
        f"{result.email}  {result.result}  score={result.score}",
        _line("reason", result.reason),
        _line("provider", result.smtp_provider or "-"),
        _line("flags", ", ".join(flags) or "-"),
    ]
    if result.did_you_mean:
        lines.append(_line("suggestion", result.did_you_mean))
    return "\n".join(lines)


def _format_line(result: VerifyResult, width: int) -> str:
    """The compact line for a run of many: address, verdict, score, and reason in columns."""
    score = f"score={result.score}"
    return f"{result.email:<{width}}  {result.result:<7}  {score:<9}  {result.reason}"


def verify(
    ctx: typer.Context,
    emails: list[str] | None = EMAILS_ARGUMENT,
    file: str | None = typer.Option(
        None, "--file", help="File with one address per line, or - for stdin."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    timeout: int | None = typer.Option(
        None, "--timeout", min=5, max=60, help="Server-side SMTP timeout in seconds."
    ),
    no_smtp: bool = typer.Option(
        False,
        "--no-smtp",
        help=(
            "Ask the API to skip the SMTP check. "
            "Still costs 1 credit, and the API may check anyway."
        ),
    ),
) -> None:
    """Verify email addresses. (1 credit each)"""
    if not emails and file is None:
        fail("Give at least one address or --file.", code=2)
    addresses = read_addresses(emails or [], file)
    count = len(addresses)
    if count == 0:
        fail(f"No email addresses in {source_name(file)}." if file else "No email addresses.")
    cost = credits(count * VERIFY_COST)
    typer.echo(f"This will use {cost}.", err=True)
    if count >= CONFIRM_THRESHOLD and not yes:
        minutes = math.ceil(count / RATE_PER_MINUTE)
        typer.echo(
            f"Tip: {fmt_int(count)} addresses take about {minutes} minutes one at a time; "
            "batch submit is faster for a list this size.",
            err=True,
        )
    confirm_spend(count, cost, stdin=file == "-", yes=yes)

    smtp_check = False if no_smtp else None
    with handle_errors(), client() as mt:
        if count == 1:
            result = mt.verify(addresses[0], timeout=timeout, smtp_check=smtp_check)
            if get_state(ctx).json:
                print_json(result.raw)
            else:
                typer.echo(_format_verify(result))
            return
        failed = _verify_many(ctx, mt, addresses, timeout=timeout, smtp_check=smtp_check)
    if failed:
        raise typer.Exit(1)


def _verify_many(
    ctx: typer.Context,
    mt: MailTester,
    addresses: list[str],
    *,
    timeout: int | None,
    smtp_check: bool | None,
) -> bool:
    """Verify each address in order, printing a line (or JSON Line) as each answer arrives.

    A 422 belongs to one address: it's reported and the run goes on. Any other error would
    hit every remaining address too, so it stops the run with a count of what was verified.
    Returns whether any address failed.
    """
    as_json = get_state(ctx).json
    width = max(map(len, addresses))
    count = len(addresses)
    verified = sent = 0
    failed = False
    try:
        for email in addresses:
            try:
                result = mt.verify(email, timeout=timeout, smtp_check=smtp_check)
            except ValidationError as exc:
                typer.echo(f"Error: {email}: {error_message(exc)}", err=True)
                failed = True
            else:
                verified += 1
                if as_json:
                    typer.echo(json.dumps(result.raw, ensure_ascii=False))
                else:
                    typer.echo(_format_line(result, width))
            sent += 1
    except MailTesterError as exc:
        left = count - sent
        typer.echo(f"Error: {error_message(exc)}", err=True)
        typer.echo(
            f"Verified {verified} of {count} addresses; "
            f"{left} {'was' if left == 1 else 'were'} not sent.",
            err=True,
        )
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.echo(f"Interrupted. Verified {verified} of {count} addresses.", err=True)
        raise typer.Exit(130) from None
    return failed


def register(app: typer.Typer) -> None:
    app.command()(verify)
