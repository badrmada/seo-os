"""Covers the "analyze's analytics/traffic calls running concurrently with
discovery" roadmap item: AnalyzeContextStage runs the channel-independent
analytics/traffic calls as its own node; AnalyzeStage uses that precomputed
result instead of re-fetching when it's present, and falls back to fetching
directly (unchanged, original behavior) when it's not — see
tests/test_pipeline.py for the end-to-end graph-level join tests."""

import asyncio

from agent.config.agent_config import AgentConfig
from agent.graph.stages.analyze import AnalyzeContextStage, AnalyzeStage
from agent.graph.tools import Tools
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.search_performance_null import NullSearchPerformanceClient
from tools.mocks.traffic_mock import MockTrafficClient


class _ExplodingAnalytics:
    """Raises if called at all — used to prove AnalyzeStage skips its own fetch
    when analyze_context was already computed upstream."""

    def report(self, limit: int = 5) -> dict:
        raise AssertionError("AnalyzeStage should not call analytics.report() when analyze_context is set")


class _ExplodingTraffic:
    def traffic_summary(self, days: int = 28) -> dict:
        raise AssertionError("AnalyzeStage should not call traffic.traffic_summary() when analyze_context is set")


def _tools(analytics=None, traffic=None) -> Tools:
    return Tools(
        search_performance=NullSearchPerformanceClient(),
        analytics=analytics or MockAppAnalyticsClient(),
        traffic=traffic or MockTrafficClient(),
        llm=MockLLMClient(),
        discovery_sources={},
    )


def _config() -> AgentConfig:
    return AgentConfig()


def test_analyze_context_stage_populates_analyze_context_key():
    stage = AnalyzeContextStage(_tools(), _config())

    result = asyncio.run(stage.run({"input": {}, "working": {}}))

    context = result["analyze_context"]
    assert context["analytics_summary"]
    assert context["traffic_summary"]
    assert context["analytics_highlights"]
    assert context["tool_errors"] == []


def test_analyze_context_stage_degrades_on_failure_without_raising():
    class _FailingAnalytics:
        def report(self, limit: int = 5) -> dict:
            raise RuntimeError("boom")

    stage = AnalyzeContextStage(_tools(analytics=_FailingAnalytics()), _config())

    result = asyncio.run(stage.run({"input": {}, "working": {}}))

    context = result["analyze_context"]
    assert context["analytics_summary"] == ""
    assert context["analytics_highlights"] == []
    assert len(context["tool_errors"]) == 1
    assert context["tool_errors"][0]["tool"] == "analytics"
    assert context["tool_errors"][0]["node"] == "analyze"  # public schema node name, unchanged


def test_analyze_stage_uses_precomputed_analyze_context_without_refetching():
    stage = AnalyzeStage(_tools(analytics=_ExplodingAnalytics(), traffic=_ExplodingTraffic()), _config())
    state = {
        "input": {"channel": "engagement_comment", "context_text": "hi"},
        "working": {},
        "analyze_context": {
            "analytics_summary": "precomputed summary",
            "analytics_highlights": [{"label": "x", "url": "y"}],
            "traffic_summary": "precomputed traffic",
            "tool_errors": [],
        },
    }

    result = asyncio.run(stage.run(state))

    working = result["working"]
    assert working["analytics_summary"] == "precomputed summary"
    assert working["traffic_summary"] == "precomputed traffic"


def test_analyze_stage_fetches_directly_when_no_analyze_context():
    stage = AnalyzeStage(_tools(), _config())
    state = {"input": {"channel": "engagement_comment", "context_text": "hi"}, "working": {}}

    result = asyncio.run(stage.run(state))

    working = result["working"]
    assert working["analytics_summary"]
    assert working["traffic_summary"]
