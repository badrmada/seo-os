import json
from dataclasses import fields
from pathlib import Path

from .. import prompts
from ..validators.template_validator import TemplateValidator
from .agent_config import AgentConfig


class AgentConfigLoader:
    """Loads a tenant AgentConfig from a JSON file, overriding only the fields it
    sets — anything it omits keeps the generic default (see AgentConfig). Unknown
    keys in the file raise, so a typo'd field name fails fast instead of being
    silently ignored."""

    def __init__(self) -> None:
        self._template_validator = TemplateValidator()

    def load(self, path: str) -> AgentConfig:
        """`prompt_templates` is special-cased: the file may override just one or two
        channels, and each overridden template is rendered against a sample context
        right here so a broken Jinja2 template (bad syntax, a variable that isn't
        actually available) fails at load time, not mid-run.

        If analytics_provider="templated", analytics_summary_template/
        analytics_highlights_template are validated the same way — but against the
        tenant's *actual* raw data (the real file, or a real request to
        analytics_api_url), not a fabricated sample, since there's no generic shape
        to fake here. A source="api" tenant's config load therefore depends on that
        endpoint being reachable; that's intentional — better to fail at
        config-save time than mid-run."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(AgentConfig)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown AgentConfig field(s) in {path}: {sorted(unknown)}")

        template_overrides = data.pop("prompt_templates", None)
        config = AgentConfig(**data)
        if template_overrides:
            if not isinstance(template_overrides, dict):
                raise ValueError(f"prompt_templates in {path} must be an object of channel -> template string")
            merged_templates = dict(config.prompt_templates)
            for channel, template_str in template_overrides.items():
                if channel not in merged_templates:
                    raise ValueError(f"Unknown channel {channel!r} in prompt_templates ({path})")
                try:
                    prompts.validate_template(channel, template_str)
                except ValueError as exc:
                    raise ValueError(f"Invalid prompt_templates.{channel} in {path}: {exc}") from exc
                merged_templates[channel] = template_str
            config.prompt_templates = merged_templates

        if config.analytics_provider == "templated":
            self._template_validator.validate_analytics(config, path)
        if config.traffic_provider == "templated":
            self._template_validator.validate_traffic(config, path)
        return config
