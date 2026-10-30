"""The `batch` group: submit, upload, status, wait, and results."""

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import typer

from mailtester_cli.client import MAX_BATCH_EMAILS, MAX_UPLOAD_BYTES, MailTester
from mailtester_cli.commands._input import (
    CONFIRM_THRESHOLD,
    confirm_spend,
    credits,
    read_addresses,
    source_name,
)
from mailtester_cli.commands._shared import (
    client,
    confirm,
    fail,
    fmt_int,
    get_state,
    handle_errors,
    print_json,
)
from mailtester_cli.errors import (
    BatchFailedError,
    BatchNotStartedError,
    BatchTimeoutError,
    MailTesterError,
    NotFoundError,
)
from mailtester_cli.models import BatchJob

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


def _count_text_lines(data: bytes) -> int | None:
    """Count non-blank lines when `data` is UTF-8 text; None for binary files such as XLSX."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return sum(1 for line in text.splitlines() if line.strip())


def _check_callback_url(value: str | None) -> None:
    """Fail with exit 2 unless `value` is None or an http(s) URL with a host."""
    if value is None:
        return
    try:
        parts = urlsplit(value)
    except ValueError:  # for example an unbalanced IPv6 bracket
        fail("--callback-url must be an http(s) URL.", code=2)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        fail("--callback-url must be an http(s) URL.", code=2)


def _status_line(job: BatchJob) -> str:
    progress = "-" if job.progress is None else f"{job.progress:g}%"
    counts = f"({fmt_int(job.processed_emails)}/{fmt_int(job.total_emails)})"
    return f"Batch {job.id}  {job.status}  {progress}  {counts}"


def _write_results(
    mt: MailTester, batch_id: int, filter_: ResultFilter, format_: ResultFormat
) -> None:
    if format_ is ResultFormat.csv:
        typer.echo(mt.batch_results_csv(batch_id, filter=filter_.value), nl=False)
    else:
        print_json([row.raw for row in mt.batch_results(batch_id, filter=filter_.value)])


def _wait_and_write(
    mt: MailTester,
    batch_id: int,
    *,
    timeout: int,
    filter_: ResultFilter,
    format_: ResultFormat,
    resume_hint: str,
) -> None:
    """Poll `batch_id` with status lines on stderr, then write its results to stdout.

    `resume_hint` is the sentence appended after an API error while polling, for example
    `Check it with: mailtester batch status 1457`.
    """
    wait_cmd = f"mailtester batch wait {batch_id}"
    try:
        mt.wait_for_batch(
            batch_id,
            timeout=timeout,
            on_poll=lambda polled: typer.echo(_status_line(polled), err=True),
        )
    except BatchNotStartedError as exc:
        # Never "run this again": for submit/upload --wait that would pay for a second batch.
        start = f"Start it in the MailTester app, then resume with: {wait_cmd}"
        fail(error=exc, hint=start)
    except BatchTimeoutError as exc:
        fail(error=exc, hint=f"Resume with: {wait_cmd}")
    except (BatchFailedError, NotFoundError):
        raise
    except MailTesterError as exc:
        running = f"Batch {batch_id} may still be running. {resume_hint}"
        fail(error=exc, hint=running)
    except KeyboardInterrupt:
        typer.echo(
            f"Stopped waiting. Batch {batch_id} is still running. Resume with: {wait_cmd}",
            err=True,
        )
        raise typer.Exit(130) from None
    try:
        _write_results(mt, batch_id, filter_, format_)
    except MailTesterError as exc:
        fetch = f"Batch {batch_id} is complete. Fetch it with: mailtester batch results {batch_id}"
        fail(error=exc, hint=fetch)


@batch_app.command()
def submit(
    ctx: typer.Context,
    file: str = typer.Argument(..., help="File with one address per line, or - for stdin."),
    name: str | None = typer.Option(None, "--name", help="A name for the batch."),
    callback_url: str | None = typer.Option(
        None, "--callback-url", help="URL the API calls when the batch completes."
    ),
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
    _check_callback_url(callback_url)
    if not wait and (timeout, filter_, format_) != (None, None, None):
        fail("--timeout, --filter, and --format only apply with --wait.", code=2)
    emails = read_addresses([], file)
    count = len(emails)
    if count == 0:
        fail(f"No email addresses in {source_name(file)}.")
    if count > MAX_BATCH_EMAILS:
        fail(f"emails must hold 1 to {MAX_BATCH_EMAILS:,} addresses (got {count:,})")
    cost = credits(count)
    typer.echo(f"This will use up to {cost}.", err=True)
    confirm_spend(count, cost, stdin=file == "-", yes=yes)

    with handle_errors(), client() as mt:
        job = mt.submit_batch(emails, name=name, callback_url=callback_url)
        submitted = f"Submitted batch {job.id} ({fmt_int(job.total_emails)} emails)"
        if not wait:
            if get_state(ctx).json:
                print_json(job.raw)
            else:
                typer.echo(submitted)
            return
        typer.echo(submitted, err=True)
        _wait_and_write(
            mt,
            job.id,
            timeout=DEFAULT_WAIT_TIMEOUT if timeout is None else timeout,
            filter_=filter_ or ResultFilter.all,
            format_=format_ or ResultFormat.json,
            resume_hint=f"Check it with: mailtester batch status {job.id}",
        )


@batch_app.command()
def upload(
    ctx: typer.Context,
    file: str = typer.Argument(..., help="CSV, TXT, or XLSX file, sent to the API as is."),
    name: str | None = typer.Option(None, "--name", help="A name for the batch."),
    callback_url: str | None = typer.Option(
        None, "--callback-url", help="URL the API calls when the batch completes."
    ),
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
    """Upload a CSV, TXT, or XLSX file. (1 credit per address)"""
    _check_callback_url(callback_url)
    if not wait and (timeout, filter_, format_) != (None, None, None):
        fail("--timeout, --filter, and --format only apply with --wait.", code=2)
    if file == "-":
        fail(
            "batch upload needs a file path, not stdin. "
            "Pipe addresses to: mailtester batch submit -"
        )
    try:
        size = Path(file).stat().st_size
    except FileNotFoundError:
        fail(f"File not found: {file}")
    except OSError as exc:
        fail(f"Could not read {file}: {exc.strerror or exc}")
    if size == 0:
        fail(f"{file} is empty.")
    if size > MAX_UPLOAD_BYTES:
        fail(f"{file} is {size / 1_000_000:.1f} MB; the upload limit is 10 MB.")
    try:
        data = Path(file).read_bytes()
    except OSError as exc:
        fail(f"Could not read {file}: {exc.strerror or exc}")

    count = _count_text_lines(data)
    if count is None:
        typer.echo(
            f"The address count in {file} isn't known until MailTester reads it. "
            "This will use 1 credit per address.",
            err=True,
        )
    else:
        typer.echo(f"This will use up to {credits(count)}.", err=True)
    needs_confirm = (count is None or count >= CONFIRM_THRESHOLD) and not yes
    if needs_confirm and not confirm("Continue?"):
        typer.echo("Aborted.", err=True)
        raise typer.Exit(1)

    with handle_errors(), client() as mt:
        # The client checks the file again; it can vanish or change size after the checks above.
        try:
            job = mt.upload_batch(file, name=name, callback_url=callback_url)
        except ValueError as exc:
            fail(str(exc))
        except OSError as exc:
            fail(f"Could not read {file}: {exc.strerror or exc}")
        submitted = f"Submitted batch {job.id} ({fmt_int(job.total_emails)} emails)"
        if not wait:
            if get_state(ctx).json:
                print_json(job.raw)
            else:
                typer.echo(submitted)
            return
        typer.echo(submitted, err=True)
        _wait_and_write(
            mt,
            job.id,
            timeout=DEFAULT_WAIT_TIMEOUT if timeout is None else timeout,
            filter_=filter_ or ResultFilter.all,
            format_=format_ or ResultFormat.json,
            resume_hint=f"Check it with: mailtester batch status {job.id}",
        )


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
def wait(
    batch_id: int = typer.Argument(..., metavar="ID", help="Batch id from `batch submit`."),
    timeout: int = typer.Option(
        DEFAULT_WAIT_TIMEOUT, "--timeout", min=0, help="Seconds to wait before giving up."
    ),
    filter_: ResultFilter = FILTER_OPTION,
    format_: ResultFormat = FORMAT_OPTION,
) -> None:
    """Wait for a batch to complete and print its results."""
    with handle_errors(), client() as mt:
        _wait_and_write(
            mt,
            batch_id,
            timeout=timeout,
            filter_=filter_,
            format_=format_,
            resume_hint=f"Resume with: mailtester batch wait {batch_id}",
        )


@batch_app.command()
def results(
    batch_id: int = typer.Argument(..., metavar="ID", help="Batch id from `batch submit`."),
    filter_: ResultFilter = FILTER_OPTION,
    format_: ResultFormat = FORMAT_OPTION,
) -> None:
    """Fetch a batch's results as JSON or CSV. (free)"""
    with handle_errors(), client() as mt:
        try:
            _write_results(mt, batch_id, filter_, format_)
        except MailTesterError as exc:
            # The API answers 400 while the batch is still pending or processing.
            if exc.status_code != 400:
                raise
            hint = f"Check it with: mailtester batch status {batch_id}"
            fail(error=exc, hint=hint)


def register(app: typer.Typer) -> None:
    """Add the `batch` group to `app`."""
    app.add_typer(batch_app, name="batch")
