from __future__ import annotations

from datetime import date, timedelta

import google_auth_httplib2
import httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build

from .search_performance_rows import enrich_rows

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# googleapiclient's default transport has no timeout at all, so a stalled
# connection hangs the call indefinitely. On a CLI that's someone pressing
# Ctrl-C; from a queue worker it's a slot held forever. One search_analytics()
# makes several requests (current window, prior window, page mapping), so this
# bounds each one, not the method.
DEFAULT_TIMEOUT_SECONDS = 30.0

# GSC data is only complete up to ~2-3 days ago; querying "today" returns partial rows.
DATA_LAG_DAYS = 3

# Google identifies a property one of two ways — a domain property
# ("sc-domain:example.com") or a URL-prefix property (a full URL, normally
# trailing-slashed, e.g. "https://example.com/"). This shape requirement is
# Google's, which is exactly why it is checked *here* and not against the generic
# AgentConfig.site_url: another search-performance provider identifies a site
# however it likes.
_PROPERTY_PREFIXES = ("sc-domain:", "http://", "https://")


class GoogleSearchConsoleClient:
    """search_performance_provider="google" — the real Search Console API.

    Implements tools/base.py's SearchPerformanceClient:

        search_analytics(days=28, row_limit=500) -> list[dict]

    The property this client reads is its own setting,
    `search_performance_options.gsc_domain`, not a field on the run's input and
    not `AgentConfig.site_url`. Those are two different things that happen to
    describe one website: `site_url` is where the site lives
    ("https://example.com"), while a Search Console property is an identifier in
    Google's namespace ("sc-domain:example.com") that only means anything to this
    provider. Deriving one from the other looks tempting and is wrong often enough
    to matter — a tenant may have a URL-prefix property, a subdomain property, or
    several — so it is asked for explicitly.

    Rows come back with the raw GSC fields (query, clicks, impressions, ctr,
    position) plus the derived decision signals, which are computed by the shared
    enrichment in search_performance_rows.py rather than here, so a tenant on a
    different rank source gets an identically-classified answer.

    Prior-period and page-mapping calls degrade gracefully: if either fails the
    row still comes back with opportunity/intent/score, just without
    trend/top_page.
    """

    def __init__(
        self, gsc_domain: str = "", key_file: str = "service_account.json", scopes=SCOPES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        # Checked at construction, so `check-data` reports it rather than a run
        # failing at the analyze step with an opaque API error.
        if not gsc_domain:
            raise ValueError(
                'search_performance_provider="google" requires '
                'search_performance_options.gsc_domain — your Search Console property, '
                'either "sc-domain:example.com" or a URL-prefix property like '
                '"https://example.com/"'
            )
        if not gsc_domain.startswith(_PROPERTY_PREFIXES):
            raise ValueError(
                f"search_performance_options.gsc_domain {gsc_domain!r} is not a valid Google "
                'Search Console property identifier — expected "sc-domain:<domain>" or a '
                'URL-prefix property starting with "http://" or "https://"'
            )
        self.gsc_domain = gsc_domain
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

    def search_analytics(self, days: int = 28, row_limit: int = 500) -> list[dict]:
        cur_start, cur_end, prior_start, prior_end = self._windows(days)

        current = self._query(cur_start, cur_end, ["query"], row_limit)
        prior_map = self._safe_map(
            lambda: self._query(prior_start, prior_end, ["query"], row_limit), key="query",
        )
        page_map = self._safe_top_page(cur_start, cur_end, row_limit)

        return enrich_rows(current, prior=prior_map, top_pages=page_map, row_limit=row_limit)

    # -- raw API -----------------------------------------------------------

    def _query(self, start, end, dimensions, row_limit) -> list[dict]:
        body = {"startDate": start, "endDate": end, "dimensions": dimensions, "rowLimit": row_limit}
        resp = self.service.searchanalytics().query(
            siteUrl=self.gsc_domain, body=body,
        ).execute()
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

    def _safe_top_page(self, start, end, row_limit) -> dict:
        """Map each query -> the page that earns it the most clicks."""
        try:
            rows = self._query(start, end, ["query", "page"], row_limit)
        except Exception:
            return {}
        best: dict[str, dict] = {}
        for r in rows:
            q = r["query"]
            if q not in best or r["clicks"] > best[q]["clicks"]:
                best[q] = r
        return {q: r["page"] for q, r in best.items()}
