from typing import Protocol


class OpportunitySource(Protocol):
    """One named entry in AgentConfig.discovery_sources (agent/config/agent_config.py)
    — free-form input, one normalized output shape, mirroring AppAnalyticsClient/
    SiteTrafficClient below. Called by agent/graph/stages/discover.py's
    DiscoverStage, once per configured entry, whenever config.discovery_sources is
    non-empty; a zero-config tenant never calls any of these.

    Implementations:
      - tools/mocks/opportunity_mock.py's MockOpportunitySource — deterministic
        fixtures, for offline runs with no real source configured.
      - tools/clients/opportunity_llm.py's LLMOpportunitySource — provider="llm":
        no external API at all; the LLM itself is the source, prompted to surface
        topics/links worth pursuing and return them in the Opportunity shape. This
        is what Echooers uses in place of a bespoke Reddit/trends integration.
      - agent_config.py's discovery_sources provider="custom" — a tenant registers
        their own class (reusing ToolsManager._load_custom), for a source too
        bespoke for "mock"/"llm" — including one whose discover() itself runs a
        full LLM tool-loop (search, browse, summarize) rather than a single
        prompt: this Protocol doesn't care whether what's behind it is a
        deterministic client, one LLM call, or a nested agent.
    """

    def discover(self, context: dict) -> list["Opportunity"]:
        """context carries whatever the run has so far (seed_keyword, context_text,
        already-known opportunities from earlier sources) — a source may ignore it
        entirely or use it to steer its own query. Opportunity is
        agent/schemas/opportunity.py's TypedDict."""
        ...


class OutputSink(Protocol):
    """Where a finished run's result goes. Unlike every other Protocol in this file,
    a sink is not a tool the agent calls to do its work — it runs once, after the
    graph has finished, at the AgentRunner/CLI boundary (see
    agent/managers/output_manager.py's OutputManager, called from src/main.py). So
    no stage ever sees a sink, and adding one changes nothing about the pipeline or
    the result shape documented in docs/output-schema.md.

    Several sinks can be configured; they run in the order listed. A sink that
    raises is reported and skipped, never fatal — by the time any sink is called
    the result is fully computed, and losing a webhook delivery is no reason to
    throw away a finished run.

    Implementations:
      - tools/sinks/json_sink.py's JsonOutputSink — provider="json": the default,
        writing the same indented JSON to stdout that this agent has always
        printed, or to a file.
      - tools/sinks/webhook_sink.py's WebhookOutputSink — provider="webhook":
        POSTs the result to an HTTP endpoint.
      - AgentConfig's output_sinks provider="custom" — a tenant-registered class,
        loaded exactly like every other "custom" provider (see
        agent/managers/plugin_loader.py's load_custom).
    """

    def emit(self, output: dict) -> None:
        """`output` is the complete run result — the same dict AgentRunner.run()
        returns and src/main.py prints (run_id, phase, input, output, discovery,
        usage, error). Sinks receive the whole thing, not just the draft, since a
        consumer usually needs to know *which* run produced it and whether it
        succeeded."""
        ...


class GSCClient(Protocol):
    """Search Console-style data: which queries/pages are close to ranking (inward signal)."""

    def search_analytics(
        self, site_url: str, days: int = 28, row_limit: int = 500
    ) -> list[dict]: ...


class AppAnalyticsClient(Protocol):
    """Internal platform/product analytics — deliberately minimal and free-form,
    since what a tenant actually tracks (ideas/upvotes, orders/revenue,
    articles/reads, ...) varies completely by product. The system (agent/graph/,
    agent/prompts/) never assumes a specific vocabulary; only the concrete client
    below — which knows its own domain — turns its data into this shape.

    Implementations:
      - tools/mocks/analytics_mock.py's MockAppAnalyticsClient — product-neutral canned
        data, for offline runs with no real tenant configured.
      - tools/clients/analytics_templated.py's TemplatedAnalyticsClient — AgentConfig's
        analytics_provider="templated": a tenant's own JSON (file or API), mapped
        into this shape via two Jinja2 templates, no code required. This is what
        Echooers itself uses (see src/tenant.json) — no tenant gets a
        bespoke Python client baked into this codebase.
      - AgentConfig's analytics_provider="custom" — a tenant registers their own
        class (agent/managers/tools_manager.py's ToolsManager._load_custom) for the
        rare case that genuinely needs code (e.g. calling another API, real
        computation), without forking this repo.
    """

    def report(self, limit: int = 5) -> dict:
        """Returns:
        {
            "summary": str,            # free text about recent activity,
                                        # tenant-authored; "" if there's nothing
                                        # to report. Dropped into the prompt
                                        # as-is — the system never parses it.
            "highlights": list[dict],  # [{"label": str, "url": str}, ...],
                                        # up to `limit` items, most-relevant
                                        # first. The smallest shape that's true
                                        # for any kind of content (a post, a
                                        # product, an article). May be empty.
        }
        """
        ...


class SiteTrafficClient(Protocol):
    """Site traffic (inward signal) — how much traffic there is and where it's from.
    Free-form like AppAnalyticsClient and for the same reason: not every traffic
    tool exposes the same metrics Cloudflare does (bot/human split, referral-source
    breakdown, ...), so the system never assumes specific numeric fields — only the
    concrete client turns its data into one short text summary.

    Implementations:
      - tools/mocks/traffic_mock.py's MockTrafficClient — product-neutral canned
        text, for offline runs with no real tenant configured.
      - tools/clients/cloudflare.py's CloudflareAnalyticsClient — a real, reusable vendor
        integration (like GoogleSearchConsoleClient or GeminiClient): calls
        Cloudflare's GraphQL Analytics API and does real computation (bot-score
        bucketing, referrer-host classification) that's genuinely code, not a
        declarative reshape — which is exactly why it stays a Python client rather
        than becoming a "templated" case.
      - tools/clients/traffic_templated.py's TemplatedTrafficClient — traffic_provider=
        "templated": a tenant's own JSON (file or API, from any traffic tool, or
        none at all — Cloudflare's just one option among several), mapped via one
        Jinja2 template, no code required.
      - tools/mocks/traffic_null.py's NullTrafficClient — traffic_provider="none": no
        traffic tool at all; always returns an empty summary, which the prompt
        template's {% if traffic_summary %} guard skips over cleanly.
      - AgentConfig's traffic_provider="custom" — a tenant registers their own class
        for the rare case that genuinely needs code, without forking this repo.
    """

    def traffic_summary(self, days: int = 28) -> dict:
        """Returns {"summary": str} — free text about site traffic, tenant-authored;
        "" if there's nothing to report. Dropped into the prompt as-is."""
        ...
