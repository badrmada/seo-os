"""Covers the SearchClient provider kind (PLAN.md Step D): the Protocol's shape,
the DuckDuckGo client that is now the default grounding, and the wiring that gets
one built from a config.

The resolution order it takes part in — search, then the LLM's own grounding, then
nothing — lives in test_opportunity_llm.py, next to the source that decides it.

Nothing here touches the network. DuckDuckGoSearchClient takes a `searcher` seam
for exactly this reason: the mapping from ddgs's row shape to this system's is
what's worth testing, and a live search would test DuckDuckGo's uptime instead.
"""

import asyncio

import pytest

from agent.config.agent_config import AgentConfig
from agent.managers.tools_manager import ToolsManager
from tools.mocks.search_mock import MockSearchClient
from tools.mocks.search_null import NullSearchClient
from tools.search.duckduckgo import DuckDuckGoSearchClient


class FakeSearcher:
    """ddgs's surface, as much of it as DuckDuckGoSearchClient uses. `by_backend`
    is how a test says "DuckDuckGo is rate-limiting us but another engine isn't" —
    a value that's an exception is raised."""

    def __init__(self, rows: list[dict] = None, by_backend: dict = None) -> None:
        self.rows = rows if rows is not None else []
        self.by_backend = by_backend
        self.calls: list[tuple[str, dict]] = []

    def text(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        if self.by_backend is None:
            return self.rows
        answer = self.by_backend[kwargs["backend"]]
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def backends(self) -> list[str]:
        return [kwargs["backend"] for _, kwargs in self.calls]


# --- the DuckDuckGo client -------------------------------------------------


def test_ddg_rows_become_the_protocols_shape():
    """ddgs says {"title", "href", "body"}; tools/base.py says
    {"title", "url", "snippet"}. Everything downstream reads the second."""
    searcher = FakeSearcher([
        {"title": "Anonymous forums", "href": "https://example.test/a", "body": "a snippet"},
    ])
    client = DuckDuckGoSearchClient(searcher=searcher)

    assert client.search("anonymous forums") == [
        {"title": "Anonymous forums", "url": "https://example.test/a", "snippet": "a snippet"},
    ]


def test_a_result_with_no_url_is_dropped():
    """The URL is the only part of a result this system actually trusts — a row
    without one can't do the job the search was for."""
    searcher = FakeSearcher([
        {"title": "no link here", "body": "..."},
        {"title": "fine", "href": "https://example.test/b", "body": "..."},
    ])

    [result] = DuckDuckGoSearchClient(searcher=searcher).search("x")

    assert result["url"] == "https://example.test/b"


def test_the_configured_engine_settings_reach_ddgs():
    searcher = FakeSearcher([])
    client = DuckDuckGoSearchClient(
        backend="duckduckgo", region="us-en", safesearch="off", timelimit="w", searcher=searcher,
    )

    client.search("anything", limit=7)

    _, kwargs = searcher.calls[0]
    assert kwargs == {
        "region": "us-en", "safesearch": "off", "timelimit": "w",
        "max_results": 7, "backend": "duckduckgo",
    }


def test_duckduckgo_is_asked_first():
    """ddgs's own default is "auto", which fans out to several engines. A provider
    named "duckduckgo" should ask DuckDuckGo first."""
    searcher = FakeSearcher([{"title": "t", "href": "https://example.test/a"}])

    DuckDuckGoSearchClient(searcher=searcher).search("x")

    assert searcher.backends == ["duckduckgo"]


def test_another_engine_answers_when_duckduckgo_is_rate_limiting():
    """Not hypothetical: DuckDuckGo rate-limits by IP, and once it does every
    search from that address raises for a while. Without this the default
    grounding quietly stops grounding partway through a busy day."""
    searcher = FakeSearcher(by_backend={
        "duckduckgo": RuntimeError("DDGSException: No results found."),
        "auto": [{"title": "t", "href": "https://example.test/a"}],
    })

    [result] = DuckDuckGoSearchClient(searcher=searcher).search("x")

    assert searcher.backends == ["duckduckgo", "auto"]
    assert result["url"] == "https://example.test/a"


def test_an_empty_answer_also_falls_over_to_the_fallback():
    """The engine's two ways of saying "no": raising, and returning nothing."""
    searcher = FakeSearcher(by_backend={
        "duckduckgo": [],
        "auto": [{"title": "t", "href": "https://example.test/a"}],
    })

    assert len(DuckDuckGoSearchClient(searcher=searcher).search("x")) == 1
    assert searcher.backends == ["duckduckgo", "auto"]


def test_the_fallback_can_be_turned_off():
    """Strictly-DuckDuckGo-or-nothing, for a tenant who means it."""
    searcher = FakeSearcher(by_backend={"duckduckgo": []})

    client = DuckDuckGoSearchClient(fallback_backend="", searcher=searcher)

    assert client.search("x") == []
    assert searcher.backends == ["duckduckgo"]


def test_the_error_is_raised_once_every_backend_has_failed():
    """Swallowing it would leave "the engine is blocking us" and "nobody has
    written about this" looking identical to the caller."""
    searcher = FakeSearcher(by_backend={
        "duckduckgo": RuntimeError("blocked"),
        "auto": RuntimeError("also blocked"),
    })

    with pytest.raises(RuntimeError, match="also blocked"):
        DuckDuckGoSearchClient(searcher=searcher).search("x")


def test_an_empty_query_returns_nothing_without_searching():
    """Queries are generated from a prompt, so one blank among several is not a
    reason to fail the source."""
    searcher = FakeSearcher([{"title": "t", "href": "https://example.test/c"}])
    client = DuckDuckGoSearchClient(searcher=searcher)

    assert client.search("   ") == []
    assert searcher.calls == []


def test_ddg_search_is_sync_so_the_framework_threads_it():
    """tools/base.py's contract takes either, but ddgs is blocking — the wrong
    choice here stalls every concurrent run on the loop (PLAN.md's invariant)."""
    from agent.utils.async_utils import is_async_callable

    assert not is_async_callable(DuckDuckGoSearchClient(searcher=FakeSearcher([])).search)


# --- the offline providers -------------------------------------------------


def test_the_mock_returns_deterministic_results_about_the_query():
    client = MockSearchClient()

    first = client.search("anonymous posting", limit=2)
    second = client.search("anonymous posting", limit=2)

    assert first == second
    assert len(first) == 2
    assert all("anonymous posting" in result["title"] for result in first)
    assert all(result["url"].startswith("https://example.com/") for result in first)


def test_the_mock_records_what_it_was_asked():
    client = MockSearchClient()

    client.search("one")
    client.search("two")

    assert client.queries == ["one", "two"]


def test_the_null_client_never_returns_results():
    """search_provider="none" — what makes discovery fall through to the LLM's own
    grounding without the caller needing a branch."""
    assert NullSearchClient().search("anything") == []


# --- how one gets built ----------------------------------------------------


def test_duckduckgo_is_the_default_search_provider():
    """The decision this step ships: grounding is the system's job, not the
    model's, so it's on by default and it's a search engine."""
    assert AgentConfig().search_provider == "duckduckgo"
    assert isinstance(ToolsManager(AgentConfig()).build_search(), DuckDuckGoSearchClient)


def test_search_options_reach_the_client():
    config = AgentConfig(search_provider="duckduckgo", search_options={
        "region": "uk-en", "backend": "auto", "timeout_seconds": 3,
    })

    client = ToolsManager(config).build_search()

    assert (client.region, client.backend, client.timeout_seconds) == ("uk-en", "auto", 3.0)


def test_search_can_be_a_tenants_own_class(tmp_path):
    tenant = tmp_path / "acme"
    (tenant / "plugins").mkdir(parents=True)
    (tenant / "plugins" / "my_search.py").write_text(
        "class Client:\n"
        "    def __init__(self, config, options=None):\n"
        "        self.options = options or {}\n"
        "\n"
        "    def search(self, query, limit=10):\n"
        "        return [{'title': query, 'url': self.options['url'], 'snippet': ''}]\n"
    )
    config = AgentConfig(
        config_base_dir=str(tenant),
        search_provider="custom", search_custom_class="my_search:Client",
        search_options={"url": "https://internal.test/1"},
    )

    client = ToolsManager(config).build_search()

    assert client.search("x")[0]["url"] == "https://internal.test/1"


def test_a_custom_search_provider_with_no_class_configured_says_so():
    with pytest.raises(ValueError, match="search_custom_class"):
        ToolsManager(AgentConfig(search_provider="custom")).build_search()


def test_the_bundle_and_the_discovery_source_share_one_search_client():
    """Built once, not per user — an "llm" source and Tools.search are the same
    object, so a tenant's own class isn't constructed (or rate-limited) twice."""
    config = AgentConfig(
        search_provider="mock",
        discovery_sources=[{"name": "trends", "provider": "llm"}],
    )

    tools = ToolsManager(config).build_all()

    assert tools.discovery_sources["trends"].search is tools.search


def test_a_hand_built_tools_bundle_stays_offline():
    """Tools(...) is constructed directly by tests and by any caller injecting its
    own clients; the default there must not reach the network."""
    from agent.graph.tools import Tools

    tools = Tools(search_performance=None, analytics=None, traffic=None, llm=None)

    assert isinstance(tools.search, NullSearchClient)


# --- verbose mode ----------------------------------------------------------


def test_a_search_is_reported_with_how_many_results_it_got():
    """Zero results is the signal that a run fell through to another grounding
    path — the number is the point of the event."""
    from agent.observability.observed import ObservedSearchClient
    from agent.observability.reporter import TOOL_END, CollectingReporter

    reporter = CollectingReporter(level=2)
    client = ObservedSearchClient(MockSearchClient(), reporter, "search")

    asyncio.run(client.search("anonymous posting", limit=2))

    end = next(e for e in reporter.events if e["event"] == TOOL_END)
    assert end["tool"] == "search"
    assert end["results"] == 2
    assert len(end["urls"]) == 2
