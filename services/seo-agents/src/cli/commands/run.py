"""`run` — execute the agent once. The only command that does any work; every
other one inspects or validates.

This command is a **channel adapter**, not the run logic: it turns flags and
files into a `RunRequest`, hands it to `AgentService`, and turns the `RunResult`
back into terminal output and an exit code. Everything between those two points —
tools, reporter, pipeline, sinks, state — belongs to the service, so an HTTP
handler or a queue worker gets the identical sequence without copying it. See
agent/service.py.
"""

from __future__ import annotations

import typer

from agent.service import AgentService, RunRequest, RunRequestError

from ..context import (
    AGENT_OPTION,
    INPUT_OPTION,
    QUIET_OPTION,
    TENANT_OPTION,
    USERDATA_OPTION,
    VERBOSE_FORMAT_OPTION,
    VERBOSE_OPTION,
    fail,
    load_input,
    open_tenant,
)


def run(
    tenant: str = TENANT_OPTION,
    userdata: str = USERDATA_OPTION,
    input_file: str = INPUT_OPTION,
    agent: str = AGENT_OPTION,
    output: str = typer.Option(
        None, "--output", "-o",
        help="Write the result JSON to this file instead of the configured output sinks.",
    ),
    verbose: int = VERBOSE_OPTION,
    quiet: bool = QUIET_OPTION,
    verbose_format: str = VERBOSE_FORMAT_OPTION,
) -> None:
    """Run the agent once against a tenant config and a run input."""
    # The workspace is opened here rather than by the service because this command
    # needs it anyway — `--input` resolves inside the tenant's folder — and because
    # a CLI owes the user one precise error per failure, which is what
    # open_tenant/load_input already produce.
    workspace, config = open_tenant(tenant, userdata)
    run_input = load_input(input_file, workspace)

    request = RunRequest(
        config=config,
        input=run_input,
        agent_type=agent or "",
        verbose=verbose,
        verbose_format=verbose_format,
        quiet=quiet,
        # A one-off destination for this run, replacing whatever the tenant
        # configured — the CLI equivalent of a single json sink with a path.
        output_sinks=(
            [{"name": "output", "provider": "json", "options": {"path": output}}]
            if output else None
        ),
    )

    try:
        result = AgentService().execute(request)
    except RunRequestError as exc:
        # The request itself was unrunnable (a broken sink config, say) — nothing
        # ran, so there is no result to print, just a clear error.
        raise fail(str(exc)) from exc

    # A snapshot that didn't land never fails the run (agent/service.py), so with
    # verbose off it would otherwise be invisible — and a store nobody notices is
    # broken is a store nobody fixes. Same warning OutputManager prints for a
    # failed sink, printed here because this adapter is the one that owns this
    # terminal, and suppressed when verbose is on for the same reason: the event
    # stream already has it, and one failure reported twice reads as two.
    if not (0 if quiet else (verbose or getattr(config, "verbose", 0))):
        for message in result.state_errors:
            typer.secho(f"warning: state store: {message}", fg=typer.colors.YELLOW, err=True)

    # The service never raises for a failed run — it comes back as
    # phase="failed" with the full result shape intact (that's the documented
    # contract, and the sinks still received it). But a CLI that exits 0 on
    # failure is a CLI nothing can be scripted around, so the exit code reflects
    # the outcome.
    if not result.ok:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.command("run")(run)
