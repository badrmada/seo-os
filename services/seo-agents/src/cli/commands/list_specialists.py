"""`list-specialists` — what this specific tenant has plugged in: the discovery
sources that find work, the data providers that inform it, and the sinks the
result goes to. `list-tools` shows what's *available*; this shows what's *wired*."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from agent.managers.output_manager import OutputManager

from ..context import TENANT_OPTION, load_config


def list_specialists(tenant: str = TENANT_OPTION) -> None:
    """List the discovery sources, data providers, and output sinks in use."""
    config = load_config(tenant)
    console = Console()

    sources = Table(title="[bold]discovery sources[/bold]", title_justify="left")
    sources.add_column("name")
    sources.add_column("provider")
    sources.add_column("details")
    for entry in config.discovery_sources:
        sources.add_row(
            entry.get("name", ""),
            entry.get("provider", "mock"),
            _describe_source(entry),
        )
    if not config.discovery_sources:
        # Worth saying explicitly rather than showing an empty table: it also
        # changes the pipeline's shape (no discover/choose_channel stages at all).
        sources.add_row("[dim]none[/dim]", "", "[dim]the agent works from input.channel/seed_keyword only[/dim]")
    console.print(sources)
    console.print()

    data = Table(title="[bold]data providers[/bold]", title_justify="left")
    data.add_column("kind")
    data.add_column("provider")
    for kind, provider in (
        ("llm", f"{config.llm_provider} ({config.llm_model})"),
        ("gsc", config.gsc_provider),
        ("traffic", config.traffic_provider),
        ("analytics", config.analytics_provider),
    ):
        data.add_row(kind, provider)
    console.print(data)
    console.print()

    sinks = Table(title="[bold]output sinks[/bold]", title_justify="left")
    sinks.add_column("name")
    sinks.add_column("type")
    sinks.add_column("destination")
    try:
        described = OutputManager(config).describe()
    except Exception as exc:  # noqa: BLE001 - listing must still work with a broken sink
        sinks.add_row("[red]invalid[/red]", "", f"[red]{exc}[/red]")
    else:
        for sink in described:
            sinks.add_row(sink["name"], sink["type"], sink["destination"])
    console.print(sinks)


def _describe_source(entry: dict) -> str:
    provider = entry.get("provider", "mock")
    if provider == "custom":
        return entry.get("class", "")
    if provider == "llm":
        grounded = "grounded" if entry.get("grounded", True) else "ungrounded"
        template = "custom prompt" if entry.get("prompt_template") else "default prompt"
        return f"{grounded}, {template}, max {entry.get('max_opportunities', 5)}"
    if provider == "mock" and entry.get("fail"):
        return "configured to fail (for testing degradation)"
    return ""


def register(app: typer.Typer) -> None:
    app.command("list-specialists")(list_specialists)
