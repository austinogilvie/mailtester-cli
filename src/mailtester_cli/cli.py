"""Entry point for the `mailtester` command."""

import typer
from typer.core import TyperGroup

from mailtester_cli import __version__
from mailtester_cli.commands import account, batch, finder, lists, verify
from mailtester_cli.commands._shared import State, set_state

COMMAND_ORDER = ["verify", "batch", "find", "find-domain", "find-company", "lists", "credits"]


class _OrderedGroup(TyperGroup):
    # Typer lists plain commands before groups like `batch`; use the README's order instead.
    def list_commands(self, ctx: typer.Context) -> list[str]:
        names = super().list_commands(ctx)
        rank = {name: i for i, name in enumerate(COMMAND_ORDER)}
        return sorted(names, key=lambda name: rank.get(name, len(rank)))


app = typer.Typer(
    cls=_OrderedGroup,
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
    set_state(ctx, State(json=json_output))


verify.register(app)
finder.register(app)
batch.register(app)
lists.register(app)
account.register(app)
