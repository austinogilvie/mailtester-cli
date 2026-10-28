"""Entry point for the `mailtester` command."""

import typer

from mailtester_cli import __version__

app = typer.Typer(
    help="Verify and find email addresses with the MailTester API.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mailtester {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Verify and find email addresses with the MailTester API."""
