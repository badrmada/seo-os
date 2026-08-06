class NullSearchPerformanceClient:
    """search_performance_provider="none" — no rank data at all. **The default.**

    Why "none" rather than "mock" is the default: the mock returns canned
    striking-distance rows, and _pick_keyword (agent/graph/stages/analyze.py)
    prefers a striking-distance row *over* the caller's own seed_keyword. So a
    tenant who hadn't connected Search Console got the fixture's keyword instead
    of the one they asked for, silently, in a real draft — while the docs said the
    agent would fall back to their seed keyword.

    Returning nothing makes that fallback real: seed keyword, then an analytics
    highlight, then a discovered opportunity. Every one of those is the tenant's
    own current data. A fixture is the right default for a *shape* nothing else
    provides; it is the wrong default for a decision the tenant can already make
    better.
    """

    def search_analytics(self, days: int = 28, row_limit: int = 500) -> list[dict]:
        return []
