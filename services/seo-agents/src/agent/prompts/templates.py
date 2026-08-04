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
    },
    Channel.ENGAGEMENT_COMMENT: {
        "context_text": "Sample post being replied to.",
        "tone": "genuine and conversational",
        "brand_description": "Sample brand description.",
    },
}
SAMPLE_CONTEXTS[Channel.EXTERNAL_ARTICLE] = SAMPLE_CONTEXTS[Channel.SITE_ARTICLE]
