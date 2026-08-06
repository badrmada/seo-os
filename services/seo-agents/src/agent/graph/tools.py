from dataclasses import dataclass, field

from tools.base import (
    AppAnalyticsClient,
    OpportunitySource,
    SearchClient,
    SearchPerformanceClient,
    SignalSource,
    SiteTrafficClient,
)
from tools.llm.base import LLMClient
from tools.mocks.search_null import NullSearchClient


@dataclass
class Tools:
    """The pluggable clients the agent calls out to. agent/managers/run_manager.py's
    AgentRunner defaults this to mocks (via agent/managers/tools_manager.py's
    ToolsManager); pass your own Tools(...) to swap in real clients, per-field or all
    at once — every stage only ever depends on the Protocols in tools/base.py."""

    # The three signal inputs with hand-shaped Protocols of their own, kept as
    # named fields because their methods (and analyze.py's use of them) predate the
    # generic SignalSource and differ from it — see tools/base.py's SignalSource.
    # Which provider fills each one is selectable either by <kind>_provider or by a
    # reserved-name entry in config.signal_sources; either way it arrives here.
    search_performance: SearchPerformanceClient
    analytics: AppAnalyticsClient
    traffic: SiteTrafficClient
    llm: LLMClient   # model-agnostic: whichever concrete client ToolsManager built
    # Keyed by AgentConfig.discovery_sources' "name"; read by
    # agent/graph/stages/discover.py's DiscoverStage, only added to the pipeline
    # when this is non-empty (see agent/graph/pipeline.py). Defaults to empty so
    # existing Tools(...) construction sites are unaffected.
    discovery_sources: dict[str, OpportunitySource] = field(default_factory=dict)
    # Every *other* signal input, keyed by AgentConfig.signal_sources' "name" —
    # anything that isn't one of the three named fields above. Collected
    # concurrently by agent/graph/stages/analyze.py and passed to the prompt keyed
    # by name, so no stage and no system template ever names a specific signal.
    # Defaults to empty, so existing Tools(...) construction sites are unaffected.
    signals: dict[str, SignalSource] = field(default_factory=dict)
    # Real web search, the system's own grounding (tools/base.py's SearchClient).
    # Defaults to the null client rather than the real one so a hand-constructed
    # Tools(...) — every test double, every caller injecting its own clients —
    # stays offline unless it says otherwise; ToolsManager passes the configured
    # one (DuckDuckGo by default). The same instance reaches an "llm" discovery
    # source at construction (see ToolsManager.build_discovery_sources).
    search: SearchClient = field(default_factory=NullSearchClient)
