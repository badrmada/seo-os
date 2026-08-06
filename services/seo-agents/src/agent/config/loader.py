import json
from dataclasses import fields
from pathlib import Path

from .. import prompts
from ..schemas.signal import BUILTIN_SIGNAL_NAMES
from ..validators.template_validator import TemplateValidator
from .agent_config import AgentConfig
from .template_files import resolve_template_files

# Settings that used to sit at the top level of a config and now belong to the
# provider that actually uses them (see AgentConfig's "Provider-owned settings").
# Kept as a map rather than deleted quietly: "Unknown AgentConfig field
# 'gemini_api_key'" is true but useless, and a tenant hitting it has no way to
# guess where the value went. Naming the destination turns a broken config into a
# two-second edit.
MOVED_FIELDS = {
    "llm_model": "llm_options.model",
    "gemini_api_key": "llm_options.api_key",
    "gsc_key_file": "search_performance_options.key_file",
    "cloudflare_api_token": "traffic_options.api_token",
    "cloudflare_zone_id": "traffic_options.zone_id",
    "traffic_source": "traffic_options.source",
    "traffic_report_path": "traffic_options.report_path",
    "traffic_api_url": "traffic_options.api_url",
    "traffic_api_method": "traffic_options.api_method",
    "traffic_api_headers": "traffic_options.api_headers",
    "traffic_api_timeout_seconds": "traffic_options.api_timeout_seconds",
    "traffic_summary_template": "traffic_options.summary_template",
    "analytics_source": "analytics_options.source",
    "analytics_report_path": "analytics_options.report_path",
    "analytics_api_url": "analytics_options.api_url",
    "analytics_api_method": "analytics_options.api_method",
    "analytics_api_headers": "analytics_options.api_headers",
    "analytics_api_timeout_seconds": "analytics_options.api_timeout_seconds",
    "analytics_summary_template": "analytics_options.summary_template",
    "analytics_highlights_template": "analytics_options.highlights_template",
}

# Fields that kept their meaning but were renamed, because the old name was a
# vendor's and the job isn't. Distinct from MOVED_FIELDS above: nothing about the
# *value* changes, so the fix is a rename rather than a relocation into an options
# object, and the message has to say so — telling someone to "move gsc_provider
# into a provider's options" would be actively wrong advice.
RENAMED_FIELDS = {
    "gsc_provider": "search_performance_provider",
    "gsc_options": "search_performance_options",
}

# AgentConfig fields the loader writes and a config file may not set. They report
# what loading did rather than asking it for anything, so a tenant naming one is a
# mistake worth failing on — same as any other unknown field.
LOADER_OWNED_FIELDS = frozenset({"template_sources"})



def _signal_names(config: AgentConfig) -> tuple:
    """The names a prompt template may use inside `signals` — this tenant's own
    signal_sources entries, minus the reserved ones, which select a built-in slot
    rather than adding a key to working.signals (see agent/schemas/signal.py).

    Reading it here rather than in the prompt layer is what keeps the check honest:
    the template is validated against exactly the signals this config builds, so a
    typo'd name fails at save time instead of mid-run.
    """
    return tuple(
        entry["name"] for entry in config.signal_sources
        if entry.get("name") and entry["name"] not in BUILTIN_SIGNAL_NAMES
    )


def _validate_pipelines(config: AgentConfig, source: str) -> None:
    """`agent_type` must name a pipeline that exists, and every declared pipeline
    must be structurally sound.

    Both are checked here, at save time, because the alternative is a typo'd agent
    type silently falling back to `seo_content` — "my audit ran and produced an
    article" is the worst possible answer to a misspelling — and a broken stage
    list in a pipeline nobody selected today failing on the day someone does.

    What is deliberately *not* checked here is whether each stage's `class` can be
    imported: that executes the tenant's Python, and a server loading a config per
    request must not run the code of pipelines this request isn't using. It
    happens when the pipeline is built — at the start of a run, and in
    `check-data`. Same line `load_dict`'s `validate` flag already draws.

    Imported inside the function: agent.graph imports agent.config, so a
    module-level import here closes that cycle (see src/tests/test_imports.py).
    """
    from ..graph.pipeline import DEFAULT_AGENT_TYPE, agent_types, validate_pipeline

    if not config.agent_type:
        raise ValueError(f'agent_type in {source} may not be empty (default: "{DEFAULT_AGENT_TYPE}")')
    available = agent_types(config)
    if config.agent_type not in available:
        raise ValueError(
            f"agent_type {config.agent_type!r} in {source} has no pipeline "
            f"(available: {', '.join(available)}). Declare its stages under "
            '"pipelines", or use one of those. See docs/configuration.md.'
        )

    for name, declaration in (config.pipelines or {}).items():
        try:
            validate_pipeline(name, declaration)
        except ValueError as exc:
            raise ValueError(f"{exc} (in {source})") from exc


class AgentConfigLoader:
    """Loads a tenant AgentConfig, overriding only the fields it sets — anything
    omitted keeps the generic default (see AgentConfig). Unknown keys raise, so a
    typo'd field name fails fast instead of being silently ignored.

    Two entry points, because a tenant config doesn't only come from a file:

      - `load(path)` — a JSON file on disk. What the CLI uses.
      - `load_dict(data, base_dir=...)` — an already-parsed config: a database
        row, an API request body, a queue message. Same validation, same
        defaults, no filesystem involved.

    Both set `config_base_dir` so the tenant's relative paths resolve against
    their own location rather than the process's working directory (see
    agent/config/paths.py).
    """

    def __init__(self) -> None:
        self._template_validator = TemplateValidator()

    def load(self, path: str, *, validate: bool = True) -> AgentConfig:
        """Load from a JSON file. `config_base_dir` becomes that file's directory,
        so `"analytics_report_path": "data/analytics.json"` means "next to this
        config" and works from any working directory."""
        config_path = Path(path).expanduser().resolve()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return self.load_dict(
            data, base_dir=str(config_path.parent), validate=validate, source=str(config_path),
        )

    def load_dict(
        self, data: dict, *, base_dir: str = "", validate: bool = False, source: str = "<config>",
    ) -> AgentConfig:
        """Load from an already-parsed config.

        Any template value written as `{"file": "name.j2"}` is read from the
        tenant's `templates/` folder first (agent/config/template_files.py), so
        everything after this point — including the validation below — sees the
        same plain string it would have seen inline. A config with no `base_dir`
        has no such folder, and says so rather than reading relative to the CWD.

        `prompt_templates` is special-cased: the config may override just one or
        two channels, and each overridden template is rendered against a sample
        context right here, so a broken Jinja2 template (bad syntax, a variable
        that isn't available) fails at load time rather than mid-run. This check
        is pure computation with no I/O, so it always runs.

        `validate` controls the *other* kind of check — the templated
        analytics/traffic providers, whose templates can only be validated
        against the tenant's actual data, which for `source="api"` means a real
        HTTP request. That belongs at config-save time, not on the critical path
        of every run: a server resolving a tenant config per request must not make
        an extra outbound call to do it. Hence it defaults on for `load()` (the
        CLI's save-time-ish path, preserving existing behavior) and off here.
        Call `validate_data_templates()` explicitly when you want it.
        """
        known = {f.name for f in fields(AgentConfig)} - LOADER_OWNED_FIELDS
        unknown = set(data) - known
        if unknown:
            # Renames are reported before relocations: a config carrying both
            # gsc_provider and gsc_key_file should be told the kind was renamed
            # first, since "put it in gsc_options.key_file" names an object that
            # no longer exists.
            renamed = sorted(name for name in unknown if name in RENAMED_FIELDS)
            if renamed:
                renames = "\n".join(f"  {name} -> {RENAMED_FIELDS[name]}" for name in renamed)
                raise ValueError(
                    f"{len(renamed)} field(s) in {source} were renamed: the kind is named after "
                    f"the job it does, not after one vendor that can do it.\n{renames}\n"
                    "The value is unchanged — only the field name. Google's own property "
                    "identifier now lives in search_performance_options.gsc_domain, and the "
                    'site itself in the top-level "site_url". See docs/configuration.md.'
                )
            moved = sorted(name for name in unknown if name in MOVED_FIELDS)
            if moved:
                relocations = "\n".join(f"  {name} -> {MOVED_FIELDS[name]}" for name in moved)
                raise ValueError(
                    f"{len(moved)} field(s) in {source} now belong to the provider that uses "
                    f"them, not to the config as a whole:\n{relocations}\n"
                    "Move the value into that provider's options object — see "
                    "docs/configuration.md."
                )
            raise ValueError(f"Unknown AgentConfig field(s) in {source}: {sorted(unknown)}")

        # Templates first, so everything below — the prompt_templates validation
        # here, TemplateValidator, every provider at run time — sees a plain string
        # and never learns that a template can come from a file.
        data, template_sources = resolve_template_files(data, base_dir=base_dir, source=source)
        template_overrides = data.pop("prompt_templates", None)
        data.setdefault("config_base_dir", base_dir)
        config = AgentConfig(**data)
        config.template_sources = template_sources

        if template_overrides:
            if not isinstance(template_overrides, dict):
                raise ValueError(f"prompt_templates in {source} must be an object of channel -> template string")
            signal_names = _signal_names(config)
            merged_templates = dict(config.prompt_templates)
            for channel, template_str in template_overrides.items():
                if channel not in merged_templates:
                    raise ValueError(f"Unknown channel {channel!r} in prompt_templates ({source})")
                try:
                    prompts.validate_template(channel, template_str, signal_names)
                except ValueError as exc:
                    raise ValueError(f"Invalid prompt_templates.{channel} in {source}: {exc}") from exc
                merged_templates[channel] = template_str
            config.prompt_templates = merged_templates

        _validate_pipelines(config, source)

        if validate:
            self.validate_data_templates(config, source)
        return config

    def validate_data_templates(self, config: AgentConfig, source: str = "<config>") -> None:
        """Validate the "templated" analytics/traffic providers against the
        tenant's *actual* raw data (the real file, or a real request to the
        configured API) — there's no generic shape to fake here.

        This does live I/O. It's the right thing to run when a tenant saves a
        config, and the wrong thing to run on every request; see load_dict's
        `validate` parameter."""
        if config.analytics_provider == "templated":
            self._template_validator.validate_analytics(config, source)
        if config.traffic_provider == "templated":
            self._template_validator.validate_traffic(config, source)
