"""Entry point for the `mailtester` command."""

import typer

from mailtester_cli import __version__
from mailtester_cli.commands import account, batch, verify
from mailtester_cli.commands._shared import State

app = typer.Typer(
    help="Verify and find email addresses with the MailTester API.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mailtester {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Print raw JSON instead of formatted output."
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Verify and find email addresses with the MailTester API."""
    ctx.obj = State(json=json_output)


# Registration order is the order `--help` lists the commands in.
verify.register(app)
batch.register(app)
account.register(app)
