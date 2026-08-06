"""Covers the provider registry (PLAN.md Step C): the catalog `list-tools` reads
and the factories that actually build things are the *same set of names*, and a
provider's settings can live in its own `options` without breaking the top-level
fields every existing tenant already has.

The set-equality tests below are the point of the step. Before it, the catalog
described a stack of if/elif ladders and a test could only check that each
catalogued name was *accepted*; a factory for a name nobody had catalogued was
invisible, and a catalogued name with no factory only showed up as a confusing
"Unknown provider" in front of a user.
"""

import json

import pytest

from agent.config.agent_config import AgentConfig
from agent.managers.output_manager import _SINK_FACTORIES, OutputManager
from agent.managers.providers import BY_KIND, CATALOG
from agent.managers.state_manager import _STORE_FACTORIES, build_state_store
from agent.managers.tools_manager import _REGISTRY, ToolsManager

# The kinds a stage actually calls. The other two — output sinks and the state
# store — are the run-context plane: their factories live in their own managers
# (see agent/managers/providers.py), so they get their own set-equality tests
# below rather than being looked up in the tools registry.
TOOL_KINDS = [kind for kind in CATALOG if kind.kind not in ("output", "state")]


def _tenant(tmp_path, plugins: dict = None, **config) -> AgentConfig:
    """A config anchored to a real folder, so plugins and relative paths resolve
    the way they do for a real tenant."""
    tenant = tmp_path / "acme"
    tenant.mkdir(parents=True, exist_ok=True)
    for filename, source in (plugins or {}).items():
        (tenant / "plugins").mkdir(exist_ok=True)
        (tenant / "plugins" / filename).write_text(source)
    return AgentConfig(config_base_dir=str(tenant), **config)


# --- the catalog and the factories are one list ----------------------------


@pytest.mark.parametrize("kind", TOOL_KINDS, ids=lambda k: k.kind)
def test_every_catalogued_tool_provider_has_a_factory_and_vice_versa(kind):
    assert set(kind.providers) == set(_REGISTRY[kind.kind]), (
        f"{kind.kind}: agent/managers/providers.py and tools_manager.py's _REGISTRY disagree"
    )


def test_the_output_sink_catalog_matches_its_factories():
    """Sinks live in OutputManager rather than the tools registry — a sink is
    run-context, not something a stage calls — but the same rule applies. "custom"
    is handled by the loader ahead of the factory table, so it's added here rather
    than being an entry in it."""
    assert set(BY_KIND["output"].providers) == set(_SINK_FACTORIES) | {"custom"}


def test_the_state_store_catalog_matches_its_factories():
    """Same rule again, for the other run-context kind. A store catalogued but
    unbuildable would reach a tenant as "Unknown state store provider 'redis'"
    from a name `list-tools` had just told them to use."""
    assert set(BY_KIND["state"].providers) == set(_STORE_FACTORIES) | {"custom"}


@pytest.mark.parametrize("kind", [k for k in TOOL_KINDS if not k.is_list], ids=lambda k: k.kind)
def test_an_uncatalogued_provider_name_is_rejected(kind):
    manager = ToolsManager(AgentConfig(**{kind.config_field: "definitely-not-a-provider"}))
    with pytest.raises(ValueError, match="Unknown"):
        getattr(manager, f"build_{kind.kind}")()


def test_an_unknown_discovery_provider_names_the_source_it_came_from():
    """With several sources configured, "unknown provider" alone doesn't say
    which entry to go fix."""
    config = AgentConfig(discovery_sources=[
        {"name": "trends", "provider": "mock"},
        {"name": "forums", "provider": "nope"},
    ])
    with pytest.raises(ValueError, match="forums"):
        ToolsManager(config).build_discovery_sources(llm=None)


def test_the_error_lists_what_is_actually_available():
    with pytest.raises(ValueError, match="'gemini'"):
        ToolsManager(AgentConfig(llm_provider="openai")).build_llm()


# --- provider-owned options, with the old fields as aliases ----------------


def test_options_are_used_when_present():
    config = AgentConfig(
        llm_provider="gemini",
        llm_options={"api_key": "from-options", "model": "gemini-from-options"},
    )
    client = ToolsManager(config).build_llm()
    assert client._default_model == "gemini-from-options"


def test_a_setting_that_moved_is_rejected_with_its_new_location(tmp_path):
    """The migration this step forces on every existing config. "Unknown field
    'gemini_api_key'" is true and useless; a tenant hitting it has no way to guess
    where the value went, so the error names the destination."""
    from agent.config.loader import AgentConfigLoader

    with pytest.raises(ValueError, match=r"llm_options\.api_key"):
        AgentConfigLoader().load_dict({"llm_provider": "gemini", "gemini_api_key": "k"})


def test_a_genuinely_unknown_field_still_says_so():
    """A typo is not a migration, and must not be reported as one."""
    from agent.config.loader import AgentConfigLoader

    with pytest.raises(ValueError, match="Unknown AgentConfig field"):
        AgentConfigLoader().load_dict({"llm_providr": "gemini"})


def test_a_per_run_model_override_beats_the_configured_one():
    """The override is a property of *this run*, so it outranks the stored one."""
    config = AgentConfig(
        llm_provider="gemini",
        llm_options={"api_key": "k", "model": "from-options"},
    )
    client = ToolsManager(config).build_llm(model_override="from-the-request")
    assert client._default_model == "from-the-request"


def test_a_provider_falls_back_to_its_own_default_when_an_option_is_absent():
    config = AgentConfig(llm_provider="gemini", llm_options={"api_key": "k"})
    assert ToolsManager(config).build_llm()._default_model == "gemini-2.0-flash"


def test_options_carry_settings_the_config_knows_nothing_about(tmp_path):
    """A provider's settings are its own — the generic config has no field for
    them, which is what stops every new provider knob from becoming one."""
    config = AgentConfig(search_performance_provider="google", search_performance_options={"timeout_seconds": 5})
    # Building the real client needs credentials; the option reaching the
    # constructor is what's under test, so a missing key file is the expected end.
    with pytest.raises(Exception) as exc:
        ToolsManager(config).build_search_performance()
    assert "timeout" not in str(exc.value).lower()


def test_a_path_option_resolves_against_the_tenant_folder(tmp_path):
    config = _tenant(
        tmp_path, analytics_provider="templated",
        analytics_options={
            "report_path": "data/report.json",
            "summary_template": "{{ data.total }} things.",
            "highlights_template": "[]",
        },
    )
    (tmp_path / "acme" / "data").mkdir()
    (tmp_path / "acme" / "data" / "report.json").write_text(json.dumps({"total": 7}))

    client = ToolsManager(config).build_analytics()

    assert client.report_path == str(tmp_path / "acme" / "data" / "report.json")


# --- "custom" everywhere, including the LLM --------------------------------


CUSTOM_LLM = '''
from tools.llm.base import LLMResponse


class Client:
    def __init__(self, config, options=None):
        self.options = options or {}

    def generate(self, prompt, *, model=None, grounded=False):
        return LLMResponse(text=self.options.get("canned", "{}"), tokens=1)
'''


def test_an_llm_can_be_a_tenants_own_class(tmp_path):
    """The one provider kind that had no "custom" slot — so bringing your own
    model meant forking."""
    config = _tenant(
        tmp_path, plugins={"my_llm.py": CUSTOM_LLM},
        llm_provider="custom", llm_custom_class="my_llm:Client",
        llm_options={"canned": '{"title": "hi"}'},
    )

    client = ToolsManager(config).build_llm()

    assert client.generate("anything").text == '{"title": "hi"}'


def test_a_custom_class_receives_its_providers_options(tmp_path):
    config = _tenant(
        tmp_path, plugins={"my_llm.py": CUSTOM_LLM},
        llm_provider="custom", llm_custom_class="my_llm:Client",
        llm_options={"canned": "x", "anything_else": 1},
    )

    assert ToolsManager(config).build_llm().options == {"canned": "x", "anything_else": 1}


def test_a_custom_provider_with_no_class_configured_says_so():
    with pytest.raises(ValueError, match="llm_custom_class"):
        ToolsManager(AgentConfig(llm_provider="custom")).build_llm()


# --- discovery entries keep their existing shape ---------------------------


def test_a_discovery_sources_settings_can_stay_on_the_entry():
    """Every existing tenant and example writes them there; that must keep
    working exactly as it did."""
    config = AgentConfig(discovery_sources=[
        {"name": "llm_source", "provider": "llm", "grounded": False, "max_opportunities": 2},
    ])

    source = ToolsManager(config).build_discovery_sources(llm=object())["llm_source"]

    assert source.grounded is False
    assert source.max_opportunities == 2


def test_a_discovery_sources_options_win_over_its_entry():
    config = AgentConfig(discovery_sources=[
        {"name": "llm_source", "provider": "llm", "grounded": True,
         "options": {"grounded": False}},
    ])

    source = ToolsManager(config).build_discovery_sources(llm=object())["llm_source"]

    assert source.grounded is False


CUSTOM_SOURCE = '''
class Source:
    def __init__(self, config, options=None):
        self.options = options or {}

    def discover(self, context):
        return []
'''


def test_a_custom_discovery_source_receives_only_its_options_object(tmp_path):
    """Not the whole entry: docs/extending.md promises a class the provider's
    `options`, and the entry's own name/provider/class keys are the framework's
    business, not the plugin's."""
    config = _tenant(
        tmp_path, plugins={"my_source.py": CUSTOM_SOURCE},
        discovery_sources=[{
            "name": "mine", "provider": "custom", "class": "my_source:Source",
            "options": {"endpoint": "https://example.test"},
        }],
    )

    source = ToolsManager(config).build_discovery_sources(llm=None)["mine"]

    assert source.options == {"endpoint": "https://example.test"}


def test_a_custom_discovery_source_error_names_the_entry(tmp_path):
    config = _tenant(
        tmp_path, plugins={"my_source.py": CUSTOM_SOURCE},
        discovery_sources=[{"name": "mine", "provider": "custom", "class": "missing:Source"}],
    )

    with pytest.raises(ValueError, match=r"discovery_sources\['mine'\]"):
        ToolsManager(config).build_discovery_sources(llm=None)


# --- sinks go through the same catalog -------------------------------------


def test_an_unknown_sink_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown output sink provider"):
        OutputManager(AgentConfig(output_sinks=[{"name": "x", "provider": "carrier-pigeon"}]))


# --- and so does the state store -------------------------------------------


def test_an_unknown_state_store_provider_is_rejected():
    """Never a silent fallback to "memory": a tenant who asked for Redis and got
    in-process snapshots finds out from an empty dashboard, days later."""
    with pytest.raises(ValueError, match="Unknown state store provider"):
        build_state_store(AgentConfig(state_provider="carrier-pigeon"))


def test_a_custom_state_store_with_no_class_configured_says_so():
    with pytest.raises(ValueError, match="state_custom_class"):
        build_state_store(AgentConfig(state_provider="custom"))
