"""Builds the run's Tools bundle from an AgentConfig — the single place a concrete
provider is chosen per client kind.

**Providers are a registry, not a ladder.** `_REGISTRY` maps
`kind -> {provider name -> factory}`, and the names in it are exactly the names
the catalog in providers.py advertises to `list-tools` — `src/tests/test_providers.py`
asserts set equality per kind, so a provider added to one without the other fails
the suite rather than reaching a user as a wrong answer from `list-tools` or a
confusing "Unknown provider" from a documented name.

Adding a provider is therefore two lines in two files (a factory here, a name and
description there) and nothing else: no new `elif`, no new error message, no new
config field — settings go in that provider's own `options`.
"""

from dataclasses import dataclass, field

from tools.clients.analytics_templated import TemplatedAnalyticsClient
from tools.clients.cloudflare import CloudflareAnalyticsClient
from tools.clients.google_search_console import GoogleSearchConsoleClient
from tools.clients.opportunity_llm import LLMOpportunitySource
from tools.clients.traffic_templated import TemplatedTrafficClient
from tools.llm.gemini_client import GeminiClient
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.gsc_mock import MockGoogleSearchConsoleClient
from tools.mocks.opportunity_mock import MockOpportunitySource
from tools.mocks.traffic_mock import MockTrafficClient
from tools.mocks.traffic_null import NullTrafficClient

from ..config.paths import resolve_path
from ..graph.tools import Tools
from .plugin_loader import load_custom

# Marks an option that has no legacy top-level field behind it — new settings
# live only in `options`, which is the point of having them.
NO_ALIAS = ""


@dataclass(frozen=True)
class ProviderContext:
    """What a factory gets: the tenant's config, the selected provider's own
    `options`, and whatever the caller has that isn't in either (`extras` — the
    per-run model override, the shared LLM client, a discovery source's name).

    `plugin_options` exists because the two are not always the same dict. A
    discovery source's built-in settings have always been written directly on its
    entry (`{"name": ..., "provider": "llm", "grounded": false}`), so `options`
    merges the entry for the built-in factories to read — but a tenant's own class
    must keep receiving exactly what docs/extending.md promises it, the entry's
    `"options"` object and nothing else.
    """

    config: object
    options: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    plugin_options: dict = None

    def option(self, key: str, alias: str = NO_ALIAS, default=None):
        """One setting, resolved: the provider's own `options` first, then the
        legacy top-level config field, then the default.

        The alias is what makes this refactor non-breaking. Moving
        `gemini_api_key` into `llm_options.api_key` is the right shape, but
        *removing* the old field would be a config migration for every existing
        tenant — an unreasonable price for an internal tidy-up. So both work, the
        option wins, and a tenant migrates when it suits them.
        """
        if key in self.options:
            return self.options[key]
        if alias:
            value = getattr(self.config, alias, None)
            if value not in (None, ""):
                return value
        return default

    def path_option(self, key: str, alias: str = NO_ALIAS, default: str = ""):
        """An option naming a file, resolved against the tenant's own folder
        rather than the process's working directory (agent/config/paths.py)."""
        return resolve_path(self.config, self.option(key, alias, default))

    def custom(self, field_name: str):
        """The `"custom"` provider, everywhere it appears. The class path comes
        from the kind's `*_custom_class` field (or, for a list kind, the entry's
        own `"class"`), and the provider's options are handed to the class when
        its constructor takes them — see plugin_loader.load_custom."""
        class_path = self.extras.get("class_path") or getattr(self.config, field_name, "")
        options = self.plugin_options if self.plugin_options is not None else self.options
        return load_custom(class_path, self.extras.get("label", field_name), self.config, options)


def _templated_analytics(ctx: ProviderContext) -> TemplatedAnalyticsClient:
    return TemplatedAnalyticsClient(
        ctx.option("source", "analytics_source", "file"),
        ctx.option("summary_template", "analytics_summary_template", ""),
        ctx.option("highlights_template", "analytics_highlights_template", ""),
        report_path=ctx.path_option("report_path", "analytics_report_path"),
        api_url=ctx.option("api_url", "analytics_api_url", ""),
        api_method=ctx.option("api_method", "analytics_api_method", "GET"),
        api_headers=ctx.option("api_headers", "analytics_api_headers", {}),
        api_timeout_seconds=ctx.option("api_timeout_seconds", "analytics_api_timeout_seconds", 10.0),
    )


def _templated_traffic(ctx: ProviderContext) -> TemplatedTrafficClient:
    return TemplatedTrafficClient(
        ctx.option("source", "traffic_source", "file"),
        ctx.option("summary_template", "traffic_summary_template", ""),
        report_path=ctx.path_option("report_path", "traffic_report_path"),
        api_url=ctx.option("api_url", "traffic_api_url", ""),
        api_method=ctx.option("api_method", "traffic_api_method", "GET"),
        api_headers=ctx.option("api_headers", "traffic_api_headers", {}),
        api_timeout_seconds=ctx.option("api_timeout_seconds", "traffic_api_timeout_seconds", 10.0),
    )


def _gemini(ctx: ProviderContext) -> GeminiClient:
    # extras["model"] is the per-run override (AgentInput.model) — a property of
    # this run, so it outranks both the option and the config field.
    return GeminiClient(
        api_key=ctx.option("api_key", "gemini_api_key", ""),
        default_model=ctx.extras.get("model") or ctx.option("model", "llm_model", ""),
        timeout_seconds=float(ctx.option("timeout_seconds", NO_ALIAS, 120.0)),
    )


def _llm_opportunity_source(ctx: ProviderContext) -> LLMOpportunitySource:
    return LLMOpportunitySource(
        ctx.extras["name"], ctx.extras["llm"], ctx.config,
        prompt_template=ctx.option("prompt_template", NO_ALIAS, ""),
        max_opportunities=ctx.option("max_opportunities", NO_ALIAS, 5),
        grounded=ctx.option("grounded", NO_ALIAS, True),
    )


# kind -> provider name -> (ProviderContext) -> a client satisfying that kind's
# Protocol. The keys here are the contract with providers.py's CATALOG; see the
# module docstring. Output sinks have the same shape in
# agent/managers/output_manager.py, where they belong (a sink is run-context, not
# a tool).
_REGISTRY = {
    "llm": {
        "mock": lambda ctx: MockLLMClient(),
        "gemini": _gemini,
        "custom": lambda ctx: ctx.custom("llm_custom_class"),
    },
    "gsc": {
        "mock": lambda ctx: MockGoogleSearchConsoleClient(),
        "google": lambda ctx: GoogleSearchConsoleClient(
            key_file=ctx.path_option("key_file", "gsc_key_file"),
            timeout_seconds=float(ctx.option("timeout_seconds", NO_ALIAS, 30.0)),
        ),
    },
    "traffic": {
        "none": lambda ctx: NullTrafficClient(),
        "mock": lambda ctx: MockTrafficClient(),
        "cloudflare": lambda ctx: CloudflareAnalyticsClient(
            api_token=ctx.option("api_token", "cloudflare_api_token", ""),
            zone_id=ctx.option("zone_id", "cloudflare_zone_id", ""),
            timeout=float(ctx.option("timeout_seconds", NO_ALIAS, 15.0)),
        ),
        "templated": _templated_traffic,
        "custom": lambda ctx: ctx.custom("traffic_custom_class"),
    },
    "analytics": {
        "mock": lambda ctx: MockAppAnalyticsClient(),
        "templated": _templated_analytics,
        "custom": lambda ctx: ctx.custom("analytics_custom_class"),
    },
    "discovery": {
        "mock": lambda ctx: MockOpportunitySource(
            ctx.extras["name"], fail=ctx.option("fail", NO_ALIAS, False),
        ),
        "llm": _llm_opportunity_source,
        # The class path is on the entry, not on a config field — see
        # ProviderContext.custom and build_discovery_sources below.
        "custom": lambda ctx: ctx.custom("class"),
    },
}


class ToolsManager:
    """Builds the run's Tools bundle from an AgentConfig. Only used when
    agent/managers/run_manager.py's AgentRunner isn't given an explicit
    tools=Tools(...) override."""

    def __init__(self, config) -> None:
        self.config = config

    def _build(self, kind: str, provider: str, options: dict = None, *,
               plugin_options: dict = None, where: str = "", **extras):
        """Look a provider up and build it. The single place an unknown provider
        name is rejected — fail-fast rather than falling through to a default,
        because silently treating llm_provider="openai" as Gemini surfaces hours
        later as a confusing API error, and `list-tools` advertises this exact set
        of names, so accepting others would make it a lie."""
        try:
            factory = _REGISTRY[kind][provider]
        except KeyError:
            available = ", ".join(sorted(repr(k) for k in _REGISTRY[kind]))
            raise ValueError(
                f"Unknown {kind} provider {provider!r}{where}; must be one of {available}"
            ) from None
        return factory(ProviderContext(self.config, options or {}, extras, plugin_options))

    def _options(self, kind_field: str) -> dict:
        return getattr(self.config, kind_field, None) or {}

    def build_llm(self, model_override: str = None):
        """The single place a concrete LLM provider is chosen — everything
        downstream (agent/graph/) only ever sees the LLMClient Protocol."""
        return self._build(
            "llm", self.config.llm_provider, self._options("llm_options"),
            model=model_override,
        )

    def build_gsc(self):
        return self._build("gsc", self.config.gsc_provider, self._options("gsc_options"))

    def build_traffic(self):
        """"cloudflare" is a real, reusable vendor integration (not a
        tenant-specific hack), kept as one option among several — a tenant not on
        Cloudflare uses "templated"/"custom"/"none" instead."""
        return self._build(
            "traffic", self.config.traffic_provider, self._options("traffic_options"),
        )

    def build_analytics(self):
        """No tenant gets a bespoke Python client baked into this codebase —
        "templated" covers any tenant's own JSON shape declaratively; "custom"
        remains for cases that genuinely need code."""
        return self._build(
            "analytics", self.config.analytics_provider, self._options("analytics_options"),
        )

    def build_discovery_sources(self, llm) -> dict:
        """AgentConfig.discovery_sources is a list (unlike the other provider
        fields) since a tenant can configure any number of named sources. `llm` is
        shared with build_llm()'s result rather than built again, so an "llm"
        source doesn't double up on client construction/config.

        A source's settings may sit directly on its entry (as they always have) or
        under an `options` key (the convention every other "custom" provider
        uses); `options` wins where both appear.
        """
        sources = {}
        for entry in self.config.discovery_sources:
            name = entry["name"]
            plugin_options = entry.get("options") or {}
            sources[name] = self._build(
                "discovery", entry.get("provider", "mock"),
                {**entry, **plugin_options},
                plugin_options=plugin_options,
                where=f" for discovery source {name!r}",
                name=name, llm=llm, class_path=entry.get("class", ""),
                label=f"discovery_sources[{name!r}].class",
            )
        return sources

    def build_all(self, model_override: str = None) -> Tools:
        """The out-of-the-box Tools: all-mock except the LLM, GSC, traffic, and
        analytics clients, which follow config.llm_provider / config.gsc_provider
        / config.traffic_provider / config.analytics_provider."""
        llm = self.build_llm(model_override)
        return Tools(
            gsc=self.build_gsc(),
            analytics=self.build_analytics(),
            traffic=self.build_traffic(),
            llm=llm,
            discovery_sources=self.build_discovery_sources(llm),
        )
