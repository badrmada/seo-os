"""`list-tools` — every pluggable interface and the providers it accepts, with
the tenant's current selection marked."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from agent.managers.providers import CATALOG

from ..context import OPTIONAL_TENANT_OPTION, USERDATA_OPTION, fail, load_config


def list_tools(
    tenant: str = OPTIONAL_TENANT_OPTION,
    userdata: str = USERDATA_OPTION,
    all_kinds: bool = typer.Option(
        False, "--all", "-a",
        help="List every provider kind, without reading a tenant config.",
    ),
) -> None:
    """Show the available tool providers, and which ones this tenant uses."""
    # This is the one command that's useful with no tenant at all — you run it
    # while deciding what to put in one — so --tenant is optional here, unlike
    # everywhere else.
    if not all_kinds and not tenant:
        raise fail("give --tenant NAME, or --all to list every provider kind")
    config = None if all_kinds else load_config(tenant, userdata)
    console = Console()

    for kind in CATALOG:
        selected = set(kind.selected(config)) if config else set()
        table = Table(
            title=f"[bold]{kind.kind}[/bold]  —  {kind.interface}",
            title_justify="left",
            caption=f"selected by {kind.config_field}"
                    + (" (several may be configured)" if kind.is_list else ""),
            caption_justify="left",
        )
        table.add_column("provider")
        table.add_column("in use", justify="center")
        table.add_column("what it does")
        for name, description in kind.providers.items():
            in_use = "[green]yes[/green]" if name in selected else ""
            table.add_row(name, in_use, description)
        console.print(table)
        console.print()


def register(app: typer.Typer) -> None:
    app.command("list-tools")(list_tools)
