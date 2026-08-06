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
from tools.clients.opportunity_mcp import MCPOpportunitySource
from tools.clients.signal_templated import TemplatedSignalSource
from tools.clients.traffic_templated import TemplatedTrafficClient
from tools.llm.gemini_client import GeminiClient
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.gsc_mock import MockGoogleSearchConsoleClient
from tools.mocks.opportunity_mock import MockOpportunitySource
from tools.mocks.search_mock import MockSearchClient
from tools.mocks.search_null import NullSearchClient
from tools.mocks.signal_mock import MockSignalSource
from tools.mocks.traffic_mock import MockTrafficClient
from tools.mocks.traffic_null import NullTrafficClient
from tools.search.duckduckgo import DuckDuckGoSearchClient

from ..config.paths import resolve_path
from ..graph.tools import Tools
from .plugin_loader import load_custom
from .providers import BUILTIN_SIGNAL_NAMES

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

    def option(self, key: str, default=None):
        """One of the selected provider's own settings, or the default.

        There is no fallback to a top-level config field: a provider's settings
        live in that provider's `options` and nowhere else, so there is exactly
        one place to look when a value isn't what you expected. Config written
        the old way is rejected at load time with the new location named — see
        agent/config/loader.py's MOVED_FIELDS.
        """
        return self.options.get(key, default)

    def path_option(self, key: str, default: str = ""):
        """An option naming a file, resolved against the tenant's own folder
        rather than the process's working directory (agent/config/paths.py)."""
        return resolve_path(self.config, self.option(key, default))

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
        ctx.option("source", "file"),
        ctx.option("summary_template", ""),
        ctx.option("highlights_template", ""),
        report_path=ctx.path_option("report_path"),
        api_url=ctx.option("api_url", ""),
        api_method=ctx.option("api_method", "GET"),
        api_headers=ctx.option("api_headers", {}),
        api_timeout_seconds=ctx.option("api_timeout_seconds", 10.0),
    )


def _templated_traffic(ctx: ProviderContext) -> TemplatedTrafficClient:
    return TemplatedTrafficClient(
        ctx.option("source", "file"),
        ctx.option("summary_template", ""),
        report_path=ctx.path_option("report_path"),
        api_url=ctx.option("api_url", ""),
        api_method=ctx.option("api_method", "GET"),
        api_headers=ctx.option("api_headers", {}),
        api_timeout_seconds=ctx.option("api_timeout_seconds", 10.0),
    )


def _gemini(ctx: ProviderContext) -> GeminiClient:
    # extras["model"] is the per-run override (AgentInput.model) — a property of
    # this run, so it outranks both the option and the config field.
    return GeminiClient(
        api_key=ctx.option("api_key", ""),
        default_model=ctx.extras.get("model") or ctx.option("model", "gemini-2.0-flash"),
        timeout_seconds=float(ctx.option("timeout_seconds", 120.0)),
    )


def _duckduckgo(ctx: ProviderContext) -> DuckDuckGoSearchClient:
    return DuckDuckGoSearchClient(
        backend=ctx.option("backend", "duckduckgo"),
        fallback_backend=ctx.option("fallback_backend", "auto"),
        region=ctx.option("region", "wt-wt"),
        safesearch=ctx.option("safesearch", "moderate"),
        timelimit=ctx.option("timelimit", ""),
        timeout_seconds=float(ctx.option("timeout_seconds", 10.0)),
    )


def _mcp_opportunity_source(ctx: ProviderContext) -> MCPOpportunitySource:
    # `cwd` is a path option: a stdio server launched from the tenant's own folder
    # is the normal case, and resolving it against the process's working directory
    # would make the same config behave differently depending on where the CLI was
    # invoked from.
    return MCPOpportunitySource(
        ctx.extras["name"], ctx.config,
        transport=ctx.option("transport", "stdio"),
        command=ctx.option("command", ""),
        args=ctx.option("args", ()),
        env=ctx.option("env", {}),
        cwd=ctx.path_option("cwd") if ctx.option("cwd") else "",
        url=ctx.option("url", ""),
        headers=ctx.option("headers", {}),
        tool_name=ctx.option("tool_name", ""),
        arguments=ctx.option("arguments", {}),
        items_template=ctx.option("items_template", ""),
        max_opportunities=ctx.option("max_opportunities", 5),
        timeout_seconds=float(ctx.option("timeout_seconds", 60.0)),
    )


def _templated_signal(ctx: ProviderContext) -> TemplatedSignalSource:
    return TemplatedSignalSource(
        ctx.extras["name"],
        ctx.option("source", "file"),
        ctx.option("summary_template", ""),
        facts_template=ctx.option("facts_template", ""),
        items_template=ctx.option("items_template", ""),
        report_path=ctx.path_option("report_path"),
        api_url=ctx.option("api_url", ""),
        api_method=ctx.option("api_method", "GET"),
        api_headers=ctx.option("api_headers", {}),
        api_timeout_seconds=ctx.option("api_timeout_seconds", 10.0),
    )


def _llm_opportunity_source(ctx: ProviderContext) -> LLMOpportunitySource:
    return LLMOpportunitySource(
        ctx.extras["name"], ctx.extras["llm"], ctx.config,
        search=ctx.extras.get("search"),
        prompt_template=ctx.option("prompt_template", ""),
        query_prompt_template=ctx.option("query_prompt_template", ""),
        max_opportunities=ctx.option("max_opportunities", 5),
        grounded=ctx.option("grounded", True),
        search_queries=ctx.option("search_queries", ()),
        max_search_queries=ctx.option("max_search_queries", 3),
        results_per_query=ctx.option("results_per_query", 5),
        max_search_results=ctx.option("max_search_results", 12),
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
    "search": {
        "duckduckgo": _duckduckgo,
        "none": lambda ctx: NullSearchClient(),
        "mock": lambda ctx: MockSearchClient(),
        "custom": lambda ctx: ctx.custom("search_custom_class"),
    },
    "gsc": {
        "mock": lambda ctx: MockGoogleSearchConsoleClient(),
        "google": lambda ctx: GoogleSearchConsoleClient(
            key_file=ctx.path_option("key_file", "service_account.json"),
            timeout_seconds=float(ctx.option("timeout_seconds", 30.0)),
        ),
    },
    "traffic": {
        "none": lambda ctx: NullTrafficClient(),
        "mock": lambda ctx: MockTrafficClient(),
        "cloudflare": lambda ctx: CloudflareAnalyticsClient(
            api_token=ctx.option("api_token", ""),
            zone_id=ctx.option("zone_id", ""),
            timeout=float(ctx.option("timeout_seconds", 15.0)),
        ),
        "templated": _templated_traffic,
        "custom": lambda ctx: ctx.custom("traffic_custom_class"),
    },
    "analytics": {
        "mock": lambda ctx: MockAppAnalyticsClient(),
        "templated": _templated_analytics,
        "custom": lambda ctx: ctx.custom("analytics_custom_class"),
    },
    "signal": {
        "mock": lambda ctx: MockSignalSource(
            ctx.extras["name"], fail=ctx.option("fail", False),
        ),
        "templated": _templated_signal,
        # The class path is on the entry, not on a config field — same as a
        # discovery source; see build_signal_sources below.
        "custom": lambda ctx: ctx.custom("class"),
    },
    "discovery": {
        "mock": lambda ctx: MockOpportunitySource(
            ctx.extras["name"], fail=ctx.option("fail", False),
        ),
        "llm": _llm_opportunity_source,
        "mcp": _mcp_opportunity_source,
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

    def build_search(self):
        """The system's own grounding, independent of the LLM provider — see
        tools/base.py's SearchClient for the resolution order it takes part in."""
        return self._build(
            "search", self.config.search_provider, self._options("search_options"),
        )

    def build_gsc(self):
        return self._build_signal_slot("gsc")

    def build_traffic(self):
        """"cloudflare" is a real, reusable vendor integration (not a
        tenant-specific hack), kept as one option among several — a tenant not on
        Cloudflare uses "templated"/"custom"/"none" instead."""
        return self._build_signal_slot("traffic")

    def build_analytics(self):
        """No tenant gets a bespoke Python client baked into this codebase —
        "templated" covers any tenant's own JSON shape declaratively; "custom"
        remains for cases that genuinely need code."""
        return self._build_signal_slot("analytics")

    def _signal_entries(self) -> dict:
        """config.signal_sources keyed by name, validated once — the single reader
        of that field, shared by the three built-in slots and build_signal_sources.

        Both rules below fail loudly rather than doing something reasonable-looking:
        an entry with no name has no way to reach the prompt (it is keyed by name)
        and a duplicate would silently shadow the earlier one, which surfaces much
        later as "my trends signal isn't running" with nothing pointing at why.
        """
        entries: dict = {}
        for position, entry in enumerate(self.config.signal_sources):
            name = entry.get("name", "")
            if not name:
                raise ValueError(f'signal_sources[{position}] has no "name"')
            if name in entries:
                raise ValueError(
                    f"duplicate signal source name {name!r} in signal_sources; "
                    "each signal reaches the prompt keyed by its name, so names must be unique"
                )
            entries[name] = entry
        return entries

    def _build_signal_slot(self, kind: str):
        """One of the three built-in signal slots — selected either by its own
        `<kind>_provider`/`<kind>_options` fields or by a `signal_sources` entry
        using that reserved name, which wins where both appear.

        Two spellings for one thing, on purpose: the fields are what every existing
        tenant, example and doc already writes and they keep meaning exactly what
        they did, while the list is what makes "here is every input this agent
        reads" a single readable block. Neither is a migration of the other — see
        AgentConfig.signal_sources.
        """
        entry = self._signal_entries().get(kind)
        if entry is None:
            return self._build(
                kind, getattr(self.config, f"{kind}_provider"), self._options(f"{kind}_options"),
            )
        options = entry.get("options") or {}
        return self._build(
            kind, entry.get("provider", getattr(self.config, f"{kind}_provider")), options,
            plugin_options=options,
            where=f" for signal source {kind!r}",
            name=kind, class_path=entry.get("class", ""),
            label=f"signal_sources[{kind!r}].class",
        )

    def build_signal_sources(self) -> dict:
        """The signals that aren't one of the three built-in slots, keyed by name,
        for Tools.signals — every input a tenant added that this repo has never
        heard of.

        Unlike a discovery source's entry, a signal's settings live only under
        `options`. There is no back-compatibility to keep here (the field is new),
        and one place to look beats two.
        """
        sources = {}
        for name, entry in self._signal_entries().items():
            if name in BUILTIN_SIGNAL_NAMES:
                continue  # a built-in slot; see _build_signal_slot
            options = entry.get("options") or {}
            sources[name] = self._build(
                "signal", entry.get("provider", "mock"), options,
                plugin_options=options,
                where=f" for signal source {name!r}",
                name=name, class_path=entry.get("class", ""),
                label=f"signal_sources[{name!r}].class",
            )
        return sources

    def build_discovery_sources(self, llm, search=None) -> dict:
        """AgentConfig.discovery_sources is a list (unlike the other provider
        fields) since a tenant can configure any number of named sources. `llm`
        and `search` are shared with build_llm()/build_search()'s results rather
        than built again, so an "llm" source doesn't double up on client
        construction/config. `search` left None builds the configured one, so a
        caller that only has an LLM to hand still gets a grounded source.

        A source's settings may sit directly on its entry (as they always have) or
        under an `options` key (the convention every other "custom" provider
        uses); `options` wins where both appear.
        """
        search = self.build_search() if search is None else search
        sources = {}
        for entry in self.config.discovery_sources:
            name = entry["name"]
            plugin_options = entry.get("options") or {}
            sources[name] = self._build(
                "discovery", entry.get("provider", "mock"),
                {**entry, **plugin_options},
                plugin_options=plugin_options,
                where=f" for discovery source {name!r}",
                name=name, llm=llm, search=search, class_path=entry.get("class", ""),
                label=f"discovery_sources[{name!r}].class",
            )
        return sources

    def build_all(self, model_override: str = None) -> Tools:
        """The out-of-the-box Tools: all-mock except the LLM, search, GSC, traffic,
        and analytics clients, which follow config.llm_provider /
        config.search_provider / config.gsc_provider / config.traffic_provider /
        config.analytics_provider."""
        llm = self.build_llm(model_override)
        search = self.build_search()
        return Tools(
            gsc=self.build_gsc(),
            analytics=self.build_analytics(),
            traffic=self.build_traffic(),
            llm=llm,
            search=search,
            discovery_sources=self.build_discovery_sources(llm, search),
            signals=self.build_signal_sources(),
        )
