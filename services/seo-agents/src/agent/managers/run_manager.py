import asyncio
import uuid

from state.memory_store import InMemoryStateStore

from .. import prompts
from ..graph.pipeline import build_graph, spec_for
from ..graph.stages import AnalyzeStage
from ..graph.tools import Tools
from ..observability import NullReporter, build_reporter, observe_tools
from ..schemas.channel import Channel
from ..schemas.io import AgentInput, AgentState
from ..utils.async_utils import deadline
from ..validators.input_validator import InputValidator
from .tools_manager import ToolsManager


def _reporter_from_config(config):
    """A tenant can turn verbose on by default in its config; src/main.py's -v flag
    overrides it by passing a reporter explicitly (the CLI always wins). Returns
    NullReporter for the default verbose=0, so this costs nothing when unused."""
    level = getattr(config, "verbose", 0) or 0
    if not level:
        return NullReporter()
    return build_reporter(level, getattr(config, "verbose_format", "text"))


def _build_agent_input(input_data: dict, channel: str = None) -> AgentInput:
    result = {
        "seed_keyword": input_data.get("seed_keyword", ""),
        "context_text": input_data.get("context_text", ""),
        "params": input_data.get("params", {}),
        "site_url": input_data.get("site_url", ""),
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

    reporter (agent/observability/) is verbose mode's entry point — it wraps the
    tools and the pipeline's stages so a run is followable while it happens.
    Defaults to the config's own verbose level, and to NullReporter when that's 0,
    so nothing about a non-verbose run changes.

    **`arun()` is the real entry point; `run()` is a thin sync wrapper.** The
    pipeline is async end to end (see agent/utils/async_utils.py), which is what
    lets many tenants' runs proceed concurrently in one process via
    `asyncio.gather` rather than one thread each. A caller that already has an
    event loop — a service layer, an HTTP handler, a queue worker — must call
    `arun()`: `run()` uses `asyncio.run()`, which refuses to nest inside a running
    loop.
    """

    def __init__(self, config, tools: Tools = None, reporter=None) -> None:
        self.config = config
        self.tools = tools
        self.reporter = reporter if reporter is not None else _reporter_from_config(config)
        self._input_validator = InputValidator()

    def _resolve_tools(self, input_data: dict) -> Tools:
        """Wrapping happens here rather than inside ToolsManager so it covers an
        explicitly injected Tools(...) too — a caller supplying their own clients
        still gets a followable run. observe_tools returns the bundle unchanged
        when reporting is off, so there are no proxies in a normal run's call path."""
        if self.tools is not None:
            tools = self.tools
        else:
            tools = ToolsManager(self.config).build_all(input_data.get("model"))
        return observe_tools(tools, self.reporter)

    def run(self, input_data: dict, *, state_store: InMemoryStateStore = None) -> dict:
        """Sync wrapper around arun(), for callers with no event loop of their own
        (the CLI, tests, scripts). Carries arun()'s never-raises contract unchanged:
        asyncio.run() re-raises whatever the coroutine raised, and arun() raises
        nothing.

        Not usable from inside a running event loop — asyncio.run() refuses to
        nest. Anything already async (a service layer, an HTTP handler) calls
        arun() directly.
        """
        return asyncio.run(self.arun(input_data, state_store=state_store))

    async def arun(self, input_data: dict, *, state_store: InMemoryStateStore = None) -> dict:
        """Always returns the same top-level shape (see agent/schemas/io.py's
        AgentState) — this is the run() boundary a caller/UI depends on, so nothing
        past this point is allowed to raise. Bad input, a graph-node exception that
        wasn't already caught internally (e.g. an LLM or rank-data call outside
        DiscoverStage's own degrade-don't-abort handling), a run that overran
        config.run_timeout_seconds, or anything else that goes wrong lands as
        {"phase": "failed", "error": str(exc), ...} instead of propagating — never
        a raw traceback in place of the documented JSON shape.
        """
        run_id = input_data.get("run_id") or str(uuid.uuid4())
        self.reporter.event(
            "run_start",
            run_id=run_id,
            channel=input_data.get("channel") or "auto",
            seed_keyword=input_data.get("seed_keyword", ""),
            sources=len(self.config.discovery_sources),
        )
        timeout = getattr(self.config, "run_timeout_seconds", 0) or 0
        bound = deadline(timeout)
        try:
            async with bound:
                result = await self._run(input_data, run_id, state_store)
        except TimeoutError as exc:
            # str(TimeoutError()) is "", so an expired deadline has to say what
            # happened. bound.expired() distinguishes it from a TimeoutError a tool
            # raised on its own — relabeling that one would send whoever reads the
            # error looking at the wrong timeout.
            if bound.expired():
                exc = TimeoutError(f"run exceeded run_timeout_seconds ({timeout:g}s)")
            return self._failed(input_data, run_id, exc, state_store)
        except Exception as exc:  # noqa: BLE001 - this is the public run() boundary; see docstring
            return self._failed(input_data, run_id, exc, state_store)

        self.reporter.event(
            "run_end",
            run_id=run_id,
            phase=result.get("phase"),
            tokens=result.get("usage", {}).get("tokens", 0),
            opportunities=len(result.get("discovery", {}).get("opportunities", ())),
            tool_errors=len(result.get("discovery", {}).get("tool_errors", ())),
            error=result.get("error") or "",
        )
        return result

    def _failed(self, input_data: dict, run_id: str, exc: BaseException, state_store) -> dict:
        """The documented failure shape (docs/output-schema.md), built in one place
        because arun() now has two ways to reach it — an ordinary exception and the
        run deadline expiring."""
        failed_state = {
            "run_id": run_id,
            # The configured agent type, not a constant: a failure report that
            # names the wrong agent is worse than one that names none, and this
            # path is reached by exactly the failures a caller most needs to
            # attribute — including "that agent type doesn't exist".
            "agent_type": getattr(self.config, "agent_type", "") or "seo_content",
            "phase": "failed",
            "input": dict(input_data),
            "output": None,
            "discovery": {"opportunities": [], "channel_decision": None, "tool_errors": []},
            "usage": {"tokens": 0, "cost_usd": 0},
            "error": str(exc),
        }
        if state_store is not None:
            state_store.save(run_id, dict(failed_state))
        self.reporter.event("run_end", run_id=run_id, phase="failed", error=str(exc))
        return failed_state

    async def _run(self, input_data: dict, run_id: str, state_store: InMemoryStateStore = None) -> dict:
        self._input_validator.validate(input_data, self.config)

        # Which pipeline this run is. Resolved before anything else because it
        # decides the rest: an unknown agent type raises here and arrives as the
        # documented phase="failed" shape rather than half a run.
        spec = spec_for(self.config)

        # Explicit input.channel is always honored as-is. Omitted + discovery
        # configured means it's genuinely undecided until ChooseChannelStage runs
        # inside the graph; omitted + no discovery configured keeps today's
        # behavior (resolve to config.default_channel up front).
        #
        # A pipeline with none of the channel-aware stages in it isn't writing
        # anything, so it has no channel and none is invented — otherwise a site
        # audit's input would carry "site_article", and every signal that reads the
        # run's input would be told this audit is drafting an article.
        channel = None
        if spec.channel_aware:
            channel = input_data.get("channel") or (
                None if self.config.discovery_sources else self.config.default_channel
            )

        tools = self._resolve_tools(input_data)
        graph = build_graph(tools, self.config, spec=spec, reporter=self.reporter)

        initial_state: AgentState = {
            "run_id": run_id,
            "agent_type": spec.agent_type,
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
            async for state in graph.astream(initial_state, stream_mode="values"):
                final_state = dict(state)
                state_store.save(run_id, final_state)
        else:
            final_state = dict(await graph.ainvoke(initial_state))

        working = final_state.pop("working", {})
        # Internal to the parallel_by_source fan-out (stages/discover.py) and never
        # part of the documented result — but LangGraph materializes every declared
        # channel, so an Annotated field with a reducer is present as [] even in a
        # graph that has no fan-out node in it. It has been leaking into the
        # returned JSON as an undocumented top-level key; a stage-scoped working key
        # does not belong in the result plane (docs/output-schema.md), whichever
        # pipeline ran.
        final_state.pop("discover_results", None)
        # Always present, whatever the pipeline was: the result shape is frozen
        # (docs/output-schema.md), so a pipeline with no discover stage reports an
        # empty discovery block rather than omitting the key. A caller parsing a
        # result never has to branch on which agent produced it — and `tool_errors`
        # in particular is where *any* stage's degraded call is recorded, including
        # a tenant's own.
        final_state["discovery"] = {
            "opportunities": working.get("opportunities", []),
            "channel_decision": working.get("channel_decision"),
            "tool_errors": working.get("tool_errors", []),
        }
        return final_state

    def preview_prompt(self, input_data: dict) -> dict:
        """Sync wrapper around apreview_prompt(), for the CLI. Same nesting caveat
        as run(): async callers use apreview_prompt()."""
        return asyncio.run(self.apreview_prompt(input_data))

    async def apreview_prompt(self, input_data: dict) -> dict:
        """Dry run: builds the exact prompt DraftStage would send to the LLM — runs the
        real AnalyzeStage (so it's real search-performance/analytics/traffic data if
        configured, not
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
        state.update(await AnalyzeStage(tools, self.config).run(state))
        working = state["working"]
        params = input_.get("params", {})

        signals = working.get("signals", {})

        if channel == Channel.ENGAGEMENT_COMMENT:
            prompt = prompts.build_comment_prompt(
                input_["context_text"], params, self.config, signals,
            )
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
                signals=signals,
            )

        return {"channel": channel, "prompt": prompt, "context": working}
