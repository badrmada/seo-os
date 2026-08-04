import uuid

from state.memory_store import InMemoryStateStore

from .. import prompts
from ..graph.pipeline import build_graph
from ..graph.stages import AnalyzeStage
from ..graph.tools import Tools
from ..schemas.channel import Channel
from ..schemas.io import AgentInput, AgentState
from ..validators.input_validator import InputValidator
from .tools_manager import ToolsManager


def _build_agent_input(input_data: dict, channel: str = None) -> AgentInput:
    result = {
        "seed_keyword": input_data.get("seed_keyword", ""),
        "context_text": input_data.get("context_text", ""),
        "params": input_data.get("params", {}),
        "gsc_domain": input_data.get("gsc_domain", ""),
    }
    # channel is omitted (not defaulted) when it's left for ChooseChannelStage to
    # decide inside the graph (see agent/graph/stages/choose_channel.py) — every
    # stage falls back to config.default_channel itself when it's genuinely absent.
    if channel:
        result["channel"] = channel
    return result


class AgentRunner:
    """Orchestrates a run of the seo_content agent: validates input, builds/reuses
    Tools (via ToolsManager), builds the LangGraph pipeline, and executes it —
    optionally checkpointing state after every node transition.

    tools=Tools(...) can be passed at construction to override the default,
    config-driven providers (e.g. for tests); otherwise each run() builds its own
    default Tools bundle via ToolsManager, matching the per-call default the
    previous free-function seo_content_agent.run() had.
    """

    def __init__(self, config, tools: Tools = None) -> None:
        self.config = config
        self.tools = tools
        self._input_validator = InputValidator()

    def _resolve_tools(self, input_data: dict) -> Tools:
        if self.tools is not None:
            return self.tools
        return ToolsManager(self.config).build_all(input_data.get("model"))

    def run(self, input_data: dict, *, state_store: InMemoryStateStore = None) -> dict:
        """Always returns the same top-level shape (see agent/schemas/io.py's
        AgentState) — this is the run() boundary a caller/UI depends on, so nothing
        past this point is allowed to raise. Bad input, a graph-node exception that
        wasn't already caught internally (e.g. an LLM or GSC call outside
        DiscoverStage's own degrade-don't-abort handling), or anything else that
        goes wrong lands as {"phase": "failed", "error": str(exc), ...} instead of
        propagating — never a raw traceback in place of the documented JSON shape.
        """
        run_id = input_data.get("run_id") or str(uuid.uuid4())
        try:
            return self._run(input_data, run_id, state_store)
        except Exception as exc:  # noqa: BLE001 - this is the public run() boundary; see docstring
            failed_state = {
                "run_id": run_id,
                "agent_type": "seo_content",
                "phase": "failed",
                "input": dict(input_data),
                "output": None,
                "discovery": {"opportunities": [], "channel_decision": None, "tool_errors": []},
                "usage": {"tokens": 0, "cost_usd": 0},
                "error": str(exc),
            }
            if state_store is not None:
                state_store.save(run_id, dict(failed_state))
            return failed_state

    def _run(self, input_data: dict, run_id: str, state_store: InMemoryStateStore = None) -> dict:
        self._input_validator.validate(input_data, self.config)

        # Explicit input.channel is always honored as-is. Omitted + discovery
        # configured means it's genuinely undecided until ChooseChannelStage runs
        # inside the graph; omitted + no discovery configured keeps today's
        # behavior (resolve to config.default_channel up front).
        channel = input_data.get("channel") or (
            None if self.config.discovery_sources else self.config.default_channel
        )

        tools = self._resolve_tools(input_data)
        graph = build_graph(tools, self.config)

        initial_state: AgentState = {
            "run_id": run_id,
            "agent_type": "seo_content",
            "phase": "queued",
            "input": _build_agent_input(input_data, channel),
            "working": {},
            "output": None,
            "usage": {"tokens": 0, "cost_usd": 0},
            "error": None,
        }

        if state_store is not None:
            # Checkpoint after every node transition so a run's progress is observable
            # mid-flight, not just at the end. graph.stream(..., stream_mode="values")
            # yields the accumulated state after each super-step.
            state_store.save(run_id, dict(initial_state))
            final_state = dict(initial_state)
            for state in graph.stream(initial_state, stream_mode="values"):
                final_state = dict(state)
                state_store.save(run_id, final_state)
        else:
            final_state = dict(graph.invoke(initial_state))

        working = final_state.pop("working", {})
        final_state["discovery"] = {
            "opportunities": working.get("opportunities", []),
            "channel_decision": working.get("channel_decision"),
            "tool_errors": working.get("tool_errors", []),
        }
        return final_state

    def preview_prompt(self, input_data: dict) -> dict:
        """Dry run: builds the exact prompt DraftStage would send to the LLM — runs the
        real AnalyzeStage (so it's real GSC/analytics/traffic data if configured, not
        fabricated) but never calls the LLM. Lets you review what a tenant's template
        actually renders to before spending a real API call.

        Always resolves channel up front (never leaves it to ChooseChannelStage,
        unlike run()) — this is a single-stage dry run of AnalyzeStage/prompt-building,
        not the full graph, so there's no discover/choose_channel step to defer to.
        Pass channel explicitly if you want to preview a specific one; otherwise it
        uses config.default_channel, same as a run() with discovery_sources unset.
        """
        channel = input_data.get("channel", self.config.default_channel)
        self._input_validator.validate({**input_data, "channel": channel}, self.config)

        tools = self._resolve_tools(input_data)
        input_ = _build_agent_input(input_data, channel)
        state: AgentState = {"input": input_, "working": {}}
        state.update(AnalyzeStage(tools, self.config).run(state))
        working = state["working"]
        params = input_.get("params", {})

        if channel == Channel.ENGAGEMENT_COMMENT:
            prompt = prompts.build_comment_prompt(input_["context_text"], params, self.config)
        else:
            source_row = working.get("chosen_keyword_row") or {}
            prompt = prompts.build_article_prompt(
                channel,
                working["chosen_keyword"],
                params,
                working["analytics_summary"],
                working["analytics_highlights"],
                working["traffic_summary"],
                self.config,
                strategy=source_row.get("reason", ""),
            )

        return {"channel": channel, "prompt": prompt, "context": working}
