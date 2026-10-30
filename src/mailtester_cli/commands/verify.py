"""`verify` command."""

import typer

from mailtester_cli.commands._shared import client, get_state, handle_errors, print_json
from mailtester_cli.models import VerifyResult

FLAGS = ("disposable", "role", "free", "accept_all", "spamtrap")
VERIFY_COST = 1  # charged even with --no-smtp


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
    no_smtp: bool = typer.Option(
        False,
        "--no-smtp",
        help=(
            "Ask the API to skip the SMTP check. "
            "Still costs 1 credit, and the API may check anyway."
        ),
    ),
) -> None:
    """Verify a single email address. (1 credit)"""
    typer.echo(f"This will use {VERIFY_COST} credit.", err=True)
    with handle_errors(), client() as mt:
        result = mt.verify(email, timeout=timeout, smtp_check=False if no_smtp else None)
    if get_state(ctx).json:
        print_json(result.raw)
    else:
        typer.echo(_format_verify(result))


def register(app: typer.Typer) -> None:
    app.command()(verify)
