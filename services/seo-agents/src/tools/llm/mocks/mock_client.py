import json
import re

from ..base import LLMResponse


class MockLLMClient:
    """Deterministic, no-network stand-in for a real LLM — used for offline test runs.

    Understands the two prompt shapes agent/graph/stages/draft.py sends and returns a matching,
    parseable JSON payload for each, so the rest of the pipeline (including self_qa's
    heuristic checks) exercises real logic instead of a canned success:
      - article prompt  (site_article/external_article) -> {title, meta_description,
        headings, body, internal_links}, keyed off the 'Target keyword/topic: "..."' line
      - comment prompt  (engagement_comment) -> {comment}, keyed off the
        'Replying to:' line
    """

    def generate(self, prompt: str, *, model: str = None, grounded: bool = False) -> LLMResponse:
        if "Replying to:" in prompt:
            return self._mock_comment(prompt)
        return self._mock_article(prompt)

    def _mock_comment(self, prompt: str) -> LLMResponse:
        match = re.search(r'Replying to:\s*"""(.*?)"""', prompt, re.DOTALL)
        context = (match.group(1).strip() if match else "this post")[:60]
        comment = (
            f'Relate to this a lot re: "{context}..." — ran into the same thing myself. '
            "Full disclosure, I help build an anonymous posting platform for exactly this "
            "kind of conversation, no login or tracking involved, so no judgment either way."
        )
        payload = {"comment": comment}
        text = json.dumps(payload)
        return LLMResponse(text=text, tokens=len(text.split()))

    def _mock_article(self, prompt: str) -> LLMResponse:
        match = re.search(r'Target keyword/topic:\s*"([^"]*)"', prompt)
        keyword = match.group(1) if match else "your topic"

        draft = {
            "title": f"The Complete Guide to {keyword.title()}",
            "meta_description": f"Everything you need to know about {keyword}, explained simply.",
            "headings": [
                f"What Is {keyword.title()}?",
                f"Why {keyword.title()} Matters",
                f"How to Get Started with {keyword.title()}",
                "Common Mistakes to Avoid",
                "Conclusion",
            ],
            "body": (
                f"# The Complete Guide to {keyword.title()}\n\n"
                f"{keyword.title()} is an important topic for anyone looking to improve their results. "
                f"In this guide we cover what {keyword} means, why it matters, and how to get started.\n\n"
                f"## What Is {keyword.title()}?\n\n"
                f"{keyword.title()} refers to the practice of optimizing for this exact need.\n\n"
                f"## Why {keyword.title()} Matters\n\n"
                f"Getting {keyword} right drives measurable outcomes.\n\n"
                f"## How to Get Started with {keyword.title()}\n\n"
                f"Start small, measure, and iterate on {keyword}.\n\n"
                "## Common Mistakes to Avoid\n\n"
                "Avoid overcomplicating the basics before you've validated them.\n\n"
                "## Conclusion\n\n"
                f"{keyword.title()} rewards consistency over time.\n"
            ),
            "internal_links": [],
        }
        text = json.dumps(draft)
        return LLMResponse(text=text, tokens=len(text.split()))
