from __future__ import annotations

import jinja2

from ..schemas.channel import Channel
from .templates import ARTICLE_JSON_INSTRUCTION, COMMENT_JSON_INSTRUCTION, sample_context

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


def validate_template(channel: str, template_str: str, signal_names=()) -> None:
    """Fails fast at config-save time if a tenant's template has bad Jinja2 syntax or
    references a variable that isn't available for this channel.

    `signal_names` is that tenant's own configured signal_sources names — the only
    part of the available context this system can't know in advance. See
    templates.py's sample_context.
    """
    render_template(template_str, sample_context(channel, signal_names))


def build_article_prompt(
    channel: str, keyword: str, params: dict, analytics_summary: str,
    analytics_highlights: list[dict], traffic_summary: str, config,
    strategy: str = "", signals: dict = None,
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
        # Every configured signal input, keyed by its configured name (see
        # tools/base.py's SignalSource) — {name: {summary, facts, items}}. Passed
        # as one bag rather than unpacked into named variables so that adding a
        # signal is config, not an edit here and in every template: the default
        # templates below loop over whatever is present, and a tenant's template
        # can reach into one it knows by name ({{ signals.trends.facts.change_pct }}).
        "signals": signals or {},
        # Free-text hint for external_article ("Medium", "Substack", ...) — the
        # caller supplies it per request; the system never hardcodes a platform
        # list. Unused by site_article's default template, harmless either way.
        "platform_name": params.get("platform_name", ""),
    }
    body = render_template(config.prompt_templates[channel], context)
    return f"{body}\n{ARTICLE_JSON_INSTRUCTION}"


def build_comment_prompt(
    context_text: str, params: dict, config, signals: dict = None,
) -> str:
    context = {
        "context_text": context_text,
        "tone": params.get("tone", config.default_comment_tone),
        "brand_description": config.brand_description,
        # Available here too, though the default comment template deliberately
        # ignores it: a reply is about the thread it answers, not about the site's
        # metrics — the same reason analytics/traffic aren't in it either. A tenant
        # whose signal genuinely bears on a reply can still reach it.
        "signals": signals or {},
    }
    body = render_template(config.prompt_templates[Channel.ENGAGEMENT_COMMENT], context)
    return f"{body}\n{COMMENT_JSON_INSTRUCTION}"
