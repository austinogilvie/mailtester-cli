"""`credits` command."""

from datetime import UTC

import typer

from mailtester_cli.commands._shared import client, fmt_int, get_state, handle_errors, print_json
from mailtester_cli.models import Credits


def _line(label: str, value: str) -> str:
    return f"{label:<11}{value}"


def _format_credits(credits: Credits) -> str:
    lines = [
        _line("Balance", f"{fmt_int(credits.balance)} credits"),
        _line("Account", credits.account_type),
    ]
    if credits.monthly_credit_amount > 0:
        monthly = fmt_int(credits.monthly_credit_amount)
        if credits.next_renewal is not None:
            monthly += f" (renews {credits.next_renewal.astimezone(UTC).date().isoformat()})"
        lines.append(_line("Monthly", monthly))
    return "\n".join(lines)


def credits(ctx: typer.Context) -> None:
    """Show your credit balance."""
    with handle_errors(), client() as mt:
        result = mt.credits()
    if get_state(ctx).json:
        print_json(result.raw)
    else:
        typer.echo(_format_credits(result))


def register(app: typer.Typer) -> None:
    app.command()(credits)
