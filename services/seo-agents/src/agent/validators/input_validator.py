from ..schemas.channel import Channel
from ..schemas.io import AgentInput

# Input fields that moved into the tenant config, with where each one went.
# Deliberately here rather than next to agent/config/loader.py's MOVED_FIELDS,
# which is the obvious home: importing config.loader from this module closes the
# agent.config <-> agent.validators cycle (loader imports TemplateValidator from
# this package), and src/tests/test_imports.py exists because that cycle has been
# introduced before. This map is only ever read here anyway.
INPUT_MOVED_FIELDS = {
    "gsc_domain": (
        'search_performance_options.gsc_domain (your Search Console property, e.g. '
        '"sc-domain:example.com"), with the site itself as the top-level "site_url" '
        '(e.g. "https://example.com")'
    ),
}

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
            # A field that *moved* gets its destination named, rather than the
            # generic "unknown field" — the same treatment AgentConfigLoader gives
            # a relocated config field, and for the same reason: "Unknown
            # AgentInput field ['gsc_domain']" is true and leaves someone with no
            # idea where the value went.
            relocated = sorted(name for name in unknown if name in INPUT_MOVED_FIELDS)
            if relocated:
                destinations = "\n".join(
                    f"  input.{name} -> {INPUT_MOVED_FIELDS[name]}" for name in relocated
                )
                raise ValueError(
                    f"{len(relocated)} field(s) in this input moved into the tenant config, "
                    f"because they describe the site rather than this run:\n{destinations}\n"
                    "See docs/configuration.md."
                )
            raise ValueError(f"Unknown AgentInput field(s): {sorted(unknown)}")

        channel = input_data.get("channel")
        if not channel:
            if config.discovery_sources:
                # The caller left channel unset and discovery is configured, so
                # ChooseChannelStage decides it once the graph runs (see
                # agent/graph/stages/choose_channel.py) — there's no fixed channel
                # to validate context_text against yet. Every stage degrades
                # gracefully if its channel-specific input ends up missing
                # (DraftStage/SelfQaStage fall back to an empty/discovered
                # context_text), so skipping this check is safe, not just deferred.
                return
            channel = config.default_channel

        if channel == Channel.ENGAGEMENT_COMMENT:
            if not input_data.get("context_text"):
                raise ValueError(f'input.context_text is required when channel="{Channel.ENGAGEMENT_COMMENT}"')

        # Nothing is required for the article channels any more. `gsc_domain` used
        # to be, which made a Google Search Console property identifier mandatory
        # on every article run — including for a tenant who had never connected
        # Search Console and never would. The site is now config
        # (`site_url`), the Search Console property belongs to the provider that
        # understands it (`search_performance_options.gsc_domain`, validated by
        # that client at construction), and a run with no rank data at all is a
        # perfectly good run: _pick_keyword falls back to the seed keyword, an
        # analytics highlight, then a discovered opportunity.
