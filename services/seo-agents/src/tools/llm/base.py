from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LLMResponse:
    text: str
    tokens: int = 0
    sources: list[str] = field(default_factory=list)  # real citation URLs from a
    # grounded (search-backed) call; empty for a plain ungrounded generate() —
    # see GeminiClient.generate's grounded= param and LLMOpportunitySource, which
    # uses this to distinguish a real citation from a model-invented one.

    grounded: bool = False
    # Whether grounding *actually happened*, not merely whether it was asked for.
    # A provider that can't ground must leave this False even when called with
    # grounded=True, and must never claim otherwise.
    #
    # This exists because "grounded, and the search cited nothing" and "this
    # provider has no grounding at all" both look identical from `sources` being
    # empty — and they call for opposite handling. In the first case an
    # unverifiable link the model claims is exactly what should be discarded; in
    # the second, discarding it silently destroys every link the model returns,
    # for every opportunity, while the run still reports success. See
    # tools/clients/opportunity_llm.py, which reads this to tell them apart.


class LLMClient(Protocol):
    def generate(self, prompt: str, *, model: str = None, grounded: bool = False) -> LLMResponse:
        """Implementations that don't support grounding should ignore the
        `grounded` flag and leave LLMResponse.grounded False — callers degrade to
        ungrounded behavior and say so, rather than silently losing data."""
        ...
