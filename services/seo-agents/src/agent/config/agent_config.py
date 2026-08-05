from dataclasses import dataclass, field

from .. import prompts
from ..schemas.channel import Channel


@dataclass
class AgentConfig:
    """Everything about how the agent behaves, in one place. Every field below is a
    generic, product-neutral system default — a tenant with zero config still gets a
    coherent, working agent. Real tenant copy (brand description, goal, provider
    choices, ...) lives in a per-tenant JSON file and overrides these via
    `AgentConfigLoader.load()`; nothing tenant-specific is hardcoded here."""

    # --- Where this config came from (agent/config/paths.py) ---
    # Set by AgentConfigLoader to the directory holding the tenant JSON, so every
    # relative path below (analytics_report_path, traffic_report_path,
    # gsc_key_file, an output sink's options.path) resolves against *this tenant's
    # own folder* rather than whatever directory the process happens to be running
    # in. That distinction doesn't matter for one person running one CLI command;
    # it matters completely once several tenants run in one server process. Empty
    # (the default, for a config built in code) keeps the old CWD-relative
    # behavior. A non-file source — a database row, an API request body — passes
    # its own base directory to AgentConfigLoader.load_dict().
    config_base_dir: str = ""

    # --- LLM ---
    llm_provider: str = "mock"  # "gemini" or "mock"
    llm_model: str = "gemini-2.0-flash"
    gemini_api_key: str = ""

    # --- Google Search Console (tools/clients/google_search_console.py GoogleSearchConsoleClient) ---
    gsc_provider: str = "mock"  # "google" (real API calls) or "mock" (offline, deterministic)
    gsc_key_file: str = "service_account.json"

    # --- Site traffic (tools/base.py's SiteTrafficClient Protocol) ---
    # "none" (no traffic tool) | "mock" (product-neutral canned text) | "cloudflare"
    # (tools/clients/cloudflare.py, a real vendor integration) | "templated" (tenant's own
    # JSON, mapped declaratively — see traffic_summary_template below) | "custom"
    # (a tenant-registered class, for cases too bespoke for a template).
    traffic_provider: str = "mock"
    cloudflare_api_token: str = ""  # used by traffic_provider="cloudflare"
    cloudflare_zone_id: str = ""  # used by traffic_provider="cloudflare"
    traffic_custom_class: str = ""  # "module.path:ClassName", used by traffic_provider="custom"

    # --- traffic_provider="templated" only (tools/clients/traffic_templated.py) ---
    traffic_source: str = "file"  # "file" (traffic_report_path) or "api" (below)
    traffic_report_path: str = ""
    traffic_api_url: str = ""
    traffic_api_method: str = "GET"
    traffic_api_headers: dict[str, str] = field(default_factory=dict)
    traffic_api_timeout_seconds: float = 10.0
    # Jinja2, rendered against {"data": <the tenant's raw JSON>, "days": ...} — same
    # mechanism as analytics_summary_template below, renders directly to text.
    traffic_summary_template: str = ""

    # --- App analytics (tools/base.py's AppAnalyticsClient Protocol) ---
    # "mock" (product-neutral canned data) | "templated" (tenant's own JSON, mapped
    # declaratively via the two templates below — see tools/clients/analytics_templated.py)
    # | "custom" (a tenant-registered Python class, for cases too bespoke for a
    # template — see analytics_custom_class below). No tenant gets a bespoke Python
    # client baked into this codebase — "templated" covers that instead.
    analytics_provider: str = "mock"
    analytics_report_path: str = "tools/report.json"  # "templated" provider + source="file"
    analytics_custom_class: str = ""  # "module.path:ClassName", used by the "custom" provider
    analytics_highlights_limit: int = 3  # how many highlights to hand the LLM per draft

    # --- "templated" analytics provider only (tools/clients/analytics_templated.py) ---
    analytics_source: str = "file"  # "file" (analytics_report_path) or "api" (below)
    analytics_api_url: str = ""
    analytics_api_method: str = "GET"
    analytics_api_headers: dict[str, str] = field(default_factory=dict)
    analytics_api_timeout_seconds: float = 10.0
    # Jinja2, rendered against {"data": <the tenant's raw JSON>, "limit": ...} — the
    # tenant's own field names, no fixed vocabulary imposed. analytics_summary_template
    # renders directly to the summary text; analytics_highlights_template must render
    # to a JSON array string (typically via Jinja2's `tojson` filter in a `{% for %}`
    # loop) of {"label": ..., "url": ...} objects — see agent/utils/analytics_schema.py's
    # infer_fields() for listing a raw JSON's available paths (e.g. for UI suggestions
    # while writing these two templates).
    analytics_summary_template: str = ""
    analytics_highlights_template: str = ""

    # --- Opportunity discovery (tools/base.py's OpportunitySource Protocol) ---
    # Each entry: {"name": str, "provider": "mock" | "llm" | "custom", ...}. Read by
    # agent/managers/tools_manager.py's ToolsManager.build_discovery_sources() into
    # Tools.discovery_sources, called by agent/graph/stages/discover.py's
    # DiscoverStage. A non-empty list also adds discover + choose_channel to the
    # pipeline (agent/graph/pipeline.py) so the agent decides `channel` itself from
    # what's discovered, instead of trusting input.channel.
    #   - "mock": deterministic fixture (tools/mocks/opportunity_mock.py); accepts
    #     an optional "fail": bool to simulate that source failing.
    #   - "llm": no external API — the LLM itself is prompted to surface
    #     opportunities (tools/clients/opportunity_llm.py); accepts optional
    #     "prompt_template" (Jinja2, defaults to a generic product-neutral prompt)
    #     and "max_opportunities" (default 5). This is what Echooers uses instead
    #     of a bespoke Reddit/trends integration.
    #   - "custom": a tenant-registered class ("class": "module.path:ClassName"),
    #     built the same way analytics_custom_class/traffic_custom_class are —
    #     including the case where the class's discover() itself runs a full LLM
    #     tool-loop rather than a single prompt.
    # Empty list (the default): no discovery sources at all, no behavior change for
    # a zero-config tenant.
    discovery_sources: list[dict] = field(default_factory=list)

    # --- Output sinks (tools/base.py's OutputSink Protocol) ---
    # Where a finished run's result goes. Each entry:
    #   {"name": str, "provider": "json" | "webhook" | "custom", "options": {...}}
    # Read by agent/managers/output_manager.py's OutputManager, called from
    # src/main.py *after* the run — never by a graph stage, and never affecting the
    # result shape (docs/output-schema.md). Sinks run in the order listed; one
    # failing is reported and skipped, never fatal.
    #   - "json": indented JSON to stdout (options.path empty, the default) or to a
    #     file (options.path, plus optional options.append for JSONL).
    #   - "webhook": POSTs the result to options.url. Auth belongs in
    #     options.headers — on the sink, not on this generic config.
    #   - "custom": a tenant-registered class ("class": "module.path:ClassName"),
    #     loaded exactly like every other custom provider.
    # The default below is the behavior this agent has always had: one indented
    # JSON document on stdout. Replacing the list replaces that entirely — list the
    # json sink alongside your own if you want both.
    output_sinks: list[dict] = field(
        default_factory=lambda: [{"name": "stdout", "provider": "json"}]
    )

    # --- Execution ---
    # An overall bound on one run, in seconds, on top of the per-call timeouts each
    # client already sets (the LLM's 120s, an api-sourced template's 10s, ...).
    # Those bound one HTTP request; this bounds the whole pipeline — a dozen
    # individually-timely calls, or a "custom" plugin with no timeout of its own,
    # can still hold a worker slot far longer than anyone intends. 0 (the default)
    # means unbounded, which stays right for a CLI someone is watching; a server
    # running many tenants' runs in one process should set it. Overrunning it ends
    # the run as phase="failed" with a clear error, exactly like any other failure
    # (see agent/managers/run_manager.py's arun()).
    run_timeout_seconds: float = 0

    # --- Verbose mode (agent/observability/) ---
    # 0 = silent (the default; a run prints nothing but its final JSON result).
    # 1 = lifecycle: each stage and each tool call, with timings and outcomes.
    # 2 = adds truncated payload previews (prompts, LLM response text, discovered
    #     topics, the channel decision and chosen keyword).
    # Sets the *default* for a tenant; src/main.py's -v/-vv flag always wins. All
    # output goes to stderr, never stdout — stdout carries the result JSON, so
    # `python src/main.py -v | jq` keeps working. Secrets are never printed (see
    # agent/observability/redaction.py) and payloads are truncated.
    verbose: int = 0
    verbose_format: str = "text"  # "text" (human-readable) or "json" (newline-delimited events)

    # --- Prompt content (folded into every draft prompt, see agent/graph/stages/) ---
    brand_description: str = (
        "A web platform that publishes content for its users and wants to grow its "
        "audience through search and genuine, organic discovery."
    )
    agent_goal: str = (
        "Increase qualified traffic to the site — attract new visitors via search and "
        "genuine discovery, not just serve people already there."
    )

    # --- Per-run defaults (used whenever input/params omits the field) ---
    default_channel: str = Channel.SITE_ARTICLE  # see agent/schemas/channel.py's Channel enum for the options
    default_max_words: int = 800
    default_article_tone: str = "informative"
    default_comment_tone: str = "genuine and conversational"

    # --- self_qa thresholds (agent/graph/stages/self_qa.py) ---
    # Heuristic, no-LLM checks run on every draft; qa_notes below is advisory, not a
    # hard gate. Thresholds below are deliberately generic; a tenant/channel with
    # different tolerances (denser writing, longer replies, more links) overrides them.
    qa_article_max_words_overage_pct: float = 0.25  # flag if body exceeds max_words by more than this fraction
    qa_article_max_avg_sentence_words: float = 30  # flag if avg sentence length (readability proxy) exceeds this
    qa_comment_max_words: int = 80  # flag if a reply is longer than this many words
    qa_comment_max_links: int = 1  # flag if a reply contains more than this many links
    # Phrases that, if a comment/reply contains them, mean it's referring to the
    # tenant's own product/brand — a generic default here on purpose, since specific
    # brand vocabulary ("no login", "anonymous", ...) belongs to the tenant, not the
    # system. Match is case-insensitive substring, no regex syntax required.
    qa_brand_mention_keywords: list[str] = field(
        default_factory=lambda: [
            "our product", "our platform", "our app", "our service",
            "the platform", "the app", "the product", "the service",
        ]
    )
    # Phrases that count as an affiliation disclosure when a brand mention (above) is
    # found — if a brand mention appears without one of these nearby, self_qa flags it.
    qa_disclosure_phrases: list[str] = field(
        default_factory=lambda: [
            "disclosure", "disclose", "i work on", "i built", "i help build",
        ]
    )

    # --- Prompt templates (agent/prompts/builder.py builds the final prompt from these) ---
    # Full Jinja2 ownership per channel — a tenant's template replaces the system's
    # generic one entirely for that channel. The trailing "return ONLY this JSON..."
    # instruction is NOT part of this template; the system appends it after
    # rendering (see prompts/builder.py's build_article_prompt/build_comment_prompt),
    # so parsing stays reliable no matter what a tenant's template does.
    prompt_templates: dict[str, str] = field(
        default_factory=lambda: dict(prompts.DEFAULT_TEMPLATES)
    )

    @classmethod
    def from_json(cls, path: str) -> "AgentConfig":
        """Convenience wrapper — see agent/config/loader.py's AgentConfigLoader for the
        actual load/validate logic."""
        from .loader import AgentConfigLoader

        return AgentConfigLoader().load(path)
