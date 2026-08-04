from ...schemas.channel import Channel
from ...schemas.io import AgentState
from ...utils.tool_errors import record_tool_error
from ..tools import Tools


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
        # No GSC data and no seed keyword: fall back to a real, current highlight
        # from the analytics client rather than a generic placeholder.
        highlight = highlights[0]
        topic = " ".join(highlight["label"].split()[:6]).rstrip(".,;:")
        return topic, {
            "reason": (
                "No GSC or seed keyword available; using a recent highlight "
                f"as the topic ({highlight['url']})."
            ),
            "source_highlight": highlight,
        }
    if opportunities:
        # No GSC, no seed keyword, no analytics highlight: fall back to whatever
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
    discover -> choose_channel chain: the analytics/traffic calls it makes don't
    depend on channel or discovered opportunities (unlike the GSC/keyword-picking
    part of analyze, which does), so there's no reason to wait for discovery to
    finish before making them.

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

    def run(self, state: AgentState) -> dict:
        tool_errors: list = []
        try:
            analytics_report = self.tools.analytics.report(limit=self.config.analytics_highlights_limit)
        except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
            record_tool_error(tool_errors, "analytics", "analyze", exc)
            analytics_report = {}

        try:
            traffic_summary = self.tools.traffic.traffic_summary().get("summary", "")
        except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
            record_tool_error(tool_errors, "traffic", "analyze", exc)
            traffic_summary = ""

        return {
            "analyze_context": {
                "analytics_summary": analytics_report.get("summary", ""),
                "analytics_highlights": analytics_report.get("highlights", []),
                "traffic_summary": traffic_summary,
                "tool_errors": tool_errors,
            }
        }


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

    def run(self, state: AgentState) -> dict:
        """Reads: working.channel if ChooseChannelStage set one (see
        agent/graph/pipeline.py — only present when config.discovery_sources is
        configured), else input.channel/config.default_channel; input.gsc_domain,
        input.seed_keyword; working.opportunities if DiscoverStage ran; and, when
        AnalyzeContextStage ran (config.discovery_sources non-empty), its output
        on state["analyze_context"] instead of fetching analytics/traffic itself.
        Writes: phase="analyze"; working.analytics_summary + working.analytics_highlights
        + working.traffic_summary (always, all channels — growth context and, absent
        other signal, a topic fallback for DraftStage); for site_article/
        external_article also working.gsc_rows, working.chosen_keyword,
        working.chosen_keyword_row (the target keyword/topic DraftStage writes for);
        working.tool_errors (appends one ToolError per client that raised).

        Every client call below (GSC, analytics, traffic) is independently
        degrade-don't-abort: one failing never fails the run or blocks the other
        two — it just falls back to an empty/default value and records why, same
        principle as DiscoverStage. There's always a usable (if less informed)
        keyword/topic to draft from, even with every one of these unavailable —
        see _pick_keyword's fallback chain.
        """
        input_ = state["input"]
        working = dict(state.get("working", {}))
        channel = working.get("channel") or input_.get("channel", self.config.default_channel)
        tool_errors = list(working.get("tool_errors", []))

        analyze_context = state.get("analyze_context")
        if analyze_context is not None:
            # AnalyzeContextStage already made these calls concurrently with
            # discovery — use its result instead of fetching again.
            working["analytics_summary"] = analyze_context["analytics_summary"]
            working["analytics_highlights"] = analyze_context["analytics_highlights"]
            working["traffic_summary"] = analyze_context["traffic_summary"]
            tool_errors.extend(analyze_context["tool_errors"])
        else:
            try:
                analytics_report = self.tools.analytics.report(limit=self.config.analytics_highlights_limit)
            except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
                record_tool_error(tool_errors, "analytics", "analyze", exc)
                analytics_report = {}
            working["analytics_summary"] = analytics_report.get("summary", "")
            working["analytics_highlights"] = analytics_report.get("highlights", [])

            try:
                working["traffic_summary"] = self.tools.traffic.traffic_summary().get("summary", "")
            except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
                record_tool_error(tool_errors, "traffic", "analyze", exc)
                working["traffic_summary"] = ""

        if channel == Channel.ENGAGEMENT_COMMENT:
            working["chosen_keyword"] = None
            working["gsc_rows"] = []
            working["chosen_keyword_row"] = None
        else:
            # gsc_domain can be empty when discovery (not the caller) decided this
            # run should be a keyword-driven channel — degrade to no GSC rows
            # rather than call the client with an empty site_url.
            gsc_domain = input_.get("gsc_domain")
            rows = []
            if gsc_domain:
                try:
                    rows = self.tools.gsc.search_analytics(site_url=gsc_domain)
                except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
                    record_tool_error(tool_errors, "gsc", "analyze", exc)
            keyword, source_row = _pick_keyword(
                rows, input_.get("seed_keyword"), working["analytics_highlights"],
                working.get("opportunities"),
            )
            working["gsc_rows"] = rows
            working["chosen_keyword"] = keyword
            working["chosen_keyword_row"] = source_row

        working["tool_errors"] = tool_errors
        return {"phase": "analyze", "working": working}
