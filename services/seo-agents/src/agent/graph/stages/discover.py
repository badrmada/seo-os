from ...schemas.io import AgentState
from ...schemas.opportunity import normalize_opportunity
from ...utils.async_utils import call as acall
from ...utils.tool_errors import record_tool_error
from ..tools import Tools


class DiscoverStage:
    """Only added to the pipeline when config.discovery_sources is non-empty (see
    agent/graph/pipeline.py:build_graph) — a zero-config tenant never runs this
    stage. Calls every configured Tools.discovery_sources client in turn and merges
    their results into working.opportunities; a single source raising never aborts
    the run, it just contributes zero opportunities and a working.tool_errors entry
    (see ChooseChannelStage for how an empty opportunities list is handled). Every
    item every source returns is passed through
    agent/schemas/opportunity.py's normalize_opportunity — mock/llm/custom sources
    are all trusted the same amount (none): a malformed item is dropped
    individually rather than corrupting ChooseChannelStage's cross-source scoring
    or raising and losing every other opportunity that source found.

    This is the "sequential" pipeline mode — used when config.discovery_sources
    has zero or exactly one entry (nothing to gain from fanning out one source).
    Two or more sources instead use the "parallel_by_source" mode (see
    agent/graph/pipeline.py's _default_spec): DiscoverSourceStage/DiscoverJoinStage
    below run the same per-source logic concurrently via LangGraph's Send.

    The three `fanout_*` attributes assigned at the bottom of this module are how
    that mode is *declared* rather than assumed: build_graph used to hardcode
    `stage.name == "discover"`, which meant no other stage — including a tenant's
    own — could ever fan out. Anything that declares the same three things can.
    """

    fanout_over = "discovery_sources"  # the Tools mapping to run one branch per

    def __init__(self, tools: Tools, config) -> None:
        self.tools = tools
        self.config = config

    async def run(self, state: AgentState) -> dict:
        """Reads: input.seed_keyword, input.context_text (passed to every source as
        steering context, which a source may ignore). Writes: phase="discover";
        working.opportunities (list[Opportunity], every source's results merged);
        working.tool_errors (appends one ToolError per source that raised).
        """
        input_ = state["input"]
        context = {
            "seed_keyword": input_.get("seed_keyword", ""),
            "context_text": input_.get("context_text", ""),
        }

        working = dict(state.get("working", {}))
        opportunities = list(working.get("opportunities", []))
        tool_errors = list(working.get("tool_errors", []))

        # Awaited one at a time on purpose: this mode only runs with zero or one
        # source configured (two or more take the parallel_by_source fan-out, where
        # LangGraph runs the branches concurrently), so there is nothing here to
        # overlap and a plain loop is the honest description of what happens.
        for name, source in self.tools.discovery_sources.items():
            try:
                for item in await acall(source.discover, context):
                    opportunity = normalize_opportunity(item, source=name)
                    if opportunity is not None:
                        opportunities.append(opportunity)
            except Exception as exc:  # noqa: BLE001 - one source failing must never abort the run
                record_tool_error(tool_errors, name, "discover", exc)

        working["opportunities"] = opportunities
        working["tool_errors"] = tool_errors
        return {"phase": "discover", "working": working}

    # Assigned below, once the two classes exist — see the note at the bottom of
    # this module. Declared here so the fan-out contract is visible on the stage
    # that opts into it rather than only at the point of assignment.
    fanout_branch = None
    fanout_join = None


class DiscoverSourceStage:
    """One branch of the "parallel_by_source" fan-out (see agent/graph/pipeline.py
    build_graph) — invoked once per configured source via LangGraph's Send, each
    call getting its own {"source_name", "context"} payload instead of the full
    graph state. Same per-source try/except + record_tool_error as DiscoverStage;
    the only difference is where the result goes (discover_results, merged by
    DiscoverJoinStage) instead of directly into working.
    """

    def __init__(self, tools: Tools, config=None) -> None:
        self.tools = tools

    async def run(self, state: dict) -> dict:
        name = state["source_name"]
        context = state["context"]
        source = self.tools.discovery_sources[name]

        opportunities: list = []
        tool_errors: list = []
        try:
            for item in await acall(source.discover, context):
                opportunity = normalize_opportunity(item, source=name)
                if opportunity is not None:
                    opportunities.append(opportunity)
        except Exception as exc:  # noqa: BLE001 - one source failing must never abort the run
            record_tool_error(tool_errors, name, "discover", exc)

        return {
            "discover_results": [
                {"tool": name, "opportunities": opportunities, "tool_errors": tool_errors}
            ]
        }


class DiscoverJoinStage:
    """Joins every DiscoverSourceStage branch back together. Reads
    state["discover_results"] (populated by the Send fan-out, concatenated across
    branches via the Annotated[list[dict], operator.add] reducer on AgentState) and
    merges it into working exactly like DiscoverStage.run's loop does for the
    sequential path — same opportunities/tool_errors contract either way.
    """

    def __init__(self, tools: Tools = None, config=None) -> None:
        pass

    async def run(self, state: AgentState) -> dict:
        working = dict(state.get("working", {}))
        opportunities = list(working.get("opportunities", []))
        tool_errors = list(working.get("tool_errors", []))

        for result in state.get("discover_results", []):
            opportunities.extend(result["opportunities"])
            tool_errors.extend(result["tool_errors"])

        working["opportunities"] = opportunities
        working["tool_errors"] = tool_errors
        return {"phase": "discover", "working": working}


# The fan-out declaration agent/graph/pipeline.py reads for mode
# "parallel_by_source". Assigned after the fact only because all three classes
# live in one module and DiscoverStage is the one worth reading first; a tenant's
# own stage declares the same three attributes inline in its class body.
DiscoverStage.fanout_branch = DiscoverSourceStage
DiscoverStage.fanout_join = DiscoverJoinStage
