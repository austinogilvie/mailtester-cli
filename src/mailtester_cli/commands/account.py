"""`lists` and `credits` commands."""

from datetime import UTC

import typer

from mailtester_cli.commands._shared import client, fmt_int, get_state, handle_errors, print_json
from mailtester_cli.models import Credits, ListJob

# (header, right-aligned)
COLUMNS = (
    ("ID", False),
    ("NAME", False),
    ("STATUS", False),
    ("EMAILS", True),
    ("VALID", True),
    ("INVALID", True),
    ("RISKY", True),
    ("CREATED", False),
)


def _row(job: ListJob) -> list[str]:
    return [
        str(job.id),
        job.name if job.name is not None else "-",
        job.status,
        fmt_int(job.total_emails),
        fmt_int(job.results.valid),
        fmt_int(job.results.invalid),
        fmt_int(job.results.risky),
        job.created_at.astimezone(UTC).date().isoformat(),
    ]


def _format_lists(jobs: list[ListJob]) -> str:
    rows = [[header for header, _ in COLUMNS], *(_row(job) for job in jobs)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(COLUMNS))]
    lines = []
    for row in rows:
        cells = [
            cell.rjust(width) if right else cell.ljust(width)
            for cell, width, (_, right) in zip(row, widths, COLUMNS, strict=True)
        ]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


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


def lists(
    ctx: typer.Context,
    per_page: int | None = typer.Option(
        None, "--per-page", min=1, max=100, help="Lists per page (1-100)."
    ),
) -> None:
    """List your bulk verification jobs."""
    with handle_errors(), client() as mt:
        jobs = mt.lists(per_page=per_page)
    if get_state(ctx).json:
        print_json([job.raw for job in jobs])
    elif jobs:
        typer.echo(_format_lists(jobs))
    else:
        typer.echo("No lists yet.")


def credits(ctx: typer.Context) -> None:
    """Show your credit balance."""
    with handle_errors(), client() as mt:
        result = mt.credits()
    if get_state(ctx).json:
        print_json(result.raw)
    else:
        typer.echo(_format_credits(result))


def register(app: typer.Typer) -> None:
    app.command()(lists)
    app.command()(credits)
