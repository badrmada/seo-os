"""Instrumentation by wrapping, not by editing. Every proxy here satisfies exactly
the Protocol it wraps (tools/base.py, tools/llm/base.py), times the call, reports
it, and hands back the untouched result — so no stage, and no tenant's "custom"
class, needs to know verbose mode exists. `observe_tools()` swaps a whole Tools
bundle for a wrapped one in a single call at the AgentRunner boundary.

This is why verbose mode touches almost nothing: the two hooks are here (tool
calls) and `observed_node()` below (pipeline stages). Neither is in a stage.

Every proxy delegates unknown attributes to the wrapped client via __getattr__, so
a client with extra public methods beyond its Protocol keeps working — the proxy
is transparent for everything it doesn't explicitly instrument.

**The sync/async fork lives here.** Every proxy method is `async def` and reaches
the wrapped client through agent/utils/async_utils.py's `call()`, which awaits an
async implementation and threads a sync one. Since the proxies already sit in
front of every tool call, that is the only place the framework has to decide —
and it means a tenant's existing sync "custom" class needs no change at all. The
reporter's own `timed()` stays a plain sync context manager: an `await` inside a
`with` block is exactly as correct as one outside it, and the block is what
carries the timing across the await.
"""

from __future__ import annotations

import time
from dataclasses import replace

from ..utils.async_utils import call as acall
from .redaction import preview
from .reporter import (
    STAGE_END,
    STAGE_ERROR,
    STAGE_START,
    TOOL_END,
    TOOL_ERROR,
    TOOL_START,
)

# Level 2 (-vv) adds payload previews: prompts, response text, chosen topics. Level
# 1 (-v) stays at lifecycle + timings + outcomes.
_DETAIL = 2


class _Observed:
    def __init__(self, inner, reporter, name: str) -> None:
        self._inner = inner
        self._reporter = reporter
        self._name = name

    def __getattr__(self, attr):
        return getattr(self._inner, attr)

    def _call(self, method: str, **fields):
        return self._reporter.timed(
            TOOL_START, TOOL_END, TOOL_ERROR, tool=self._name, method=method, **fields
        )


class ObservedLLMClient(_Observed):
    """LLMClient proxy. Reports token usage and — the detail that actually matters
    for debugging discovery — whether a grounded call came back with real citation
    sources. A grounded call returning zero sources is exactly the condition that
    makes LLMOpportunitySource silently drop every link (see PLAN.md Step 2a), so
    surfacing it here turns a silent data-loss bug into a visible one."""

    async def generate(self, prompt: str, *, model: str = None, grounded: bool = False):
        fields = {"grounded": grounded}
        if model:
            fields["model"] = model
        if self._reporter.level >= _DETAIL:
            fields["prompt"] = preview(prompt)
        with self._call("generate", **fields) as call:
            response = await acall(self._inner.generate, prompt, model=model, grounded=grounded)
            call["tokens"] = getattr(response, "tokens", 0)
            call["sources"] = len(getattr(response, "sources", ()) or ())
            if self._reporter.level >= _DETAIL:
                call["text"] = preview(getattr(response, "text", ""))
            if grounded and not getattr(response, "grounded", False):
                # Asked for grounding, didn't get it. The run degrades to
                # ungrounded discovery rather than losing every link, but that is
                # a real change in what the results mean, so it is never silent.
                call["grounding_unsupported"] = True
                self._reporter.event(
                    TOOL_ERROR, tool=self._name, method="generate",
                    error="grounding requested but this provider did not perform it; "
                          "links are unverified",
                )
            return response


class ObservedSearchClient(_Observed):
    """SearchClient proxy. How many results a query got back is the one number
    that says which grounding path a run actually took: zero means discovery fell
    through to the LLM's own grounding (or to none at all), which changes what the
    results mean — see tools/clients/opportunity_llm.py's resolution order."""

    async def search(self, query: str, limit: int = 10) -> list:
        fields = {"limit": limit}
        if self._reporter.level >= _DETAIL:
            fields["query"] = preview(query, 120)
        with self._call("search", **fields) as call:
            results = await acall(self._inner.search, query, limit=limit)
            call["results"] = len(results or ())
            if self._reporter.level >= _DETAIL and results:
                call["urls"] = [
                    result.get("url", "") for result in results if isinstance(result, dict)
                ]
            return results


class ObservedGSCClient(_Observed):
    async def search_analytics(self, site_url: str, days: int = 28, row_limit: int = 500):
        with self._call("search_analytics", days=days) as call:
            rows = await acall(
                self._inner.search_analytics, site_url=site_url, days=days, row_limit=row_limit
            )
            call["rows"] = len(rows or ())
            return rows


class ObservedAnalyticsClient(_Observed):
    async def report(self, limit: int = 5) -> dict:
        with self._call("report", limit=limit) as call:
            report = await acall(self._inner.report, limit=limit)
            call["highlights"] = len((report or {}).get("highlights", ()))
            if self._reporter.level >= _DETAIL:
                call["summary"] = preview((report or {}).get("summary", ""))
            return report


class ObservedTrafficClient(_Observed):
    async def traffic_summary(self, days: int = 28) -> dict:
        with self._call("traffic_summary", days=days) as call:
            result = await acall(self._inner.traffic_summary, days=days)
            if self._reporter.level >= _DETAIL:
                call["summary"] = preview((result or {}).get("summary", ""))
            return result


class ObservedSignalSource(_Observed):
    """SignalSource proxy. Named after the signal_sources registry key, since a run
    can have any number of signals and they are collected in one concurrent gather
    — without the name, interleaved events say nothing about which input is slow or
    which one came back empty.

    The result may legitimately be a bare summary string or None (see
    agent/schemas/signal.py's normalize_signal), and this proxy runs *before* that
    normalization, so it must not assume a dict."""

    async def collect(self, context: dict) -> dict:
        with self._call("collect") as call:
            result = await acall(self._inner.collect, context)
            data = result if isinstance(result, dict) else {}
            call["facts"] = len(data.get("facts") or ())
            call["items"] = len(data.get("items") or ())
            if self._reporter.level >= _DETAIL:
                summary = result if isinstance(result, str) else data.get("summary", "")
                call["summary"] = preview(summary)
            return result


class ObservedOpportunitySource(_Observed):
    """OpportunitySource proxy. Named after the discovery_sources registry key, so
    with several sources configured (and especially under the parallel_by_source
    fan-out, where branches interleave) each line says which source it came from."""

    async def discover(self, context: dict) -> list:
        with self._call("discover") as call:
            opportunities = await acall(self._inner.discover, context)
            call["found"] = len(opportunities or ())
            if self._reporter.level >= _DETAIL and opportunities:
                call["topics"] = [item.get("topic", "") for item in opportunities if isinstance(item, dict)]
            return opportunities


def observe_tools(tools, reporter):
    """Wrap every client in a Tools bundle for reporting, returning a new Tools.
    Called at the AgentRunner boundary rather than inside ToolsManager so it covers
    an explicitly injected Tools(...) too — a caller passing their own tools still
    gets a followable run, and tests that inject fakes still see them instrumented.

    Uses dataclasses.replace rather than constructing a Tools directly, for two
    reasons: importing Tools here would close an import cycle (graph.pipeline needs
    observed_node from this package), and replace() carries through any field Tools
    grows later without this function having to learn about it.

    Returns the bundle unchanged when reporting is off, so a non-verbose run has no
    proxies in the call path at all.

    One granularity note, true of the LLM and the search client alike: a discovery
    source is handed its clients at construction (ToolsManager, before a reporter
    exists), so the LLM and search calls a source makes *inside* discover() are
    reported as that one `discover` event rather than individually. The proxies
    below cover every call made through the Tools bundle itself.
    """
    if getattr(reporter, "level", 0) < 1:
        return tools
    return replace(
        tools,
        gsc=ObservedGSCClient(tools.gsc, reporter, "gsc"),
        analytics=ObservedAnalyticsClient(tools.analytics, reporter, "analytics"),
        traffic=ObservedTrafficClient(tools.traffic, reporter, "traffic"),
        llm=ObservedLLMClient(tools.llm, reporter, "llm"),
        search=ObservedSearchClient(tools.search, reporter, "search"),
        discovery_sources={
            name: ObservedOpportunitySource(source, reporter, name)
            for name, source in tools.discovery_sources.items()
        },
        signals={
            name: ObservedSignalSource(signal, reporter, name)
            for name, signal in tools.signals.items()
        },
    )


def observed_node(reporter, name: str, run):
    """Wrap a pipeline stage's run() for reporting (see agent/graph/pipeline.py).

    Stage events come from here rather than from AgentRunner's
    graph.stream(...) loop, which is the other obvious hook. The stream only yields
    accumulated state *after* each super-step, which gives three things this needs
    to avoid: no stage_start (so a user watching a slow grounded discovery call
    sees nothing until it's already finished — the opposite of "what is happening
    now"), no true per-node timing, and no way to tell concurrent fan-out branches
    apart when several nodes complete in one super-step. Wrapping the node callable
    gets all three, and costs one line in build_graph.

    Returns the original callable untouched when reporting is off.

    The wrapper is async and goes through async_utils.call for the same reason the
    tool proxies do: a stage may be `async def` (all the built-in ones are) or a
    plain sync callable (a hand-registered node, a test double), and neither the
    graph nor this function should care which.
    """
    if getattr(reporter, "level", 0) < 1:
        return run

    async def observed(state):
        # The parallel_by_source fan-out invokes one node per source with its own
        # {"source_name", "context"} payload instead of the full graph state — label
        # the branch so interleaved concurrent events stay tellable apart.
        branch = state.get("source_name") if isinstance(state, dict) else None
        fields = {"stage": name}
        if branch:
            fields["branch"] = branch

        reporter.event(STAGE_START, **fields)
        started = time.perf_counter()
        try:
            result = await acall(run, state)
        except Exception as exc:  # noqa: BLE001 - reported, then re-raised untouched
            reporter.event(
                STAGE_ERROR,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
                **fields,
            )
            raise
        reporter.event(
            STAGE_END,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            **fields,
            **_summarize(result, reporter.level),
        )
        return result

    return observed


def _summarize(result, level: int) -> dict:
    """Pull the interesting bits out of whatever a stage returned, generically —
    which is what keeps stages themselves free of reporting code. Every stage
    returns only the state keys it changed, so this reads that diff rather than
    knowing anything stage-specific.

    Tool failures are surfaced at level 1, not 2: the pipeline degrades rather than
    aborting, so a failed analytics call leaves a run looking successful apart from
    a tool_errors entry buried in the final JSON. That's precisely what someone
    watching a run needs told about immediately.
    """
    if not isinstance(result, dict):
        return {}

    summary: dict = {}
    phase = result.get("phase")
    if phase:
        summary["phase"] = phase
    if result.get("error"):
        summary["error"] = result["error"]

    working = result.get("working")
    if isinstance(working, dict):
        if "opportunities" in working:
            summary["opportunities"] = len(working["opportunities"])
        errors = working.get("tool_errors") or []
        if errors:
            summary["tool_errors"] = len(errors)
        if level >= _DETAIL:
            decision = working.get("channel_decision")
            if decision:
                summary["channel"] = decision.get("chosen")
                summary["reason"] = preview(decision.get("reason", ""), 120)
            if working.get("chosen_keyword"):
                summary["keyword"] = working["chosen_keyword"]

    # The parallel fan-out's per-branch return shape (see stages/discover.py's
    # DiscoverSourceStage), which writes to discover_results instead of working.
    for entry in result.get("discover_results", []) or []:
        summary["opportunities"] = len(entry.get("opportunities", ()))
        if entry.get("tool_errors"):
            summary["tool_errors"] = len(entry["tool_errors"])

    return summary
