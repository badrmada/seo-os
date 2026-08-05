"""Covers the "grounded by default" + "contract enforced, not trusted" roadmap
items, and the grounding resolution order Step D added on top of them:

  1. a configured SearchClient (the default, DuckDuckGo) — real results in the
     prompt, their URLs the only trusted ones;
  2. else the LLM's own grounding, if the provider has any;
  3. else ungrounded, links unverified.

Each step falls through to the next when it produces nothing, and a claimed link
is only kept when it matches whatever the trusted list turned out to be. Malformed
items (from any source, not just llm) are dropped individually via
normalize_opportunity rather than raising or corrupting the rest of the batch."""

import asyncio
import json

from agent.config.agent_config import AgentConfig
from agent.schemas.opportunity import normalize_opportunity
from tools.clients.opportunity_llm import LLMOpportunitySource
from tools.llm.base import LLMResponse


class FakeLLMClient:
    """Records the last call's kwargs and returns a canned response, so tests can
    assert both what LLMOpportunitySource asked for (grounded=...) and how it
    handles what comes back."""

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.last_kwargs: dict = {}

    def generate(self, prompt: str, *, model: str = None, grounded: bool = False) -> LLMResponse:
        self.last_kwargs = {"model": model, "grounded": grounded}
        return self.response


class ScriptedLLMClient:
    """Two calls happen once search is in play — the model writes the search
    queries, then reads the results — so this answers them in order and keeps
    every prompt and kwarg for inspection."""

    def __init__(self, *responses: LLMResponse) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    def generate(self, prompt: str, *, model: str = None, grounded: bool = False) -> LLMResponse:
        self.prompts.append(prompt)
        self.kwargs.append({"model": model, "grounded": grounded})
        return self.responses[min(len(self.prompts), len(self.responses)) - 1]


class FakeSearchClient:
    """A SearchClient (tools/base.py) that answers from a fixed map, so a test can
    say what each generated query finds."""

    def __init__(self, by_query: dict = None, results: list = None, error: Exception = None) -> None:
        self.by_query = by_query or {}
        self.results = results
        self.error = error
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 10) -> list[dict]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        found = self.results if self.results is not None else self.by_query.get(query, [])
        return found[:limit]


def _result(url: str, title: str = "a page", snippet: str = "about something") -> dict:
    return {"title": title, "url": url, "snippet": snippet}


def _queries_response(*queries: str) -> LLMResponse:
    return LLMResponse(text=json.dumps(list(queries)))


def _config() -> AgentConfig:
    return AgentConfig()


def _payload(items: list[dict]) -> str:
    return json.dumps(items)


# --- grounded by default ---


def test_discover_calls_llm_grounded_by_default():
    llm = FakeLLMClient(LLMResponse(text=_payload([]), sources=[]))
    source = LLMOpportunitySource("llm_source", llm, _config())

    asyncio.run(source.discover({}))

    assert llm.last_kwargs["grounded"] is True


def test_discover_respects_grounded_false():
    llm = FakeLLMClient(LLMResponse(text=_payload([]), sources=[]))
    source = LLMOpportunitySource("llm_source", llm, _config(), grounded=False)

    asyncio.run(source.discover({}))

    assert llm.last_kwargs["grounded"] is False


# --- link only trusted if it's a real grounding citation ---


def test_grounded_link_kept_when_it_matches_a_real_citation():
    real_url = "https://example.com/real-thread"
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [{"topic": "widgets", "signal_strength": 0.8, "reason": "trending", "link": real_url}]
            ),
            sources=[real_url],
            grounded=True,
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["link"] == real_url
    assert opportunity["raw"]["grounding_sources"] == [real_url]


def test_grounded_link_dropped_when_not_a_real_citation():
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [
                    {
                        "topic": "widgets",
                        "signal_strength": 0.8,
                        "reason": "trending",
                        "link": "https://example.com/made-up",
                    }
                ]
            ),
            sources=["https://example.com/a-different-real-page"],
            grounded=True,
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["link"] == ""


def test_ungrounded_link_passes_through_unverified():
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [{"topic": "widgets", "signal_strength": 0.8, "reason": "trending", "link": "https://x.test/y"}]
            ),
            sources=[],
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config(), grounded=False)

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["link"] == "https://x.test/y"


def test_a_grounded_call_that_cited_nothing_still_drops_the_link():
    """Grounding ran and returned no citations, so a link the model claims is
    unverifiable — exactly the case link-checking exists for."""
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [{"topic": "widgets", "signal_strength": 0.8, "reason": "trending",
                  "link": "https://example.com/made-up"}]
            ),
            sources=[],
            grounded=True,
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["link"] == ""


def test_links_survive_a_provider_that_cannot_ground():
    """The bug this guards: a provider that ignores grounded= returns no sources,
    which used to be read as "nothing was cited" — silently stripping every link
    from every opportunity while the run still reported success. An unperformed
    grounding is not a failed verification."""
    real_url = "https://example.com/a-real-thread"
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [{"topic": "widgets", "signal_strength": 0.8, "reason": "trending", "link": real_url}]
            ),
            sources=[],       # this provider never returns citations...
            grounded=False,   # ...because it does not ground at all
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())  # grounded=True by default

    [opportunity] = asyncio.run(source.discover({}))

    assert llm.last_kwargs["grounded"] is True   # it did ask
    assert opportunity["raw"]["link"] == real_url  # and kept the data anyway


# --- step 1: a configured SearchClient wins ---


def _opportunity(link: str, topic: str = "widgets") -> str:
    return json.dumps([{"topic": topic, "signal_strength": 0.8, "reason": "trending", "link": link}])


def test_search_results_are_searched_for_and_fed_into_the_prompt():
    """The model writes the queries (a run usually has no seed keyword — that is
    what discovery is for), they get searched, and the real results go into the
    discovery prompt."""
    search = FakeSearchClient(results=[_result("https://example.test/thread", "A real thread")])
    llm = ScriptedLLMClient(
        _queries_response("anonymous forums 2026", "reddit alternatives"),
        LLMResponse(text=_opportunity("https://example.test/thread")),
    )
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    [opportunity] = asyncio.run(source.discover({}))

    # Sorted, not in order: the queries go out concurrently, so which one reaches
    # the client first is a race — the *results* keep argument order (gather does),
    # which is what test_results_are_deduplicated_across_queries_and_capped pins.
    assert sorted(search.queries) == ["anonymous forums 2026", "reddit alternatives"]
    assert "https://example.test/thread" in llm.prompts[1]
    assert "A real thread" in llm.prompts[1]
    assert opportunity["raw"]["link"] == "https://example.test/thread"


def test_search_grounding_does_not_also_ask_the_model_to_ground():
    """Searching twice for one answer costs money and latency and makes "which
    URLs are trustworthy?" ambiguous — the facts are already in the prompt."""
    search = FakeSearchClient(results=[_result("https://example.test/a")])
    llm = ScriptedLLMClient(_queries_response("q"), LLMResponse(text=_opportunity("")))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    asyncio.run(source.discover({}))

    assert [kwargs["grounded"] for kwargs in llm.kwargs] == [False, False]


def test_a_link_that_is_not_a_search_result_is_dropped():
    """Same rule as a grounding citation: an unverifiable URL is indistinguishable
    from an invented one."""
    search = FakeSearchClient(results=[_result("https://example.test/real")])
    llm = ScriptedLLMClient(
        _queries_response("q"), LLMResponse(text=_opportunity("https://example.test/invented")),
    )
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["link"] == ""
    assert opportunity["raw"]["grounding_sources"] == ["https://example.test/real"]


def test_configured_queries_skip_the_query_writing_call():
    """A tenant that knows what it wants watched shouldn't pay for a model call
    to be told."""
    search = FakeSearchClient(results=[_result("https://example.test/a")])
    llm = ScriptedLLMClient(LLMResponse(text=_opportunity("")))
    source = LLMOpportunitySource(
        "llm_source", llm, _config(), search=search, search_queries=["fixed query", "  "],
    )

    asyncio.run(source.discover({}))

    assert search.queries == ["fixed query"]
    assert len(llm.prompts) == 1


def test_results_are_deduplicated_across_queries_and_capped():
    """Queries overlap on purpose (different angles on one product), so the same
    page comes back more than once; sending it twice just spends tokens."""
    search = FakeSearchClient(by_query={
        "a": [_result("https://example.test/1"), _result("https://example.test/2")],
        "b": [_result("https://example.test/2"), _result("https://example.test/3")],
    })
    llm = ScriptedLLMClient(_queries_response("a", "b"), LLMResponse(text="[]"))
    source = LLMOpportunitySource(
        "llm_source", llm, _config(), search=search, max_search_results=2,
    )

    asyncio.run(source.discover({}))

    prompt = llm.prompts[1]
    assert prompt.count("https://example.test/2") == 1
    assert "https://example.test/3" not in prompt


def test_a_seed_keyword_is_the_fallback_query():
    """No usable queries from the model, but the caller did say what they want —
    searching that beats not searching."""
    search = FakeSearchClient(results=[_result("https://example.test/a")])
    llm = ScriptedLLMClient(LLMResponse(text="not json at all"), LLMResponse(text="[]"))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    asyncio.run(source.discover({"seed_keyword": "static site seo"}))

    assert search.queries == ["static site seo"]


def test_no_queries_and_no_seed_keyword_means_no_search():
    """Rather than guessing at a query from the brand description and grounding
    the run in whatever that happened to find."""
    search = FakeSearchClient(results=[_result("https://example.test/a")])
    llm = ScriptedLLMClient(LLMResponse(text="not json at all"), LLMResponse(text="[]"))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    asyncio.run(source.discover({}))

    assert search.queries == []


# --- which step ran is recorded, not left to be guessed ---


def test_each_opportunity_records_which_grounding_it_got():
    """The failure this exists for: a search outage turns verified links into
    unverified ones while the run still reports success, and nothing in the output
    says which of the two you're reading. Found the hard way on a real run."""
    search = FakeSearchClient(results=[_result("https://example.test/a")])
    llm = ScriptedLLMClient(_queries_response("q"), LLMResponse(text=_opportunity("")))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["grounding"] == "search"
    assert "grounding_error" not in opportunity["raw"]


def test_a_search_outage_is_recorded_on_what_it_degraded_to():
    search = FakeSearchClient(error=RuntimeError("rate limited"))
    llm = ScriptedLLMClient(_queries_response("q"), LLMResponse(text=_opportunity("")))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["grounding"] == "none"   # the mock LLM cannot ground either
    assert "rate limited" in opportunity["raw"]["grounding_error"]


def test_one_failed_query_among_several_is_not_a_degraded_run():
    """The prompt still got real results — reporting that as degraded would make
    the signal useless."""
    calls = {"n": 0}

    class FlakySearch(FakeSearchClient):
        def search(self, query, limit=10):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("one bad query")
            return [_result("https://example.test/ok")]

    llm = ScriptedLLMClient(_queries_response("a", "b"), LLMResponse(text=_opportunity("")))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=FlakySearch())

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["grounding"] == "search"
    assert "grounding_error" not in opportunity["raw"]


def test_the_models_own_grounding_is_recorded_as_such():
    llm = FakeLLMClient(LLMResponse(text=_opportunity(""), sources=["https://x.test"], grounded=True))
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["raw"]["grounding"] == "llm"


# --- falling through to step 2 and step 3 ---


def test_a_failing_search_falls_back_to_the_models_own_grounding():
    """Search is an outbound call to somebody else's service. Losing it costs this
    source its search grounding, not its results."""
    search = FakeSearchClient(error=RuntimeError("duckduckgo is having a day"))
    llm = ScriptedLLMClient(
        _queries_response("q"),
        LLMResponse(text=_opportunity("https://example.test/cited"),
                    sources=["https://example.test/cited"], grounded=True),
    )
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    [opportunity] = asyncio.run(source.discover({}))

    assert llm.kwargs[1]["grounded"] is True   # step 2, not a failed run
    assert opportunity["raw"]["link"] == "https://example.test/cited"


def test_a_search_that_finds_nothing_falls_back_the_same_way():
    search = FakeSearchClient(results=[])
    llm = ScriptedLLMClient(_queries_response("q"), LLMResponse(text="[]", grounded=True))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search)

    asyncio.run(source.discover({}))

    assert llm.kwargs[1]["grounded"] is True


def test_no_search_client_configured_is_the_old_behavior_exactly():
    """search_provider="none", and every existing caller that passes no search at
    all — step 2 unchanged."""
    llm = FakeLLMClient(LLMResponse(text="[]", sources=[]))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=None)

    asyncio.run(source.discover({}))

    assert llm.last_kwargs["grounded"] is True


def test_grounded_false_skips_search_entirely():
    """Opting out of grounding opts out of all of it — not just the model's half,
    which would leave a "ungrounded" source making network calls."""
    search = FakeSearchClient(results=[_result("https://example.test/a")])
    llm = ScriptedLLMClient(LLMResponse(text=_opportunity("https://x.test/y")))
    source = LLMOpportunitySource("llm_source", llm, _config(), search=search, grounded=False)

    [opportunity] = asyncio.run(source.discover({}))

    assert search.queries == []
    assert len(llm.prompts) == 1
    assert opportunity["raw"]["link"] == "https://x.test/y"   # unverified, passed through


# --- malformed items dropped individually, not raised ---


def test_malformed_item_dropped_others_kept():
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [
                    {"topic": "", "signal_strength": 0.9, "reason": "no topic, dropped"},
                    {"topic": "good one", "signal_strength": "not-a-number", "reason": "kept, coerced"},
                ]
            ),
            sources=[],
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    opportunities = asyncio.run(source.discover({}))

    assert len(opportunities) == 1
    assert opportunities[0]["topic"] == "good one"
    assert opportunities[0]["signal_strength"] == 0.5  # coerced default, not raised


def test_out_of_range_signal_strength_is_clamped():
    llm = FakeLLMClient(
        LLMResponse(text=_payload([{"topic": "widgets", "signal_strength": 5.0}]), sources=[])
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["signal_strength"] == 1.0


def test_invalid_intent_and_channel_hint_fall_back_to_defaults():
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [{"topic": "widgets", "intent": "not-a-real-intent", "suggested_channel_hint": "not-a-channel"}]
            ),
            sources=[],
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = asyncio.run(source.discover({}))

    assert opportunity["intent"] == "informational"
    assert opportunity["suggested_channel_hint"] is None


# --- normalize_opportunity directly, since discover.py applies it to every
# source (mock/llm/custom), not only the llm one ---


def test_normalize_opportunity_rejects_non_dict():
    assert normalize_opportunity("not a dict", source="custom_source") is None


def test_normalize_opportunity_rejects_missing_topic():
    assert normalize_opportunity({"signal_strength": 0.5}, source="custom_source") is None


def test_normalize_opportunity_overrides_claimed_source():
    opportunity = normalize_opportunity({"topic": "x", "source": "spoofed"}, source="real_source")
    assert opportunity["source"] == "real_source"
