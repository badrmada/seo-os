"""Covers the search-performance kind: the vendor-neutral rename, `site_url` as
config, the shared row enrichment, and the four providers behind it.

The test that matters most here is
`test_the_default_provider_does_not_override_a_callers_seed_keyword`. The kind
used to be `gsc_provider`, defaulting to `"mock"`, and the mock returned canned
striking-distance rows — which `_pick_keyword` prefers *over* the caller's own
`seed_keyword`. So a config asking for "cron job monitoring" silently drafted
about the fixture's keyword instead, while README and configuration.md both
promised the seed keyword would be used. Every other test passed throughout: the
old input validator required `gsc_domain`, and the tests that omitted it never
reached the client at all.
"""

import asyncio
import json

import pytest

from agent.config.agent_config import AgentConfig
from agent.config.loader import AgentConfigLoader
from agent.graph.stages.analyze import AnalyzeStage
from agent.graph.tools import Tools
from agent.managers.providers import BY_KIND
from agent.managers.tools_manager import ToolsManager
from agent.validators.input_validator import InputValidator
from tools.clients.search_performance_rows import enrich_rows, normalize_raw_row
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.search_performance_mock import MockSearchPerformanceClient
from tools.mocks.search_performance_null import NullSearchPerformanceClient
from tools.mocks.traffic_mock import MockTrafficClient


def _tools(search_performance=None) -> Tools:
    return Tools(
        search_performance=search_performance or NullSearchPerformanceClient(),
        analytics=MockAppAnalyticsClient(),
        traffic=MockTrafficClient(),
        llm=MockLLMClient(),
    )


def _chosen_keyword(search_performance=None, seed_keyword="cron job monitoring") -> str:
    stage = AnalyzeStage(_tools(search_performance), AgentConfig())
    state = {"input": {"channel": "site_article", "seed_keyword": seed_keyword}, "working": {}}
    return asyncio.run(stage.run(state))["working"]["chosen_keyword"]


def _tenant(tmp_path, files: dict = None, **config) -> AgentConfig:
    tenant = tmp_path / "acme"
    tenant.mkdir(parents=True, exist_ok=True)
    for filename, content in (files or {}).items():
        (tenant / filename).parent.mkdir(parents=True, exist_ok=True)
        (tenant / filename).write_text(content)
    return AgentConfig(config_base_dir=str(tenant), **config)


# --- the bug this kind was reworked for -------------------------------------


def test_the_default_provider_does_not_override_a_callers_seed_keyword():
    """The whole point of defaulting to "none". A tenant who hasn't connected a
    rank source asks for a keyword and gets that keyword."""
    assert ToolsManager(AgentConfig()).build_search_performance().__class__ is (
        NullSearchPerformanceClient
    )
    assert _chosen_keyword() == "cron job monitoring"


def test_a_configured_rank_source_is_still_allowed_to_win():
    """"none" is a default, not a policy: real striking-distance data is better
    evidence than a seed keyword, and still outranks it."""
    chosen = _chosen_keyword(MockSearchPerformanceClient())

    assert chosen != "cron job monitoring"
    assert chosen in {row["query"] for row in MockSearchPerformanceClient().search_analytics()}


def test_the_mock_is_product_neutral():
    """It shipped one real product's queries and a live URL on that product's
    domain, so every example and every unconnected tenant drafted against someone
    else's keywords."""
    rows = MockSearchPerformanceClient().search_analytics()
    blob = json.dumps(rows).lower()

    for leak in ("echooers", "anonymous social", "post without login"):
        assert leak not in blob


def test_no_rank_data_is_not_a_tool_error():
    """Returning nothing is a valid answer, not a degrade — nothing should appear
    in tool_errors for it."""
    stage = AnalyzeStage(_tools(), AgentConfig())
    state = {"input": {"channel": "site_article", "seed_keyword": "kettles"}, "working": {}}

    working = asyncio.run(stage.run(state))["working"]

    assert working["search_performance_rows"] == []
    assert working["tool_errors"] == []


def test_a_failing_rank_source_degrades_to_the_seed_keyword():
    class Exploding:
        def search_analytics(self, days: int = 28, row_limit: int = 500):
            raise RuntimeError("rank API down")

    stage = AnalyzeStage(_tools(Exploding()), AgentConfig())
    state = {"input": {"channel": "site_article", "seed_keyword": "kettles"}, "working": {}}

    working = asyncio.run(stage.run(state))["working"]

    assert working["chosen_keyword"] == "kettles"
    assert [e["tool"] for e in working["tool_errors"]] == ["search_performance"]


# --- the shared enrichment --------------------------------------------------


def test_striking_distance_is_positions_5_to_20():
    rows = enrich_rows([
        {"query": "deep", "position": 40, "impressions": 100},
        {"query": "close", "position": 12, "impressions": 100},
        {"query": "winning", "position": 2, "impressions": 100},
    ])
    by_query = {row["query"]: row["opportunity"] for row in rows}

    assert by_query == {"deep": "low_priority", "close": "striking_distance", "winning": "defend"}


def test_rows_come_back_highest_score_first():
    """_pick_keyword takes striking[0], so the order is load-bearing rather than
    cosmetic."""
    rows = enrich_rows([
        {"query": "small", "position": 12, "impressions": 100},
        {"query": "big", "position": 12, "impressions": 5000},
    ])

    assert [row["query"] for row in rows] == ["big", "small"]


def test_a_provider_with_no_prior_period_still_gets_classified():
    """A single snapshot — which is all a templated source usually has — degrades
    to trend="flat" rather than being unusable."""
    row = enrich_rows([{"query": "q", "position": 12, "impressions": 900}])[0]

    assert row["trend"] == "flat"
    assert row["top_page"] is None
    assert row["opportunity"] == "striking_distance"
    assert row["reason"]


def test_numeric_strings_are_coerced_but_real_numbers_are_left_alone():
    """A templated row comes out of Jinja2, so "11.2" is routine. An int that is
    already an int must not become 42.0 in the run's own output."""
    coerced = normalize_raw_row({"query": "q", "position": "11.2", "clicks": 42})

    assert coerced["position"] == 11.2
    assert coerced["clicks"] == 42
    assert isinstance(coerced["clicks"], int)


@pytest.mark.parametrize(
    "row",
    [{"position": 4}, {"query": "  "}, {"query": "q", "position": "soon"}],
)
def test_an_unusable_row_says_what_is_wrong_with_it(row):
    with pytest.raises(ValueError):
        normalize_raw_row(row)


# --- the providers ----------------------------------------------------------


def test_the_catalog_offers_a_real_menu_not_just_one_vendor():
    """The complaint that started this: every other kind had a no-code path and an
    escape hatch, and this one had "google" or a fixture."""
    assert set(BY_KIND["search_performance"].providers) == {
        "none", "google", "templated", "mock", "custom",
    }


RANKINGS_JSON = json.dumps({
    "rows": [
        {"term": "cron job monitoring", "rank": 12.0, "seen": 3100, "visits": 42},
        {"term": "uptime alerts", "rank": 2.0, "seen": 1200, "visits": 96},
    ]
})

ROWS_TEMPLATE = (
    "[{% for r in data.rows %}"
    '{"query": {{ r.term|tojson }}, "position": {{ r.rank }}, '
    '"impressions": {{ r.seen }}, "clicks": {{ r.visits }}, "ctr": 0.0135}'
    "{% if not loop.last %},{% endif %}{% endfor %}]"
)


def test_a_templated_provider_maps_a_tenants_own_rank_data(tmp_path):
    config = _tenant(
        tmp_path, files={"data/rankings.json": RANKINGS_JSON},
        search_performance_provider="templated",
        search_performance_options={
            "source": "file", "report_path": "data/rankings.json",
            "rows_template": ROWS_TEMPLATE,
        },
    )

    rows = asyncio.run(ToolsManager(config).build_search_performance().search_analytics())

    assert [row["query"] for row in rows] == ["cron job monitoring", "uptime alerts"]
    assert rows[0]["opportunity"] == "striking_distance"


def test_a_templated_provider_gets_the_same_classification_as_any_other(tmp_path):
    """The template supplies data, never judgement — otherwise "which keyword is
    worth targeting" would quietly vary by data source."""
    config = _tenant(
        tmp_path, files={"data/rankings.json": RANKINGS_JSON},
        search_performance_provider="templated",
        search_performance_options={
            "source": "file", "report_path": "data/rankings.json",
            "rows_template": ROWS_TEMPLATE,
        },
    )
    client = ToolsManager(config).build_search_performance()

    rows = asyncio.run(client.search_analytics())
    equivalent = enrich_rows([
        {"query": "cron job monitoring", "position": 12.0, "impressions": 3100,
         "clicks": 42, "ctr": 0.0135},
    ])

    assert rows[0]["score"] == equivalent[0]["score"]
    assert rows[0]["reason"] == equivalent[0]["reason"]


def test_a_templated_provider_with_no_template_says_so():
    config = AgentConfig(search_performance_provider="templated")

    with pytest.raises(ValueError, match="rows_template"):
        ToolsManager(config).build_search_performance()


def test_a_templated_provider_that_renders_junk_names_the_option(tmp_path):
    config = _tenant(
        tmp_path, files={"data/rankings.json": RANKINGS_JSON},
        search_performance_provider="templated",
        search_performance_options={
            "source": "file", "report_path": "data/rankings.json",
            "rows_template": "not json at all",
        },
    )
    client = ToolsManager(config).build_search_performance()

    with pytest.raises(ValueError, match="rows_template"):
        asyncio.run(client.search_analytics())


CUSTOM_RANKS = '''
class Ranks:
    def __init__(self, config, options=None):
        self.options = options or {}

    def search_analytics(self, days=28, row_limit=500):
        return [{"query": self.options["term"], "position": 12, "impressions": 900,
                 "clicks": 1, "ctr": 0.01, "opportunity": "striking_distance"}]
'''


def test_rank_data_can_come_from_a_tenants_own_class(tmp_path):
    """Bing Webmaster Tools, Ahrefs, an internal warehouse — the escape hatch this
    kind never had."""
    tenant = tmp_path / "acme"
    (tenant / "plugins").mkdir(parents=True)
    (tenant / "plugins" / "ranks.py").write_text(CUSTOM_RANKS)
    config = AgentConfig(
        config_base_dir=str(tenant),
        search_performance_provider="custom",
        search_performance_custom_class="ranks:Ranks",
        search_performance_options={"term": "from my own warehouse"},
    )

    client = ToolsManager(config).build_search_performance()

    assert client.search_analytics()[0]["query"] == "from my own warehouse"


def test_the_google_provider_requires_its_own_property_identifier():
    """It is Google's identifier, so it is Google's option — and it is checked at
    construction so check-data reports it rather than a run failing mid-analyze."""
    config = AgentConfig(search_performance_provider="google")

    with pytest.raises(ValueError, match="gsc_domain"):
        ToolsManager(config).build_search_performance()


def test_the_google_provider_rejects_a_site_url_in_the_property_slot():
    """`site_url` and a Search Console property are two different things; pasting
    the first into the second is the obvious mistake."""
    config = AgentConfig(
        search_performance_provider="google",
        search_performance_options={"gsc_domain": "example.com"},
    )

    with pytest.raises(ValueError, match="sc-domain"):
        ToolsManager(config).build_search_performance()


# --- migration --------------------------------------------------------------


def test_a_renamed_config_field_names_its_replacement():
    with pytest.raises(ValueError, match="search_performance_provider"):
        AgentConfigLoader().load_dict({"gsc_provider": "google"})


def test_the_rename_message_does_not_tell_you_to_move_it_into_options():
    """MOVED_FIELDS' advice — "move it into that provider's options" — is wrong
    for a rename, and gsc_key_file maps into an options object that no longer
    exists under the old name."""
    with pytest.raises(ValueError) as exc:
        AgentConfigLoader().load_dict({"gsc_provider": "google", "gsc_key_file": "k.json"})

    assert "renamed" in str(exc.value)
    assert "search_performance_provider" in str(exc.value)


def test_a_relocated_input_field_names_its_new_home():
    with pytest.raises(ValueError, match="search_performance_options.gsc_domain"):
        InputValidator().validate(
            {"channel": "site_article", "gsc_domain": "sc-domain:example.com"}, AgentConfig(),
        )


def test_a_genuinely_unknown_input_field_still_says_so():
    """A typo is not a migration, and must not be reported as one."""
    with pytest.raises(ValueError, match="Unknown AgentInput field"):
        InputValidator().validate({"seed_keywrod": "x"}, AgentConfig())


def test_an_article_run_no_longer_requires_a_search_console_property():
    """It used to, which made a Google identifier mandatory for every article run
    — including for a tenant who had never connected Search Console."""
    InputValidator().validate({"channel": "site_article", "seed_keyword": "kettles"}, AgentConfig())


def test_site_url_is_optional_so_a_zero_config_tenant_still_runs():
    InputValidator().validate({}, AgentConfig())
    assert AgentConfig().site_url == ""
