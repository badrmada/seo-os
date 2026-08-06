"""Covers the "parallel discovery fan-out" roadmap item: _default_spec's mode
selection, the untouched sequential DiscoverStage path, the new
DiscoverSourceStage/DiscoverJoinStage pair in isolation, and an end-to-end
build_graph().invoke() run with 2+ discovery sources, checked against what an
equivalent sequential run produces."""

import asyncio

from agent.config.agent_config import AgentConfig
from agent.graph.pipeline import PipelineStage, _default_spec, build_graph
from agent.graph.stages.discover import DiscoverJoinStage, DiscoverSourceStage, DiscoverStage
from agent.graph.tools import Tools
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.search_performance_null import NullSearchPerformanceClient
from tools.mocks.opportunity_mock import MockOpportunitySource
from tools.mocks.traffic_mock import MockTrafficClient


def _base_tools(discovery_sources=None) -> Tools:
    return Tools(
        search_performance=NullSearchPerformanceClient(),
        analytics=MockAppAnalyticsClient(),
        traffic=MockTrafficClient(),
        llm=MockLLMClient(),
        discovery_sources=discovery_sources or {},
    )


def _config(discovery_sources: list[dict]) -> AgentConfig:
    return AgentConfig(discovery_sources=discovery_sources)


# --- _default_spec mode selection ---


def test_default_spec_zero_sources_omits_discover():
    spec = _default_spec(_config([]))
    names = [s.name for s in spec.stages]
    assert "discover" not in names
    assert "choose_channel" not in names


def test_default_spec_one_source_is_sequential():
    spec = _default_spec(_config([{"name": "a", "provider": "mock"}]))
    discover_stage = next(s for s in spec.stages if s.name == "discover")
    assert discover_stage.mode == "sequential"


def test_default_spec_two_sources_is_parallel():
    spec = _default_spec(
        _config([{"name": "a", "provider": "mock"}, {"name": "b", "provider": "mock"}])
    )
    discover_stage = next(s for s in spec.stages if s.name == "discover")
    assert discover_stage.mode == "parallel_by_source"


def test_default_spec_zero_sources_omits_analyze_context():
    spec = _default_spec(_config([]))
    names = [s.name for s in spec.stages]
    assert "analyze_context" not in names


def test_default_spec_with_discovery_includes_analyze_context():
    spec = _default_spec(_config([{"name": "a", "provider": "mock"}]))
    analyze_context_stage = next(s for s in spec.stages if s.name == "analyze_context")
    assert analyze_context_stage.mode == "concurrent_from_start"


# --- sequential DiscoverStage, unchanged ---


class _MalformedItemSource:
    """Stands in for a buggy custom OpportunitySource that returns one good item
    and one malformed one in the same call — DiscoverStage must keep the good
    item via normalize_opportunity rather than let the bad one raise and lose
    both (see tests/test_opportunity_llm.py for normalize_opportunity itself)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def discover(self, context: dict) -> list[dict]:
        return [
            {"source": self.name, "topic": "", "signal_strength": 0.9, "reason": "no topic"},
            {"source": self.name, "topic": "real topic", "signal_strength": 0.9, "reason": "kept"},
        ]


def test_discover_stage_drops_malformed_item_keeps_the_rest():
    tools = _base_tools({"custom_source": _MalformedItemSource("custom_source")})
    stage = DiscoverStage(tools, _config([]))
    state = {"input": {"seed_keyword": "widgets"}, "working": {}}

    result = asyncio.run(stage.run(state))

    opportunities = result["working"]["opportunities"]
    assert len(opportunities) == 1
    assert opportunities[0]["topic"] == "real topic"
    assert result["working"]["tool_errors"] == []


def test_discover_stage_sequential_degrades_on_one_source_failing():
    tools = _base_tools(
        {"good": MockOpportunitySource("good"), "bad": MockOpportunitySource("bad", fail=True)}
    )
    stage = DiscoverStage(tools, _config([]))
    state = {"input": {"seed_keyword": "widgets"}, "working": {}}

    result = asyncio.run(stage.run(state))

    assert result["phase"] == "discover"
    opportunities = result["working"]["opportunities"]
    assert len(opportunities) == 1
    assert opportunities[0]["source"] == "good"
    tool_errors = result["working"]["tool_errors"]
    assert len(tool_errors) == 1
    assert tool_errors[0]["tool"] == "bad"
    assert tool_errors[0]["node"] == "discover"


# --- DiscoverSourceStage / DiscoverJoinStage in isolation ---


def test_discover_source_stage_success():
    tools = _base_tools({"good": MockOpportunitySource("good")})
    stage = DiscoverSourceStage(tools)

    result = asyncio.run(stage.run({"source_name": "good", "context": {"seed_keyword": "widgets"}}))

    [entry] = result["discover_results"]
    assert entry["tool"] == "good"
    assert len(entry["opportunities"]) == 1
    assert entry["tool_errors"] == []


def test_discover_source_stage_failure_degrades():
    tools = _base_tools({"bad": MockOpportunitySource("bad", fail=True)})
    stage = DiscoverSourceStage(tools)

    result = asyncio.run(stage.run({"source_name": "bad", "context": {}}))

    [entry] = result["discover_results"]
    assert entry["opportunities"] == []
    assert len(entry["tool_errors"]) == 1
    assert entry["tool_errors"][0]["tool"] == "bad"


def test_discover_join_stage_merges_all_branches():
    discover_results = [
        {"tool": "a", "opportunities": [{"source": "a"}], "tool_errors": []},
        {"tool": "b", "opportunities": [], "tool_errors": [{"tool": "b"}]},
    ]
    state = {"working": {}, "discover_results": discover_results}

    result = asyncio.run(DiscoverJoinStage().run(state))

    assert result["phase"] == "discover"
    assert result["working"]["opportunities"] == [{"source": "a"}]
    assert result["working"]["tool_errors"] == [{"tool": "b"}]


# --- end-to-end build_graph with 2+ sources ---


def test_build_graph_parallel_matches_sequential_merge_contract():
    discovery_config = [
        {"name": "good", "provider": "mock"},
        {"name": "bad", "provider": "mock", "fail": True},
    ]
    tools = _base_tools(
        {
            "good": MockOpportunitySource("good"),
            "bad": MockOpportunitySource("bad", fail=True),
        }
    )
    config = _config(discovery_config)
    graph = build_graph(tools, config)

    result = asyncio.run(graph.ainvoke(
        {
            "input": {"seed_keyword": "widgets"},
            "working": {},
        }
    ))

    opportunities = result["working"]["opportunities"]
    tool_errors = result["working"]["tool_errors"]
    assert len(opportunities) == 1
    assert opportunities[0]["source"] == "good"
    assert len(tool_errors) == 1
    assert tool_errors[0]["tool"] == "bad"


def test_build_graph_analyze_context_joins_correctly_with_one_source():
    """Sequential discover path (1 source): analyze_context (child of START) and
    discover -> choose_channel must both complete before "analyze" runs — this
    would previously raise langgraph.errors.InvalidUpdateError (two nodes writing
    the shared "phase"/"working" keys in the same superstep) if analyze fired as
    soon as analyze_context alone finished, instead of waiting for choose_channel
    too. Reaching "done" at all proves the join actually waited for both.
    """
    tools = _base_tools({"good": MockOpportunitySource("good")})
    config = _config([{"name": "good", "provider": "mock"}])
    graph = build_graph(tools, config)

    result = asyncio.run(graph.ainvoke(
        {
            "input": {"seed_keyword": "widgets"},
            "working": {},
        }
    ))

    assert result["phase"] == "done"
    assert result["working"]["analytics_summary"]
    assert result["working"]["traffic_summary"]


def test_build_graph_analyze_context_joins_correctly_with_parallel_sources():
    """Same join guarantee as above, but for the parallel_by_source discover path
    (2+ sources) — analyze_context runs alongside the Send fan-out + discover_join
    + choose_channel chain, at a different depth, and must still be waited on."""
    tools = _base_tools(
        {"a": MockOpportunitySource("a"), "b": MockOpportunitySource("b")}
    )
    config = _config([{"name": "a", "provider": "mock"}, {"name": "b", "provider": "mock"}])
    graph = build_graph(tools, config)

    result = asyncio.run(graph.ainvoke(
        {
            "input": {"seed_keyword": "widgets"},
            "working": {},
        }
    ))

    assert result["phase"] == "done"
    assert result["working"]["analytics_summary"]
    assert result["working"]["traffic_summary"]


def test_build_graph_rejects_parallel_mode_on_other_stages():
    tools = _base_tools()
    config = _config([])
    spec_cls = type(_default_spec(config))
    bad_spec = spec_cls(stages=(PipelineStage("analyze", mode="parallel_by_source"),))

    import pytest

    with pytest.raises(ValueError):
        build_graph(tools, config, spec=bad_spec)
