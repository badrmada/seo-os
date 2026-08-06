from __future__ import annotations

from typing import TypedDict

# The AgentConfig.signal_sources names that select one of the three built-in
# signal kinds (each with its own Protocol in tools/base.py and its own slot on
# Tools) rather than a generic signal.
#
# It lives here, in a module that imports nothing but `typing`, because three
# packages need it — agent/managers/providers.py to attribute a config entry to
# the right kind, agent/managers/tools_manager.py to route it, and
# agent/config/loader.py to know which names can appear in `signals` in a prompt
# template. Any other home makes at least one of those a deferred import to avoid
# closing a cycle (see src/tests/test_imports.py).
BUILTIN_SIGNAL_NAMES = ("gsc", "traffic", "analytics")


class Signal(TypedDict):
    """Normalized shape every signal input (AgentConfig.signal_sources, see
    tools/base.py's SignalSource Protocol) contributes, so a stage and a prompt
    template can read a signal they have never heard of — collected by
    agent/graph/stages/analyze.py into working.signals, keyed by the signal's
    configured name.

    Deliberately as free-form as AppAnalyticsClient/SiteTrafficClient already are,
    and for the same reason: what a trends feed, a rank tracker, a crawler or an
    MCP server actually knows about varies completely, so the system never assumes
    a vocabulary. `summary` is prose the prompt uses as-is; `facts` and `items`
    are for a stage or a tenant template that knows what it asked for.
    """

    summary: str          # free text, tenant/provider-authored, dropped into the
                           # prompt as-is — the system never parses it
    facts: dict           # provider-specific named values (counts, rates, dates)
    items: list[dict]     # provider-specific rows, most-relevant first


def normalize_signal(result) -> Signal:
    """Coerce whatever a signal's collect() returned into a valid Signal.

    A signal is the most open-ended thing a tenant can plug in — the whole point
    of the step is that we don't know what it is — so what crosses this boundary
    is validated rather than trusted, exactly like normalize_opportunity does for
    discovery. The difference is what happens to a bad value: an opportunity is
    one of many and gets dropped, but a signal *is* the whole contribution, so a
    malformed one raises and is recorded as a tool error by the caller instead of
    being silently flattened to nothing. A degrade nothing records is a bug.

    Two conveniences are genuine, not sloppiness:

      - `None` means "ran, nothing to report" — the same thing an empty analytics
        summary already means, not a failure.
      - a bare string is read as the summary, since summary is the only field a
        signal must produce and returning one is the obvious mistake to forgive.
    """
    if result is None:
        return {"summary": "", "facts": {}, "items": []}
    if isinstance(result, str):
        return {"summary": result, "facts": {}, "items": []}
    if not isinstance(result, dict):
        raise ValueError(
            "collect() must return a dict of {summary, facts, items} (or a summary "
            f"string, or None), got {type(result).__name__}"
        )

    summary = result.get("summary", "")
    if not isinstance(summary, str):
        raise ValueError(f"collect()['summary'] must be a string, got {type(summary).__name__}")

    # `if x is None else` rather than `or`: an empty list where facts belongs is a
    # type error that happens to be falsy, and `result.get("facts") or {}` would
    # quietly turn it into a valid empty dict — hiding exactly the mistake this
    # function exists to catch.
    facts = result.get("facts")
    facts = {} if facts is None else facts
    if not isinstance(facts, dict):
        raise ValueError(f"collect()['facts'] must be an object, got {type(facts).__name__}")

    items = result.get("items")
    items = [] if items is None else items
    if not isinstance(items, list):
        raise ValueError(f"collect()['items'] must be an array, got {type(items).__name__}")

    return {
        "summary": summary,
        "facts": dict(facts),
        "items": [item for item in items if isinstance(item, dict)],
    }


def empty_signal() -> Signal:
    """What a signal that failed, or answered in a shape nothing could read,
    contributes.

    It contributes an *entry*, not nothing: working.signals always has exactly one
    key per configured signal_sources name, whatever happened to it. That is what
    lets a tenant write `{{ signals.rank_tracker.facts.striking_distance }}` and
    have it mean the same thing on every run — and it is why the templates a
    tenant writes can be validated against their own config at save time
    (agent/config/loader.py) instead of failing mid-run.

    Dropping a failed signal instead would make a prompt template's variables
    depend on whether an API happened to answer, which is the same class of bug as
    a failing analytics call changing the prompt's shape — and the reason
    AnalyzeStage degrades that one to "" rather than to a missing key.
    """
    return {"summary": "", "facts": {}, "items": []}
