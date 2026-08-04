class MockGoogleSearchConsoleClient:
    """Canned Search Console rows, same enriched shape as GoogleSearchConsoleClient
    (tools/google_search_console.py), so the agent can reason about query
    opportunities before a real service account key is wired in."""

    _ROWS = [
        {
            "query": "anonymous social media app",
            "clicks": 42,
            "impressions": 3100,
            "ctr": 0.0135,
            "position": 11.2,
            "opportunity": "striking_distance",
            "trend": "rising",
            "intent": "informational",
            "top_page": None,
            "impressions_delta_pct": 0.22,
            "score": 245.8,
            "reason": (
                "[informational] ranks ~11, close to page 1 — on-page + depth should "
                "push it up. Trending up vs. last period. No strong page yet; a new "
                "article can own this."
            ),
        },
        {
            "query": "post without login",
            "clicks": 18,
            "impressions": 1450,
            "ctr": 0.0124,
            "position": 8.4,
            "opportunity": "striking_distance",
            "trend": "flat",
            "intent": "informational",
            "top_page": "https://echooers.com/blog/post-without-login",
            "impressions_delta_pct": 0.02,
            "score": 132.4,
            "reason": (
                "[informational] ranks ~8, close to page 1 — on-page + depth should "
                "push it up. A page already ranks (https://echooers.com/blog/"
                "post-without-login); improve it rather than writing a duplicate."
            ),
        },
        {
            "query": "best anonymous social apps",
            "clicks": 5,
            "impressions": 980,
            "ctr": 0.0051,
            "position": 15.7,
            "opportunity": "low_ctr",
            "trend": "decaying",
            "intent": "commercial",
            "top_page": None,
            "impressions_delta_pct": -0.19,
            "score": 61.2,
            "reason": (
                "[commercial] 980 impressions but few clicks — rewrite title/meta to "
                "match intent. Losing ground vs. last period — worth acting now. No "
                "strong page yet; a new article can own this."
            ),
        },
    ]

    def search_analytics(self, site_url: str, days: int = 28, row_limit: int = 500) -> list[dict]:
        return self._ROWS[:row_limit]
