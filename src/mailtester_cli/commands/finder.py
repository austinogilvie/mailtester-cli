"""The finders: `find` for a person, `find-domain` and `find-company` for role mailboxes."""

import typer

from mailtester_cli.commands._shared import client, get_state, handle_errors, print_json
from mailtester_cli.models import RoleFinderResult

FIND_COST = 3  # every finder call, whether or not anything is found


def _note_cost() -> None:
    typer.echo(f"This will use {FIND_COST} credits.", err=True)


def find(
    ctx: typer.Context,
    first: str = typer.Argument(..., help="First name."),
    last: str = typer.Argument(..., help="Last name."),
    domain: str = typer.Argument(..., help="Domain, e.g. acme.com."),
) -> None:
    """Find a person's email from name + domain. (3 credits)"""
    _note_cost()
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


def _format_roles(result: RoleFinderResult) -> str:
    """One line per mailbox: address and status padded to the longest, then confidence=N."""
    width = max(len(c.email) for c in result.emails)
    status_width = max(len(c.status) for c in result.emails)
    return "\n".join(
        f"{c.email:<{width}}  {c.status:<{status_width}}  confidence={c.confidence}"
        for c in result.emails
    )


def _report_roles(
    ctx: typer.Context, result: RoleFinderResult, *, not_found: str, resolved: str | None = None
) -> None:
    """Print the rows (or the raw payload) and the stderr notes; exit 1 when there are no rows.

    Under --json only the payload is printed: the resolved domain, `total`/`limit`, and
    `is_catch_all` are all in it.
    """
    if get_state(ctx).json:
        print_json(result.raw)
    elif result.emails:
        if resolved is not None:
            typer.echo(resolved, err=True)
        typer.echo(_format_roles(result))
        if result.total > len(result.emails):
            shown = f"Showing {len(result.emails)} of {result.total} role mailboxes."
            typer.echo(shown, err=True)
        if all(c.is_catch_all for c in result.emails):
            note = f"Note: {result.domain} accepts all mail, so these results are low confidence."
            typer.echo(note, err=True)
    if not result.emails:
        typer.echo(not_found, err=True)
        raise typer.Exit(1)


def find_domain(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Domain, e.g. acme.com."),
) -> None:
    """Find role mailboxes at a domain. (3 credits)"""
    _note_cost()
    with handle_errors(), client() as mt:
        result = mt.find_domain(domain)
    _report_roles(ctx, result, not_found=f"No role mailboxes found at {domain}.")


def find_company(
    ctx: typer.Context,
    company: str = typer.Argument(..., help='Company name, e.g. "Acme Inc".'),
) -> None:
    """Find a company's domain and role mailboxes. (3 credits)"""
    _note_cost()
    with handle_errors(), client() as mt:
        result = mt.find_company(company)
    _report_roles(
        ctx,
        result,
        not_found=f"No role mailboxes found for {company} (tried {result.domain}).",
        resolved=f"Resolved {company} to {result.domain}.",
    )


def register(app: typer.Typer) -> None:
    app.command()(find)
    app.command()(find_domain)
    app.command()(find_company)
