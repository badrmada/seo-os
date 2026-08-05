"""Covers PLAN.md Step 9 Part 1: a tenant config that doesn't depend on where the
process happens to be running, and one that doesn't have to come from a file at
all. Both are prerequisites for running several tenants in one server process."""

import asyncio
import json

import pytest

from agent.config import AgentConfigLoader
from agent.config.agent_config import AgentConfig
from agent.config.paths import resolve_path
from agent.managers import ToolsManager
from agent.managers.output_manager import OutputManager

ANALYTICS = {"total": 7, "items": [{"title": "A post", "url": "https://example.com/a"}]}
TENANT = {
    "llm_provider": "mock",
    "analytics_provider": "templated",
    "analytics_report_path": "data/analytics.json",
    "analytics_summary_template": "{{ data.total }} things.",
    "analytics_highlights_template": (
        "[{% for i in data['items'] %}{\"label\": {{ i.title|tojson }}, "
        "\"url\": {{ i.url|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
    ),
}


@pytest.fixture
def tenant_dir(tmp_path):
    """A tenant laid out the way the examples are: config at the top, data beside it."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "analytics.json").write_text(json.dumps(ANALYTICS))
    (tmp_path / "tenant.json").write_text(json.dumps(TENANT))
    return tmp_path


# --- path resolution -------------------------------------------------------

def test_a_relative_path_resolves_against_the_config_not_the_cwd(tenant_dir, monkeypatch):
    """The whole point: the same config read from a different working directory
    must find the same file. Before this, it depended on where you stood."""
    monkeypatch.chdir(tenant_dir.parent)  # deliberately NOT the tenant's own folder

    config = AgentConfigLoader().load(str(tenant_dir / "tenant.json"))
    report = asyncio.run(ToolsManager(config).build_analytics().report(limit=3))

    assert report["summary"] == "7 things."


def test_two_tenants_with_the_same_relative_path_read_different_files(tmp_path):
    """The multi-tenant failure this prevents: one server process, one working
    directory, two tenants that both say "data/analytics.json"."""
    configs = []
    for name, total in (("alpha", 1), ("beta", 2)):
        tenant = tmp_path / name
        (tenant / "data").mkdir(parents=True)
        (tenant / "data" / "analytics.json").write_text(json.dumps({**ANALYTICS, "total": total}))
        (tenant / "tenant.json").write_text(json.dumps(TENANT))
        configs.append(AgentConfigLoader().load(str(tenant / "tenant.json")))

    summaries = [
        asyncio.run(ToolsManager(c).build_analytics().report())["summary"] for c in configs
    ]
    assert summaries == ["1 things.", "2 things."]


def test_an_absolute_path_is_left_alone(tenant_dir):
    config = AgentConfigLoader().load(str(tenant_dir / "tenant.json"))
    absolute = str(tenant_dir / "data" / "analytics.json")
    assert resolve_path(config, absolute) == absolute


def test_a_config_built_in_code_keeps_the_old_cwd_relative_behavior():
    """No base directory means no change — every AgentConfig(...) built directly,
    including in every existing test, behaves exactly as before."""
    assert resolve_path(AgentConfig(), "data/x.json") == "data/x.json"


def test_an_output_sink_path_resolves_the_same_way(tenant_dir, monkeypatch):
    monkeypatch.chdir(tenant_dir.parent)
    config = AgentConfigLoader().load(str(tenant_dir / "tenant.json"))
    config.output_sinks = [{"name": "f", "provider": "json", "options": {"path": "out/result.json"}}]

    OutputManager(config).emit({"phase": "done"})

    assert json.loads((tenant_dir / "out" / "result.json").read_text())["phase"] == "done"


# --- loading from something that isn't a file ------------------------------

def test_load_dict_accepts_an_already_parsed_config():
    """What an API request body or a database row goes through."""
    config = AgentConfigLoader().load_dict({"llm_provider": "mock", "default_max_words": 500})

    assert config.llm_provider == "mock"
    assert config.default_max_words == 500


def test_load_dict_takes_an_explicit_base_dir(tenant_dir):
    config = AgentConfigLoader().load_dict(TENANT, base_dir=str(tenant_dir))
    assert asyncio.run(ToolsManager(config).build_analytics().report())["summary"] == "7 things."


def test_load_dict_rejects_unknown_fields_like_load_does():
    with pytest.raises(ValueError, match="Unknown AgentConfig field"):
        AgentConfigLoader().load_dict({"seed_keywrod": "typo"})


def test_load_dict_still_validates_prompt_templates():
    """Prompt-template checking is pure computation, so it always runs — unlike
    the data-template validation below, which does live I/O."""
    with pytest.raises(ValueError, match="prompt_templates"):
        AgentConfigLoader().load_dict({"prompt_templates": {"no_such_channel": "hi"}})


# --- validation is separable from loading ----------------------------------

def test_load_dict_does_no_data_io_by_default():
    """A server resolving a tenant config per request must not make an outbound
    call to validate a template. The config below points at a file that does not
    exist; loading it must still succeed."""
    config = AgentConfigLoader().load_dict({**TENANT, "analytics_report_path": "nope.json"})
    assert config.analytics_provider == "templated"


def test_validation_can_be_requested_explicitly():
    loader = AgentConfigLoader()
    config = loader.load_dict({**TENANT, "analytics_report_path": "nope.json"})
    with pytest.raises(ValueError, match="Could not load analytics data"):
        loader.validate_data_templates(config)


def test_load_from_file_still_validates_by_default(tmp_path):
    """Existing CLI behavior is unchanged: a broken templated config fails on load."""
    tenant = tmp_path / "tenant.json"
    tenant.write_text(json.dumps({**TENANT, "analytics_report_path": "nope.json"}))
    with pytest.raises(ValueError, match="Could not load analytics data"):
        AgentConfigLoader().load(str(tenant))
