"""Reading addresses and confirming spend for the commands that take many: verify, batch submit."""

import sys
from pathlib import Path

import typer

from mailtester_cli.commands._shared import confirm, fail, fmt_int

CONFIRM_THRESHOLD = 100  # this many addresses or more asks before spending


def read_addresses(args: list[str], file: str | None) -> list[str]:
    """Arguments first, then one address per line from `file` (`-` for stdin).

    Strips whitespace, skips blank lines, and drops repeats case-insensitively, keeping the
    first spelling and the input order. Prints `Skipped N duplicate(s).` on stderr when it
    dropped anything. Read errors become `Error:` lines (exit 1).
    """
    lines = [*args, *(_read_lines(file) if file is not None else [])]
    given = [line for line in (raw.strip() for raw in lines) if line]
    first: dict[str, str] = {}
    for address in given:
        first.setdefault(address.lower(), address)
    addresses = list(first.values())
    skipped = len(given) - len(addresses)
    if skipped:
        typer.echo(f"Skipped {skipped} duplicate{'' if skipped == 1 else 's'}.", err=True)
    return addresses


def source_name(file: str) -> str:
    """How messages name `file`: `stdin` for `-`, the path otherwise."""
    return "stdin" if file == "-" else file


def credits(count: int) -> str:
    """`1 credit`, `2 credits`, `1,000 credits`."""
    return f"{fmt_int(count)} credit{'' if count == 1 else 's'}"


def confirm_spend(
    count: int, cost: str, *, stdin: bool, yes: bool, note: str | None = None
) -> None:
    """At CONFIRM_THRESHOLD addresses or more, ask before spending `cost` unless `yes`.

    `note`, when given, goes to stderr just before the question. Exits 1 on no. Stdin can't
    both hold the addresses and answer the prompt, so reading from it needs `yes` at that size.
    """
    if count < CONFIRM_THRESHOLD or yes:
        return
    if note:
        typer.echo(note, err=True)
    if stdin:
        fail(f"Pass --yes to confirm spending {cost} when reading from stdin.")
    if not confirm("Continue?"):
        typer.echo("Aborted.", err=True)
        raise typer.Exit(1)


def _read_lines(file: str) -> list[str]:
    source = source_name(file)
    try:
        data = sys.stdin.buffer.read() if file == "-" else Path(file).read_bytes()
        return data.decode("utf-8-sig").splitlines()
    except FileNotFoundError:
        fail(f"File not found: {file}")
    except UnicodeDecodeError:
        fail(f"Could not read {source}: it isn't UTF-8 text.")
    except OSError as exc:
        fail(f"Could not read {source}: {exc.strerror or exc}")
