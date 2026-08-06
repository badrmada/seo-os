from tools.clients.search_performance_rows import enrich_rows


class MockSearchPerformanceClient:
    """search_performance_provider="mock" — canned rows for offline runs and tests.

    **Product-neutral, like every other mock in this package.** The previous
    version shipped one real product's queries ("anonymous social media app",
    "post without login") and a live URL on that product's domain, so every
    example and every tenant who hadn't connected Search Console silently drafted
    against someone else's keywords — an unrelated example asking for "cron job
    monitoring" targeted "anonymous social media app" instead. Canned data is a
    stand-in for a shape, so it must not be recognisably about anything.

    The rows below carry only the four raw numbers a real source has; the
    opportunity/trend/intent/score/reason fields come from the same enrichment the
    Google and templated providers use, so this fixture cannot drift from what a
    real provider would produce.
    """

    _RAW_ROWS = [
        {"query": "example topic guide", "clicks": 42, "impressions": 3100, "ctr": 0.0135, "position": 11.2},
        {"query": "how to choose an example tool", "clicks": 18, "impressions": 1450, "ctr": 0.0124, "position": 8.4},
        {"query": "best example tools", "clicks": 5, "impressions": 980, "ctr": 0.0051, "position": 15.7},
        {"query": "example tool pricing", "clicks": 96, "impressions": 1200, "ctr": 0.08, "position": 2.1},
    ]

    # The prior period, so `trend` is exercised rather than always "flat" — a run
    # against the mock should show the same variety a real one does.
    _PRIOR = {
        "example topic guide": {"impressions": 2540},
        "how to choose an example tool": {"impressions": 1430},
        "best example tools": {"impressions": 1210},
    }

    _TOP_PAGES = {"how to choose an example tool": "https://example.com/guides/choosing"}

    def search_analytics(self, days: int = 28, row_limit: int = 500) -> list[dict]:
        return enrich_rows(
            self._RAW_ROWS, prior=self._PRIOR, top_pages=self._TOP_PAGES, row_limit=row_limit,
        )
