"""Covers PLAN.md Step J: a template value written as {"file": "name.j2"} and read
from the tenant's templates/ folder, anywhere a template string is accepted.

The two things worth testing beyond "it reads the file" are the two that make it
safe to hand a tenant: everything downstream still sees a plain string (so
save-time validation, the templated providers and every prompt work untouched),
and the folder is a boundary rather than a starting point for a path.
"""

import json

import pytest

from agent.config import AgentConfigLoader
from agent.config.agent_config import AgentConfig
from agent.config.template_files import resolve_template_files

ANALYTICS = {"total": 7, "items": [{"title": "A post", "url": "https://example.com/a"}]}
HIGHLIGHTS_TEMPLATE = (
    "[{% for i in data['items'] %}{\"label\": {{ i.title|tojson }}, "
    "\"url\": {{ i.url|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
)
ARTICLE_TEMPLATE = """You write for: {{ brand_description }}
Goal: {{ agent_goal }}
Keyword: "{{ keyword }}" — {{ max_words }} words, {{ tone }}.
{% for name, signal in signals.items() %}- {{ name }}: {{ signal.summary }}
{% endfor %}"""


def _tenant(tmp_path, config: dict, templates: dict = None, data: dict = None):
    """A tenant folder laid out like the examples: config at the top, templates/
    beside it."""
    (tmp_path / "tenant.json").write_text(json.dumps(config), encoding="utf-8")
    if templates:
        (tmp_path / "templates").mkdir(exist_ok=True)
        for name, text in templates.items():
            path = tmp_path / "templates" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if data:
        (tmp_path / "data").mkdir(exist_ok=True)
        for name, payload in data.items():
            (tmp_path / "data" / name).write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path / "tenant.json"


def _load(path):
    return AgentConfigLoader().load(str(path))


# --- the feature itself -------------------------------------------------------


def test_prompt_template_from_a_file_is_a_string_by_the_time_the_config_exists(tmp_path):
    """The whole point of resolving at load time: nothing downstream — validation,
    the prompt builder, a sink serializing the config — learns that a template can
    come from a file."""
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": {"file": "site_article.j2"}}},
        templates={"site_article.j2": ARTICLE_TEMPLATE},
    )
    config = _load(path)

    assert config.prompt_templates["site_article"] == ARTICLE_TEMPLATE
    assert isinstance(config.prompt_templates["site_article"], str)


def test_a_provider_option_template_comes_from_a_file_too(tmp_path):
    """`prompt_templates` is the loudest case but not a special one — the rule is
    every key ending in _template, which is how a new provider's template option
    gets this without touching template_files.py."""
    path = _tenant(
        tmp_path,
        {
            "llm_provider": "mock",
            "analytics_provider": "templated",
            "analytics_options": {
                "report_path": "data/analytics.json",
                "summary_template": {"file": "summary.j2"},
                "highlights_template": {"file": "highlights.json.j2"},
            },
        },
        templates={"summary.j2": "{{ data.total }} things.", "highlights.json.j2": HIGHLIGHTS_TEMPLATE},
        data={"analytics.json": ANALYTICS},
    )
    config = _load(path)

    assert config.analytics_options["summary_template"] == "{{ data.total }} things."
    assert config.analytics_options["highlights_template"] == HIGHLIGHTS_TEMPLATE


def test_a_signal_source_option_deep_in_a_list_resolves(tmp_path):
    """signal_sources/discovery_sources/output_sinks are lists of dicts of options,
    so the resolver has to walk, not scan a fixed set of fields."""
    path = _tenant(
        tmp_path,
        {
            "llm_provider": "mock",
            "signal_sources": [{
                "name": "trends",
                "provider": "templated",
                "options": {
                    "source": "file",
                    "report_path": "data/trends.json",
                    "summary_template": {"file": "trends.j2"},
                },
            }],
        },
        templates={"trends.j2": "{{ data.rising|length }} rising queries."},
        data={"trends.json": {"rising": [1, 2]}},
    )
    config = _load(path)

    assert config.signal_sources[0]["options"]["summary_template"] == "{{ data.rising|length }} rising queries."


def test_a_subfolder_is_allowed(tmp_path):
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": {"file": "prompts/article.j2"}}},
        templates={"prompts/article.j2": ARTICLE_TEMPLATE},
    )
    assert _load(path).prompt_templates["site_article"] == ARTICLE_TEMPLATE


def test_a_plain_string_still_means_exactly_what_it_did(tmp_path):
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": "Write about {{ keyword }}."}},
    )
    assert _load(path).prompt_templates["site_article"] == "Write about {{ keyword }}."


def test_config_with_no_template_files_records_nothing(tmp_path):
    path = _tenant(tmp_path, {"llm_provider": "mock"})
    assert _load(path).template_sources == []


# --- save-time validation still applies to a file-loaded template ---------------


def test_a_broken_template_in_a_file_fails_at_load_time(tmp_path):
    """The reason to resolve before validating: a template from a file gets the
    identical check an inline one gets, with no second code path."""
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": {"file": "broken.j2"}}},
        templates={"broken.j2": "{% for x in %}"},
    )
    with pytest.raises(ValueError, match="prompt_templates.site_article"):
        _load(path)


def test_a_template_naming_an_unconfigured_signal_still_fails(tmp_path):
    """Signal-name validation is what Step F added; loading from a file must not
    quietly skip it."""
    path = _tenant(
        tmp_path,
        {
            "llm_provider": "mock",
            "signal_sources": [{"name": "trends", "provider": "mock"}],
            "prompt_templates": {"site_article": {"file": "typo.j2"}},
        },
        templates={"typo.j2": "{{ signals.trneds.summary }}"},
    )
    with pytest.raises(ValueError, match="prompt_templates.site_article"):
        _load(path)


# --- containment: the folder is a boundary, not a starting point ---------------


def test_absolute_path_is_rejected(tmp_path):
    secret = tmp_path / "secret.j2"
    secret.write_text("{{ keyword }}", encoding="utf-8")
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": {"file": str(secret)}}},
        templates={"unused.j2": "x"},
    )
    with pytest.raises(ValueError, match="not an absolute path"):
        _load(path)


def test_dot_dot_is_rejected(tmp_path):
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": {"file": "../tenant.json"}}},
        templates={"unused.j2": "x"},
    )
    with pytest.raises(ValueError, match=r"\.\."):
        _load(path)


def test_a_symlink_leaving_the_folder_is_rejected(tmp_path):
    """Textual checks miss this one, which is why containment is verified after
    resolving rather than before."""
    outside = tmp_path / "outside.j2"
    outside.write_text("{{ keyword }}", encoding="utf-8")
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": {"file": "link.j2"}}},
        templates={"real.j2": "x"},
    )
    (tmp_path / "templates" / "link.j2").symlink_to(outside)

    with pytest.raises(ValueError, match="outside"):
        _load(path)


def test_a_missing_file_lists_what_is_there(tmp_path):
    """"No such file" alone sends someone looking in the wrong directory."""
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": {"file": "artcile.j2"}}},
        templates={"article.j2": ARTICLE_TEMPLATE, "comment.j2": "hi"},
    )
    with pytest.raises(ValueError) as excinfo:
        _load(path)
    message = str(excinfo.value)
    assert "artcile.j2" in message
    assert "article.j2" in message and "comment.j2" in message


def test_a_config_with_no_workspace_says_so(tmp_path):
    """A config built in code has no templates/ folder, so {"file": ...} is a clear
    error rather than a read relative to whatever directory the process is in."""
    with pytest.raises(ValueError, match="no tenant folder"):
        AgentConfigLoader().load_dict(
            {"llm_provider": "mock", "prompt_templates": {"site_article": {"file": "x.j2"}}}
        )


# --- the shape of the object itself --------------------------------------------


def test_a_misspelled_key_is_rejected_rather_than_treated_as_a_template(tmp_path):
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "prompt_templates": {"site_article": {"path": "x.j2"}}},
        templates={"x.j2": "hi"},
    )
    with pytest.raises(ValueError, match="must be a template string"):
        _load(path)


def test_a_file_object_somewhere_that_is_not_a_template_is_named(tmp_path):
    """Left alone it reaches a provider as a dict where a string was expected and
    fails with something unrecognizable."""
    path = _tenant(
        tmp_path,
        {"llm_provider": "mock", "analytics_options": {"report_path": {"file": "x.j2"}}},
        templates={"x.j2": "hi"},
    )
    with pytest.raises(ValueError, match="only accepted for template values"):
        _load(path)


def test_an_http_header_named_file_is_left_alone():
    """Header names, env var names and an MCP tool's parameter names are somebody
    else's vocabulary — one of them being "file" is ordinary, and must not be read
    as a file reference."""
    data = {
        "analytics_options": {"api_headers": {"file": "report.csv"}},
        "discovery_sources": [{
            "name": "server",
            "provider": "mcp",
            "options": {"tool_name": "t", "arguments": {"file": "notes.md"}, "env": {"file": "1"}},
        }],
    }
    resolved, loaded = resolve_template_files(data, base_dir="", source="<test>")

    assert resolved == data
    assert loaded == []


# --- provenance ----------------------------------------------------------------


def test_loaded_templates_are_recorded_for_check_data(tmp_path):
    """`check-data` exists to answer "will this config work", and "which file is
    this prompt coming from" is part of that — a template edited in the wrong file
    renders perfectly and says the wrong thing."""
    path = _tenant(
        tmp_path,
        {
            "llm_provider": "mock",
            "prompt_templates": {"site_article": {"file": "article.j2"}},
            "traffic_provider": "templated",
            "traffic_options": {"report_path": "data/traffic.json", "summary_template": {"file": "traffic.j2"}},
        },
        templates={"article.j2": ARTICLE_TEMPLATE, "traffic.j2": "{{ data.visits }} visits."},
        data={"traffic.json": {"visits": 3}},
    )
    config = _load(path)

    slots = {entry["slot"]: entry for entry in config.template_sources}
    assert set(slots) == {"prompt_templates.site_article", "traffic_options.summary_template"}
    assert slots["prompt_templates.site_article"]["file"] == "article.j2"
    assert slots["prompt_templates.site_article"]["path"].endswith("templates/article.j2")


def test_a_tenant_cannot_set_the_provenance_field_itself(tmp_path):
    """It reports what loading did rather than asking it for anything, so a config
    naming it is a mistake — same as any other unknown field."""
    path = _tenant(tmp_path, {"llm_provider": "mock", "template_sources": [{"slot": "x"}]})
    with pytest.raises(ValueError, match="Unknown AgentConfig field"):
        _load(path)


def test_agent_config_built_in_code_still_needs_no_arguments():
    assert AgentConfig().template_sources == []
