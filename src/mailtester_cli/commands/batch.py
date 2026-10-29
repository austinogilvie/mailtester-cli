"""The `batch` group: submit, status, and results."""

import sys
from enum import StrEnum
from pathlib import Path

import typer

from mailtester_cli.client import MAX_BATCH_EMAILS, MailTester
from mailtester_cli.commands._shared import (
    client,
    fail,
    fmt_int,
    get_state,
    handle_errors,
    print_json,
)
from mailtester_cli.errors import BatchTimeoutError
from mailtester_cli.models import BatchJob

CONFIRM_THRESHOLD = 100
DEFAULT_WAIT_TIMEOUT = 1800

batch_app = typer.Typer(
    help="Submit, track, and fetch bulk verification jobs.", no_args_is_help=True
)


class ResultFilter(StrEnum):
    all = "all"
    valid = "valid"
    invalid = "invalid"
    risky = "risky"
    unknown = "unknown"


class ResultFormat(StrEnum):
    json = "json"
    csv = "csv"


FILTER_OPTION = typer.Option(ResultFilter.all, "--filter", help="Which rows to return.")
FORMAT_OPTION = typer.Option(ResultFormat.json, "--format", help="Output format.")


def _read_emails(file: str) -> list[str]:
    """Read one address per line from `file` (or stdin for `-`), skipping blank lines."""
    if file == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(file).read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            fail(f"File not found: {file}")
        except OSError as exc:
            fail(f"Could not read {file}: {exc.strerror or exc}")
    return [line for line in (raw.strip() for raw in text.splitlines()) if line]


def _status_line(job: BatchJob) -> str:
    progress = "-" if job.progress is None else f"{job.progress}%"
    counts = f"({fmt_int(job.processed_emails)}/{fmt_int(job.total_emails)})"
    return f"Batch {job.id}  {job.status}  {progress}  {counts}"


def _write_results(
    mt: MailTester, batch_id: int, filter_: ResultFilter, format_: ResultFormat
) -> None:
    if format_ is ResultFormat.csv:
        typer.echo(mt.batch_results_csv(batch_id, filter=filter_.value), nl=False)
    else:
        print_json([row.raw for row in mt.batch_results(batch_id, filter=filter_.value)])


@batch_app.command()
def submit(
    ctx: typer.Context,
    file: str = typer.Argument(..., help="File with one address per line, or - for stdin."),
    name: str | None = typer.Option(None, "--name", help="A name for the batch."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    wait: bool = typer.Option(False, "--wait", help="Wait for completion and print results."),
    timeout: int = typer.Option(
        DEFAULT_WAIT_TIMEOUT, "--timeout", min=0, help="Seconds to wait with --wait."
    ),
    filter_: ResultFilter = FILTER_OPTION,
    format_: ResultFormat = FORMAT_OPTION,
) -> None:
    """Submit addresses for bulk verification. (1 credit per address)"""
    emails = _read_emails(file)
    count = len(emails)
    if count == 0:
        fail(f"No email addresses in {'stdin' if file == '-' else file}.")
    if count > MAX_BATCH_EMAILS:
        fail(f"emails must hold 1 to {MAX_BATCH_EMAILS:,} addresses (got {count:,})")
    cost = f"{fmt_int(count)} credit{'' if count == 1 else 's'}"
    typer.echo(f"This will use up to {cost}.", err=True)
    if count >= CONFIRM_THRESHOLD and not yes:
        if file == "-":
            fail(f"Pass --yes to confirm spending {cost} when reading from stdin.")
        if not typer.confirm("Continue?", default=False, err=True):
            typer.echo("Aborted.", err=True)
            raise typer.Exit(1)

    with handle_errors(), client() as mt:
        job = mt.submit_batch(emails, name=name)
        submitted = f"Submitted batch {job.id} ({fmt_int(job.total_emails)} emails)"
        if not wait:
            if get_state(ctx).json:
                print_json(job.raw)
            else:
                typer.echo(submitted)
            return
        typer.echo(submitted, err=True)
        hint = f"mailtester batch status {job.id}"
        try:
            mt.wait_for_batch(
                job.id,
                timeout=timeout,
                on_poll=lambda polled: typer.echo(_status_line(polled), err=True),
            )
        except BatchTimeoutError as exc:
            fail(f"{exc}. Check later with: {hint}")
        except KeyboardInterrupt:
            typer.echo(
                f"Stopped waiting. Batch {job.id} is still running. Check it with: {hint}",
                err=True,
            )
            raise typer.Exit(130) from None
        _write_results(mt, job.id, filter_, format_)


@batch_app.command()
def status(
    ctx: typer.Context,
    batch_id: int = typer.Argument(..., metavar="ID", help="Batch id from `batch submit`."),
) -> None:
    """Show a batch's progress. (free)"""
    with handle_errors(), client() as mt:
        job = mt.batch_status(batch_id)
    if get_state(ctx).json:
        print_json(job.raw)
        return
    typer.echo(_status_line(job))
    if job.is_complete:
        typer.echo(
            f"  valid {fmt_int(job.valid_emails)}  invalid {fmt_int(job.invalid_emails)}"
            f"  unknown {fmt_int(job.unknown_emails)}"
        )


@batch_app.command()
def results(
    batch_id: int = typer.Argument(..., metavar="ID", help="Batch id from `batch submit`."),
    filter_: ResultFilter = FILTER_OPTION,
    format_: ResultFormat = FORMAT_OPTION,
) -> None:
    """Fetch a batch's results as JSON or CSV. (free)"""
    with handle_errors(), client() as mt:
        _write_results(mt, batch_id, filter_, format_)


def register(app: typer.Typer) -> None:
    """Add the `batch` group to `app`."""
    app.add_typer(batch_app, name="batch")
