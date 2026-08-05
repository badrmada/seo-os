from __future__ import annotations

import math
from datetime import date, timedelta

import google_auth_httplib2
import httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# googleapiclient's default transport has no timeout at all, so a stalled
# connection hangs the call indefinitely. On a CLI that's someone pressing
# Ctrl-C; from a queue worker it's a slot held forever. One search_analytics()
# makes several requests (current window, prior window, page mapping), so this
# bounds each one, not the method.
DEFAULT_TIMEOUT_SECONDS = 30.0

# GSC data is only complete up to ~2-3 days ago; querying "today" returns partial rows.
DATA_LAG_DAYS = 3

# Crude intent hints so the writer can pick an angle (list-post vs how-to vs comparison).
_COMMERCIAL = ("best", "top", "review", "vs", "versus", "price", "cheap", "buy", "deal", "alternative")
_INFORMATIONAL = ("how to", "how do", "what is", "why", "guide", "tutorial", "examples", "ideas")


class GoogleSearchConsoleClient:
    """Real GSCClient. Implements the GSCClient Protocol:

        search_analytics(site_url, days=28, row_limit=500) -> list[dict]

    Every returned row keeps the raw GSC fields the pipeline already reads
    (query, clicks, impressions, ctr, position) and adds derived decision
    signals so the agent reasons about *why* a row matters:

        opportunity : structural lever  -> striking_distance | low_ctr | defend | low_priority
        trend       : momentum vs prior -> rising | flat | decaying
        intent      : angle hint        -> commercial | informational | mixed
        top_page    : the URL currently ranking for this query (improve vs. create-new)
        score       : combined priority, rows come back sorted by it (desc)
        reason      : one-line rationale, safe to drop straight into the draft prompt

    Prior-period and page-mapping calls degrade gracefully: if either fails the
    row still comes back with opportunity/intent/score, just without trend/top_page.
    """

    def __init__(
        self, key_file: str = "service_account.json", scopes=SCOPES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        creds = service_account.Credentials.from_service_account_file(key_file, scopes=scopes)
        # Built explicitly rather than via build(credentials=...) purely so the
        # underlying transport can carry a timeout — that path constructs its own
        # httplib2.Http with none.
        authorized = google_auth_httplib2.AuthorizedHttp(
            creds, http=httplib2.Http(timeout=timeout_seconds),
        )
        self.service = build("searchconsole", "v1", http=authorized, cache_discovery=False)

    def _windows(self, days: int) -> tuple[str, str, str, str]:
        """Return (cur_start, cur_end, prior_start, prior_end) as ISO date strings.

        cur_end backs off DATA_LAG_DAYS since GSC data isn't complete up to today.
        The prior window is the same length, immediately before the current one,
        so period-over-period trend compares like with like.
        """
        cur_end = date.today() - timedelta(days=DATA_LAG_DAYS)
        cur_start = cur_end - timedelta(days=days)
        prior_end = cur_start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=days)
        return (cur_start.isoformat(), cur_end.isoformat(),
                prior_start.isoformat(), prior_end.isoformat())

    # -- Protocol method ---------------------------------------------------

    def search_analytics(self, site_url: str, days: int = 28, row_limit: int = 500) -> list[dict]:
        cur_start, cur_end, prior_start, prior_end = self._windows(days)

        current = self._query(site_url, cur_start, cur_end, ["query"], row_limit)

        prior_map = self._safe_map(
            lambda: self._query(site_url, prior_start, prior_end, ["query"], row_limit),
            key="query",
        )
        page_map = self._safe_top_page(site_url, cur_start, cur_end, row_limit)

        rows = [self._enrich(r, prior_map.get(r["query"]), page_map.get(r["query"])) for r in current]
        rows.sort(key=lambda r: r["score"], reverse=True)
        return rows[:row_limit]

    # -- raw API -----------------------------------------------------------

    def _query(self, site_url, start, end, dimensions, row_limit) -> list[dict]:
        body = {"startDate": start, "endDate": end, "dimensions": dimensions, "rowLimit": row_limit}
        resp = self.service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        out = []
        for row in resp.get("rows", []):
            keys = row.get("keys", [])
            entry = dict(zip(dimensions, keys))
            entry.update(
                clicks=row.get("clicks", 0),
                impressions=row.get("impressions", 0),
                ctr=row.get("ctr", 0.0),
                position=row.get("position", 0.0),
            )
            out.append(entry)
        return out

    def _safe_map(self, fetch, key) -> dict:
        try:
            return {r[key]: r for r in fetch()}
        except Exception:
            return {}

    def _safe_top_page(self, site_url, start, end, row_limit) -> dict:
        """Map each query -> the page that earns it the most clicks."""
        try:
            rows = self._query(site_url, start, end, ["query", "page"], row_limit)
        except Exception:
            return {}
        best: dict[str, dict] = {}
        for r in rows:
            q = r["query"]
            if q not in best or r["clicks"] > best[q]["clicks"]:
                best[q] = r
        return {q: r["page"] for q, r in best.items()}

    # -- enrichment --------------------------------------------------------

    def _enrich(self, row: dict, prior: dict , top_page: str ) -> dict:
        pos, impr, ctr = row["position"], row["impressions"], row["ctr"]

        opportunity = self._opportunity(pos, impr, ctr)
        trend, impr_delta = self._trend(impr, prior)
        intent = self._intent(row["query"])

        row["opportunity"] = opportunity
        row["trend"] = trend
        row["intent"] = intent
        row["top_page"] = top_page
        row["impressions_delta_pct"] = impr_delta
        row["score"] = self._score(opportunity, trend, impr, pos)
        row["reason"] = self._reason(opportunity, trend, intent, pos, impr, top_page)
        return row

    @staticmethod
    def _opportunity(pos: float, impr: float, ctr: float) -> str:
        if 5 <= pos <= 20:
            return "striking_distance"          # winnable: small push clears page 1
        if impr >= 800 and ctr < 0.02:
            return "low_ctr"                     # seen but not clicked: title/meta fix
        if pos <= 3:
            return "defend"                      # already strong: protect, refresh
        return "low_priority"                    # too deep or too little demand

    @staticmethod
    def _trend(impr: float, prior: dict ) -> tuple[str, float ]:
        if not prior or prior.get("impressions", 0) == 0:
            return "flat", None
        delta = (impr - prior["impressions"]) / prior["impressions"]
        if delta >= 0.15:
            return "rising", delta
        if delta <= -0.15:
            return "decaying", delta
        return "flat", delta

    @staticmethod
    def _intent(query: str) -> str:
        q = query.lower()
        commercial = any(t in q for t in _COMMERCIAL)
        informational = any(t in q for t in _INFORMATIONAL)
        if commercial and not informational:
            return "commercial"
        if informational and not commercial:
            return "informational"
        return "mixed"

    @staticmethod
    def _score(opportunity: str, trend: str, impr: float, pos: float) -> float:
        opp_weight = {"striking_distance": 1.0, "low_ctr": 0.45, "defend": 0.2, "low_priority": 0.05}
        trend_mult = {"rising": 1.4, "flat": 1.0, "decaying": 0.7}
        # Reward demand (impressions), discount distance (position). Tune freely.
        base = impr * opp_weight[opportunity] / math.sqrt(max(pos, 1))
        return round(base * trend_mult[trend], 2)

    @staticmethod
    def _reason(opportunity, trend, intent, pos, impr, top_page) -> str:
        lever = {
            "striking_distance": f"ranks ~{pos:.0f}, close to page 1 — on-page + depth should push it up",
            "low_ctr": f"{impr:,} impressions but few clicks — rewrite title/meta to match intent",
            "defend": f"already ranks ~{pos:.0f} — refresh to hold the position",
            "low_priority": "low demand or too far back — deprioritize",
        }[opportunity]
        momentum = {"rising": " Trending up vs. last period.", "decaying": " Losing ground vs. last period — worth acting now.", "flat": ""}[trend]
        page_note = (
            f" A page already ranks ({top_page}); improve it rather than writing a duplicate."
            if top_page else " No strong page yet; a new article can own this."
        )
        return f"[{intent}] {lever}.{momentum}{page_note}"


if __name__ == "__main__":
    gsc = GoogleSearchConsoleClient()
    for r in gsc.search_analytics("sc-domain:echooers.com", days=28)[:5]:
        print(f'{r["score"]:>8}  {r["opportunity"]:<17} {r["trend"]:<8} {r["query"]}')
        print(f'          {r["reason"]}')