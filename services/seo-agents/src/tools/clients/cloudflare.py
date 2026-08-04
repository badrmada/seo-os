from __future__ import annotations

from datetime import date, timedelta

import requests

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
REST_URL = "https://api.cloudflare.com/client/v4"

# Cloudflare Bot Management scores requests 1-99 (lower = more likely automated).
# 30 is the threshold Cloudflare's own dashboard uses to bucket "likely bot" traffic.
BOT_SCORE_THRESHOLD = 30

_SEARCH_ENGINE_HOSTS = (
    "google.", "bing.", "yahoo.", "duckduckgo.", "baidu.", "yandex.", "ecosia.",
)
_SOCIAL_HOSTS = (
    "facebook.", "twitter.", "x.com", "t.co", "instagram.", "linkedin.",
    "reddit.", "tiktok.", "pinterest.", "threads.net",
)


class CloudflareAnalyticsClient:
    """Real SiteTrafficClient (tools/base.py) for tenants on Cloudflare specifically.
    A genuine, reusable vendor integration — like GoogleSearchConsoleClient or
    GeminiClient — not a tenant-specific hack, so it stays a Python class rather
    than becoming a "templated" case: turning raw event-level data into
    organic/social/direct/referral percentages and a bot/human split is real
    computation (bucketing by bot score, classifying referrer hosts), not just a
    declarative reshape of existing fields.

    Implements the Protocol via traffic_summary(days=28) -> {"summary": str},
    formatted from the same underlying numbers traffic_split()/performance() below
    expose raw (kept as bonus methods for callers who want the structured data
    directly, e.g. a dashboard — it's just no longer what the agent pipeline
    consumes).

    Backed by two Cloudflare GraphQL Analytics API datasets:

      - httpRequests1dGroups (Zone Analytics): requests_total, trend, cache_hit_ratio.
        Always available on any zone with the "Analytics" API token permission.
      - rumPageloadEventsAdaptiveGroups (Web Analytics / RUM): referer-host breakdown,
        used to classify organic_search_pct / social_pct / referral_pct / direct_pct,
        and bot score distribution for human_pct / bot_pct.
        Only available if Cloudflare Web Analytics / Bot Management is enabled on the
        zone. If either query fails (not enabled, insufficient token scope, etc.) those
        fields degrade to 0.0 rather than raising — same graceful-degradation pattern
        as GoogleSearchConsoleClient.
    """

    def __init__(self, api_token: str, zone_id: str = "", timeout: float = 15.0):
        self.zone_id = zone_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        })

    def list_zones(self) -> list[dict]:
        """List the zones (domains) this API token can see, via the REST API
        (GraphQL has no zone-listing query). Needs the token's "Zone" -> "Zone"
        -> "Read" permission. Use this to find the zone_id to pass into __init__
        — or read it off the Cloudflare dashboard: the domain's Overview page,
        right-hand sidebar under "API".

        Returns [{"id", "name", "status"}, ...], one per zone visible to the token.
        """
        zones = []
        page = 1
        while True:
            resp = self.session.get(
                f"{REST_URL}/zones", params={"page": page, "per_page": 50}, timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success", False):
                raise RuntimeError(f"Cloudflare API error: {payload.get('errors')}")
            zones.extend(
                {"id": z["id"], "name": z["name"], "status": z["status"]}
                for z in payload["result"]
            )
            info = payload.get("result_info", {})
            if page >= info.get("total_pages", 1):
                break
            page += 1
        return zones

    def _windows(self, days: int) -> tuple[str, str, str, str]:
        cur_end = date.today() - timedelta(days=1)  # yesterday: last fully-complete day
        cur_start = cur_end - timedelta(days=days - 1)
        prior_end = cur_start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=days - 1)
        return (cur_start.isoformat(), cur_end.isoformat(),
                prior_start.isoformat(), prior_end.isoformat())

    # -- Protocol method -------------------------------------------------------

    def traffic_summary(self, days: int = 28) -> dict:
        stats = self.traffic_split(days)
        return {
            "summary": (
                f"{stats['requests_total']:,} requests over the last {stats['period_days']} days, "
                f"{stats['organic_search_pct']:.0%} from organic search, "
                f"trending {stats['trend_vs_prior_period_pct']:+.1f}% vs. the prior period."
            ),
        }

    # -- Bonus methods (not part of SiteTrafficClient, kept for direct use) ----

    def traffic_split(self, days: int = 28) -> dict:
        cur_start, cur_end, prior_start, prior_end = self._windows(days)

        current = self._request_totals(cur_start, cur_end)
        prior = self._safe(lambda: self._request_totals(prior_start, prior_end), default=None)

        requests_total = current["requests"]
        trend = 0.0
        if prior and prior["requests"]:
            trend = round((requests_total - prior["requests"]) / prior["requests"] * 100, 1)

        sources = self._safe(
            lambda: self._referer_source_split(cur_start, cur_end),
            default={"organic_search_pct": 0.0, "referral_pct": 0.0, "direct_pct": 0.0, "social_pct": 0.0},
        )
        bot_split = self._safe(
            lambda: self._bot_human_split(cur_start, cur_end),
            default={"human_pct": 0.0, "bot_pct": 0.0},
        )

        return {
            "period_days": days,
            "requests_total": requests_total,
            "trend_vs_prior_period_pct": trend,
            **bot_split,
            **sources,
        }

    def performance(self) -> dict:
        cur_start, cur_end, _, _ = self._windows(1)
        totals = self._request_totals(cur_start, cur_end)
        cache_hit_ratio = round(totals["cachedRequests"] / totals["requests"], 4) if totals["requests"] else 0.0
        avg_ttfb_ms = self._safe(lambda: self._avg_ttfb_ms(cur_start, cur_end), default=None)
        return {"avg_ttfb_ms": avg_ttfb_ms, "cache_hit_ratio": cache_hit_ratio}

    # -- GraphQL calls ---------------------------------------------------------

    def _graphql(self, query: str, variables: dict) -> dict:
        resp = self.session.post(
            GRAPHQL_URL, json={"query": query, "variables": variables}, timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"Cloudflare GraphQL error: {payload['errors']}")
        return payload["data"]

    def _request_totals(self, start: str, end: str) -> dict:
        query = """
        query ZoneRequests($zoneTag: String!, $start: Date!, $end: Date!) {
          viewer {
            zones(filter: { zoneTag: $zoneTag }) {
              httpRequests1dGroups(limit: 100, filter: { date_geq: $start, date_leq: $end }) {
                sum { requests cachedRequests }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"zoneTag": self.zone_id, "start": start, "end": end})
        groups = data["viewer"]["zones"][0]["httpRequests1dGroups"]
        return {
            "requests": sum(g["sum"]["requests"] for g in groups),
            "cachedRequests": sum(g["sum"]["cachedRequests"] for g in groups),
        }

    def _bot_human_split(self, start: str, end: str) -> dict:
        query = """
        query ZoneBotScores($zoneTag: String!, $start: Date!, $end: Date!) {
          viewer {
            zones(filter: { zoneTag: $zoneTag }) {
              httpRequestsAdaptiveGroups(
                limit: 1000
                filter: { date_geq: $start, date_leq: $end }
              ) {
                count
                dimensions { botScore }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"zoneTag": self.zone_id, "start": start, "end": end})
        groups = data["viewer"]["zones"][0]["httpRequestsAdaptiveGroups"]
        total = sum(g["count"] for g in groups)
        if not total:
            return {"human_pct": 0.0, "bot_pct": 0.0}
        bot = sum(g["count"] for g in groups if g["dimensions"]["botScore"] < BOT_SCORE_THRESHOLD)
        return {"human_pct": round((total - bot) / total, 4), "bot_pct": round(bot / total, 4)}

    def _referer_source_split(self, start: str, end: str) -> dict:
        query = """
        query ZoneReferers($zoneTag: String!, $start: Date!, $end: Date!) {
          viewer {
            zones(filter: { zoneTag: $zoneTag }) {
              rumPageloadEventsAdaptiveGroups(
                limit: 1000
                filter: { date_geq: $start, date_leq: $end }
              ) {
                count
                dimensions { refererHost }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"zoneTag": self.zone_id, "start": start, "end": end})
        groups = data["viewer"]["zones"][0]["rumPageloadEventsAdaptiveGroups"]
        total = sum(g["count"] for g in groups)
        if not total:
            return {"organic_search_pct": 0.0, "referral_pct": 0.0, "direct_pct": 0.0, "social_pct": 0.0}

        search = social = direct = referral = 0
        for g in groups:
            host = (g["dimensions"]["refererHost"] or "").lower()
            count = g["count"]
            if not host:
                direct += count
            elif any(h in host for h in _SEARCH_ENGINE_HOSTS):
                search += count
            elif any(h in host for h in _SOCIAL_HOSTS):
                social += count
            else:
                referral += count

        return {
            "organic_search_pct": round(search / total, 4),
            "referral_pct": round(referral / total, 4),
            "direct_pct": round(direct / total, 4),
            "social_pct": round(social / total, 4),
        }

    def _avg_ttfb_ms(self, start: str, end: str) -> float:
        query = """
        query ZonePerf($zoneTag: String!, $start: Date!, $end: Date!) {
          viewer {
            zones(filter: { zoneTag: $zoneTag }) {
              rumPerformanceEventsAdaptiveGroups(
                limit: 100
                filter: { date_geq: $start, date_leq: $end }
              ) {
                avg { ttfb }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"zoneTag": self.zone_id, "start": start, "end": end})
        groups = data["viewer"]["zones"][0]["rumPerformanceEventsAdaptiveGroups"]
        if not groups:
            return None
        return round(sum(g["avg"]["ttfb"] for g in groups) / len(groups), 1)

    @staticmethod
    def _safe(fetch, default):
        try:
            return fetch()
        except Exception:
            return default


if __name__ == "__main__":
    import os
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "")

    client = CloudflareAnalyticsClient(api_token=api_token)

    zone_id = os.environ.get("CLOUDFLARE_ZONE_ID", "")  # replace with your zone ID
    if not zone_id:
        print("No CLOUDFLARE_ZONE_ID set — zones visible to this token:")
        for z in client.list_zones():
            print(f'  {z["id"]}  {z["name"]}  ({z["status"]})')
        raise SystemExit(0)

    client.zone_id = zone_id
    print(client.traffic_split())
    print(client.performance())
