"""`show-graph` — the pipeline this tenant's config actually produces.

Renders from the PipelineSpec, not from a built Tools bundle: which stages exist
is purely a function of config, so answering it must not require a live API key.
The mermaid format asks LangGraph itself to draw the compiled graph, which needs
*a* Tools object to construct the stage instances — placeholder mocks are used,
and nothing is ever invoked, so no client is contacted either way.
"""

from __future__ import annotations

import typer

from agent.graph.pipeline import build_graph, default_spec
from agent.graph.tools import Tools
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.gsc_mock import MockGoogleSearchConsoleClient
from tools.mocks.opportunity_mock import MockOpportunitySource
from tools.mocks.traffic_mock import MockTrafficClient

from ..context import TENANT_OPTION, USERDATA_OPTION, fail, load_config


def show_graph(
    tenant: str = TENANT_OPTION,
    userdata: str = USERDATA_OPTION,
    fmt: str = typer.Option(
        "text", "--format", "-f",
        help="text (default, annotated) or mermaid (paste into docs).",
    ),
) -> None:
    """Print the effective pipeline graph for this config."""
    config = load_config(tenant, userdata)

    if fmt == "text":
        for line in _render_text(config):
            typer.echo(line)
        return
    if fmt == "mermaid":
        typer.echo(build_graph(_placeholder_tools(config), config).get_graph().draw_mermaid())
        return
    raise fail(f'unknown --format {fmt!r}; must be "text" or "mermaid"')


def _render_text(config) -> list[str]:
    spec = default_spec(config)
    source_names = [entry.get("name", "?") for entry in config.discovery_sources]

    lines = ["", f"Pipeline — {len(spec.stages)} stages", "", "  START"]
    joins: list[str] = []
    for stage in spec.stages:
        if stage.mode == "parallel_by_source":
            names = ", ".join(source_names)
            fanout = f"discover_source × {len(source_names)}"
            lines.append(f"   → {fanout:<20} one branch per source ({names}), run concurrently")
            lines.append(f"   → {'discover_join':<20} merges every branch's opportunities")
        elif stage.mode == "concurrent_from_start":
            lines.append(f"   ⇢ {stage.name:<20} direct child of START, runs alongside the chain above")
            joins.append(stage.name)
        else:
            note = f"waits for: {', '.join(joins)}" if joins else ""
            lines.append(f"   → {stage.name:<20} {note}".rstrip())
            joins = []
    lines.append("   → END")
    lines.append("")

    if not config.discovery_sources:
        lines.append("  No discovery sources configured, so discover / choose_channel /")
        lines.append("  analyze_context are absent from this graph entirely — not merely no-ops.")
        lines.append("")
    return lines


def _placeholder_tools(config) -> Tools:
    """Stage instances need a Tools object to be constructed with; the graph is
    only drawn, never invoked, so these are never called."""
    return Tools(
        gsc=MockGoogleSearchConsoleClient(),
        analytics=MockAppAnalyticsClient(),
        traffic=MockTrafficClient(),
        llm=MockLLMClient(),
        discovery_sources={
            entry.get("name", "?"): MockOpportunitySource(entry.get("name", "?"))
            for entry in config.discovery_sources
        },
    )


def register(app: typer.Typer) -> None:
    app.command("show-graph")(show_graph)
