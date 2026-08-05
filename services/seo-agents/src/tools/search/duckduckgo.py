"""search_provider="duckduckgo" — the default SearchClient (tools/base.py).

DuckDuckGo is the default because grounding should work for every tenant on the
first run: no API key, no account, no billing relationship, and no dependence on
which LLM provider happens to be configured. A tenant who wants a keyed engine
(Bing, Serper, Brave, a self-hosted SearxNG) writes a "custom" class; nobody is
blocked waiting for one.

Sync on purpose. `ddgs` is a blocking library, and tools/base.py's contract takes
a sync *or* an async implementation — the framework runs this one in a worker
thread (agent/utils/async_utils.py's call()), so it never stalls the event loop
other tenants' runs are sharing. This is the same branch GoogleSearchConsoleClient
takes, and for the same reason.
"""

from __future__ import annotations

from ddgs import DDGS

# One search is fast (~1-2s) but it is still an outbound call that can hang, and a
# discovery run makes several of them. Same rule as every other client here: a
# bound, always.
DEFAULT_TIMEOUT_SECONDS = 10.0


class DuckDuckGoSearchClient:
    """`backend` is DuckDuckGo, and `fallback_backend` is what happens when
    DuckDuckGo won't answer.

    That second one is not defensive programming, it's the observed behavior:
    DuckDuckGo rate-limits by IP, and once it does, every search from that
    address raises `DDGSException: No results found` for a while. Measured here,
    it took about twenty searches. Without a fallback the *default* grounding
    would quietly stop grounding partway through a busy day — the run still
    succeeds, the links just stop being verified — which is the worst version of
    a default. `"auto"` lets ddgs answer from whichever engine it can reach, so
    the first choice is still genuinely DuckDuckGo and the run still gets real
    pages when it isn't available.

    Set `fallback_backend` to `""` for strictly-DuckDuckGo-or-nothing.

    `searcher` is the seam tests use: any object with ddgs's
    `.text(query, region=..., safesearch=..., max_results=..., backend=...)`
    signature. Left None (every real config), a DDGS is built per search — the
    library keeps no useful state between calls, and a per-call object is what
    keeps this safe to use from several concurrent runs.
    """

    def __init__(
        self,
        *,
        backend: str = "duckduckgo",
        fallback_backend: str = "auto",
        region: str = "wt-wt",
        safesearch: str = "moderate",
        timelimit: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        searcher=None,
    ) -> None:
        self.backend = backend
        self.fallback_backend = fallback_backend
        self.region = region
        self.safesearch = safesearch
        self.timelimit = timelimit or None  # ddgs wants None, config says ""
        self.timeout_seconds = timeout_seconds
        self._searcher = searcher

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """An empty query returns no results rather than raising: the caller
        (LLMOpportunitySource) generates its queries from a prompt, and one blank
        query among several is not a reason to fail the source — it just
        contributes nothing.

        A failing *backend* does raise, once both have been tried. The caller
        degrades and records why (see LLMOpportunitySource._search); swallowing it
        here would leave "the engine is blocking us" and "nobody has written about
        this" looking identical two layers up.
        """
        query = (query or "").strip()
        if not query:
            return []

        backends = [self.backend] + ([self.fallback_backend] if self.fallback_backend else [])
        last_error = None
        for backend in backends:
            try:
                results = self._search_with(backend, query, limit)
            except Exception as exc:  # noqa: BLE001 - re-raised below if nothing works
                last_error = exc
                continue
            if results:
                return results
        if last_error is not None:
            raise last_error
        return []

    def _search_with(self, backend: str, query: str, limit: int) -> list[dict]:
        searcher = self._searcher or DDGS(timeout=int(self.timeout_seconds))
        rows = searcher.text(
            query,
            region=self.region,
            safesearch=self.safesearch,
            timelimit=self.timelimit,
            max_results=limit,
            backend=backend,
        )
        return [result for result in map(_normalize, rows or []) if result]


def _normalize(row) -> dict | None:
    """ddgs returns {"title", "href", "body"}; this Protocol says
    {"title", "url", "snippet"}. A row with no URL is dropped — the URL is the
    only part of a result this system actually trusts, so a result without one
    can't do the job the search was for."""
    if not isinstance(row, dict):
        return None
    url = (row.get("href") or row.get("url") or "").strip()
    if not url:
        return None
    return {
        "title": (row.get("title") or "").strip(),
        "url": url,
        "snippet": (row.get("body") or row.get("snippet") or "").strip(),
    }
