"""The `lists` group: the table, `lists show`, and (from 022) `lists delete`."""

from datetime import UTC, datetime

import typer

from mailtester_cli.commands._shared import (
    client,
    fail,
    fmt_int,
    get_state,
    handle_errors,
    print_json,
)
from mailtester_cli.models import ListJob, ListPage

ALL_PER_PAGE = 100  # --all without --per-page: the fewest requests the API allows

lists_app = typer.Typer(invoke_without_command=True, no_args_is_help=False)

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


def _page_hint(page: ListPage) -> str | None:
    """The stderr line after a table that isn't the last page, else None."""
    if page.current_page >= page.last_page:
        return None
    return (
        f"Page {page.current_page} of {page.last_page} ({fmt_int(page.total)} lists). "
        f"Use --page {page.current_page + 1} or --all for more."
    )


@lists_app.callback()
def lists(
    ctx: typer.Context,
    page: int | None = typer.Option(None, "--page", min=1, help="Page to show (default 1)."),
    per_page: int | None = typer.Option(
        None, "--per-page", min=1, max=100, help="Lists per page (1-100)."
    ),
    all_: bool = typer.Option(False, "--all", help="Fetch every page."),
) -> None:
    """List, inspect, and delete your bulk verification jobs."""
    if ctx.invoked_subcommand is not None:
        # click runs this callback before `lists show` and `lists delete` as well.
        if page is not None or per_page is not None or all_:
            fail("--page, --per-page, and --all apply to `mailtester lists` only.", code=2)
        return
    if all_ and page is not None:
        fail("--all and --page can't be combined.", code=2)
    fetched: ListPage | None = None
    with handle_errors(), client() as mt:
        if all_:
            jobs = list(mt.iter_lists(per_page=per_page or ALL_PER_PAGE))
        else:
            fetched = mt.lists_page(page=page, per_page=per_page)
            jobs = fetched.items
    if get_state(ctx).json:
        print_json([job.raw for job in jobs])
    elif jobs:
        typer.echo(_format_lists(jobs))
    elif fetched is not None and fetched.total > 0:
        typer.echo(f"No lists on page {fetched.current_page} (last page is {fetched.last_page}).")
    else:
        typer.echo("No lists yet.")
    if fetched is not None and (hint := _page_hint(fetched)) is not None:
        typer.echo(hint, err=True)


def _line(label: str, value: str) -> str:
    return f"  {label:<12}{value}"


def _timestamp(value: datetime | None) -> str:
    return "-" if value is None else value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _status_line(job: ListJob) -> str:
    name = job.name if job.name is not None else "-"
    counts = f"({fmt_int(job.processed_emails)}/{fmt_int(job.total_emails)})"
    return f"List {job.id}  {name}  {job.status}  {job.progress:g}%  {counts}"


def _format_show(job: ListJob) -> str:
    lines = [
        _status_line(job),
        _line("valid", fmt_int(job.results.valid)),
        _line("invalid", fmt_int(job.results.invalid)),
        _line("risky", fmt_int(job.results.risky)),
        _line("unknown", fmt_int(job.results.unknown)),
        _line("credits", fmt_int(job.credits_used)),
        _line("created", _timestamp(job.created_at)),
        _line("started", _timestamp(job.started_at)),
        _line("completed", _timestamp(job.completed_at)),
    ]
    return "\n".join(lines)


@lists_app.command()
def show(
    ctx: typer.Context,
    list_id: int = typer.Argument(..., metavar="ID", help="List id, as shown by `lists`."),
) -> None:
    """Show one list's status and counts. (free)"""
    with handle_errors(), client() as mt:
        job = mt.get_list(list_id)
    if get_state(ctx).json:
        print_json(job.raw)
    else:
        typer.echo(_format_show(job))


def register(app: typer.Typer) -> None:
    """Add the `lists` group to `app`."""
    app.add_typer(lists_app, name="lists")
