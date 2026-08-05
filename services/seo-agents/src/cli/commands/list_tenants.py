"""`list-tenants` — what's in the workspace.

The first thing to run when `--tenant` doesn't match anything, and the answer to
"what can I pass to --tenant?"
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from agent.config.workspace import TenantWorkspace, list_tenants as find_tenants, resolve_root

from ..context import USERDATA_OPTION


def list_tenants(userdata: str = USERDATA_OPTION) -> None:
    """List the tenants in the workspace."""
    root = resolve_root(userdata)
    names = find_tenants(root)

    console = Console()
    table = Table(
        title=f"[bold]tenants[/bold] in {root}",
        title_justify="left",
        caption="pass one of these to --tenant",
        caption_justify="left",
    )
    table.add_column("name")
    table.add_column("plugins", justify="right")
    table.add_column("templates", justify="right")
    table.add_column("input.json", justify="center")

    for name in names:
        described = TenantWorkspace(root=root, name=name).describe()
        table.add_row(
            name,
            str(described["plugins"]) or "",
            str(described["templates"]) or "",
            "[green]yes[/green]" if described["has_input"] else "",
        )
    if not names:
        table.add_row("[dim]none[/dim]", "", "", "")
    console.print(table)

    if not names:
        typer.secho(
            f"\nNo tenants found. A tenant is a folder containing tenant.json, "
            f"directly under {root}.",
            err=True,
        )
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.command("list-tenants")(list_tenants)
