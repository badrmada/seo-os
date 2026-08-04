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


class LLMClient(Protocol):
    def generate(self, prompt: str, *, model: str = None, grounded: bool = False) -> LLMResponse: ...
