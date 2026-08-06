"""Turning raw search-performance rows into rows the agent can decide from.

Every provider of the SearchPerformanceClient kind (tools/base.py) produces the
same four raw numbers per query — clicks, impressions, ctr, position — because
that is what every search-performance source has, whether it's Google Search
Console's API, Bing Webmaster Tools, a rank tracker's export, or a CSV someone
downloads once a month. What the *agent* needs on top of those is the judgement:
which rows are winnable, which are moving, what angle they want, and which one to
pick first.

That judgement lives here rather than inside the Google client, for one concrete
reason: it is the valuable part and it is not Google-specific. When it lived in
GoogleSearchConsoleClient, "use a different rank source" meant reimplementing
striking-distance classification and scoring — in Jinja2, for the templated
provider — and getting a subtly different answer. Now a provider supplies the
four numbers it has and gets the same classification every other provider gets.

Nothing here does I/O, so it is also the part that is cheap to test directly.
"""

from __future__ import annotations

import math

# Crude intent hints so the writer can pick an angle (list-post vs how-to vs comparison).
_COMMERCIAL = ("best", "top", "review", "vs", "versus", "price", "cheap", "buy", "deal", "alternative")
_INFORMATIONAL = ("how to", "how do", "what is", "why", "guide", "tutorial", "examples", "ideas")

__all__ = ["enrich_rows", "enrich_row", "normalize_raw_row"]


def enrich_rows(
    rows: list[dict], *, prior: dict = None, top_pages: dict = None, row_limit: int = 500,
) -> list[dict]:
    """Classify, score and sort a provider's raw rows.

    `prior` maps query -> the same query's row in the previous period, and
    `top_pages` maps query -> the URL currently earning it the most clicks. Both
    are optional because not every source has them: a provider that can't answer
    "how did this do last month?" still gets opportunity/intent/score, just with
    trend="flat" and no top_page. Degrading a *derived* field is not the same as
    failing, and a source shouldn't have to fake data to take part.

    Rows come back sorted by score, highest first — which is what lets
    agent/graph/stages/analyze.py's _pick_keyword take `striking[0]` and get the
    best striking-distance row rather than an arbitrary one.
    """
    prior = prior or {}
    top_pages = top_pages or {}
    enriched = [
        enrich_row(row, prior.get(row.get("query")), top_pages.get(row.get("query")))
        for row in rows
    ]
    enriched.sort(key=lambda row: row["score"], reverse=True)
    return enriched[:row_limit]


def enrich_row(row: dict, prior: dict = None, top_page: str = None) -> dict:
    """One row, enriched in place-ish (a copy) with the derived decision signals:

        opportunity : structural lever  -> striking_distance | low_ctr | defend | low_priority
        trend       : momentum vs prior -> rising | flat | decaying
        intent      : angle hint        -> commercial | informational | mixed
        top_page    : the URL currently ranking for this query (improve vs. create-new)
        score       : combined priority; enrich_rows sorts on it
        reason      : one-line rationale, safe to drop straight into the draft prompt
    """
    row = normalize_raw_row(row)
    position, impressions, ctr = row["position"], row["impressions"], row["ctr"]

    opportunity = _opportunity(position, impressions, ctr)
    trend, impressions_delta = _trend(impressions, prior)
    intent = _intent(row["query"])

    row["opportunity"] = opportunity
    row["trend"] = trend
    row["intent"] = intent
    row["top_page"] = top_page
    row["impressions_delta_pct"] = impressions_delta
    row["score"] = _score(opportunity, trend, impressions, position)
    row["reason"] = _reason(opportunity, trend, intent, position, impressions, top_page)
    return row


def normalize_raw_row(row: dict) -> dict:
    """Coerce one raw row into the four numbers the classification needs.

    A templated provider's row comes out of a tenant's own JSON via Jinja2, so
    `position` arriving as the string "11.2" is routine rather than exceptional —
    and `float("11.2")` in the classifier would work while `"11.2" >= 5` raises.
    Coercing once here means every provider is held to the same shape and no
    caller has to know which ones were already clean.
    """
    if not isinstance(row, dict):
        raise ValueError(f"a search-performance row must be an object, got {type(row).__name__}")
    query = str(row.get("query") or "").strip()
    if not query:
        raise ValueError(f'a search-performance row needs a non-empty "query": {row!r}')
    return {
        **row,
        "query": query,
        "clicks": _number(row.get("clicks"), "clicks", query),
        "impressions": _number(row.get("impressions"), "impressions", query),
        "ctr": _number(row.get("ctr"), "ctr", query),
        "position": _number(row.get("position"), "position", query),
    }


def _number(value, field: str, query: str):
    """Coerce to a number, but leave one that already *is* a number alone.

    `float(42)` would be correct and would also turn every integer click count in
    the returned rows into `42.0` — visible in the run's own output and in the
    prompt. Coercion is for the templated provider's strings, not a reformatting
    pass over the Google client's clean data.
    """
    if value is None or value == "":
        return 0
    if isinstance(value, bool):  # bool is an int subclass; a boolean here is a mistake
        raise ValueError(f"search-performance row {query!r} has a boolean {field}")
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"search-performance row {query!r} has a non-numeric {field}: {value!r}"
        ) from None


def _opportunity(position: float, impressions: float, ctr: float) -> str:
    if 5 <= position <= 20:
        return "striking_distance"          # winnable: small push clears page 1
    if impressions >= 800 and ctr < 0.02:
        return "low_ctr"                     # seen but not clicked: title/meta fix
    if position <= 3:
        return "defend"                      # already strong: protect, refresh
    return "low_priority"                    # too deep or too little demand


def _trend(impressions: float, prior: dict) -> tuple[str, float | None]:
    if not prior or prior.get("impressions", 0) == 0:
        return "flat", None
    delta = (impressions - prior["impressions"]) / prior["impressions"]
    if delta >= 0.15:
        return "rising", delta
    if delta <= -0.15:
        return "decaying", delta
    return "flat", delta


def _intent(query: str) -> str:
    lowered = query.lower()
    commercial = any(term in lowered for term in _COMMERCIAL)
    informational = any(term in lowered for term in _INFORMATIONAL)
    if commercial and not informational:
        return "commercial"
    if informational and not commercial:
        return "informational"
    return "mixed"


def _score(opportunity: str, trend: str, impressions: float, position: float) -> float:
    opp_weight = {"striking_distance": 1.0, "low_ctr": 0.45, "defend": 0.2, "low_priority": 0.05}
    trend_mult = {"rising": 1.4, "flat": 1.0, "decaying": 0.7}
    # Reward demand (impressions), discount distance (position). Tune freely.
    base = impressions * opp_weight[opportunity] / math.sqrt(max(position, 1))
    return round(base * trend_mult[trend], 2)


def _reason(opportunity, trend, intent, position, impressions, top_page) -> str:
    lever = {
        "striking_distance": f"ranks ~{position:.0f}, close to page 1 — on-page + depth should push it up",
        "low_ctr": f"{impressions:,.0f} impressions but few clicks — rewrite title/meta to match intent",
        "defend": f"already ranks ~{position:.0f} — refresh to hold the position",
        "low_priority": "low demand or too far back — deprioritize",
    }[opportunity]
    momentum = {
        "rising": " Trending up vs. last period.",
        "decaying": " Losing ground vs. last period — worth acting now.",
        "flat": "",
    }[trend]
    page_note = (
        f" A page already ranks ({top_page}); improve it rather than writing a duplicate."
        if top_page else " No strong page yet; a new article can own this."
    )
    return f"[{intent}] {lever}.{momentum}{page_note}"
