"""`run` — execute the agent once. The only command that does any work; every
other one inspects or validates."""

from __future__ import annotations

import typer

from agent.managers import AgentRunner
from agent.managers.output_manager import OutputManager
from state.memory_store import InMemoryStateStore

from ..context import (
    INPUT_OPTION,
    QUIET_OPTION,
    TENANT_OPTION,
    VERBOSE_FORMAT_OPTION,
    VERBOSE_OPTION,
    fail,
    load_config,
    load_input,
    make_reporter,
)

def run(
    tenant: str = TENANT_OPTION,
    input_file: str = INPUT_OPTION,
    output: str = typer.Option(
        None, "--output", "-o",
        help="Write the result JSON to this file instead of the configured output sinks.",
    ),
    verbose: int = VERBOSE_OPTION,
    quiet: bool = QUIET_OPTION,
    verbose_format: str = VERBOSE_FORMAT_OPTION,
) -> None:
    """Run the agent once against a tenant config and a run input."""
    config = load_config(tenant)
    run_input = load_input(input_file)
    reporter = make_reporter(config, verbose, quiet, verbose_format)

    if output:
        # A one-off destination for this run, replacing whatever the tenant
        # configured — the CLI equivalent of a single json sink with a path.
        config.output_sinks = [
            {"name": "output", "provider": "json", "options": {"path": output}}
        ]

    # Sinks are built before the run, not after: a broken sink config should fail
    # now rather than once a full pipeline has spent real LLM calls.
    try:
        sinks = OutputManager(config, reporter=reporter)
    except Exception as exc:  # noqa: BLE001 - a misconfigured sink is a user-facing error
        raise fail(f"output sink configuration: {exc}") from exc

    store = InMemoryStateStore()  # in-memory only; a single, in-process run
    result = AgentRunner(config, reporter=reporter).run(run_input, state_store=store)
    sinks.emit(result)

    # AgentRunner never raises — a failed run comes back as phase="failed" with the
    # full result shape intact (that's the documented run() contract, and the
    # sinks above still receive it). But a CLI that exits 0 on failure is a CLI
    # nothing can be scripted around, so the exit code reflects the outcome.
    if result.get("phase") == "failed":
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.command("run")(run)
