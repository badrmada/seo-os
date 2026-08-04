from google import genai
from google.genai import types

from .base import LLMResponse


class GeminiClient:
    def __init__(self, api_key: str, default_model: str):
        self._client = genai.Client(api_key=api_key)
        self._default_model = default_model


    def generate(self, prompt: str, *, model: str = None, grounded: bool = False) -> LLMResponse:
        config = None
        if grounded:
            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            )
        response = self._client.models.generate_content(
            model=model or self._default_model,
            contents=prompt,
            config=config,
        )
        tokens = 0
        if response.usage_metadata is not None:
            tokens = response.usage_metadata.total_token_count or 0
        return LLMResponse(text=response.text or "", tokens=tokens, sources=_grounding_sources(response))


def _grounding_sources(response) -> list[str]:
    """Real citation URLs Google Search grounding actually used, if any — the one
    signal that tells a caller (LLMOpportunitySource) a URL is real rather than
    invented by the model. Absent entirely on an ungrounded call or a grounded one
    that found nothing to cite."""
    urls: list[str] = []
    for candidate in response.candidates or []:
        metadata = getattr(candidate, "grounding_metadata", None)
        if not metadata or not metadata.grounding_chunks:
            continue
        for chunk in metadata.grounding_chunks:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None) if web else None
            if uri:
                urls.append(uri)
    return urls
