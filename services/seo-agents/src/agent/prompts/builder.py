from __future__ import annotations

import jinja2

from ..schemas.channel import Channel
from .templates import ARTICLE_JSON_INSTRUCTION, COMMENT_JSON_INSTRUCTION, SAMPLE_CONTEXTS

_ENV = jinja2.Environment(
    trim_blocks=True, lstrip_blocks=True, undefined=jinja2.StrictUndefined,
)


def render_template(template_str: str, context: dict) -> str:
    """Render a tenant's Jinja2 template. Raises ValueError (not a jinja2 exception)
    on bad syntax or a missing variable, so callers don't need to know Jinja2's
    exception types — see validate_template for the save-time check that surfaces
    this as a clear config error instead of a mid-run crash."""
    try:
        template = _ENV.from_string(template_str)
        return template.render(**context)
    except jinja2.TemplateError as exc:
        raise ValueError(f"prompt template error: {exc}") from exc


def validate_template(channel: str, template_str: str) -> None:
    """Fails fast at config-save time if a tenant's template has bad Jinja2 syntax or
    references a variable that isn't available for this channel."""
    if channel not in SAMPLE_CONTEXTS:
        raise ValueError(f"unknown channel {channel!r} for prompt template validation")
    render_template(template_str, SAMPLE_CONTEXTS[channel])


def build_article_prompt(
    channel: str, keyword: str, params: dict, analytics_summary: str,
    analytics_highlights: list[dict], traffic_summary: str, config,
    strategy: str = "",
) -> str:
    context = {
        "channel": channel,
        "keyword": keyword,
        "tone": params.get("tone", config.default_article_tone),
        "max_words": params.get("max_words", config.default_max_words),
        "brand_description": config.brand_description,
        "agent_goal": config.agent_goal,
        "strategy": strategy,
        "analytics_summary": analytics_summary,
        "traffic_summary": traffic_summary,
        "highlights": analytics_highlights or [],
        # Free-text hint for external_article ("Medium", "Substack", ...) — the
        # caller supplies it per request; the system never hardcodes a platform
        # list. Unused by site_article's default template, harmless either way.
        "platform_name": params.get("platform_name", ""),
    }
    body = render_template(config.prompt_templates[channel], context)
    return f"{body}\n{ARTICLE_JSON_INSTRUCTION}"


def build_comment_prompt(context_text: str, params: dict, config) -> str:
    context = {
        "context_text": context_text,
        "tone": params.get("tone", config.default_comment_tone),
        "brand_description": config.brand_description,
    }
    body = render_template(config.prompt_templates[Channel.ENGAGEMENT_COMMENT], context)
    return f"{body}\n{COMMENT_JSON_INSTRUCTION}"
