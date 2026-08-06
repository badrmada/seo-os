from __future__ import annotations

from dataclasses import dataclass, field

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from ..observability import NullReporter, observed_node
from ..schemas.io import AgentState
from .stages import (
    AnalyzeContextStage,
    AnalyzeStage,
    ChooseChannelStage,
    DiscoverStage,
    DraftStage,
    SelfQaStage,
)
from .tools import Tools

# The graph is assembled from a PipelineSpec instead of literal add_node/add_edge
# calls, so which stages run is a function of config, not a hardcoded wiring. The
# built-in "seo_content" agent type produces exactly three shapes:
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
# **"seo_content" is one agent type, not the only one.** `config.pipelines` maps a
# name to a list of stages, each of which may be a tenant's own class from
# plugins/ — so "the deliverable is not always a draft" is something a tenant can
# act on (a site audit, a link report, a brief) without this repo shipping, or
# taking a position on, a second agent. `--agent <name>` / `RunRequest.agent_type`
# selects one; see spec_for below and docs/configuration.md.
#
# Each stage's run() returns only the state keys it changes; LangGraph merges them
# into the running state before calling the next node. There's no branching or
# interrupt inside this agent (the approval gate belongs to the future
# worker/control-plane, not to the agent itself).
#
# input.channel, when given, picks which of three things gets drafted, inside the
# *same* three stages — see schemas/channel.py's Channel enum for what each one
# does differently. When discovery is configured and the caller omits it,
# choose_channel decides instead (see stages/choose_channel.py). A pipeline with
# none of the channel-aware stages in it has no channel at all — see
# PipelineSpec.channel_aware.

DEFAULT_AGENT_TYPE = "seo_content"

MODES = ("sequential", "parallel_by_source", "concurrent_from_start")

# name -> class, constructed as `cls(tools, config)` (plus an optional third
# `options` argument, see build_stage). A config-declared stage supplying its own
# `"class"` joins this by the same contract; these are the ones that ship.
BUILTIN_STAGES = {
    "discover": DiscoverStage,
    "choose_channel": ChooseChannelStage,
    "analyze_context": AnalyzeContextStage,
    "analyze": AnalyzeStage,
    "draft": DraftStage,
    "self_qa": SelfQaStage,
}

# The stages that only mean something for a run with a `channel` — which of three
# things is being written. A pipeline containing none of them isn't drafting
# anything, so its run has no channel to resolve and none is invented for it (see
# PipelineSpec.channel_aware and agent/managers/run_manager.py's _run).
_CHANNEL_STAGES = frozenset({"choose_channel", "analyze", "draft", "self_qa"})

_RESERVED_STAGE_NAMES = frozenset({"START", "END", "__start__", "__end__"})


@dataclass(frozen=True)
class PipelineStage:
    name: str
    # "sequential": one node, one predecessor/successor edge (the default, used by
    # most stages).
    #
    # "parallel_by_source": one branch per entry of a Tools collection, fanned out
    # via LangGraph's Send into a join node. Valid for any stage whose class
    # *declares* a fan-out (fanout_over/fanout_branch/fanout_join — see
    # stages/discover.py), which is the requirement rather than the stage being
    # named "discover".
    #
    # "concurrent_from_start": a direct child of START that runs alongside whatever
    # sequential chain precedes it, joining back in at the *next* stage in the spec
    # instead of chaining from the current prev_exit. Valid for any stage, with one
    # structural requirement build_graph enforces: something must come after it to
    # join into, or the branch would dangle.
    mode: str = "sequential"
    # None means "look me up in BUILTIN_STAGES by name". A config-declared stage
    # carries the class resolved from its tenant's plugins/ folder.
    cls: type = None
    # This stage's own settings, handed to a class that asks for a third
    # constructor argument — the same opt-in every "custom" provider has.
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineSpec:
    stages: tuple[PipelineStage, ...]
    agent_type: str = DEFAULT_AGENT_TYPE

    @property
    def channel_aware(self) -> bool:
        """Whether a run of this pipeline has a `channel` at all.

        Computed from the stages present rather than from the agent type, so a
        tenant pipeline that reuses `draft` still resolves a channel and a pure
        audit pipeline doesn't get a meaningless "site_article" written into its
        input. See agent/managers/run_manager.py's _run.
        """
        return any(stage.name in _CHANNEL_STAGES for stage in self.stages)


def spec_for(config, agent_type: str = "") -> PipelineSpec:
    """The pipeline for one agent type: a tenant's declared one if they have it,
    else the built-in "seo_content" shapes.

    `agent_type` overrides `config.agent_type` for one run (`--agent`,
    `RunRequest.agent_type`). An unknown name raises rather than silently falling
    back to seo_content — "my audit ran and produced an article" is the worst
    possible answer to a typo.

    Resolving a spec builds no tools and contacts nothing, which is what lets
    `show-graph` answer a purely structural question with no API key. It *does*
    import a declared stage's class, so it is also where an unimportable plugin
    surfaces — see validate_pipeline for why that isn't done at config load.
    """
    declared = getattr(config, "pipelines", None) or {}
    name = agent_type or getattr(config, "agent_type", "") or DEFAULT_AGENT_TYPE

    if name in declared:
        return _declared_spec(name, declared[name], config)
    if name == DEFAULT_AGENT_TYPE:
        return _default_spec(config)

    available = ", ".join(sorted({DEFAULT_AGENT_TYPE, *declared})) or DEFAULT_AGENT_TYPE
    raise ValueError(f"unknown agent type {name!r} (available: {available})")


def agent_types(config) -> list[str]:
    """Every agent type this config can run, for the CLI and for validation."""
    return sorted({DEFAULT_AGENT_TYPE, *(getattr(config, "pipelines", None) or {})})


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
    return PipelineSpec(stages=tuple(stages), agent_type=DEFAULT_AGENT_TYPE)


def _declared_spec(agent_type: str, declaration, config) -> PipelineSpec:
    """Turn one `config.pipelines` entry into a spec, resolving every custom class
    now rather than at run time — so `check-data` and `show-graph` report an
    unimportable stage without spending an LLM call.

    Order in the list *is* the chain. There is deliberately no `after:` field: the
    graph this builds is a chain with two declared exceptions to it (the two
    non-sequential modes), and a field whose only legal values restate the list
    order would be a second way to say one thing. A genuinely arbitrary DAG is a
    different feature, and no use case has asked for one.
    """
    # Deferred: agent.managers imports this module (run_manager -> build_graph), so
    # a module-level import here closes that cycle. Same fix as
    # agent/validators/template_validator.py's — see src/tests/test_imports.py.
    from ..managers.plugin_loader import load_custom_class

    stages = []
    for where, entry in validate_pipeline(agent_type, declaration):
        name = entry["name"]
        class_path = entry.get("class", "")
        cls = (
            load_custom_class(class_path, f"{where}.class", config)
            if class_path else BUILTIN_STAGES[name]
        )
        stages.append(PipelineStage(
            name=name,
            mode=entry.get("mode", "sequential"),
            cls=cls,
            options=entry.get("options", {}),
        ))

    return PipelineSpec(stages=tuple(stages), agent_type=agent_type)


def validate_pipeline(agent_type: str, declaration) -> list[tuple[str, dict]]:
    """Everything about a declared pipeline that can be checked without importing
    the tenant's code — names, duplicates, modes, and that a stage with no `class`
    names a built-in.

    Split from `_declared_spec` so the config loader can run it for *every*
    declared pipeline at load time while resolving *no* classes. Importing a
    plugin executes a tenant's Python, and a server resolving a config per request
    must not run the code of pipelines this request isn't going to use — the same
    line `load_dict`'s `validate` flag already draws between pure checks and ones
    with side effects. An unimportable class therefore surfaces when the pipeline
    is built: at the start of a run, and in `check-data`, which resolves every
    stage precisely so that isn't left to a real run to discover.
    """
    entries = declaration.get("stages") if isinstance(declaration, dict) else declaration
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            f'pipelines.{agent_type} must be an object with a non-empty "stages" list'
        )

    checked: list[tuple[str, dict]] = []
    seen = set()
    for index, entry in enumerate(entries):
        where = f"pipelines.{agent_type}.stages[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object, got {type(entry).__name__}")

        name = entry.get("name", "")
        if not isinstance(name, str) or not name or name in _RESERVED_STAGE_NAMES:
            raise ValueError(
                f'{where} needs a "name", and it may not be one of {sorted(_RESERVED_STAGE_NAMES)}'
            )
        if name in seen:
            # They become LangGraph node names, and the second add_node would
            # replace the first rather than complain.
            raise ValueError(f"{where}: duplicate stage name {name!r} — node names must be unique")
        seen.add(name)

        mode = entry.get("mode", "sequential")
        if mode not in MODES:
            raise ValueError(f"{where}: unknown mode {mode!r} (one of {', '.join(MODES)})")

        if not entry.get("class", "") and name not in BUILTIN_STAGES:
            raise ValueError(
                f'{where}: no built-in stage named {name!r}, so it needs a "class" '
                f'("module:ClassName" in this tenant\'s plugins/). '
                f"Built-in stages: {', '.join(sorted(BUILTIN_STAGES))}"
            )

        options = entry.get("options", {})
        if not isinstance(options, dict):
            raise ValueError(f"{where}.options must be an object, got {type(options).__name__}")

        checked.append((where, entry))

    return checked


def build_stage(stage: PipelineStage, tools: Tools, config):
    """Construct one stage. `(tools, config)`, with `(tools, config, options)` as
    an opt-in — the same shape, and the same signature inspection, that a
    `"custom"` provider's `(config)` / `(config, options)` pair uses.

    Every stage takes `tools` whether or not it calls anything, because a registry
    that remembers which stages want tools is a registry a tenant's own stage
    can't join.
    """
    from ..managers.plugin_loader import accepts_extra_arg  # deferred: see _declared_spec

    cls = stage.cls or BUILTIN_STAGES.get(stage.name)
    if cls is None:
        raise ValueError(f"unknown pipeline stage {stage.name!r}")
    if accepts_extra_arg(cls, required=1, base=2):
        return cls(tools, config, stage.options)
    return cls(tools, config)


def _fanout(stage: PipelineStage, tools: Tools):
    """What a "parallel_by_source" stage must declare, and the entries it fans out
    over.

    This is the requirement that replaced `stage.name == "discover"`. A stage class
    opting into this mode declares three things (see stages/discover.py):

      - `fanout_over`: the name of a Tools attribute holding a `{name: client}`
        mapping — one branch per entry.
      - `fanout_branch`: the class run once per entry, receiving
        `{"source_name": str, "context": {...}}` rather than the graph state, and
        returning its contribution under a state key with a merge reducer.
      - `fanout_join`: the class that merges those contributions back into
        `working`, run once after every branch.

    Keying off the declaration rather than the name is what lets a tenant's own
    stage use this mode; keying off a mapping that has to exist on Tools is what
    stops a stage from declaring a fan-out over nothing.
    """
    cls = stage.cls or BUILTIN_STAGES.get(stage.name)
    over = getattr(cls, "fanout_over", "")
    branch = getattr(cls, "fanout_branch", None)
    join = getattr(cls, "fanout_join", None)
    if not (over and branch and join):
        raise ValueError(
            f'stage {stage.name!r} cannot use mode "parallel_by_source": its class '
            f"{getattr(cls, '__name__', cls)!r} must declare fanout_over (a Tools "
            "attribute), fanout_branch and fanout_join"
        )

    entries = getattr(tools, over, None)
    if not isinstance(entries, dict):
        raise ValueError(
            f'stage {stage.name!r} fans out over Tools.{over}, which is '
            f"{type(entries).__name__}, not a mapping of name -> client"
        )
    return over, branch, join, entries


def _fanout_to_branches(node: str, entries: dict):
    """Conditional-edge routing function: dynamically sends one Send per entry to
    the branch node, each with its own {source_name, context} payload (LangGraph's
    standard map-reduce pattern) instead of the full graph state. Every branch is a
    separate invocation of the same node.
    """

    def route(state: AgentState) -> list[Send]:
        input_ = state["input"]
        context = {
            "seed_keyword": input_.get("seed_keyword", ""),
            "context_text": input_.get("context_text", ""),
        }
        return [
            Send(node, {"source_name": name, "context": context}) for name in entries
        ]

    return route


def build_graph(tools: Tools, config, spec: PipelineSpec = None, reporter=None):
    """reporter (agent/observability/) instruments each stage for verbose mode.
    Defaults to NullReporter, in which case observed_node returns each stage's run()
    callable untouched — the assembled graph is then byte-for-byte what it was
    before verbose mode existed."""
    spec = spec if spec is not None else spec_for(config)
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
            graph.add_node(
                stage.name,
                observed_node(reporter, stage.name, build_stage(stage, tools, config).run),
            )
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
            _, branch_cls, join_cls, entries = _fanout(stage, tools)
            branch_node = f"{stage.name}_source"
            join_node = f"{stage.name}_join"
            graph.add_node(
                branch_node,
                observed_node(reporter, branch_node, branch_cls(tools, config).run),
            )
            graph.add_node(
                join_node,
                observed_node(reporter, join_node, join_cls(tools, config).run),
            )
            graph.add_conditional_edges(prev_exit, _fanout_to_branches(branch_node, entries))
            graph.add_edge(branch_node, join_node)
            prev_exit = join_node
        elif stage.mode == "concurrent_from_start":
            graph.add_node(
                stage.name,
                observed_node(reporter, stage.name, build_stage(stage, tools, config).run),
            )
            graph.add_edge(START, stage.name)
            pending_joins.append(stage.name)
        else:
            raise NotImplementedError(f"pipeline stage mode {stage.mode!r} not supported yet")

    if pending_joins:
        # The one structural requirement of concurrent_from_start: something has to
        # come after it. Left dangling, the branch would run and its writes would
        # land in the same superstep as END — which is not an error LangGraph
        # reports, just a result missing whatever that stage produced.
        raise ValueError(
            f"stage(s) {', '.join(pending_joins)} use mode \"concurrent_from_start\" but "
            "nothing follows them in the pipeline to join into — a stage in that mode "
            "must be followed by a sequential one"
        )

    graph.add_edge(prev_exit, END)

    return graph.compile()
