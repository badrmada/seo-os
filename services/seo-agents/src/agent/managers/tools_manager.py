import importlib

from tools.clients.analytics_templated import TemplatedAnalyticsClient
from tools.clients.cloudflare import CloudflareAnalyticsClient
from tools.clients.google_search_console import GoogleSearchConsoleClient
from tools.clients.traffic_templated import TemplatedTrafficClient
from tools.clients.opportunity_llm import LLMOpportunitySource
from tools.llm.gemini_client import GeminiClient
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.gsc_mock import MockGoogleSearchConsoleClient
from tools.mocks.opportunity_mock import MockOpportunitySource
from tools.mocks.traffic_mock import MockTrafficClient
from tools.mocks.traffic_null import NullTrafficClient

from ..graph.tools import Tools


class ToolsManager:
    """Builds the run's Tools bundle from an AgentConfig — the single place a
    concrete provider is chosen per client kind, so adding a provider means
    changing this class and agent/config/agent_config.py only. Only used when
    agent/managers/run_manager.py's AgentRunner isn't given an explicit
    tools=Tools(...) override."""

    def __init__(self, config) -> None:
        self.config = config

    def _load_custom(self, class_path: str, field_name: str):
        """Shared by every "custom" provider (analytics, traffic, discovery sources):
        a tenant registers their own class instead of forking this codebase.
        class_path is "module.path:ClassName"; the class is instantiated with the
        tenant's AgentConfig so it can read whatever fields it needs."""
        if not class_path:
            raise ValueError(f'provider="custom" requires {field_name} to be set')
        module_path, _, class_name = class_path.partition(":")
        if not class_name:
            raise ValueError(f'{field_name} must be "module.path:ClassName", got {class_path!r}')
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls(self.config)

    def build_llm(self, model_override: str = None):
        """The single place a concrete LLM provider is chosen — everything downstream
        (agent/graph/) only ever sees the LLMClient Protocol."""
        config = self.config
        if config.llm_provider == "mock":
            return MockLLMClient()
        return GeminiClient(api_key=config.gemini_api_key, default_model=model_override or config.llm_model)

    def build_gsc(self):
        """The single place a concrete GSCClient provider is chosen — mirrors
        build_traffic()."""
        config = self.config
        if config.gsc_provider == "mock":
            return MockGoogleSearchConsoleClient()
        return GoogleSearchConsoleClient(key_file=config.gsc_key_file)

    def build_traffic(self):
        """The single place a concrete SiteTrafficClient provider is chosen — mirrors
        build_analytics(). "cloudflare" is a real, reusable vendor integration (not a
        tenant-specific hack), kept as one option among several — a tenant not on
        Cloudflare uses "templated"/"custom"/"none" instead."""
        config = self.config
        if config.traffic_provider == "none":
            return NullTrafficClient()
        if config.traffic_provider == "mock":
            return MockTrafficClient()
        if config.traffic_provider == "cloudflare":
            return CloudflareAnalyticsClient(
                api_token=config.cloudflare_api_token, zone_id=config.cloudflare_zone_id,
            )
        if config.traffic_provider == "custom":
            return self._load_custom(config.traffic_custom_class, "traffic_custom_class")
        if config.traffic_provider == "templated":
            return TemplatedTrafficClient(
                config.traffic_source,
                config.traffic_summary_template,
                report_path=config.traffic_report_path,
                api_url=config.traffic_api_url,
                api_method=config.traffic_api_method,
                api_headers=config.traffic_api_headers,
                api_timeout_seconds=config.traffic_api_timeout_seconds,
            )
        raise ValueError(
            f'Unknown traffic_provider {config.traffic_provider!r}; must be '
            '"none", "mock", "cloudflare", "templated", or "custom"'
        )

    def build_analytics(self):
        """The single place a concrete AppAnalyticsClient provider is chosen — mirrors
        build_traffic(). No tenant (Echooers included) gets a bespoke Python client
        anymore — "templated" (agent/config's analytics_summary_template/
        analytics_highlights_template) covers any tenant's own JSON shape declaratively;
        "custom" remains for cases that genuinely need code."""
        config = self.config
        if config.analytics_provider == "mock":
            return MockAppAnalyticsClient()
        if config.analytics_provider == "custom":
            return self._load_custom(config.analytics_custom_class, "analytics_custom_class")
        if config.analytics_provider == "templated":
            return TemplatedAnalyticsClient(
                config.analytics_source,
                config.analytics_summary_template,
                config.analytics_highlights_template,
                report_path=config.analytics_report_path,
                api_url=config.analytics_api_url,
                api_method=config.analytics_api_method,
                api_headers=config.analytics_api_headers,
                api_timeout_seconds=config.analytics_api_timeout_seconds,
            )
        raise ValueError(
            f'Unknown analytics_provider {config.analytics_provider!r}; must be '
            '"mock", "templated", or "custom"'
        )

    def build_discovery_sources(self, llm) -> dict:
        """The single place AgentConfig.discovery_sources (a list, unlike the other
        provider fields, since a tenant can configure any number of named sources)
        gets turned into concrete OpportunitySource instances, read by
        agent/graph/stages/discover.py's DiscoverStage. llm is shared with
        build_llm()'s result rather than built again, so an "llm" source doesn't
        double up on client construction/config."""
        sources = {}
        for entry in self.config.discovery_sources:
            name = entry["name"]
            provider = entry.get("provider", "mock")
            if provider == "mock":
                sources[name] = MockOpportunitySource(name, fail=entry.get("fail", False))
            elif provider == "llm":
                sources[name] = LLMOpportunitySource(
                    name, llm, self.config,
                    prompt_template=entry.get("prompt_template", ""),
                    max_opportunities=entry.get("max_opportunities", 5),
                    grounded=entry.get("grounded", True),
                )
            elif provider == "custom":
                sources[name] = self._load_custom(entry.get("class", ""), f'discovery_sources[{name!r}].class')
            else:
                raise ValueError(
                    f'Unknown discovery source provider {provider!r} for {name!r}; must be '
                    '"mock", "llm", or "custom"'
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
