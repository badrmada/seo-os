from __future__ import annotations

from ..schemas.channel import Channel

# Tenants own the full prompt template per channel (see agent/config/agent_config.py's
# AgentConfig.prompt_templates) — Jinja2, rendered with the context dicts built in
# builder.py. The one thing tenants can't touch is the trailing JSON-schema
# instruction: it's appended by the system after the template renders (see
# builder.py's build_article_prompt/build_comment_prompt), so
# agent/utils/json_utils.py's extract_json stays reliable no matter how creative a
# tenant's template gets.

ARTICLE_JSON_INSTRUCTION = (
    "Return ONLY a JSON object with keys: title, meta_description, "
    "headings (array of strings), body (markdown string), internal_links (array of strings). "
    "No prose outside the JSON."
)

COMMENT_JSON_INSTRUCTION = (
    "Return ONLY a JSON object with key: comment (string). No prose outside the JSON."
)

# Generic, product-neutral defaults — a zero-config tenant gets these; Echooers (or
# any tenant) overrides per-channel via AgentConfig.prompt_templates.
#
# Note on analytics_summary/highlights/traffic_summary: these come from
# AppAnalyticsClient/SiteTrafficClient (see tools/base.py), both deliberately
# free-form — the system never assumes a specific tenant's metrics vocabulary
# (upvotes, orders, reads, requests, ...). Each is plain text the client wrote about
# its own domain (highlights is the one structured exception: a generic label/url
# list). All three may be empty for a tenant with no analytics/traffic tool
# configured — every reference to them below is guarded with {% if %} so that's a
# no-op, not a missing-field error.
#
# `signals` is the same idea taken all the way: every *other* configured input
# (AgentConfig.signal_sources, tools/base.py's SignalSource), as {name: {summary,
# facts, items}}. The templates below loop over it by name instead of naming any
# particular signal, which is what lets a tenant add a trends feed, a rank tracker
# or a crawler without this file — or any stage — changing.
#
# Its keys are exactly the tenant's configured signal names, on every run: a
# signal that failed or had nothing to report contributes an empty entry rather
# than disappearing (agent/schemas/signal.py's empty_signal). So a tenant's own
# template may name its own signal — `{{ signals.rank_tracker.facts.tracked }}` —
# and sample_context below validates exactly those names at config-save time.
# Hence the guards below test `signal.summary`, not the dict.
DEFAULT_TEMPLATES: dict[str, str] = {
    Channel.SITE_ARTICLE: (
        "You are a content writer for the following product:\n"
        "{{ brand_description }}\n\n"
        "Goal: {{ agent_goal }}\n"
        'Target keyword/topic: "{{ keyword }}"\n'
        "{% if strategy %}Why this keyword (use it to choose angle, depth, and whether to go "
        "broad or deep): {{ strategy }}\n{% endif %}"
        "Tone: {{ tone }}. Max words: {{ max_words }}. Write as an SEO-optimized article for "
        "our own website.\n"
        "Write to plausibly grow traffic toward that goal (a title/headings that match real "
        "search intent, genuinely useful content) rather than just filling words.\n"
        "{% if analytics_summary %}You may reference this activity naturally if relevant, "
        "don't force it: {{ analytics_summary }}\n{% endif %}"
        "{% if traffic_summary %}You may reference site traffic naturally if relevant, don't "
        "force it: {{ traffic_summary }}\n{% endif %}"
        "{% set reported = signals.values()|selectattr('summary')|list %}"
        "{% if reported %}\n"
        "What this site's other data sources currently show (use only where genuinely "
        "relevant; don't recite them):\n"
        "{% for name, signal in signals.items() %}"
        "{% if signal.summary %}- {{ name }}: {{ signal.summary }}\n{% endif %}"
        "{% endfor %}"
        "{% endif %}"
        "{% if highlights %}\n"
        "Recent content worth grounding examples in (real, current — you may paraphrase 1-2 as "
        "concrete examples of what people are discussing; if you do, include their link in "
        "internal_links so the article drives traffic back to them):\n"
        "{% for item in highlights %}"
        '- "{{ item.label[:200] }}" — {{ item.url }}\n'
        "{% endfor %}"
        "{% endif %}"
    ),
    Channel.EXTERNAL_ARTICLE: (
        "You are a content writer for the following product:\n"
        "{{ brand_description }}\n\n"
        "Goal: {{ agent_goal }}\n"
        'Target topic: "{{ keyword }}"\n'
        "{% if strategy %}Why this topic (use it to choose angle, depth, and whether to go "
        "broad or deep): {{ strategy }}\n{% endif %}"
        "Tone: {{ tone }}. Max words: {{ max_words }}. Write in a friendly, tutorial-style "
        "voice suitable for {{ platform_name|default('an external publishing platform', true) }}. "
        "End with one short, natural mention of the platform below as a soft sign-off — never "
        "oversell it.\n"
        "Write to plausibly grow traffic toward that goal (a title/headings that match real "
        "search intent, genuinely useful content) rather than just filling words.\n"
        "{% if analytics_summary %}You may reference this activity naturally if relevant, "
        "don't force it: {{ analytics_summary }}\n{% endif %}"
        "{% if traffic_summary %}You may reference site traffic naturally if relevant, don't "
        "force it: {{ traffic_summary }}\n{% endif %}"
        "{% set reported = signals.values()|selectattr('summary')|list %}"
        "{% if reported %}\n"
        "What this site's other data sources currently show (use only where genuinely "
        "relevant; don't recite them):\n"
        "{% for name, signal in signals.items() %}"
        "{% if signal.summary %}- {{ name }}: {{ signal.summary }}\n{% endif %}"
        "{% endfor %}"
        "{% endif %}"
        "{% if highlights %}\n"
        "Recent content worth grounding examples in (real, current — you may paraphrase 1-2 as "
        "concrete examples of what people are discussing; if you do, include their link in "
        "internal_links so the article drives traffic back to them):\n"
        "{% for item in highlights %}"
        '- "{{ item.label[:200] }}" — {{ item.url }}\n'
        "{% endfor %}"
        "{% endif %}"
    ),
    Channel.ENGAGEMENT_COMMENT: (
        "You are replying as a genuine community member, not a marketer.\n"
        "Product you may optionally, tastefully mention: {{ brand_description }}\n\n"
        'Replying to: """{{ context_text }}"""\n'
        "Tone: {{ tone }}. Keep it short (2-4 sentences), authentic, and directly relevant to "
        "what was said. Only bring up the product if it's genuinely relevant to this exact "
        "discussion, and if you do, clearly disclose your affiliation "
        '(e.g. "full disclosure, I help build ..."). Never link-drop or sound like an ad.'
    ),
}

# Sample context used to validate a tenant's template at save time — must contain
# every variable/attribute the default templates above reference, so a template
# using any of the "available" variables renders without error.
SAMPLE_CONTEXTS: dict[str, dict] = {
    Channel.SITE_ARTICLE: {
        "channel": Channel.SITE_ARTICLE,
        "keyword": "sample keyword",
        "tone": "informative",
        "max_words": 800,
        "brand_description": "Sample brand description.",
        "agent_goal": "Sample agent goal.",
        "strategy": "Sample reason this keyword was chosen.",
        "platform_name": "",  # only meaningful for external_article; see builder.py's build_article_prompt
        "analytics_summary": "Sample recent activity summary.",
        "traffic_summary": "Sample site traffic summary.",
        "highlights": [{"label": "Sample highlight", "url": "https://example.com/1"}],
        # Filled in per tenant by sample_context() below; empty here so a direct
        # validate_template() call still renders a template that only loops.
        "signals": {},
    },
    Channel.ENGAGEMENT_COMMENT: {
        "context_text": "Sample post being replied to.",
        "tone": "genuine and conversational",
        "brand_description": "Sample brand description.",
        "signals": {},
    },
}
SAMPLE_CONTEXTS[Channel.EXTERNAL_ARTICLE] = SAMPLE_CONTEXTS[Channel.SITE_ARTICLE]

class _AnyKey(dict):
    """A mapping that answers to any key, for validating a template against a
    signal's `facts`/`items`.

    A signal's *name* is knowable at config-save time (it's in signal_sources) and
    is checked strictly, so a typo'd name fails there. The keys *inside* facts and
    items are not: they are the provider's own vocabulary, and this system
    deliberately never assumes one — the same reason the templated analytics and
    traffic providers are validated against the tenant's real data instead of a
    fabricated sample (agent/validators/template_validator.py). There is no real
    data at prompt-save time, so the choice is between rejecting every template
    that reads a fact and accepting any key. Accepting is right: the check that
    matters — syntax, and that the signal exists at all — still happens, and a
    wrong fact key degrades at run time to an empty value in a prompt rather than
    to a failed run.
    """

    def __missing__(self, key):
        return _ANY_KEY


_ANY_KEY = _AnyKey()

# What one signal looks like while validating — all three parts populated, so a
# template reaching into facts or items validates rather than only one that loops
# over summaries.
SAMPLE_SIGNAL = {
    "summary": "Sample signal summary.",
    "facts": _AnyKey(change_pct=12.0),
    "items": [_AnyKey(label="Sample signal row", url="https://example.com/1", value=1)],
}


def sample_context(channel: str, signal_names=()) -> dict:
    """The context a tenant's template is rendered against at config-save time.

    `signal_names` is that tenant's own configured signal_sources names, which is
    what makes `{{ signals.rank_tracker.facts.tracked }}` checkable: the name is
    the tenant's, so no fixed sample could contain it, and without this a template
    naming its own signal would either fail validation for existing or skip the
    check entirely. Passing the real names gets both halves right — a typo'd
    signal name fails at save time, naming a configured one does not.

    This works precisely because working.signals' keys are the configured names on
    every run, whatever each signal did — see agent/schemas/signal.py's
    empty_signal.
    """
    if channel not in SAMPLE_CONTEXTS:
        raise ValueError(f"unknown channel {channel!r} for prompt template validation")
    return {
        **SAMPLE_CONTEXTS[channel],
        "signals": {name: dict(SAMPLE_SIGNAL) for name in signal_names},
    }
