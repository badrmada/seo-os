from __future__ import annotations

from typing import Literal, TypedDict

_VALID_INTENTS = {"commercial", "informational", "mixed", "discussion"}
_VALID_CHANNEL_HINTS = {"site_article", "external_article", "engagement_comment"}


class Opportunity(TypedDict):
    """Normalized shape every discovery source (AgentConfig.discovery_sources, see
    tools/base.py's OpportunitySource Protocol) returns, so
    agent/graph/stages/choose_channel.py's ChooseChannelStage never needs to care
    which source produced it — merged from every configured source by
    agent/graph/stages/discover.py's DiscoverStage into working.opportunities."""

    source: str                              # discovery_sources registry key, e.g. "trends", "reddit", "echooers_ideas"
    topic: str
    signal_strength: float                   # normalized 0-1, comparable across sources
    intent: Literal["commercial", "informational", "mixed", "discussion"]
    suggested_channel_hint: str | None        # a Channel value, or None
    raw: dict                                # source-specific payload, kept for prompt context
    reason: str                              # human-readable "why this is worth doing"


def normalize_opportunity(item: dict, *, source: str) -> Opportunity | None:
    """Coerces one raw item (from *any* OpportunitySource — mock, llm, or a
    tenant's own "custom" class) into a valid Opportunity, or returns None if it's
    unusable (not a dict, or no topic at all). A provider is free-form in what it
    computes internally, but every field that crosses this boundary is validated
    here rather than trusted as-is — one malformed item degrades to "dropped", not
    a crash or a silently wrong cross-source score in
    agent/graph/stages/choose_channel.py (which sums signal_strength per
    suggested_channel_hint across every source's opportunities, so a bad value
    there corrupts that decision for every source, not just the one that produced
    it). `source` always wins over anything an item claims for its own "source"
    key, so a misbehaving custom provider can't misattribute its output either.
    """
    if not isinstance(item, dict):
        return None

    topic = str(item.get("topic") or "").strip()
    if not topic:
        return None

    try:
        signal_strength = float(item.get("signal_strength", 0.5))
    except (TypeError, ValueError):
        signal_strength = 0.5
    signal_strength = max(0.0, min(1.0, signal_strength))

    intent = item.get("intent")
    if intent not in _VALID_INTENTS:
        intent = "informational"

    suggested_channel_hint = item.get("suggested_channel_hint")
    if suggested_channel_hint not in _VALID_CHANNEL_HINTS:
        suggested_channel_hint = None

    return {
        "source": source,
        "topic": topic,
        "signal_strength": signal_strength,
        "intent": intent,
        "suggested_channel_hint": suggested_channel_hint,
        "raw": dict(item),
        "reason": str(item.get("reason", "")),
    }


class ToolError(TypedDict):
    """One external-call failure, recorded on working.tool_errors instead of only
    logged, so it's visible in the final run output (see
    agent/managers/run_manager.py's AgentRunner.run(), which surfaces it as
    discovery.tool_errors) rather than only stdout/stderr. Currently only written
    by agent/graph/stages/discover.py's DiscoverStage (node="discover") when one
    OpportunitySource raises; the shape is generic so other stages can adopt the
    same degrade-don't-abort pattern for their own tool calls later."""

    tool: str            # discovery_sources/signal_sources key, or
                          # "search_performance"/"analytics"/"traffic"/"llm"
    node: str             # which graph node triggered it
    error_type: str       # exception class name
    message: str          # str(exception), truncated
    occurred_at: str       # ISO timestamp
