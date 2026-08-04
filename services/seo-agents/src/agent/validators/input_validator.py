from ..schemas.channel import Channel
from ..schemas.io import AgentInput

# Google Search Console identifies a property one of two ways — a domain property
# ("sc-domain:example.com") or a URL-prefix property (a full URL, normally trailing-
# slashed, e.g. "https://example.com/"). This shape requirement is Google's, not a
# generic one, so it's only enforced when gsc_provider="google" — a different (or
# future) GSC-like provider could accept a different identifier shape entirely.
_GOOGLE_GSC_PREFIXES = ("sc-domain:", "http://", "https://")

# The same "reject a typo'd key immediately" guarantee AgentConfigLoader gives
# tenant.json (see agent/config/loader.py) — without this, an unknown key in
# input.json (e.g. "seed_keywrod") would just be silently ignored instead of
# failing at the one place a caller can still notice it.
_KNOWN_INPUT_FIELDS = set(AgentInput.__annotations__)


class InputValidator:
    """Validates a run's AgentInput before the graph executes — called by
    agent/managers/run_manager.py's AgentRunner (run() and preview_prompt())."""

    def validate(self, input_data: dict, config) -> None:
        unknown = set(input_data) - _KNOWN_INPUT_FIELDS
        if unknown:
            raise ValueError(f"Unknown AgentInput field(s): {sorted(unknown)}")

        channel = input_data.get("channel")
        if not channel:
            if config.discovery_sources:
                # The caller left channel unset and discovery is configured, so
                # ChooseChannelStage decides it once the graph runs (see
                # agent/graph/stages/choose_channel.py) — there's no fixed channel
                # to validate gsc_domain/context_text against yet. Every stage
                # degrades gracefully if its channel-specific input ends up
                # missing (AnalyzeStage skips GSC without gsc_domain,
                # DraftStage/SelfQaStage fall back to an empty/discovered
                # context_text), so skipping this check is safe, not just deferred.
                return
            channel = config.default_channel

        if channel == Channel.ENGAGEMENT_COMMENT:
            if not input_data.get("context_text"):
                raise ValueError(f'input.context_text is required when channel="{Channel.ENGAGEMENT_COMMENT}"')
            return

        gsc_domain = input_data.get("gsc_domain")
        if not gsc_domain:
            raise ValueError(f'input.gsc_domain is required when channel="{channel}"')
        if config.gsc_provider == "google" and not gsc_domain.startswith(_GOOGLE_GSC_PREFIXES):
            raise ValueError(
                f'input.gsc_domain {gsc_domain!r} is not a valid Google Search Console property '
                f'identifier for gsc_provider="google" — expected "sc-domain:<domain>" or a '
                f'URL-prefix property starting with "http://" or "https://"'
            )
