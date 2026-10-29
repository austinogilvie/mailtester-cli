"""The `batch` group: submit, status, and results."""

import sys
from enum import StrEnum
from pathlib import Path

import typer

from mailtester_cli.client import MAX_BATCH_EMAILS, MailTester
from mailtester_cli.commands._shared import (
    client,
    error_message,
    fail,
    fmt_int,
    get_state,
    handle_errors,
    print_json,
)
from mailtester_cli.errors import BatchFailedError, BatchTimeoutError, MailTesterError
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
# submit takes these only with --wait, so they default to None to tell "not given" apart.
WAIT_FILTER_OPTION = typer.Option(
    None, "--filter", help="With --wait: which rows to return (default all)."
)
WAIT_FORMAT_OPTION = typer.Option(
    None, "--format", help="With --wait: output format (default json)."
)


def _read_emails(file: str) -> list[str]:
    """Read one address per line from `file` (or stdin for `-`), skipping blank lines."""
    source = "stdin" if file == "-" else file
    try:
        data = sys.stdin.buffer.read() if file == "-" else Path(file).read_bytes()
        text = data.decode("utf-8-sig")
    except FileNotFoundError:
        fail(f"File not found: {file}")
    except UnicodeDecodeError:
        fail(f"Could not read {source}: it isn't UTF-8 text.")
    except OSError as exc:
        fail(f"Could not read {source}: {exc.strerror or exc}")
    return [line for line in (raw.strip() for raw in text.splitlines()) if line]


def _confirm(question: str) -> bool:
    # typer.confirm prints the prompt's trailing space to stdout; keep stdout for results only.
    typer.echo(f"{question} [y/N]: ", err=True, nl=False)
    answer = sys.stdin.readline()
    if not answer:
        typer.echo(err=True)
    return answer.strip().lower() in {"y", "yes"}


def _with_hint(message: str, hint: str) -> str:
    return f"{message.rstrip('.')}. {hint}"


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
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        min=0,
        help=f"With --wait: seconds to wait (default {DEFAULT_WAIT_TIMEOUT}).",
    ),
    filter_: ResultFilter | None = WAIT_FILTER_OPTION,
    format_: ResultFormat | None = WAIT_FORMAT_OPTION,
) -> None:
    """Submit addresses for bulk verification. (1 credit per address)"""
    if not wait and (timeout, filter_, format_) != (None, None, None):
        fail("--timeout, --filter, and --format only apply with --wait.", code=2)
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
        if not _confirm("Continue?"):
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
                timeout=DEFAULT_WAIT_TIMEOUT if timeout is None else timeout,
                on_poll=lambda polled: typer.echo(_status_line(polled), err=True),
            )
        except BatchTimeoutError as exc:
            fail(f"{exc}. Check later with: {hint}")
        except BatchFailedError:
            raise
        except MailTesterError as exc:
            # The batch was paid for and may still be running: say how to pick it back up.
            running = f"Batch {job.id} may still be running. Check it with: {hint}"
            fail(_with_hint(error_message(exc), running))
        except KeyboardInterrupt:
            typer.echo(
                f"Stopped waiting. Batch {job.id} is still running. Check it with: {hint}",
                err=True,
            )
            raise typer.Exit(130) from None
        try:
            _write_results(mt, job.id, filter_ or ResultFilter.all, format_ or ResultFormat.json)
        except MailTesterError as exc:
            fetch = f"Batch {job.id} is complete. Fetch it with: mailtester batch results {job.id}"
            fail(_with_hint(error_message(exc), fetch))


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
