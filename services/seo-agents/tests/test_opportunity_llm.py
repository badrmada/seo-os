"""Covers the "grounded by default" + "contract enforced, not trusted" roadmap
items: LLMOpportunitySource defaults to a grounded LLMClient.generate() call,
only trusts a claimed link that matches a real grounding citation, and drops
malformed items (from any source, not just llm) via normalize_opportunity
instead of raising or corrupting the rest of the batch."""

import json

from agent.config.agent_config import AgentConfig
from agent.schemas.opportunity import normalize_opportunity
from tools.clients.opportunity_llm import LLMOpportunitySource
from tools.llm.base import LLMResponse


class FakeLLMClient:
    """Records the last call's kwargs and returns a canned response, so tests can
    assert both what LLMOpportunitySource asked for (grounded=...) and how it
    handles what comes back."""

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.last_kwargs: dict = {}

    def generate(self, prompt: str, *, model: str = None, grounded: bool = False) -> LLMResponse:
        self.last_kwargs = {"model": model, "grounded": grounded}
        return self.response


def _config() -> AgentConfig:
    return AgentConfig()


def _payload(items: list[dict]) -> str:
    return json.dumps(items)


# --- grounded by default ---


def test_discover_calls_llm_grounded_by_default():
    llm = FakeLLMClient(LLMResponse(text=_payload([]), sources=[]))
    source = LLMOpportunitySource("llm_source", llm, _config())

    source.discover({})

    assert llm.last_kwargs["grounded"] is True


def test_discover_respects_grounded_false():
    llm = FakeLLMClient(LLMResponse(text=_payload([]), sources=[]))
    source = LLMOpportunitySource("llm_source", llm, _config(), grounded=False)

    source.discover({})

    assert llm.last_kwargs["grounded"] is False


# --- link only trusted if it's a real grounding citation ---


def test_grounded_link_kept_when_it_matches_a_real_citation():
    real_url = "https://example.com/real-thread"
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [{"topic": "widgets", "signal_strength": 0.8, "reason": "trending", "link": real_url}]
            ),
            sources=[real_url],
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = source.discover({})

    assert opportunity["raw"]["link"] == real_url
    assert opportunity["raw"]["grounding_sources"] == [real_url]


def test_grounded_link_dropped_when_not_a_real_citation():
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [
                    {
                        "topic": "widgets",
                        "signal_strength": 0.8,
                        "reason": "trending",
                        "link": "https://example.com/made-up",
                    }
                ]
            ),
            sources=["https://example.com/a-different-real-page"],
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = source.discover({})

    assert opportunity["raw"]["link"] == ""


def test_ungrounded_link_passes_through_unverified():
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [{"topic": "widgets", "signal_strength": 0.8, "reason": "trending", "link": "https://x.test/y"}]
            ),
            sources=[],
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config(), grounded=False)

    [opportunity] = source.discover({})

    assert opportunity["raw"]["link"] == "https://x.test/y"


# --- malformed items dropped individually, not raised ---


def test_malformed_item_dropped_others_kept():
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [
                    {"topic": "", "signal_strength": 0.9, "reason": "no topic, dropped"},
                    {"topic": "good one", "signal_strength": "not-a-number", "reason": "kept, coerced"},
                ]
            ),
            sources=[],
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    opportunities = source.discover({})

    assert len(opportunities) == 1
    assert opportunities[0]["topic"] == "good one"
    assert opportunities[0]["signal_strength"] == 0.5  # coerced default, not raised


def test_out_of_range_signal_strength_is_clamped():
    llm = FakeLLMClient(
        LLMResponse(text=_payload([{"topic": "widgets", "signal_strength": 5.0}]), sources=[])
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = source.discover({})

    assert opportunity["signal_strength"] == 1.0


def test_invalid_intent_and_channel_hint_fall_back_to_defaults():
    llm = FakeLLMClient(
        LLMResponse(
            text=_payload(
                [{"topic": "widgets", "intent": "not-a-real-intent", "suggested_channel_hint": "not-a-channel"}]
            ),
            sources=[],
        )
    )
    source = LLMOpportunitySource("llm_source", llm, _config())

    [opportunity] = source.discover({})

    assert opportunity["intent"] == "informational"
    assert opportunity["suggested_channel_hint"] is None


# --- normalize_opportunity directly, since discover.py applies it to every
# source (mock/llm/custom), not only the llm one ---


def test_normalize_opportunity_rejects_non_dict():
    assert normalize_opportunity("not a dict", source="custom_source") is None


def test_normalize_opportunity_rejects_missing_topic():
    assert normalize_opportunity({"signal_strength": 0.5}, source="custom_source") is None


def test_normalize_opportunity_overrides_claimed_source():
    opportunity = normalize_opportunity({"topic": "x", "source": "spoofed"}, source="real_source")
    assert opportunity["source"] == "real_source"
