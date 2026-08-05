import json
from dataclasses import fields
from pathlib import Path

from .. import prompts
from ..validators.template_validator import TemplateValidator
from .agent_config import AgentConfig

# Settings that used to sit at the top level of a config and now belong to the
# provider that actually uses them (see AgentConfig's "Provider-owned settings").
# Kept as a map rather than deleted quietly: "Unknown AgentConfig field
# 'gemini_api_key'" is true but useless, and a tenant hitting it has no way to
# guess where the value went. Naming the destination turns a broken config into a
# two-second edit.
MOVED_FIELDS = {
    "llm_model": "llm_options.model",
    "gemini_api_key": "llm_options.api_key",
    "gsc_key_file": "gsc_options.key_file",
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
        known = {f.name for f in fields(AgentConfig)}
        unknown = set(data) - known
        if unknown:
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

        data = dict(data)
        template_overrides = data.pop("prompt_templates", None)
        data.setdefault("config_base_dir", base_dir)
        config = AgentConfig(**data)

        if template_overrides:
            if not isinstance(template_overrides, dict):
                raise ValueError(f"prompt_templates in {source} must be an object of channel -> template string")
            merged_templates = dict(config.prompt_templates)
            for channel, template_str in template_overrides.items():
                if channel not in merged_templates:
                    raise ValueError(f"Unknown channel {channel!r} in prompt_templates ({source})")
                try:
                    prompts.validate_template(channel, template_str)
                except ValueError as exc:
                    raise ValueError(f"Invalid prompt_templates.{channel} in {source}: {exc}") from exc
                merged_templates[channel] = template_str
            config.prompt_templates = merged_templates

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
