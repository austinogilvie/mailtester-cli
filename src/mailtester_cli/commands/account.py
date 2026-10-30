"""`credits` command."""

from datetime import UTC

import typer

from mailtester_cli.client import RateLimit
from mailtester_cli.commands._shared import client, fmt_int, get_state, handle_errors, print_json
from mailtester_cli.models import Credits


def _line(label: str, value: str) -> str:
    return f"{label:<11}{value}"


def _rate_limit_line(rate_limit: RateLimit) -> str | None:
    """`35 of 60 left, resets ... UTC`, or None without the headers.

    The headers don't say how long the window is, so the line doesn't either.
    """
    if rate_limit.limit is None and rate_limit.remaining is None:
        return None
    value = f"{fmt_int(rate_limit.remaining)} of {fmt_int(rate_limit.limit)} left"
    if rate_limit.reset is not None:
        value += f", resets {rate_limit.reset.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"
    return _line("Rate limit", value)


def _format_credits(credits: Credits, rate_limit: RateLimit) -> str:
    lines = [
        _line("Balance", f"{fmt_int(credits.balance)} credits"),
        _line("Account", credits.account_type),
    ]
    if credits.monthly_credit_amount > 0:
        monthly = fmt_int(credits.monthly_credit_amount)
        if credits.next_renewal is not None:
            monthly += f" (renews {credits.next_renewal.astimezone(UTC).date().isoformat()})"
        lines.append(_line("Monthly", monthly))
    rate_line = _rate_limit_line(rate_limit)
    if rate_line is not None:
        lines.append(rate_line)
    return "\n".join(lines)


def credits(ctx: typer.Context) -> None:
    """Show your credit balance."""
    with handle_errors(), client() as mt:
        result = mt.credits()
    if get_state(ctx).json:
        print_json(result.raw)  # the API's data only; the rate limit comes from the headers
    else:
        typer.echo(_format_credits(result, mt.rate_limit))


def register(app: typer.Typer) -> None:
    app.command()(credits)
