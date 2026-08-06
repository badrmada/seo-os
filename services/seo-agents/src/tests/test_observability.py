"""Covers verbose mode (PLAN.md Step 7): the redaction rules, the tool/stage
wrapping hooks, and the three guarantees that make it safe to leave in the code
path — output goes to stderr only, a broken reporter never fails a run, and
reporting off costs nothing."""

import asyncio
import io
import json

import pytest

from agent.config.agent_config import AgentConfig
from agent.graph.tools import Tools
from agent.managers.run_manager import AgentRunner
from agent.observability import NullReporter, build_reporter, observe_tools
from agent.observability.redaction import looks_secret, preview, redact
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.search_performance_null import NullSearchPerformanceClient
from tools.mocks.opportunity_mock import MockOpportunitySource
from tools.mocks.traffic_mock import MockTrafficClient


def _tools(discovery_sources=None) -> Tools:
    return Tools(
        search_performance=NullSearchPerformanceClient(),
        analytics=MockAppAnalyticsClient(),
        traffic=MockTrafficClient(),
        llm=MockLLMClient(),
        discovery_sources=discovery_sources or {},
    )


def _run(config, reporter, tools=None) -> dict:
    return AgentRunner(config, tools=tools or _tools(), reporter=reporter).run(
        {"seed_keyword": "static site seo"}
    )


def _events(stream) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# --- redaction -------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["gemini_api_key", "cloudflare_api_token", "key_file", "X-Api-Key",
     "Authorization", "password", "dsn", "sessionSecret"],
)
def test_secret_field_names_are_recognized(name):
    assert looks_secret(name) is True


@pytest.mark.parametrize(
    "name",
    # Each of these trips a naive substring check ("key" in "seed_keyword",
    # "token" in "tokens", "auth" in "author") — the exact fields verbose mode
    # exists to show, so segment matching has to keep them visible.
    ["seed_keyword", "keyword", "chosen_keyword", "tokens", "author", "topics", "stage"],
)
def test_non_secret_field_names_are_not_redacted(name):
    assert looks_secret(name) is False


def test_redact_replaces_secret_values_at_any_depth():
    redacted = redact({"api_headers": {"Authorization": "Bearer abc123"}, "days": 28})
    assert redacted["api_headers"]["Authorization"] != "Bearer abc123"
    assert redacted["days"] == 28


def test_preview_truncates_and_flattens():
    assert preview("a\nb  c") == "a b c"
    assert len(preview("x" * 5000)) < 500


# --- the three safety guarantees -------------------------------------------

def test_reporting_off_leaves_the_tools_bundle_untouched():
    """NullReporter must not put proxies in the call path at all — not disabled
    ones. This is what makes verbose mode free when it's off."""
    tools = _tools()
    assert observe_tools(tools, NullReporter()) is tools


def test_default_run_emits_nothing():
    stream = io.StringIO()
    _run(AgentConfig(), build_reporter(0, "text", stream=stream))
    assert stream.getvalue() == ""


def test_a_reporter_that_raises_never_fails_the_run():
    """A formatting bug must degrade to no output, not to a failed run — same
    principle as the pipeline's own degrade-don't-abort handling."""
    reporter = build_reporter(2, "text", stream=io.StringIO())
    reporter._format = lambda *args, **kwargs: 1 / 0

    result = _run(AgentConfig(), reporter)

    assert result["phase"] == "done"
    assert result["error"] is None


# --- what actually gets reported -------------------------------------------

def test_a_run_reports_its_stages_and_tool_calls():
    stream = io.StringIO()
    _run(AgentConfig(), build_reporter(1, "json", stream=stream))
    events = _events(stream)

    kinds = [event["event"] for event in events]
    assert kinds[0] == "run_start"
    assert kinds[-1] == "run_end"
    assert {"stage_start", "stage_end", "tool_start", "tool_end"} <= set(kinds)

    stages = {event["stage"] for event in events if event["event"] == "stage_end"}
    assert stages == {"analyze", "draft", "self_qa"}  # the zero-discovery shape

    assert all("elapsed_ms" in e for e in events if e["event"] in ("stage_end", "tool_end"))
    assert events[-1]["phase"] == "done"


def test_discovery_stages_and_branch_labels_are_reported():
    config = AgentConfig(discovery_sources=[
        {"name": "trends", "provider": "mock"},
        {"name": "forums", "provider": "mock"},
    ])
    tools = _tools({
        "trends": MockOpportunitySource("trends"),
        "forums": MockOpportunitySource("forums"),
    })
    stream = io.StringIO()
    _run(config, build_reporter(1, "json", stream=stream), tools=tools)
    events = _events(stream)

    # Under the parallel_by_source fan-out, concurrent branches interleave — every
    # event has to say which source it belongs to or the stream is unreadable.
    branches = {e.get("branch") for e in events if e["event"] == "stage_end" and e["stage"] == "discover_source"}
    assert branches == {"trends", "forums"}


def test_a_failing_tool_is_reported_and_the_run_still_succeeds():
    config = AgentConfig(discovery_sources=[{"name": "broken", "provider": "mock", "fail": True}])
    tools = _tools({"broken": MockOpportunitySource("broken", fail=True)})
    stream = io.StringIO()
    result = _run(config, build_reporter(1, "json", stream=stream), tools=tools)

    errors = [e for e in _events(stream) if e["event"] == "tool_error"]
    assert [e["tool"] for e in errors] == ["broken"]
    # The proxy re-raises unchanged, so DiscoverStage's own handling still applies.
    assert result["phase"] == "done"
    assert len(result["discovery"]["tool_errors"]) == 1


def test_payload_previews_only_appear_at_level_2():
    quiet, loud = io.StringIO(), io.StringIO()
    _run(AgentConfig(), build_reporter(1, "json", stream=quiet))
    _run(AgentConfig(), build_reporter(2, "json", stream=loud))

    def llm_start(stream):
        return next(e for e in _events(stream) if e["event"] == "tool_start" and e["tool"] == "llm")

    assert "prompt" not in llm_start(quiet)
    assert llm_start(loud)["prompt"]


def test_grounding_that_produced_no_sources_is_visible():
    """A grounded call returning zero sources is the condition that used to make
    LLMOpportunitySource silently drop every link (PLAN.md Step 2a). The dropping
    is fixed; reporting the call's shape is what keeps it diagnosable."""
    stream = io.StringIO()
    reporter = build_reporter(1, "json", stream=stream)
    llm = observe_tools(_tools(), reporter).llm

    asyncio.run(llm.generate("find me some topics", grounded=True))

    end = next(e for e in _events(stream) if e["event"] == "tool_end")
    assert end["grounded"] is True
    assert end["sources"] == 0


def test_proxies_delegate_unknown_attributes():
    """The proxies must be transparent for anything they don't instrument, so a
    client with extra public methods keeps working behind one."""
    tools = _tools({"trends": MockOpportunitySource("trends")})
    observed = observe_tools(tools, build_reporter(1, "text", stream=io.StringIO()))
    assert observed.discovery_sources["trends"].name == "trends"


def test_json_format_is_one_parseable_object_per_line():
    stream = io.StringIO()
    _run(AgentConfig(), build_reporter(1, "json", stream=stream))
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) > 1
    assert all(isinstance(json.loads(line), dict) for line in lines)


def test_unknown_verbose_format_fails_fast():
    with pytest.raises(ValueError):
        build_reporter(1, "yaml")


def test_unperformed_grounding_is_reported_as_a_tool_error():
    """Degrading to ungrounded discovery changes what the results mean, so it is
    never silent — see PLAN.md Step 2a and tools/llm/base.py's LLMResponse.grounded."""
    stream = io.StringIO()
    reporter = build_reporter(1, "json", stream=stream)
    llm = observe_tools(_tools(), reporter).llm  # MockLLMClient: does not ground

    asyncio.run(llm.generate("find me some topics", grounded=True))

    events = _events(stream)
    errors = [e for e in events if e["event"] == "tool_error"]
    assert len(errors) == 1
    assert "did not perform it" in errors[0]["error"]
    assert next(e for e in events if e["event"] == "tool_end")["grounding_unsupported"] is True


def test_a_provider_that_grounds_produces_no_such_warning():
    stream = io.StringIO()
    reporter = build_reporter(1, "json", stream=stream)

    class GroundingClient:
        def generate(self, prompt, *, model=None, grounded=False):
            from tools.llm.base import LLMResponse
            return LLMResponse(text="{}", grounded=grounded, sources=["https://x.test"])

    tools = _tools()
    tools.llm = GroundingClient()
    asyncio.run(observe_tools(tools, reporter).llm.generate("x", grounded=True))

    assert not [e for e in _events(stream) if e["event"] == "tool_error"]
