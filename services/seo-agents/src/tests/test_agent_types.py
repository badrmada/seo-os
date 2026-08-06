"""Covers PLAN.md Step G: config-declared stages, a pipeline spec per agent type,
and `--agent` selecting one.

The bar this step set for itself is concrete — *a tenant should be able to build a
site audit on it without touching `src/`* — so the end of this file builds one:
three stages from a tenant's own plugins folder, producing `kind: "site_audit"`
in the frozen result schema, with no built-in stage involved and nothing in `src/`
knowing the agent type exists.
"""

import asyncio
import json

import pytest

from agent.config import AgentConfigLoader
from agent.config.agent_config import AgentConfig
from agent.graph.pipeline import (
    DEFAULT_AGENT_TYPE,
    PipelineSpec,
    PipelineStage,
    agent_types,
    build_graph,
    spec_for,
)
from agent.graph.tools import Tools
from agent.managers import AgentRunner
from agent.service import AgentService, RunRequest, RunRequestError
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.opportunity_mock import MockOpportunitySource
from tools.mocks.search_performance_null import NullSearchPerformanceClient
from tools.mocks.traffic_mock import MockTrafficClient

AUDIT_PLUGIN = '''
class CrawlStage:
    def __init__(self, tools, config, options):
        self.pages = options["pages"]

    async def run(self, state):
        working = dict(state.get("working", {}))
        working["pages"] = self.pages
        return {"phase": "crawl", "working": working}


class FindingsStage:
    def __init__(self, tools, config):
        self.config = config

    async def run(self, state):
        working = dict(state["working"])
        working["findings"] = [
            {"issue": "missing meta description", "severity": "high",
             "urls": [p["url"] for p in working["pages"] if not p.get("meta")]}
        ]
        return {"phase": "findings", "working": working}


class VerifyStage:
    """Sync on purpose — a tenant's stage is not forced to be async."""

    def __init__(self, tools, config):
        self.config = config

    def run(self, state):
        working = dict(state["working"])
        crawled = {p["url"] for p in working["pages"]}
        findings = [f for f in working["findings"] if set(f["urls"]) <= crawled]
        return {
            "phase": "done",
            "working": working,
            "output": {
                "kind": "site_audit",
                "title": "Site audit",
                "content": "# findings",
                "format": "markdown",
                "metadata": {"findings": findings, "pages_crawled": len(working["pages"])},
            },
        }
'''

PAGES = [
    {"url": "https://example.com/a", "meta": "yes"},
    {"url": "https://example.com/b", "meta": ""},
]

AUDIT_STAGES = [
    {"name": "crawl", "class": "audit:CrawlStage", "options": {"pages": PAGES}},
    {"name": "findings", "class": "audit:FindingsStage"},
    {"name": "verify", "class": "audit:VerifyStage"},
]


def _tenant(tmp_path, config: dict, plugin: str = AUDIT_PLUGIN):
    (tmp_path / "plugins").mkdir(exist_ok=True)
    (tmp_path / "plugins" / "audit.py").write_text(plugin, encoding="utf-8")
    (tmp_path / "tenant.json").write_text(json.dumps(config), encoding="utf-8")
    return AgentConfigLoader().load(str(tmp_path / "tenant.json"))


def _audit_config(tmp_path, **overrides):
    return _tenant(tmp_path, {
        "llm_provider": "mock",
        "analytics_provider": "mock",
        "agent_type": "site_audit",
        "pipelines": {"site_audit": {"stages": AUDIT_STAGES}},
        **overrides,
    })


def _tools(discovery_sources=None) -> Tools:
    return Tools(
        search_performance=NullSearchPerformanceClient(),
        analytics=MockAppAnalyticsClient(),
        traffic=MockTrafficClient(),
        llm=MockLLMClient(),
        discovery_sources=discovery_sources or {},
    )


# --- the built-in agent type is unchanged --------------------------------------


def test_default_agent_type_still_produces_the_seo_content_shapes():
    """The three shapes this repo has always had must be exactly what a config
    with no `pipelines` gets — nothing about a zero-config tenant changes."""
    config = AgentConfig()
    spec = spec_for(config)

    assert spec.agent_type == DEFAULT_AGENT_TYPE
    assert [stage.name for stage in spec.stages] == ["analyze", "draft", "self_qa"]


def test_default_agent_type_with_discovery_is_unchanged():
    config = AgentConfig(discovery_sources=[
        {"name": "a", "provider": "mock"}, {"name": "b", "provider": "mock"},
    ])
    spec = spec_for(config)

    assert [stage.name for stage in spec.stages] == [
        "discover", "choose_channel", "analyze_context", "analyze", "draft", "self_qa",
    ]
    assert spec.stages[0].mode == "parallel_by_source"
    assert spec.stages[2].mode == "concurrent_from_start"


def test_seo_content_is_channel_aware_and_an_audit_is_not(tmp_path):
    assert spec_for(AgentConfig()).channel_aware is True
    assert spec_for(_audit_config(tmp_path)).channel_aware is False


# --- selecting an agent type ---------------------------------------------------


def test_agent_types_lists_the_builtin_and_the_declared_ones(tmp_path):
    assert agent_types(_audit_config(tmp_path)) == ["seo_content", "site_audit"]


def test_a_run_can_select_the_other_agent_type(tmp_path):
    """The tenant's default is site_audit; asking for seo_content gets the
    built-in pipeline out of the same config."""
    config = _audit_config(tmp_path)
    assert spec_for(config).agent_type == "site_audit"
    assert spec_for(config, "seo_content").agent_type == "seo_content"


def test_an_unknown_agent_type_raises_rather_than_falling_back(tmp_path):
    """"My audit ran and produced an article" is the worst possible answer to a
    typo."""
    with pytest.raises(ValueError, match="unknown agent type"):
        spec_for(_audit_config(tmp_path), "site_audti")


def test_a_config_whose_agent_type_has_no_pipeline_fails_at_load(tmp_path):
    with pytest.raises(ValueError, match="has no pipeline"):
        _tenant(tmp_path, {"llm_provider": "mock", "agent_type": "site_audit"})


def test_the_service_rejects_an_unknown_agent_type_as_a_request_error(tmp_path):
    """Nothing was attempted, so this is a 4xx, not a failed run."""
    request = RunRequest(config=_audit_config(tmp_path), input={}, agent_type="nope")
    with pytest.raises(RunRequestError, match="unknown agent type"):
        AgentService().execute(request)


def test_the_service_override_does_not_write_back_to_the_config(tmp_path):
    config = _audit_config(tmp_path)
    AgentService().execute(RunRequest(
        config=config, input={}, agent_type="site_audit", stdout=None, warn_stream=None,
    ))
    assert config.agent_type == "site_audit"


# --- config-declared stages ----------------------------------------------------


def test_a_declared_pipeline_may_reuse_builtin_stages_by_name(tmp_path):
    config = _tenant(tmp_path, {
        "llm_provider": "mock",
        "agent_type": "draft_only",
        "pipelines": {"draft_only": {"stages": [{"name": "analyze"}, {"name": "draft"}]}},
    })
    spec = spec_for(config)

    assert [stage.name for stage in spec.stages] == ["analyze", "draft"]
    assert spec.channel_aware is True


def test_a_stage_with_no_class_and_no_builtin_name_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="no built-in stage named"):
        _tenant(tmp_path, {
            "agent_type": "x",
            "pipelines": {"x": {"stages": [{"name": "nonesuch"}]}},
        })


def test_duplicate_stage_names_are_rejected(tmp_path):
    """They become LangGraph node names, which must be unique — and the second
    add_node would otherwise silently replace the first."""
    with pytest.raises(ValueError, match="duplicate stage name"):
        _tenant(tmp_path, {
            "agent_type": "x",
            "pipelines": {"x": {"stages": [{"name": "analyze"}, {"name": "analyze"}]}},
        })


def test_an_unimportable_stage_class_is_named_when_the_pipeline_is_built(tmp_path):
    """Not at config load: importing a plugin executes the tenant's Python, and a
    server loading a config per request must not run the code of pipelines this
    request isn't using. `check-data` builds every spec precisely so this isn't
    left to a real run to discover."""
    config = _tenant(tmp_path, {
        "agent_type": "x",
        "pipelines": {"x": {"stages": [{"name": "s", "class": "missing:Stage"}]}},
    })
    with pytest.raises(ValueError, match="no plugin"):
        spec_for(config)


def test_an_unknown_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown mode"):
        _tenant(tmp_path, {
            "agent_type": "x",
            "pipelines": {"x": {"stages": [{"name": "analyze", "mode": "whenever"}]}},
        })


def test_an_empty_pipeline_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        _tenant(tmp_path, {"agent_type": "x", "pipelines": {"x": {"stages": []}}})


# --- the mode/name coupling is gone --------------------------------------------


def test_parallel_by_source_now_keys_off_a_declaration_not_the_name(tmp_path):
    """Before this step, build_graph raised unless the stage was literally named
    "discover"; now the requirement is that the class declares a fan-out."""
    spec = PipelineSpec(stages=(PipelineStage("analyze", mode="parallel_by_source"),))
    with pytest.raises(ValueError, match="must declare fanout_over"):
        build_graph(_tools(), AgentConfig(), spec=spec)


def test_a_stage_declaring_a_fanout_may_use_the_mode_whatever_it_is_called():
    """The discover stage's own classes, registered under a different node name —
    what a tenant's fan-out stage would do."""
    from agent.graph.stages.discover import DiscoverStage

    spec = PipelineSpec(stages=(
        PipelineStage("scan", mode="parallel_by_source", cls=DiscoverStage),
        PipelineStage("analyze"),
        PipelineStage("draft"),
        PipelineStage("self_qa"),
    ))
    tools = _tools({"a": MockOpportunitySource("a"), "b": MockOpportunitySource("b")})

    graph = build_graph(tools, AgentConfig(), spec=spec)
    result = asyncio.run(graph.ainvoke({"input": {"seed_keyword": "widgets"}, "working": {}}))

    assert result["phase"] == "done"
    assert len(result["working"]["opportunities"]) == 2


def test_concurrent_from_start_now_works_for_any_stage_but_needs_a_join():
    """Its requirement is structural, not a name: something must follow it. Left
    dangling, the branch runs and its writes land in the same superstep as END —
    which LangGraph does not report, it just loses whatever the stage produced."""
    spec = PipelineSpec(stages=(PipelineStage("analyze_context", mode="concurrent_from_start"),))
    with pytest.raises(ValueError, match="nothing follows them"):
        build_graph(_tools(), AgentConfig(), spec=spec)


# --- a stage's own options -----------------------------------------------------


def test_a_stage_receives_its_options_when_it_asks_for_them(tmp_path):
    config = _audit_config(tmp_path)
    result = AgentRunner(config, tools=_tools()).run({})

    assert result["output"]["metadata"]["pages_crawled"] == len(PAGES)


def test_a_stage_that_does_not_ask_for_options_is_built_with_two_arguments(tmp_path):
    """FindingsStage takes (tools, config) only — the signature inspection has to
    not pass it a third argument, exactly as for a `"custom"` provider."""
    config = _audit_config(tmp_path)
    assert AgentRunner(config, tools=_tools()).run({})["phase"] == "done"


# --- the whole point: a site audit, no src/ change -----------------------------


def test_a_tenant_declared_audit_runs_end_to_end_and_keeps_the_result_shape(tmp_path):
    config = _audit_config(tmp_path)

    result = AgentRunner(config, tools=_tools()).run({"seed_keyword": "anything"})

    assert result["phase"] == "done"
    assert result["agent_type"] == "site_audit"
    # A new deliverable is a new `kind`, not a new top-level field.
    assert result["output"]["kind"] == "site_audit"
    assert result["output"]["metadata"]["findings"][0]["urls"] == ["https://example.com/b"]
    assert set(result) == {
        "run_id", "agent_type", "phase", "input", "output", "discovery", "usage", "error",
    }


def test_an_audit_run_has_no_channel_invented_for_it(tmp_path):
    """Otherwise the input would carry "site_article", and every signal reading the
    run's input would be told this audit is drafting an article."""
    result = AgentRunner(_audit_config(tmp_path), tools=_tools()).run({})

    assert "channel" not in result["input"]


def test_an_audit_still_reports_the_frozen_discovery_block(tmp_path):
    """Empty rather than absent: a caller parsing a result never has to branch on
    which agent produced it."""
    result = AgentRunner(_audit_config(tmp_path), tools=_tools()).run({})

    assert result["discovery"] == {
        "opportunities": [], "channel_decision": None, "tool_errors": [],
    }


def test_a_failing_audit_reports_its_own_agent_type(tmp_path):
    """A failure report that names the wrong agent is worse than one that names
    none, and this path is reached by exactly the failures worth attributing."""
    broken = AUDIT_PLUGIN.replace('working["pages"] = self.pages', 'raise RuntimeError("boom")')
    config = _tenant(tmp_path, {
        "llm_provider": "mock",
        "agent_type": "site_audit",
        "pipelines": {"site_audit": {"stages": AUDIT_STAGES}},
    }, plugin=broken)

    result = AgentRunner(config, tools=_tools()).run({})

    assert result["phase"] == "failed"
    assert result["agent_type"] == "site_audit"
    assert "boom" in result["error"]


def test_the_same_config_still_runs_the_built_in_agent_on_request(tmp_path):
    """One tenant, two deliverables — which is the argument for a spec per agent
    type rather than one global default."""
    config = _audit_config(tmp_path)
    config.agent_type = "seo_content"

    result = AgentRunner(config, tools=_tools()).run({"seed_keyword": "widgets"})

    assert result["agent_type"] == "seo_content"
    assert result["output"]["kind"] == "site_article"


# --- the internal fan-out key stays out of the result --------------------------


def test_discover_results_is_not_a_top_level_result_key():
    """LangGraph materializes every declared channel, so the Annotated fan-out key
    is present as [] even in a graph with no fan-out node — it was leaking into the
    returned JSON as an undocumented top-level field."""
    result = AgentRunner(AgentConfig(), tools=_tools()).run({"seed_keyword": "widgets"})

    assert "discover_results" not in result
