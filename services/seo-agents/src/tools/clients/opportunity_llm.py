from __future__ import annotations

import asyncio
import json
import re

from agent.schemas.opportunity import normalize_opportunity
from agent.utils.async_utils import call as acall

from .templated_json import render_text

# provider="llm" (agent/config/agent_config.py's discovery_sources) — the LLM itself
# is the discovery source, in place of a bespoke Reddit/trends integration.
#
# Grounded by default, and grounded by *search* by default. The order, which is
# also documented on tools/base.py's SearchClient:
#
#   1. A configured SearchClient (search_provider, "duckduckgo" out of the box):
#      write a few search queries, run them, put the real results in the prompt,
#      and treat those URLs as the only trustworthy ones. This is the default
#      because it doesn't depend on which LLM a tenant picked — Gemini can ground
#      natively, a local model or a gateway generally cannot, and grounding should
#      not be a property of the model.
#   2. Else the LLM's own grounding — generate(..., grounded=True), with
#      LLMResponse.sources as the trusted list. What this did before search
#      existed, and still the path for search_provider="none".
#   3. Else ungrounded generation, links passed through unverified.
#
# Each step falls through to the next when it produces nothing: a search that
# errors or returns no results lands on the model's own grounding, and a provider
# that can't ground lands on unverified links (which the reporter says out loud —
# see agent/observability/observed.py's ObservedLLMClient). Nothing here aborts the
# run; a discovery source that fails is one source contributing nothing
# (agent/graph/stages/discover.py).
#
# Set `grounded: false` on the entry to skip both 1 and 2 and go straight to 3.

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

# Step 1's first half. A search engine needs keywords, and the run often has none
# (the real tenant's input.seed_keyword is empty — discovery is precisely the case
# where nobody has told the agent what to look for). So the model writes the
# queries: one short, cheap, deliberately ungrounded call, whose whole output is a
# handful of search strings. Set `search_queries` on the entry to fix them and
# skip this call entirely.
DEFAULT_QUERY_PROMPT_TEMPLATE = (
    "You are planning web searches to find current content opportunities for this product:\n"
    "{{ brand_description }}\n\n"
    "Goal: {{ agent_goal }}\n"
    "{% if seed_keyword %}Focus on this topic/keyword: {{ seed_keyword }}\n{% endif %}"
    "{% if context_text %}Relevant context already gathered: {{ context_text }}\n{% endif %}"
    "Write up to {{ max_queries }} short web search queries (3-8 words each, the way a person "
    "types them) that would surface current discussions, rising topics, and pages worth "
    "writing about or replying to. Make them different from each other — cover distinct "
    "angles rather than rewording one query."
)

QUERY_JSON_INSTRUCTION = (
    "Return ONLY a JSON array of strings (no wrapping object, no prose outside it)."
)

JSON_INSTRUCTION = (
    "Return ONLY a JSON array (no wrapping object, no prose outside it) of objects, each "
    "with keys: topic (string), signal_strength (number 0-1), intent (one of "
    '"commercial", "informational", "mixed", "discussion"), suggested_channel_hint (one of '
    '"site_article", "external_article", "engagement_comment", or null), reason (string, '
    "why this is worth pursuing), and link (string URL, or empty string)."
)

# The bridge between step 1's two halves: real results in, and an instruction that
# names the only URLs the model is allowed to use. Belt and braces — the claimed
# link is checked against this same list afterwards regardless of what the model
# does with the instruction, since an instruction is not a guarantee.
RESULTS_HEADER = (
    "Live web search results, fetched just now. These pages exist; your training data is "
    "older than they are, so prefer what is here over what you remember:"
)

RESULTS_INSTRUCTION = (
    "Base the opportunities on these results. A link must be copied character-for-character "
    "from the list above — any other URL will be discarded, so leave link empty rather than "
    "writing one that isn't there."
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


def _format_results(results: list[dict]) -> str:
    lines = [RESULTS_HEADER]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result.get('title') or '(untitled)'}")
        lines.append(f"   URL: {result['url']}")
        snippet = (result.get("snippet") or "").strip()
        if snippet:
            lines.append(f"   {snippet}")
    lines.append("")
    lines.append(RESULTS_INSTRUCTION)
    return "\n".join(lines)


class LLMOpportunitySource:
    """OpportunitySource (tools/base.py) backed by an LLM plus, by default, a real
    web search. `name` is the discovery_sources registry key this instance was
    built for (agent/managers/tools_manager.py) — it becomes Opportunity.source, so
    multiple LLM-backed sources (e.g. different prompts/angles) can be told apart.

    `search` is the SearchClient (tools/base.py); ToolsManager passes the
    configured one, DuckDuckGo unless a tenant says otherwise. None means the same
    thing as search_provider="none" — skip step 1 of the order in this module's
    docstring.
    """

    def __init__(
        self,
        name: str,
        llm,
        config,
        *,
        search=None,
        prompt_template: str = "",
        query_prompt_template: str = "",
        max_opportunities: int = 5,
        grounded: bool = True,
        search_queries=(),
        max_search_queries: int = 3,
        results_per_query: int = 5,
        max_search_results: int = 12,
    ) -> None:
        self.name = name
        self.llm = llm
        self.config = config
        self.search = search
        self.prompt_template = prompt_template or DEFAULT_PROMPT_TEMPLATE
        self.query_prompt_template = query_prompt_template or DEFAULT_QUERY_PROMPT_TEMPLATE
        self.max_opportunities = max_opportunities
        self.grounded = grounded
        self.search_queries = [q for q in (search_queries or ()) if str(q).strip()]
        self.max_search_queries = max_search_queries
        self.results_per_query = results_per_query
        self.max_search_results = max_search_results

    async def discover(self, context: dict) -> list[dict]:
        """Async because the clients it holds may be (GeminiClient is) — and it
        reaches them through async_utils.call() rather than awaiting directly, so a
        sync client (MockLLMClient, DuckDuckGoSearchClient, a tenant's own) works
        here too."""
        prompt_context = {
            "brand_description": self.config.brand_description,
            "agent_goal": self.config.agent_goal,
            "seed_keyword": context.get("seed_keyword", ""),
            "context_text": context.get("context_text", ""),
            "max_opportunities": self.max_opportunities,
        }
        body = render_text(self.prompt_template, prompt_context)
        results, search_error = await self._search(prompt_context)

        if results:
            # Step 1. The model is asked for nothing but judgement: the facts are
            # in the prompt, so native grounding is switched off even where the
            # provider has it — searching twice for one answer costs money and
            # latency and makes "which URLs are trustworthy?" ambiguous.
            response = await acall(
                self.llm.generate,
                f"{body}\n\n{_format_results(results)}\n\n{JSON_INSTRUCTION}",
                grounded=False,
            )
            sources = [result["url"] for result in results]
            verify_links = True
        else:
            # Steps 2 and 3. Links are only verified against the model's own
            # citations when grounding actually happened — which is not the same
            # as having asked for it. A provider that ignores `grounded` returns no
            # sources, and treating that as "nothing was cited" would strip every
            # link from every opportunity while the run still reported success.
            # Degrade to ungrounded handling instead: the claimed link passes
            # through unverified, exactly as it does for a call that never asked
            # for grounding. See tools/llm/base.py's LLMResponse.grounded.
            response = await acall(
                self.llm.generate, f"{body}\n{JSON_INSTRUCTION}", grounded=self.grounded
            )
            sources = list(getattr(response, "sources", None) or [])
            verify_links = self.grounded and getattr(response, "grounded", False)

        items = _extract_json_array(response.text)[: self.max_opportunities]

        # Which step of the order actually produced this, recorded on every item.
        # Falling through is silent otherwise: a search outage turns verified links
        # into unverified ones while the run still reports success, and nothing in
        # the output says which of the two you're looking at. It goes in `raw`,
        # which is free-form and already reaches the final JSON — no change to the
        # result schema (docs/output-schema.md).
        grounding = "search" if results else ("llm" if verify_links else "none")

        opportunities = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            item["grounding"] = grounding
            if search_error:
                item["grounding_error"] = search_error
            # A claimed link is only trusted if it's one of the URLs that was
            # actually returned — by the search in step 1, by the model's own
            # grounding in step 2. Otherwise it's indistinguishable from a
            # hallucinated one, so it's dropped rather than propagated as if it
            # were real.
            link = item.get("link") or ""
            if verify_links and link not in sources:
                link = ""
            item["link"] = link
            if sources:
                item["grounding_sources"] = sources

            opportunity = normalize_opportunity(item, source=self.name)
            if opportunity is not None:
                opportunities.append(opportunity)
        return opportunities

    # --- step 1: real search -------------------------------------------------

    async def _search(self, prompt_context: dict) -> tuple[list[dict], str]:
        """The search half of step 1: `(results, error)`, either possibly empty.

        Never raises — search is an outbound call to somebody else's service, and
        losing it should cost this source its grounding, not its results; the next
        step of the order still produces opportunities. But it never *hides* the
        failure either: `error` is what discover() records on the items, because
        "the search engine rate-limited us" and "there was nothing to find" look
        identical from an empty list and mean completely different things.
        """
        if self.search is None or not self.grounded:
            return [], ""
        queries = await self._queries(prompt_context)
        if not queries:
            return [], ""

        # One search per query, concurrently — this is the whole reason async
        # execution was worth doing, and it makes three queries cost about what one
        # does. return_exceptions keeps a single failing query to itself.
        batches = await asyncio.gather(
            *(
                acall(self.search.search, query, limit=self.results_per_query)
                for query in queries
            ),
            return_exceptions=True,
        )

        results: list[dict] = []
        seen: set[str] = set()
        errors: list[str] = []
        for batch in batches:
            if isinstance(batch, BaseException):
                errors.append(f"{type(batch).__name__}: {batch}")
                continue
            if not batch:
                continue
            for result in batch:
                if not isinstance(result, dict):
                    continue
                url = (result.get("url") or "").strip()
                # Queries overlap on purpose (different angles on one product), so
                # the same page comes back more than once; sending it to the model
                # twice just spends tokens saying the same thing.
                if not url or url in seen:
                    continue
                seen.add(url)
                results.append({
                    "title": result.get("title") or "",
                    "url": url,
                    "snippet": result.get("snippet") or "",
                })

        error = ""
        if errors and not results:
            # Only when the whole search came back empty. One query of three
            # failing while the others answered is not something to report as a
            # degraded run — the prompt still got real results.
            error = f"{len(errors)} of {len(queries)} searches failed: {errors[0][:200]}"
        return results[: self.max_search_results], error

    async def _queries(self, prompt_context: dict) -> list[str]:
        """What to search for. Configured queries win; otherwise the model writes
        them; if that call fails or returns nothing usable, the seed keyword is the
        last resort, and no seed keyword means no search at all rather than a
        guess at what this product is about."""
        if self.search_queries:
            return self.search_queries[: self.max_search_queries]

        seed = (prompt_context.get("seed_keyword") or "").strip()
        try:
            prompt = render_text(
                self.query_prompt_template,
                {**prompt_context, "max_queries": self.max_search_queries},
            )
            response = await acall(
                self.llm.generate, f"{prompt}\n{QUERY_JSON_INSTRUCTION}", grounded=False
            )
            queries = [
                str(query).strip()
                for query in _extract_json_array(response.text)
                if str(query).strip()
            ]
        except Exception:  # noqa: BLE001 - a failed query call degrades, see _search
            queries = []
        return (queries or ([seed] if seed else []))[: self.max_search_queries]
