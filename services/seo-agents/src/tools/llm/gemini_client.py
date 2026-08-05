from google import genai
from google.genai import types

from .base import LLMResponse

# An LLM call is the slowest thing in a run and the one most likely to hang. On a
# CLI that's someone pressing Ctrl-C; from a queue worker or an API it's a slot
# held forever. So there is always a bound — generous, because a grounded call
# does real searching before it answers, but never unlimited.
DEFAULT_TIMEOUT_SECONDS = 120.0


class GeminiClient:
    def __init__(self, api_key: str, default_model: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self._client = genai.Client(api_key=api_key)
        self._default_model = default_model
        self._timeout_ms = int(timeout_seconds * 1000)  # google-genai takes milliseconds

    async def generate(self, prompt: str, *, model: str = None, grounded: bool = False) -> LLMResponse:
        """Natively async — google-genai ships a real coroutine API (`client.aio`),
        so the slowest call in a run doesn't occupy a worker thread while it waits.
        Callers reach it through agent/utils/async_utils.py's call(), which is also
        why a tenant's own sync LLM client keeps working unchanged."""
        config = types.GenerateContentConfig(
            http_options=types.HttpOptions(timeout=self._timeout_ms),
            tools=[types.Tool(google_search=types.GoogleSearch())] if grounded else None,
        )
        response = await self._client.aio.models.generate_content(
            model=model or self._default_model,
            contents=prompt,
            config=config,
        )
        tokens = 0
        if response.usage_metadata is not None:
            tokens = response.usage_metadata.total_token_count or 0
        return LLMResponse(
            text=response.text or "",
            tokens=tokens,
            sources=_grounding_sources(response),
            # Gemini genuinely performs grounding when asked, so the request and
            # the reality are the same thing here. A grounded call that cited
            # nothing still reports grounded=True with empty sources — which is
            # what tells LLMOpportunitySource that an unverifiable link really is
            # unverifiable, rather than that this provider can't ground at all.
            grounded=grounded,
        )


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
