"""`preview-prompt` — the exact prompt a draft would send, without sending it.

Runs the real AnalyzeStage (so the context is real search-performance/analytics/traffic data if
those are configured, not fabricated) but never calls the LLM — see
AgentRunner.preview_prompt.
"""

from __future__ import annotations

import typer

from agent.managers import AgentRunner

from ..context import (
    INPUT_OPTION,
    TENANT_OPTION,
    USERDATA_OPTION,
    load_input,
    open_tenant,
)


def preview_prompt(
    tenant: str = TENANT_OPTION,
    userdata: str = USERDATA_OPTION,
    input_file: str = INPUT_OPTION,
) -> None:
    """Preview the rendered prompt for a config and input, without drafting."""
    workspace, config = open_tenant(tenant, userdata)
    run_input = load_input(input_file, workspace)

    result = AgentRunner(config).preview_prompt(run_input)
    typer.echo(f"--- channel: {result['channel']} ---\n")
    typer.echo(result["prompt"])


def register(app: typer.Typer) -> None:
    app.command("preview-prompt")(preview_prompt)
