"""`verify` and `find` commands."""

import typer

from mailtester_cli.commands._shared import client, get_state, handle_errors, print_json
from mailtester_cli.models import VerifyResult

FLAGS = ("disposable", "role", "free", "accept_all", "spamtrap")


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


def verify(
    ctx: typer.Context,
    email: str = typer.Argument(..., help="Address to verify."),
    timeout: int | None = typer.Option(
        None, "--timeout", min=5, max=60, help="Server-side SMTP timeout in seconds."
    ),
    no_smtp: bool = typer.Option(False, "--no-smtp", help="Skip the SMTP mailbox check."),
) -> None:
    """Verify a single email address. (1 credit)"""
    with handle_errors(), client() as mt:
        result = mt.verify(email, timeout=timeout, smtp_check=False if no_smtp else None)
    if get_state(ctx).json:
        print_json(result.raw)
    else:
        typer.echo(_format_verify(result))


def find(
    ctx: typer.Context,
    first: str = typer.Argument(..., help="First name."),
    last: str = typer.Argument(..., help="Last name."),
    domain: str = typer.Argument(..., help="Domain, e.g. acme.com."),
) -> None:
    """Find a person's email from name + domain. (3 credits)"""
    with handle_errors(), client() as mt:
        result = mt.find_email(first, last, domain)
    if get_state(ctx).json:
        print_json(result.raw)
    elif result.email:
        line = f"{result.email}  {result.status}  confidence={result.confidence}"
        if result.pattern:
            line += f"  pattern={result.pattern}"
        typer.echo(line)
    if not result.email:
        typer.echo(f"No address found for {first} {last} at {domain}.", err=True)
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.command()(verify)
    app.command()(find)
