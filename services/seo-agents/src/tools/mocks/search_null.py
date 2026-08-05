class NullSearchClient:
    """SearchClient (tools/base.py) for a tenant that wants no search step at all —
    search_provider="none". Always returns no results, which is exactly the
    condition that makes LLMOpportunitySource fall through to the LLM's own
    grounding (step 2 of the order in tools/base.py's SearchClient), so the caller
    needs no branch for "search isn't configured".

    Same shape and same reason as tools/mocks/traffic_null.py's NullTrafficClient.
    """

    def search(self, query: str, limit: int = 10) -> list[dict]:
        return []
