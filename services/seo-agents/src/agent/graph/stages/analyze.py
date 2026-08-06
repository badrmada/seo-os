import asyncio

from ...schemas.channel import Channel
from ...schemas.io import AgentState
from ...schemas.signal import empty_signal, normalize_signal
from ...utils.async_utils import call as acall
from ...utils.tool_errors import record_tool_error
from ..tools import Tools


def signal_context(input_: dict, config, channel: str = "") -> dict:
    """What a signal is told about the run when it's asked to collect (see
    tools/base.py's SignalSource) — the same steering context a discovery source
    gets from DiscoverStage, plus the site the run is about.

    `site_url` comes from the config, since the site is a property of the tenant
    and doesn't change between runs; `input.site_url` overrides it for a caller
    driving several sites through one config. It used to be read off
    `input.gsc_domain`, which handed every signal a *Google Search Console
    property identifier* ("sc-domain:example.com") under a name promising a URL —
    so a crawler or a sitemap reader taking it at face value would have fetched a
    string that isn't an address.

    `channel` is "" when this runs concurrently with ChooseChannelStage, which
    hasn't decided one yet — the honest answer at that point, and the reason a
    signal must treat every key here as optional.
    """
    return {
        "seed_keyword": input_.get("seed_keyword", ""),
        "context_text": input_.get("context_text", ""),
        "site_url": input_.get("site_url") or getattr(config, "site_url", "") or "",
        "channel": channel,
    }


def _fold_signals(names: list[str], results: list, tool_errors: list) -> dict:
    """Turn the gathered collect() results back into {name: Signal}, recording a
    tool error for each one that raised. Shared by the two paths below so a signal
    behaves identically whether or not AnalyzeContextStage is in the pipeline.

    **Every configured name gets an entry**, including one whose signal failed —
    see agent/schemas/signal.py's empty_signal for why the keys have to be a
    function of the config rather than of what happened at run time.
    """
    signals: dict = {}
    for name, result in zip(names, results):
        if isinstance(result, BaseException):
            record_tool_error(tool_errors, name, "analyze", result)
            signals[name] = empty_signal()
            continue
        try:
            signals[name] = normalize_signal(result)
        except ValueError as exc:  # a signal that answered in a shape nothing can read
            record_tool_error(tool_errors, name, "analyze", exc)
            signals[name] = empty_signal()
    return signals


def _raise_if_cancelled(results) -> None:
    """A cancellation (the run deadline expiring, the caller giving up) is not a
    tool failure and must not be degraded into one — it has to keep unwinding.
    Only ordinary exceptions become tool_errors."""
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result


async def collect_context(tools: Tools, config, input_: dict, channel: str = "") -> dict:
    """Every channel-independent input this agent reads, fetched concurrently:
    analytics, traffic, and each configured signal (Tools.signals).

    One function with two callers — AnalyzeContextStage, which runs it as its own
    node alongside discovery, and AnalyzeStage, which runs it inline when that node
    isn't in the pipeline. Sharing it is what makes a signal behave identically in
    both graph shapes; when these were two hand-written versions, only the
    concurrent one had been generalized and the difference was invisible until a
    tenant configured discovery.

    Nothing here depends on the channel or on what discovery found, which is why it
    can run before either is known — `channel` is passed to the signals purely as
    context, and is "" on the concurrent path.

    Every call is independently degrade-don't-abort: one failing never fails the
    run or blocks the others, it just falls back to an empty value and records a
    tool error. gather preserves argument order, so tool_errors reads analytics,
    traffic, then signals in config order regardless of which failed first in
    wall-clock time.
    """
    signal_names = list(tools.signals)
    context = signal_context(input_, config, channel)
    results = await asyncio.gather(
        acall(tools.analytics.report, limit=config.analytics_highlights_limit),
        acall(tools.traffic.traffic_summary),
        *(acall(tools.signals[name].collect, context) for name in signal_names),
        return_exceptions=True,
    )
    _raise_if_cancelled(results)
    analytics_report, traffic_result = results[0], results[1]

    tool_errors: list = []
    if isinstance(analytics_report, BaseException):
        record_tool_error(tool_errors, "analytics", "analyze", analytics_report)
        analytics_report = {}
    if isinstance(traffic_result, BaseException):
        record_tool_error(tool_errors, "traffic", "analyze", traffic_result)
        traffic_summary = ""
    else:
        traffic_summary = (traffic_result or {}).get("summary", "")

    return {
        "analytics_summary": analytics_report.get("summary", ""),
        "analytics_highlights": analytics_report.get("highlights", []),
        "traffic_summary": traffic_summary,
        "signals": _fold_signals(signal_names, results[2:], tool_errors),
        "tool_errors": tool_errors,
    }


def _pick_keyword(
    rows: list[dict], seed_keyword: str, highlights: list[dict] = None,
    opportunities: list[dict] = None,
) -> tuple[str, dict]:
    """Prefer a 'striking distance' query (position 5-20, i.e. close to page 1 but not there yet)."""
    # Rows arrive from the client already classified and sorted by score (desc),
    # so a rising striking-distance query outranks a flat one automatically.
    striking = [r for r in rows if r.get("opportunity") == "striking_distance"]
    if striking:
        return striking[0]["query"], striking[0]
    if seed_keyword:
        return seed_keyword, None
    if rows:
        row = max(rows, key=lambda r: r.get("clicks", 0))
        return row["query"], row
    if highlights:
        # No rank data and no seed keyword: fall back to a real, current highlight
        # from the analytics client rather than a generic placeholder.
        highlight = highlights[0]
        topic = " ".join(highlight["label"].split()[:6]).rstrip(".,;:")
        return topic, {
            "reason": (
                "No search-performance data or seed keyword available; using a recent highlight "
                f"as the topic ({highlight['url']})."
            ),
            "source_highlight": highlight,
        }
    if opportunities:
        # No rank data, no seed keyword, no analytics highlight: fall back to whatever
        # DiscoverStage found (see stages/discover.py), highest signal first —
        # still a real, current topic rather than a generic placeholder.
        top = max(opportunities, key=lambda o: o.get("signal_strength", 0) or 0)
        return top.get("topic", "your topic"), {
            "reason": top.get("reason") or f"Chosen from a discovered opportunity ({top.get('source')}).",
            "source_opportunity": top,
        }
    return "your topic", None


class AnalyzeContextStage:
    """Only added to the pipeline when config.discovery_sources is non-empty (see
    agent/graph/pipeline.py:build_graph) — a zero-config tenant never runs this
    stage, since there's nothing for it to run concurrently with. Runs as its own
    LangGraph node, a direct child of START in parallel with the
    discover -> choose_channel chain: the analytics/traffic/signal calls it makes
    don't depend on channel or discovered opportunities (unlike the
    search-performance/keyword-picking part of analyze, which does), so there's no reason to wait
    for discovery to finish before making them. All of them are one gather — see
    collect_context above, which is also what AnalyzeStage runs when this node
    isn't in the pipeline.

    Writes to state["analyze_context"] rather than state["working"] — not
    working directly, because this node and the discover chain both being direct
    children of START means they run in the same LangGraph superstep, and two
    nodes returning the same "working" key in one superstep is an unresolvable
    conflict (working has no merge reducer, unlike discover_results' Annotated
    list — see schemas/io.py). AnalyzeStage reads state["analyze_context"] (its
    sole reader) and folds it into working itself, once it has both this and
    choose_channel's output — a plain merge, safe because only one node (analyze)
    ever writes working at that point.
    """

    def __init__(self, tools: Tools, config) -> None:
        self.tools = tools
        self.config = config

    async def run(self, state: AgentState) -> dict:
        # channel is left "" rather than guessed: this node runs alongside
        # ChooseChannelStage, so at this point nobody knows it yet.
        return {"analyze_context": await collect_context(self.tools, self.config, state["input"])}


class AnalyzeStage:
    """First pipeline step: analyze -> draft -> self_qa.

    Instantiated with the run's Tools/AgentConfig (see
    agent/graph/pipeline.py:build_graph); `.run(state)` is registered as the
    "analyze" LangGraph node. Also called directly (bypassing the graph) by
    agent/managers/run_manager.py's AgentRunner.preview_prompt(), since a dry-run
    prompt preview still needs real analyze() output.
    """

    def __init__(self, tools: Tools, config) -> None:
        self.tools = tools
        self.config = config

    async def run(self, state: AgentState) -> dict:
        """Reads: working.channel if ChooseChannelStage set one (see
        agent/graph/pipeline.py — only present when config.discovery_sources is
        configured), else input.channel/config.default_channel; input.seed_keyword; working.opportunities if DiscoverStage ran; and, when
        AnalyzeContextStage ran (config.discovery_sources non-empty), its output
        on state["analyze_context"] instead of collecting that context itself.
        Writes: phase="analyze"; working.analytics_summary + working.analytics_highlights
        + working.traffic_summary + working.signals (always, all channels — growth
        context and, absent other signal, a topic fallback for DraftStage); for
        site_article/external_article also working.search_performance_rows, working.chosen_keyword,
        working.chosen_keyword_row (the target keyword/topic DraftStage writes for);
        working.tool_errors (appends one ToolError per client that raised).

        Every client call below (search performance, analytics, traffic, and each configured
        signal) is independently degrade-don't-abort: one failing never fails the
        run or blocks the others — it just falls back to an empty/default value and
        records why, same principle as DiscoverStage. There's always a usable (if
        less informed) keyword/topic to draft from, even with every one of these
        unavailable — see _pick_keyword's fallback chain.
        """
        input_ = state["input"]
        working = dict(state.get("working", {}))
        channel = working.get("channel") or input_.get("channel", self.config.default_channel)
        tool_errors = list(working.get("tool_errors", []))

        analyze_context = state.get("analyze_context")
        if analyze_context is None:
            # AnalyzeContextStage isn't in this pipeline (no discovery configured,
            # or this is preview_prompt calling the stage directly), so make the
            # same calls here.
            analyze_context = await collect_context(
                self.tools, self.config, input_, channel,
            )
        # Otherwise AnalyzeContextStage already made them concurrently with
        # discovery — use its result instead of fetching again. `signals` is read
        # with a default because a caller constructing an analyze_context by hand
        # predates it and shouldn't crash for omitting it.
        working["analytics_summary"] = analyze_context["analytics_summary"]
        working["analytics_highlights"] = analyze_context["analytics_highlights"]
        working["traffic_summary"] = analyze_context["traffic_summary"]
        working["signals"] = analyze_context.get("signals", {})
        tool_errors.extend(analyze_context["tool_errors"])

        if channel == Channel.ENGAGEMENT_COMMENT:
            working["chosen_keyword"] = None
            working["search_performance_rows"] = []
            working["chosen_keyword_row"] = None
        else:
            # No "is a site configured?" guard here any more: the client knows
            # which site it is about (see tools/base.py's SearchPerformanceClient),
            # and the default provider returns no rows rather than needing one. A
            # provider that can't answer degrades to [] exactly like a failing one,
            # and _pick_keyword's chain covers both.
            try:
                # googleapiclient is httplib2-based and sync-only; acall runs it in
                # a worker thread so it can't stall the event loop.
                rows = await acall(self.tools.search_performance.search_analytics)
            except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
                record_tool_error(tool_errors, "search_performance", "analyze", exc)
                rows = []
            keyword, source_row = _pick_keyword(
                rows, input_.get("seed_keyword"), working["analytics_highlights"],
                working.get("opportunities"),
            )
            working["search_performance_rows"] = rows
            working["chosen_keyword"] = keyword
            working["chosen_keyword_row"] = source_row

        working["tool_errors"] = tool_errors
        return {"phase": "analyze", "working": working}
