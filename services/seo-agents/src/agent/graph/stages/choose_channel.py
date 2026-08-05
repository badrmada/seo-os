from collections import defaultdict

from ...schemas.channel import Channel
from ...schemas.io import AgentState


class ChooseChannelStage:
    """Only added to the pipeline when config.discovery_sources is non-empty (see
    agent/graph/pipeline.py:build_graph), immediately after DiscoverStage. Scores
    working.opportunities and sets working.channel — AnalyzeStage/DraftStage/
    SelfQaStage read working.channel when it's present, falling back to
    input.channel/config.default_channel otherwise (so a pipeline without this
    stage behaves exactly as it did before discovery existed).

    An explicit input.channel always wins outright: discovery informs an
    undecided run (input.channel omitted), it never overrides a caller's explicit
    request — that keeps every existing caller's behavior unchanged even once
    discovery_sources is configured.
    """

    def __init__(self, config) -> None:
        self.config = config

    async def run(self, state: AgentState) -> dict:
        """Reads: input.channel (if the caller gave one), working.opportunities.
        Writes: phase="choose_channel"; working.channel; working.channel_decision
        ({"chosen": Channel, "reason": str, "fallback": bool} — fallback=True means
        no opportunity suggested a channel, config.default_channel was used instead
        of a real decision); for an undecided run landing on
        engagement_comment with no input.context_text, also working.context_text
        (borrowed from the top-scoring opportunity, so DraftStage/SelfQaStage have
        something concrete to reply to instead of an empty string).
        """
        input_ = state["input"]
        working = dict(state.get("working", {}))
        opportunities = working.get("opportunities", [])

        if input_.get("channel"):
            channel = input_["channel"]
            working["channel"] = channel
            working["channel_decision"] = {
                "chosen": channel, "reason": "explicit input.channel", "fallback": False,
            }
            return {"phase": "choose_channel", "working": working}

        scores = defaultdict(float)
        for opp in opportunities:
            hint = opp.get("suggested_channel_hint")
            if hint:
                scores[hint] += opp.get("signal_strength", 0) or 0

        if scores:
            channel = max(scores, key=scores.get)
            reason = (
                f"Highest-scoring channel hint across {len(opportunities)} discovered "
                f"opportunit{'y' if len(opportunities) == 1 else 'ies'}: "
                f"{dict(sorted(scores.items(), key=lambda kv: -kv[1]))}."
            )
            fallback = False
        else:
            channel = self.config.default_channel
            reason = "No discovered opportunity suggested a channel; used config.default_channel."
            fallback = True

        working["channel"] = channel
        working["channel_decision"] = {"chosen": channel, "reason": reason, "fallback": fallback}

        if channel == Channel.ENGAGEMENT_COMMENT and not input_.get("context_text") and opportunities:
            top = max(opportunities, key=lambda o: o.get("signal_strength", 0) or 0)
            working["context_text"] = f"{top.get('topic', '')} — {top.get('reason', '')}".strip(" —")

        return {"phase": "choose_channel", "working": working}
