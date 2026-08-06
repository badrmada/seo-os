"""The catalog of every pluggable provider kind and the provider names each one
accepts — one declarative place, so the CLI's `list-tools` reports what the system
actually supports instead of a hand-maintained list that drifts.

This file stays free of client imports on purpose: `list-tools` answers "what
could I configure?" without constructing anything, so it must not drag in
google-genai, httpx, or a tenant's plugins to do it. The factories therefore live
next to the things they build — `agent/managers/tools_manager.py`'s `_REGISTRY`
for tools, `agent/managers/output_manager.py`'s `_SINK_FACTORIES` for sinks — and
`src/tests/test_providers.py` asserts, per kind, that the names here and the
names there are the *same set*. Neither file can grow a provider the other
doesn't have.
"""

from dataclasses import dataclass

# The one exception to "this file imports nothing": a pure-`typing` schema module,
# not a client. See agent/schemas/signal.py for why the reserved names live there
# rather than here.
from ..schemas.signal import BUILTIN_SIGNAL_NAMES

_SIGNAL_SOURCES = "signal_sources"


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
        """Which provider(s) this tenant's config actually selects.

        Both branches account for `signal_sources` being able to spell the same
        choice two ways (see AgentConfig.signal_sources): an entry named
        "search_performance" selects that kind, so it has to be read by that kind and skipped by
        the signal kind. Getting this wrong wouldn't break a run — it would just
        make `list-tools` quietly describe the wrong thing as in use, which is the
        one job this file has.
        """
        value = getattr(config, self.config_field, None)
        if not self.is_list:
            entry = _reserved_entry(config, self.kind)
            if entry is not None:
                return [entry.get("provider", value)]
            return [value] if value else []
        entries = value or []
        if self.config_field == _SIGNAL_SOURCES:
            entries = [e for e in entries if e.get("name") not in BUILTIN_SIGNAL_NAMES]
        return [entry.get("provider", "mock") for entry in entries]


def _reserved_entry(config, kind: str):
    """The signal_sources entry claiming one of the built-in kinds, if any."""
    if kind not in BUILTIN_SIGNAL_NAMES:
        return None
    for entry in getattr(config, _SIGNAL_SOURCES, None) or []:
        if entry.get("name") == kind:
            return entry
    return None


CUSTOM = 'a class of your own ("module.path:ClassName")'

CATALOG = (
    ProviderKind(
        kind="llm",
        interface="tools/llm/base.py::LLMClient",
        config_field="llm_provider",
        providers={
            "gemini": "Google Gemini, with optional Google Search grounding",
            "mock": "offline, deterministic — no API calls",
            "custom": CUSTOM,
        },
    ),
    ProviderKind(
        kind="search",
        interface="tools/base.py::SearchClient",
        config_field="search_provider",
        providers={
            "duckduckgo": "real web search, no API key — the default grounding",
            "none": "no search tool; falls back to the LLM's own grounding",
            "mock": "offline, deterministic — no network calls",
            "custom": CUSTOM,
        },
    ),
    ProviderKind(
        kind="search_performance",
        interface="tools/base.py::SearchPerformanceClient",
        config_field="search_performance_provider",
        providers={
            "none": "no rank data; the topic comes from your seed keyword, analytics or discovery",
            "google": "Google Search Console API",
            "templated": "your own rank data (file or API), mapped by a Jinja2 template",
            "mock": "offline, deterministic — no API calls",
            "custom": CUSTOM,
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
        kind="signal",
        interface="tools/base.py::SignalSource",
        config_field="signal_sources",
        is_list=True,
        providers={
            "mock": "offline, deterministic fixtures",
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
            "llm": "the LLM itself surfaces opportunities, web-search-grounded by default",
            "mcp": "a tool on an MCP server, over stdio or streamable HTTP",
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
