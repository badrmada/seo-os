from ... import prompts
from ...schemas.channel import Channel
from ...schemas.io import AgentState
from ...utils.async_utils import call as acall
from ...utils.json_utils import extract_json
from ...utils.tool_errors import record_tool_error
from ..tools import Tools


class DraftStage:
    """Second pipeline step: analyze -> draft -> self_qa. `.run(state)` is
    registered as the "draft" LangGraph node by agent/graph/pipeline.py:build_graph."""

    def __init__(self, tools: Tools, config) -> None:
        self.tools = tools
        self.config = config

    async def run(self, state: AgentState) -> dict:
        """Reads: working.channel if ChooseChannelStage set one, else
        input.channel/config.default_channel; input.params; working.chosen_keyword
        or working.context_text/input.context_text (depending on channel);
        working.analytics_summary, working.analytics_highlights,
        working.traffic_summary.
        Writes, on success: phase="draft"; working.draft (the LLM's parsed JSON —
        an article shape {title, meta_description, headings, body, internal_links}
        for site_article/external_article, or {comment} for engagement_comment);
        usage.tokens (incremented).

        Unlike AnalyzeStage's tool calls, there's no meaningful draft to degrade
        to if prompt-building or the LLM call itself fails (a bad/malformed
        response included) — there's nothing worth handing SelfQaStage. So on
        failure this writes phase="failed", error, and working.tool_errors
        instead, and deliberately does *not* write working.draft — SelfQaStage
        checks for that and passes the failure through unchanged rather than
        attempting to QA a draft that doesn't exist. Either way, this method
        itself never raises past this boundary.
        """
        working = dict(state.get("working", {}))
        try:
            draft_obj, tokens_used = await self._generate(state, working)
        except Exception as exc:  # noqa: BLE001 - degrade, don't abort the run
            tool_errors = list(working.get("tool_errors", []))
            record_tool_error(tool_errors, "llm", "draft", exc)
            working["tool_errors"] = tool_errors
            return {
                "phase": "failed",
                "working": working,
                "error": f"draft failed: {exc}",
            }

        working["draft"] = draft_obj
        usage = dict(state.get("usage", {}))
        usage["tokens"] = usage.get("tokens", 0) + tokens_used
        return {"phase": "draft", "working": working, "usage": usage}

    async def _generate(self, state: AgentState, working: dict) -> tuple[dict, int]:
        input_ = state["input"]
        channel = working.get("channel") or input_.get("channel", self.config.default_channel)
        params = input_.get("params", {})

        if channel == Channel.ENGAGEMENT_COMMENT:
            # working.context_text is only set by ChooseChannelStage, and only when
            # it picked engagement_comment itself with no input.context_text given
            # (see stages/choose_channel.py) — everyone else uses input.context_text.
            context_text = working.get("context_text") or input_.get("context_text", "")
            prompt = prompts.build_comment_prompt(context_text, params, self.config)
        else:
            source_row = working.get("chosen_keyword_row") or {}
            prompt = prompts.build_article_prompt(
                channel,
                working["chosen_keyword"],
                params,
                working["analytics_summary"],
                working["analytics_highlights"],
                working["traffic_summary"],
                self.config,
                strategy=source_row.get("reason", ""),
            )

        response = await acall(self.tools.llm.generate, prompt)
        return extract_json(response.text), response.tokens
