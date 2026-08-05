class MockSearchClient:
    """SearchClient (tools/base.py) for offline runs and tests — search_provider=
    "mock". Results are derived from the query so they're recognizably *about* it,
    and deterministic so a test can assert on them, but the URLs are on
    example.com and lead nowhere real. That is the point: nothing here should ever
    be mistaken for a page that exists.
    """

    def __init__(self, results_per_query: int = 3) -> None:
        self.results_per_query = results_per_query
        self.queries: list[str] = []  # what was asked, in order — for assertions

    def search(self, query: str, limit: int = 10) -> list[dict]:
        query = (query or "").strip()
        self.queries.append(query)
        if not query:
            return []
        slug = "-".join(query.lower().split())[:60]
        count = min(limit, self.results_per_query)
        return [
            {
                "title": f"{query} — result {index + 1}",
                "url": f"https://example.com/{slug}/{index + 1}",
                "snippet": f"A deterministic offline stand-in for a real page about {query}.",
            }
            for index in range(count)
        ]
