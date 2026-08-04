import re

from ...schemas.channel import Channel
from ...schemas.io import AgentState
from ...utils.text_utils import contains_any


class SelfQaStage:
    """Third pipeline step: analyze -> draft -> self_qa. `.run(state)` is
    registered as the "self_qa" LangGraph node by agent/graph/pipeline.py:build_graph.

    Heuristic checks only, no second LLM call — different checks per channel
    because "good" means something different for an article vs a reply.
    """

    def __init__(self, config) -> None:
        self.config = config

    def run(self, state: AgentState) -> dict:
        """Reads: working.channel if ChooseChannelStage set one, else
        input.channel/config.default_channel; working.draft, and (per channel)
        working.chosen_keyword or working.context_text/input.context_text.
        Writes, on success: phase="done"; the public `output` field (working is
        scratch and gets stripped by AgentRunner.run()).

        If DraftStage already failed (phase="failed", no working.draft), this
        passes the failure through unchanged instead of crashing on a missing
        key — there's nothing to QA. And, like every other stage, this method
        itself never raises past this boundary: an unexpected problem applying
        the heuristic checks below (e.g. a malformed draft shape that slipped
        past DraftStage's own handling) is caught and turned into the same
        phase="failed" shape, not an unhandled exception.
        """
        if state.get("phase") == "failed" or "draft" not in state.get("working", {}):
            return {}

        input_ = state["input"]
        working = state["working"]
        channel = working.get("channel") or input_.get("channel", self.config.default_channel)
        draft_obj = working["draft"]

        try:
            if channel == Channel.ENGAGEMENT_COMMENT:
                context_text = working.get("context_text") or input_.get("context_text", "")
                output = self._qa_comment(context_text, draft_obj)
            else:
                output = self._qa_article(channel, input_, working["chosen_keyword"], draft_obj)
        except Exception as exc:  # noqa: BLE001 - this is the last node; never raise past it
            return {"phase": "failed", "output": None, "error": f"self_qa failed: {exc}"}

        return {"phase": "done", "output": output}

    def _qa_article(self, channel: str, input_, keyword: str, draft_obj: dict) -> dict:
        params = input_.get("params", {})
        max_words = params.get("max_words", self.config.default_max_words)

        body = draft_obj.get("body", "")
        title = draft_obj.get("title", "")
        headings = draft_obj.get("headings", [])
        word_count = len(body.split())

        qa_notes = []
        haystack = " ".join([title, *headings, body]).lower()
        if keyword.lower() not in haystack:
            qa_notes.append(f'Target keyword/topic "{keyword}" not found in title/headings/body.')
        max_words_overage_pct = self.config.qa_article_max_words_overage_pct
        if word_count == 0:
            qa_notes.append("Draft body is empty.")
        elif word_count > max_words * (1 + max_words_overage_pct):
            qa_notes.append(
                f"Draft ({word_count} words) exceeds max_words ({max_words}) "
                f"by >{max_words_overage_pct:.0%}."
            )

        sentences = [s for s in re.split(r"[.!?]+", body) if s.strip()]
        avg_sentence_len = (word_count / len(sentences)) if sentences else 0
        if avg_sentence_len > self.config.qa_article_max_avg_sentence_words:
            qa_notes.append(f"Average sentence length ({avg_sentence_len:.1f} words) may hurt readability.")

        return {
            "kind": channel,  # "site_article" or "external_article"
            "title": title,
            "content": body,
            "format": "markdown",
            "metadata": {
                "target_keyword": keyword,
                "meta_description": draft_obj.get("meta_description", ""),
                "headings": headings,
                "internal_links": draft_obj.get("internal_links", []),
                "word_count": word_count,
                "qa_notes": qa_notes,
                "originality": "not_checked",  # seam: no plagiarism-detection tool exists yet
            },
        }

    def _qa_comment(self, context_text: str, draft_obj: dict) -> dict:
        comment = draft_obj.get("comment", "")
        word_count = len(comment.split())

        qa_notes = []
        if word_count == 0:
            qa_notes.append("Comment is empty.")
        elif word_count > self.config.qa_comment_max_words:
            qa_notes.append(
                f"Comment ({word_count} words) is long for a community reply; consider trimming."
            )

        mentions_platform = contains_any(comment, self.config.qa_brand_mention_keywords)
        has_disclosure = contains_any(comment, self.config.qa_disclosure_phrases)
        if mentions_platform and not has_disclosure:
            qa_notes.append("Mentions the product without a clear affiliation disclosure.")

        link_count = len(re.findall(r"https?://", comment))
        if link_count > self.config.qa_comment_max_links:
            qa_notes.append(
                f"Comment contains {link_count} links; keep it to at most "
                f"{self.config.qa_comment_max_links} to avoid reading as spam."
            )

        return {
            "kind": "comment",
            "title": None,
            "content": comment,
            "format": "markdown",
            "metadata": {
                "in_reply_to": context_text[:200],
                "mentions_platform": mentions_platform,
                "disclosure_included": has_disclosure,
                "word_count": word_count,
                "qa_notes": qa_notes,
                "originality": "not_checked",
            },
        }
