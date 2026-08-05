from __future__ import annotations

import json
import re

from agent.schemas.opportunity import normalize_opportunity

from .templated_json import render_text

# provider="llm" (agent/config/agent_config.py's discovery_sources) — the LLM itself
# is the discovery source, in place of a bespoke Reddit/trends integration. Grounded
# by default (see LLMOpportunitySource.__init__'s grounded=True): the underlying
# LLMClient.generate(..., grounded=True) call lets the model actually search rather
# than invent, and response.sources (LLMResponse.sources) carries the real citation
# URLs used to validate a link isn't hallucinated. Set grounded=False (or
# discovery_sources[...].grounded: false in config) to fall back to the old
# ungrounded, training-knowledge-only behavior.

DEFAULT_PROMPT_TEMPLATE = (
    "You are scouting content opportunities for the following product:\n"
    "{{ brand_description }}\n\n"
    "Goal: {{ agent_goal }}\n"
    "{% if seed_keyword %}Steer toward this topic/keyword if relevant, but don't force it: "
    "{{ seed_keyword }}\n{% endif %}"
    "{% if context_text %}Relevant context already gathered: {{ context_text }}\n{% endif %}"
    "Search for and identify up to {{ max_opportunities }} distinct, concrete content "
    "opportunities: rising topics, discussions, or angles genuinely worth writing about or "
    "replying to right now. Prefer specific, real topics backed by what you find over "
    "generic ones. If you find a specific existing post/thread/page worth referencing, "
    "include its real URL; otherwise leave link empty — never invent a URL."
)

JSON_INSTRUCTION = (
    "Return ONLY a JSON array (no wrapping object, no prose outside it) of objects, each "
    "with keys: topic (string), signal_strength (number 0-1), intent (one of "
    '"commercial", "informational", "mixed", "discussion"), suggested_channel_hint (one of '
    '"site_article", "external_article", "engagement_comment", or null), reason (string, '
    "why this is worth pursuing), and link (string URL, or empty string)."
)


def _extract_json_array(text: str) -> list:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    payload = json.loads(cleaned)
    if isinstance(payload, dict):
        # tolerate a wrapping {"opportunities": [...]} even though the prompt asks for a
        # bare array — LLMs sometimes wrap it despite instructions.
        payload = payload.get("opportunities", [])
    if not isinstance(payload, list):
        raise ValueError(f"expected a JSON array of opportunities, got: {text!r}")
    return payload


class LLMOpportunitySource:
    """OpportunitySource (tools/base.py) backed by a single LLM call instead of an
    external API. `name` is the discovery_sources registry key this instance was
    built for (agent/managers/tools_manager.py) — it becomes Opportunity.source, so
    multiple LLM-backed sources (e.g. different prompts/angles) can be told apart.
    `grounded=True` (the default) makes that call search-backed instead of a bare
    training-knowledge guess — see module docstring.
    """

    def __init__(
        self,
        name: str,
        llm,
        config,
        *,
        prompt_template: str = "",
        max_opportunities: int = 5,
        grounded: bool = True,
    ) -> None:
        self.name = name
        self.llm = llm
        self.config = config
        self.prompt_template = prompt_template or DEFAULT_PROMPT_TEMPLATE
        self.max_opportunities = max_opportunities
        self.grounded = grounded

    def discover(self, context: dict) -> list[dict]:
        prompt_context = {
            "brand_description": self.config.brand_description,
            "agent_goal": self.config.agent_goal,
            "seed_keyword": context.get("seed_keyword", ""),
            "context_text": context.get("context_text", ""),
            "max_opportunities": self.max_opportunities,
        }
        body = render_text(self.prompt_template, prompt_context)
        response = self.llm.generate(f"{body}\n{JSON_INSTRUCTION}", grounded=self.grounded)
        items = _extract_json_array(response.text)[: self.max_opportunities]

        # Links are only verified against the search results when grounding
        # actually happened — which is not the same as having asked for it. A
        # provider that ignores `grounded` returns no sources, and treating that
        # as "nothing was cited" would strip every link from every opportunity
        # while the run still reported success. Degrade to ungrounded handling
        # instead: the claimed link passes through unverified, exactly as it does
        # for a call that never asked for grounding. See tools/llm/base.py's
        # LLMResponse.grounded.
        verify_links = self.grounded and getattr(response, "grounded", False)

        opportunities = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            # A grounded call's claimed link is only trusted if it's one of the URLs
            # the search tool actually returned — otherwise it's indistinguishable
            # from a hallucinated one, so it's dropped rather than propagated as if
            # it were real.
            link = item.get("link") or ""
            if verify_links and link not in response.sources:
                link = ""
            item["link"] = link
            if response.sources:
                item["grounding_sources"] = response.sources

            opportunity = normalize_opportunity(item, source=self.name)
            if opportunity is not None:
                opportunities.append(opportunity)
        return opportunities
