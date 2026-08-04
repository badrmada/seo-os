"""The catalog of every pluggable provider kind and the provider names each one
accepts — one declarative place, so the CLI's `list-tools` reports what the system
actually supports instead of a hand-maintained list that drifts.

Today the concrete builders in tools_manager.py are still if/elif ladders and this
catalog describes them; `src/tests/test_providers.py` asserts the two agree, so a
provider added to one without the other fails the suite. PLAN.md Step 1 replaces
those ladders with a registry built from this catalog, at which point the two
cannot disagree at all.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderKind:
    """One pluggable interface, and the providers that implement it."""

    kind: str            # short name used in CLI output
    interface: str       # the Protocol a provider must satisfy
    config_field: str    # the AgentConfig field that selects it
    providers: dict      # provider name -> one-line description
    is_list: bool = False  # True when the config field is a list of entries
                            # (several may be configured at once) rather than a
                            # single chosen provider

    def selected(self, config) -> list[str]:
        """Which provider(s) this tenant's config actually selects."""
        value = getattr(config, self.config_field, None)
        if not self.is_list:
            return [value] if value else []
        return [entry.get("provider", "mock") for entry in value or []]


CUSTOM = 'a class of your own ("module.path:ClassName")'

CATALOG = (
    ProviderKind(
        kind="llm",
        interface="tools/llm/base.py::LLMClient",
        config_field="llm_provider",
        providers={
            "gemini": "Google Gemini, with optional Google Search grounding",
            "mock": "offline, deterministic — no API calls",
        },
    ),
    ProviderKind(
        kind="gsc",
        interface="tools/base.py::GSCClient",
        config_field="gsc_provider",
        providers={
            "google": "Google Search Console API",
            "mock": "offline, deterministic — no API calls",
        },
    ),
    ProviderKind(
        kind="traffic",
        interface="tools/base.py::SiteTrafficClient",
        config_field="traffic_provider",
        providers={
            "none": "no traffic tool at all; always an empty summary",
            "mock": "offline, deterministic — no API calls",
            "cloudflare": "Cloudflare GraphQL Analytics API",
            "templated": "your own JSON (file or API), mapped by a Jinja2 template",
            "custom": CUSTOM,
        },
    ),
    ProviderKind(
        kind="analytics",
        interface="tools/base.py::AppAnalyticsClient",
        config_field="analytics_provider",
        providers={
            "mock": "offline, deterministic — no API calls",
            "templated": "your own JSON (file or API), mapped by Jinja2 templates",
            "custom": CUSTOM,
        },
    ),
    ProviderKind(
        kind="discovery",
        interface="tools/base.py::OpportunitySource",
        config_field="discovery_sources",
        is_list=True,
        providers={
            "mock": "offline, deterministic fixtures",
            "llm": "the LLM itself surfaces opportunities, grounded by default",
            "custom": CUSTOM,
        },
    ),
    ProviderKind(
        kind="output",
        interface="tools/base.py::OutputSink",
        config_field="output_sinks",
        is_list=True,
        providers={
            "json": "indented JSON to stdout, or to a file (optionally JSONL)",
            "webhook": "POST the result to an HTTP endpoint",
            "custom": CUSTOM,
        },
    ),
)

BY_KIND = {kind.kind: kind for kind in CATALOG}
