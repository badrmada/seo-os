"""`check-data` — everything that can be validated without spending an LLM call.

Deliberately reuses the validators the run path already uses (AgentConfigLoader's
template validation, InputValidator) rather than reimplementing their rules, so
this can't pass on something a real run would reject. It builds every configured
provider too, since "the config parses" and "the tool can actually be
constructed" are different questions — a missing service-account file or an
unimportable custom class only shows up in the second.

What it deliberately does *not* do is run the pipeline: no LLM call, no draft.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from agent.managers import ToolsManager
from agent.managers.output_manager import OutputManager
from agent.validators.input_validator import InputValidator

from ..context import (
    INPUT_OPTION,
    TENANT_OPTION,
    USERDATA_OPTION,
    load_input,
    open_tenant,
)


def check_data(
    tenant: str = TENANT_OPTION,
    userdata: str = USERDATA_OPTION,
    input_file: str = INPUT_OPTION,
    skip_input: bool = typer.Option(
        False, "--skip-input", help="Check the tenant config and tools only.",
    ),
) -> None:
    """Validate the config, the input, and that every configured tool builds."""
    workspace, config = open_tenant(tenant, userdata)
    checks: list[tuple[str, bool, str]] = [
        ("tenant config", True, "loaded; prompt and data templates render"),
    ]

    if not skip_input:
        checks.append(_check_input(config, input_file, workspace))

    manager = ToolsManager(config)
    checks.append(_check("llm", lambda: manager.build_llm()))
    checks.append(_check("search", manager.build_search))
    checks.append(_check("search performance", manager.build_search_performance))
    checks.append(_check("traffic", manager.build_traffic))
    checks.append(_check("analytics", manager.build_analytics))
    if config.signal_sources:
        # The three rows above already cover a signal_sources entry using a
        # reserved name (build_search_performance/build_traffic/build_analytics
        # read it); this row
        # is the rest, and the one place a duplicate or unnamed entry surfaces.
        checks.append(_check(
            "signal sources", lambda: f"{len(manager.build_signal_sources())} built",
        ))
    if config.discovery_sources:
        checks.append(_check(
            "discovery sources",
            lambda: f"{len(manager.build_discovery_sources(_NoLLM()))} built",
        ))
    checks.append(_check("output sinks", lambda: f"{len(OutputManager(config).sinks)} built"))

    console = Console()
    table = Table(title="[bold]check-data[/bold]", title_justify="left")
    table.add_column("check")
    table.add_column("status", justify="center")
    table.add_column("detail")
    for name, ok, detail in checks:
        table.add_row(name, "[green]ok[/green]" if ok else "[red]FAIL[/red]", detail)
    console.print(table)

    failures = [name for name, ok, _ in checks if not ok]
    if failures:
        typer.secho(f"\n{len(failures)} check(s) failed: {', '.join(failures)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("\nAll checks passed.", fg=typer.colors.GREEN)


def _check(name: str, build) -> tuple[str, bool, str]:
    """Run one build, turning success into a short description and any failure into
    a reportable row — one failing check must not hide the ones after it."""
    try:
        result = build()
    except Exception as exc:  # noqa: BLE001 - reporting failures is this command's whole job
        return (name, False, f"{type(exc).__name__}: {exc}")
    return (name, True, result if isinstance(result, str) else type(result).__name__)


def _check_input(config, input_file: str, workspace) -> tuple[str, bool, str]:
    run_input = load_input(input_file, workspace)
    try:
        InputValidator().validate(run_input, config)
    except Exception as exc:  # noqa: BLE001 - a bad input is exactly what this reports
        return ("input", False, str(exc))
    channel = run_input.get("channel") or (
        "decided by discovery" if config.discovery_sources else config.default_channel
    )
    return ("input", True, f"valid; channel: {channel}")


class _NoLLM:
    """Stands in for the LLM client when building discovery sources: an "llm"
    source only stores the client at construction, so building the *sources* can
    be verified without also requiring a working LLM provider. Any real use raises
    rather than silently doing nothing."""

    def generate(self, *args, **kwargs):
        raise RuntimeError("check-data never calls the LLM")


def register(app: typer.Typer) -> None:
    app.command("check-data")(check_data)
