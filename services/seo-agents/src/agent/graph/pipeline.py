from __future__ import annotations

from dataclasses import dataclass

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from ..observability import NullReporter, observed_node
from ..schemas.io import AgentState
from .stages import (
    AnalyzeContextStage,
    AnalyzeStage,
    ChooseChannelStage,
    DiscoverJoinStage,
    DiscoverSourceStage,
    DiscoverStage,
    DraftStage,
    SelfQaStage,
)
from .tools import Tools

# The graph is assembled from a PipelineSpec instead of literal add_node/add_edge
# calls, so which stages run is a function of config, not a hardcoded wiring:
#
#   0 discovery_sources (the default) -> START -> analyze -> draft -> self_qa -> END
#   1 discovery_source                -> START -> discover -> choose_channel -\
#                                     -> analyze_context ----------------------> analyze -> draft -> self_qa -> END
#   2+ discovery_sources              -> START -> Send(discover_source) x N -> discover_join
#                                                 -> choose_channel ----------\
#                                     -> analyze_context ----------------------> analyze -> draft -> self_qa -> END
#
# A zero-config tenant only ever gets the first shape — discover/choose_channel/
# analyze_context don't exist in their graph at all, not just no-ops. The third
# shape (parallel_by_source) runs the same per-source logic as the second, just
# fanned out via LangGraph's Send and merged in discover_join instead of looped
# in one node — see stages/discover.py's DiscoverSourceStage/DiscoverJoinStage.
#
# analyze_context is a direct child of START, run concurrently with the
# discover -> choose_channel chain rather than after it: the analytics/traffic
# calls it makes don't depend on channel or discovered opportunities (unlike the
# search-performance/keyword-picking part of analyze), so there's no reason to wait for
# discovery to finish first. It joins back in at "analyze", which needs both
# choose_channel's channel decision and analyze_context's data before it can
# finish the channel-dependent part — see stages/analyze.py's
# AnalyzeContextStage/AnalyzeStage.
#
# Each stage's run() returns only the state keys it changes; LangGraph merges them
# into the running state before calling the next node. There's no branching or
# interrupt inside this agent (the approval gate belongs to the future
# worker/control-plane, not to the agent itself).
#
# input.channel, when given, picks which of three things gets drafted, inside the
# *same* three stages — see schemas/channel.py's Channel enum for what each one
# does differently. When discovery is configured and the caller omits it,
# choose_channel decides instead (see stages/choose_channel.py).


@dataclass(frozen=True)
class PipelineStage:
    name: str
    # "sequential": one node, one predecessor/successor edge (the default, used by
    # most stages). "parallel_by_source" is only valid for the "discover" stage —
    # one branch per Tools.discovery_sources key, fanned out via LangGraph's Send
    # into a join node, see build_graph below. "concurrent_from_start" is only
    # valid for "analyze_context" — a direct child of START that runs alongside
    # whatever sequential chain precedes it, joining back in at the *next* stage
    # in the spec instead of chaining from the current prev_exit.
    mode: str = "sequential"


@dataclass(frozen=True)
class PipelineSpec:
    stages: tuple[PipelineStage, ...]


# name -> (tools, config) -> object with a .run(state) method, registered as that
# LangGraph node. Extending this (plus _default_spec) is how a stage gets added to
# the pipeline. Not used for "discover" in parallel_by_source mode — see
# build_graph, which wires DiscoverSourceStage/DiscoverJoinStage instead.
_STAGE_FACTORIES = {
    "discover": lambda tools, config: DiscoverStage(tools, config),
    "choose_channel": lambda tools, config: ChooseChannelStage(config),
    "analyze_context": lambda tools, config: AnalyzeContextStage(tools, config),
    "analyze": lambda tools, config: AnalyzeStage(tools, config),
    "draft": lambda tools, config: DraftStage(tools, config),
    "self_qa": lambda tools, config: SelfQaStage(config),
}

# discover_source node name used by the parallel_by_source fan-out; see build_graph.
_DISCOVER_SOURCE_NODE = "discover_source"
_DISCOVER_JOIN_NODE = "discover_join"


def _default_spec(config) -> PipelineSpec:
    stages = []
    if config.discovery_sources:
        # Fanning out is only worth it once there's more than one source to run
        # concurrently — a single source has nothing to parallelize, so it stays on
        # the plain, long-proven sequential path (DiscoverStage).
        mode = "parallel_by_source" if len(config.discovery_sources) > 1 else "sequential"
        stages.append(PipelineStage("discover", mode=mode))
        stages.append(PipelineStage("choose_channel"))
        # Only worth running concurrently when there's a discover/choose_channel
        # chain for it to overlap with — a zero-discovery tenant's analyze has
        # nothing to wait for in the first place, so it stays a single
        # self-contained node (see AnalyzeStage.run's analyze_context is None branch).
        stages.append(PipelineStage("analyze_context", mode="concurrent_from_start"))
    stages.append(PipelineStage("analyze"))
    stages.append(PipelineStage("draft"))
    stages.append(PipelineStage("self_qa"))
    return PipelineSpec(stages=tuple(stages))


# Public alias: the CLI's `show-graph` renders a tenant's effective pipeline from
# the spec alone, without building any tools (so it needs no API key to answer a
# purely structural question).
default_spec = _default_spec


def _fanout_to_sources(tools: Tools):
    """Conditional-edge routing function: dynamically sends one Send per configured
    discovery source to the discover_source node, each with its own {source_name,
    context} payload (LangGraph's standard map-reduce pattern) instead of the full
    graph state. Every branch is a separate invocation of the same node.
    """

    def route(state: AgentState) -> list[Send]:
        input_ = state["input"]
        context = {
            "seed_keyword": input_.get("seed_keyword", ""),
            "context_text": input_.get("context_text", ""),
        }
        return [
            Send(_DISCOVER_SOURCE_NODE, {"source_name": name, "context": context})
            for name in tools.discovery_sources
        ]

    return route


def build_graph(tools: Tools, config, spec: PipelineSpec = None, reporter=None):
    """reporter (agent/observability/) instruments each stage for verbose mode.
    Defaults to NullReporter, in which case observed_node returns each stage's run()
    callable untouched — the assembled graph is then byte-for-byte what it was
    before verbose mode existed."""
    spec = spec or _default_spec(config)
    reporter = reporter or NullReporter()

    graph = StateGraph(AgentState)
    prev_exit = START
    # Nodes wired as a direct child of START (mode="concurrent_from_start") don't
    # chain from prev_exit and don't become the new prev_exit themselves — they
    # join back in as an *extra* predecessor of whichever stage comes next in the
    # spec (a plain multi-predecessor LangGraph join, same mechanism as any
    # fan-in): tracked here until that next stage is wired.
    pending_joins: list[str] = []
    for stage in spec.stages:
        if stage.mode == "sequential":
            try:
                factory = _STAGE_FACTORIES[stage.name]
            except KeyError:
                raise ValueError(f"unknown pipeline stage {stage.name!r}") from None
            graph.add_node(stage.name, observed_node(reporter, stage.name, factory(tools, config).run))
            if pending_joins:
                # A single multi-source add_edge is required here, not one call per
                # source — StateGraph.add_edge([a, b], c) is an AND-join (c waits for
                # both a and b); calling add_edge(a, c) and add_edge(b, c) separately
                # makes each an independent OR-trigger, so c could fire on whichever
                # of a/b finishes first, with the other's write landing in the same
                # superstep as an unrelated later node and colliding on shared keys
                # like "phase".
                graph.add_edge([prev_exit, *pending_joins], stage.name)
            else:
                graph.add_edge(prev_exit, stage.name)
            pending_joins = []
            prev_exit = stage.name
        elif stage.mode == "parallel_by_source":
            if stage.name != "discover":
                raise ValueError(
                    f"parallel_by_source mode is only valid for the discover stage, got {stage.name!r}"
                )
            graph.add_node(
                _DISCOVER_SOURCE_NODE,
                observed_node(reporter, _DISCOVER_SOURCE_NODE, DiscoverSourceStage(tools).run),
            )
            graph.add_node(
                _DISCOVER_JOIN_NODE,
                observed_node(reporter, _DISCOVER_JOIN_NODE, DiscoverJoinStage().run),
            )
            graph.add_conditional_edges(prev_exit, _fanout_to_sources(tools))
            graph.add_edge(_DISCOVER_SOURCE_NODE, _DISCOVER_JOIN_NODE)
            prev_exit = _DISCOVER_JOIN_NODE
        elif stage.mode == "concurrent_from_start":
            if stage.name != "analyze_context":
                raise ValueError(
                    f"concurrent_from_start mode is only valid for analyze_context, got {stage.name!r}"
                )
            factory = _STAGE_FACTORIES[stage.name]
            graph.add_node(stage.name, observed_node(reporter, stage.name, factory(tools, config).run))
            graph.add_edge(START, stage.name)
            pending_joins.append(stage.name)
        else:
            raise NotImplementedError(f"pipeline stage mode {stage.mode!r} not supported yet")

    graph.add_edge(prev_exit, END)

    return graph.compile()
