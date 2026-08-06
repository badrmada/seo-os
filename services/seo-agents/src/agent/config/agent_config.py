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
    # relative path a provider is given (a templated provider's report_path,
    # search_performance_options.key_file, an output sink's options.path) resolves against *this tenant's
    # own folder* rather than whatever directory the process happens to be running
    # in. That distinction doesn't matter for one person running one CLI command;
    # it matters completely once several tenants run in one server process. Empty
    # (the default, for a config built in code) keeps the old CWD-relative
    # behavior. A non-file source — a database row, an API request body — passes
    # its own base directory to AgentConfigLoader.load_dict().
    config_base_dir: str = ""

    # Which templates came from a file, and which file (agent/config/template_files.py).
    # Written by the loader, never by a tenant — AgentConfigLoader rejects it as an
    # input field, since it reports what happened rather than asking for anything.
    # Each entry is {"slot", "file", "path"}, e.g.
    # {"slot": "prompt_templates.site_article", "file": "site_article.j2",
    #  "path": ".../acme/templates/site_article.j2"}. Plain data on purpose: a
    # template file is read in full at load time, so nothing here is a live
    # reference and a cached or serialized config stays serializable.
    template_sources: list[dict] = field(default_factory=list)

    # --- The site this agent works on ---
    # One general, vendor-neutral answer to "which website is this?", e.g.
    # "https://example.com". Read by any tool that needs to know the site rather
    # than a vendor's name for it, and passed to every signal as context.site_url
    # (see agent/graph/stages/analyze.py's signal_context).
    #
    # It is deliberately *not* a provider's identifier. Google Search Console
    # names a property "sc-domain:example.com", which is an identifier in Google's
    # namespace and means nothing to anything else — that belongs in
    # search_performance_options.gsc_domain, with the provider that understands
    # it. A future rank source, crawler or sitemap reader wanting "the site" reads
    # this field instead of learning Google's spelling.
    #
    # Empty (the default) is fine: nothing requires it, and a zero-config tenant
    # still runs.
    site_url: str = ""

    # --- Provider-owned settings ---
    # Each provider kind below is two fields: `<kind>_provider` (which
    # implementation) and `<kind>_options` (that implementation's own settings and
    # secrets). Nothing provider-specific lives at the top level of this config.
    #
    #   {"llm_provider": "gemini",
    #    "llm_options": {"api_key": "...", "model": "gemini-2.5-flash"}}
    #
    # Why: the settings a provider needs are *its own*, and which keys are even
    # meaningful depends on which one is selected — `api_token`/`zone_id` for
    # Cloudflare, `source`/`report_path`/`summary_template` for a templated one.
    # Flattening them made every tenant's config carry every provider's fields,
    # put credentials on a generic object that mostly isn't about them, and left a
    # tenant's own "custom" class with nowhere to put its settings at all.
    #
    # `<kind>_custom_class` stays alongside `<kind>_provider` rather than moving
    # into options: it selects *which implementation*, exactly like a discovery
    # source's "class" sits beside its "provider" and its "options".
    #
    # Option names per provider are documented in docs/configuration.md; the
    # loader (agent/config/loader.py) names the new location when it sees a field
    # that used to live here.

    # --- LLM ---
    llm_provider: str = "mock"  # "gemini", "mock", or "custom"
    # "gemini": api_key, model (default "gemini-2.0-flash"), timeout_seconds (120)
    # "custom": whatever the tenant's class reads
    llm_options: dict = field(default_factory=dict)
    llm_custom_class: str = ""  # "module:ClassName", used by llm_provider="custom"

    # --- Web search (tools/base.py's SearchClient Protocol) ---
    # The system's own grounding for "llm" discovery: search the real web first,
    # put the results in the prompt, and trust only those URLs. It defaults to
    # "duckduckgo" — on rather than off, and a search engine rather than the
    # model — because grounding shouldn't depend on which LLM a tenant picked.
    # Gemini has native grounding; a local model or a gateway generally doesn't,
    # and DuckDuckGo needs no API key, account, or billing relationship, so every
    # tenant gets real pages on their first run.
    #   "duckduckgo": the default. Options: backend ("duckduckgo"; "auto" lets
    #                 ddgs aggregate several engines), region ("wt-wt"),
    #                 safesearch ("moderate"), timelimit ("" | "d"/"w"/"m"/"y"),
    #                 timeout_seconds (10).
    #   "none":       no search tool; discovery falls back to the LLM's own
    #                 grounding, then to ungrounded generation.
    #   "mock":       offline, deterministic, no network.
    #   "custom":     a tenant's own class (Bing, Serper, a self-hosted SearxNG,
    #                 an internal index) via search_custom_class.
    # Only reached when a discovery source has grounding on; a tenant with no
    # discovery_sources never searches. See tools/clients/opportunity_llm.py for
    # the full resolution order.
    search_provider: str = "duckduckgo"
    search_options: dict = field(default_factory=dict)
    search_custom_class: str = ""  # "module.path:ClassName", used by search_provider="custom"

    # --- Search performance (tools/base.py's SearchPerformanceClient Protocol) ---
    # How the site already performs in search: which queries it appears for, where
    # they rank, and which are close enough to page one to be worth work. Named
    # after the question, not after Google — Bing Webmaster Tools, a rank
    # tracker's export or an agency CSV answer it just as well.
    #   "none":      no rank data at all. **The default.** The run picks its topic
    #                from the seed keyword, then an analytics highlight, then a
    #                discovered opportunity — all the tenant's own current data.
    #   "google":    the real Search Console API. Options: gsc_domain (required —
    #                your property, "sc-domain:example.com" or a URL-prefix
    #                property), key_file (default "service_account.json"),
    #                timeout_seconds (30).
    #   "templated": your own rank data (file or API) mapped by one Jinja2
    #                template. Options: source ("file" | "api"), report_path,
    #                api_url, api_method, api_headers, api_timeout_seconds, and
    #                rows_template — which must render to a JSON array of
    #                {"query", "clicks", "impressions", "ctr", "position"}
    #                objects. It supplies data, never judgement: opportunity/score/
    #                reason are computed centrally so every source classifies
    #                "striking distance" the same way.
    #   "mock":      canned, product-neutral rows; offline and deterministic.
    #   "custom":    a tenant's own class via search_performance_custom_class.
    #
    # Why the default is "none" and not "mock": the mock returns
    # striking-distance rows, and _pick_keyword prefers one of those *over* the
    # caller's seed_keyword — so a fixture keyword silently replaced the one a
    # tenant actually asked for. A fixture is the right default for a shape
    # nothing else provides; it is the wrong default for a decision the tenant can
    # already make better.
    search_performance_provider: str = "none"
    search_performance_options: dict = field(default_factory=dict)
    # "module.path:ClassName", used by search_performance_provider="custom"
    search_performance_custom_class: str = ""

    # --- Site traffic (tools/base.py's SiteTrafficClient Protocol) ---
    # "none" (no traffic tool) | "mock" (product-neutral canned text) | "cloudflare"
    # (tools/clients/cloudflare.py, a real vendor integration) | "templated" (tenant's own
    # JSON, mapped declaratively via traffic_options.summary_template) | "custom"
    # (a tenant-registered class, for cases too bespoke for a template).
    traffic_provider: str = "mock"
    # "cloudflare": api_token, zone_id, timeout_seconds (15)
    # "templated":  source ("file" | "api"), report_path, api_url, api_method,
    #               api_headers, api_timeout_seconds, and summary_template — Jinja2
    #               rendered against {"data": <the tenant's raw JSON>, "days": ...},
    #               producing the summary text directly.
    # "custom":     whatever the tenant's class reads
    traffic_options: dict = field(default_factory=dict)
    traffic_custom_class: str = ""  # "module.path:ClassName", used by traffic_provider="custom"

    # --- App analytics (tools/base.py's AppAnalyticsClient Protocol) ---
    # "mock" (product-neutral canned data) | "templated" (tenant's own JSON, mapped
    # declaratively via two templates — see tools/clients/analytics_templated.py)
    # | "custom" (a tenant-registered Python class, for cases too bespoke for a
    # template — see analytics_custom_class below). No tenant gets a bespoke Python
    # client baked into this codebase — "templated" covers that instead.
    analytics_provider: str = "mock"
    # "templated": source ("file" | "api"), report_path, api_url, api_method,
    #              api_headers, api_timeout_seconds, and two Jinja2 templates
    #              rendered against {"data": <the tenant's raw JSON>, "limit": ...}:
    #              summary_template renders directly to the summary text;
    #              highlights_template must render to a JSON array string (typically
    #              via `tojson` in a `{% for %}` loop) of {"label", "url"} objects.
    #              See agent/utils/analytics_schema.py's infer_fields() for listing a
    #              raw JSON's available paths while writing them.
    # "custom":    whatever the tenant's class reads
    analytics_options: dict = field(default_factory=dict)
    analytics_custom_class: str = ""  # "module.path:ClassName", used by the "custom" provider
    # Not provider-specific: how many highlights the *agent* asks for, whichever
    # provider answers (agent/graph/stages/analyze.py passes it to report()).
    analytics_highlights_limit: int = 3

    # --- Signal inputs (tools/base.py's SignalSource Protocol) ---
    # Every *input* the agent reads about the site and its market, as one named
    # list — the generalization the three fixed slots above are not. Each entry:
    #   {"name": str, "provider": "mock" | "templated" | "custom", "options": {...}}
    # Read by agent/managers/tools_manager.py's ToolsManager.build_signal_sources()
    # into Tools.signals, collected concurrently by agent/graph/stages/analyze.py
    # and reaching the prompt as `signals`, keyed by name — so neither a stage nor
    # the system's own templates ever have to learn a new signal's name.
    #   - "mock": deterministic fixture (tools/mocks/signal_mock.py); accepts an
    #     optional "fail": bool to simulate that signal failing.
    #   - "templated": the tenant's own JSON (file or API), mapped by Jinja2 —
    #     source ("file" | "api"), report_path, api_url, api_method, api_headers,
    #     api_timeout_seconds, then summary_template (renders to text; the only
    #     required one), facts_template (-> a JSON object) and items_template
    #     (-> a JSON array). All three render against {"data": <raw JSON>,
    #     "context": <the run's context>}.
    #   - "custom": a tenant-registered class ("class": "module.path:ClassName"),
    #     loaded exactly like every other custom provider.
    #
    # **`search_performance`, `traffic` and `analytics` are reserved names here.** An entry using
    # one selects that built-in slot's provider instead of adding a fourth signal,
    # so the whole set of inputs can be written as one list:
    #
    #   "signal_sources": [
    #     {"name": "search_performance", "provider": "google", "options": {...}},
    #     {"name": "traffic", "provider": "cloudflare", "options": {...}},
    #     {"name": "trends",  "provider": "custom", "class": "trends:Client"}
    #   ]
    #
    # The three `<kind>_provider`/`<kind>_options` fields above keep working
    # untouched and mean exactly what they always did; an entry here for one of
    # those names simply wins over them. Their clients keep their own Protocols
    # (search_analytics/report/traffic_summary) rather than collect() — see
    # tools/base.py's SignalSource for why the three stay hand-shaped.
    #
    # Empty list (the default): no extra signals, no behavior change for an
    # existing tenant.
    signal_sources: list[dict] = field(default_factory=list)

    # --- Opportunity discovery (tools/base.py's OpportunitySource Protocol) ---
    # Each entry: {"name": str, "provider": "mock" | "llm" | "mcp" | "custom", ...}. Read by
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
    #   - "mcp": one tool call against an MCP server
    #     (tools/clients/opportunity_mcp.py). options: tool_name (required);
    #     transport ("stdio" | "http", default "stdio"); command/args/env/cwd for
    #     stdio, url/headers for http; arguments (Jinja2-rendered string values,
    #     defaulting to {"query": seed_keyword or brand_description});
    #     items_template (Jinja2 -> a JSON array, for a server answering in its
    #     own vocabulary); max_opportunities (5); timeout_seconds (60). This is
    #     the boilerplate every "custom" MCP client used to repeat — the transport
    #     and the protocol are built in, only the mapping stays configuration.
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

    # --- Which agent this tenant runs (agent/graph/pipeline.py) ---
    # The built-in "seo_content" writes an article or a reply: discover ->
    # choose_channel -> analyze -> draft -> self_qa, with which of those exist
    # decided by discovery_sources. `pipelines` adds others, each a named list of
    # stages, and each stage either a built-in name or a class in this tenant's
    # plugins/ folder:
    #
    #   "agent_type": "site_audit",
    #   "pipelines": {
    #     "site_audit": {"stages": [
    #       {"name": "crawl",    "class": "audit:CrawlStage", "options": {...}},
    #       {"name": "findings", "class": "audit:FindingsStage"},
    #       {"name": "verify",   "class": "audit:VerifyStage"}
    #     ]}
    #   }
    #
    # Each entry: "name" (the node name, unique), optional "class"
    # ("module:ClassName" — required unless "name" is a built-in stage), optional
    # "mode" ("sequential" | "concurrent_from_start" | "parallel_by_source"), and
    # optional "options" handed to a class that asks for a third constructor
    # argument. List order is the chain.
    #
    # Why this rather than a second agent shipped here: writing an article is one
    # way to grow a site and telling someone what to fix on the one they have is
    # another, but which findings matter and what a crawler does are a tenant's
    # position to hold, not this repo's. A stage writes `output` with its own
    # `kind` ("site_audit", ...), so the result shape in docs/output-schema.md
    # takes a non-draft deliverable without a new field.
    #
    # `agent_type` is this tenant's default; a run overrides it with `--agent
    # <name>` or RunRequest.agent_type. It reaches the result as
    # AgentState.agent_type. The loader rejects an agent_type with no pipeline, so
    # a typo fails at save time rather than running the wrong agent.
    agent_type: str = "seo_content"
    pipelines: dict[str, dict] = field(default_factory=dict)

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
