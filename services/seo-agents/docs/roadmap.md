# Roadmap

What's actually built vs. what's next — kept honest and short. For *how* the
shipped parts work, see [architecture.md](architecture.md); this page is only
about status and direction.

## Shipped

- **Product-agnostic providers.** LLM, GSC, site traffic, and app analytics
  are all config-driven (`mock`/`templated`/`custom`, plus real vendor
  clients for LLM/GSC/traffic) — no tenant gets a bespoke Python client baked
  into this repo for data it can express declaratively. See
  [configuration.md](configuration.md).
- **Opportunity discovery.** `discovery_sources` (`mock`/`llm`/`custom`) lets
  the agent surface its own topics/threads/links instead of only reacting to
  a caller-supplied keyword or thread. See
  [architecture.md](architecture.md#discovery-the-agent-finding-its-own-work).
- **Agent-decided channel.** With discovery configured and no explicit
  `input.channel`, `choose_channel` scores discovered opportunities and picks
  the channel itself; an explicit `input.channel` still always wins.
- **Dynamic pipeline assembly.** `build_graph()` renders a small
  `PipelineSpec` instead of hardcoded `add_node`/`add_edge` calls — which
  stages exist is a function of config (`discovery_sources` empty vs. not),
  not a fixed shape every tenant gets.
- **Tool-as-agent, proven, not just claimed.** The `"custom"` provider
  mechanism (shared across analytics/traffic/discovery-source classes)
  places no constraint on what's behind the Protocol — see
  [extending.md](extending.md#walkthrough-an-opportunity-source-thats-itself-an-agent)
  for a worked example of a discovery source that runs its own multi-step
  tool loop rather than a single call.
- **Degrade, don't abort — everywhere in the pipeline, not just discovery.**
  `discover`'s sources, `analyze`'s GSC/analytics/traffic calls, and
  `draft`'s LLM call are all wrapped individually at their own call sites. A
  tool, API, or connection failure degrades to a safe default plus one
  recorded `ToolError` wherever a fallback is meaningful (`discover`,
  `analyze`); where it isn't (`draft` — there's no fallback article for a
  failed or unparseable LLM response), the stage fails cleanly
  (`phase="failed"`, a clear `error`) instead of raising, and `self_qa`
  passes that through rather than crashing on a missing draft.
  `AgentRunner.run()` itself is the outermost safety net for anything that
  reaches it first (bad input, before the graph even starts): it always
  returns the same top-level shape (see [output-schema.md](output-schema.md))
  — a caller never needs a `try/except` around `run()`, and nothing in this
  pipeline crashes the process because of a tool, a connection, or an API
  call. See
  [architecture.md](architecture.md#error-handling-degrade-dont-crash).
- **Parallel discovery fan-out.** With 2+ configured `discovery_sources`,
  `_default_spec` now picks `PipelineStage("discover", mode="parallel_by_source")`
  and `build_graph` fans out one LangGraph `Send` per source into a
  `discover_source` node, joined by `discover_join` before `choose_channel` —
  instead of `DiscoverStage` looping through them one at a time. 0 or 1
  sources still use the original sequential `DiscoverStage` path unchanged
  (nothing to gain from fanning out one source). Same
  opportunities/tool_errors merge contract either way — see
  [architecture.md](architecture.md#how-the-pipeline-is-assembled).
- **Grounded `"llm"` discovery, default on.** `LLMClient.generate()` takes a
  `grounded` param; `GeminiClient` backs it with Google Search grounding, so
  the model searches for real topics/threads instead of guessing from
  training data, and real citation URLs come back on `LLMResponse.sources`.
  `LLMOpportunitySource` defaults `grounded=True` and only trusts a `link` the
  model claims if it's one of those real citations — set
  `discovery_sources[...].grounded: false` to opt back into the old
  ungrounded behavior. This replaces most of the motivation for a bespoke
  Reddit/trends vendor client (below): real web results without maintaining
  another API integration.

- **`SearchClient` — grounding is the system's job, not the model's.** A
  `search_provider`, defaulting to **`duckduckgo`** (no API key, no account),
  that `"llm"` discovery uses *ahead of* the model's own grounding. The order is
  documented and tested: a configured search client, else the LLM's native
  grounding, else ungrounded — each step falling through to the next when it
  yields nothing, so a search outage costs a source its grounding rather than its
  results.

  The reason it outranks native grounding: Gemini can search for itself and most
  things can't, so building discovery on that made "can this agent see the real
  web?" a property of which model a tenant picked. Now it isn't — a local model
  behind `llm_provider: "custom"` grounds exactly as well as Gemini does.

  How it works: one cheap ungrounded call asks the model for a few short search
  queries (a run usually has no seed keyword — that is what discovery is *for*),
  those run concurrently, and the merged, de-duplicated results go into the
  discovery prompt with their URLs as the only trusted list. Native grounding is
  switched off for that call even on Gemini: the facts are already in the prompt,
  and searching twice for one answer makes "which URLs are trustworthy?"
  ambiguous. `"search_provider": "none"` restores the previous behavior exactly,
  and `"grounded": false` on a source still skips all of it.

  Two things a real run taught, both now in the code: DuckDuckGo **rate-limits by
  IP** (about twenty searches in, everything fails for a while), so
  `fallback_backend` asks another engine before a run gives up on grounding; and
  falling through must never be silent, so every opportunity records
  `raw.grounding` (`"search"`/`"llm"`/`"none"`) and `raw.grounding_error`. The
  first version of this shipped a run that reported success while quietly
  producing unverified links, with nothing anywhere saying so. See
  [architecture.md](architecture.md#grounding-a-system-capability-not-a-model-feature).

- **Discovery contract enforced, not trusted.** Every item any
  `OpportunitySource` returns (`mock`/`llm`/`custom` alike) is coerced by
  `agent/schemas/opportunity.py`'s `normalize_opportunity`, called from
  `discover.py` for every source — a malformed item (bad `signal_strength`,
  invalid `intent`/`suggested_channel_hint`, no `topic`) is dropped
  individually instead of raising and losing every other opportunity that
  source found, or corrupting `choose_channel`'s cross-source scoring.
- **`analyze`'s analytics/traffic calls running concurrently with
  discovery.** When `discovery_sources` is configured, `analyze`'s
  channel-independent analytics/traffic calls now run in their own
  `analyze_context` node, a direct child of `START` alongside
  `discover -> choose_channel`, instead of after it — `analyze` waits on
  both (a LangGraph AND-join, `add_edge([choose_channel, analyze_context],
  "analyze")`) before doing its own channel-dependent GSC/keyword-picking
  half. A zero-discovery tenant is unaffected: `analyze_context` isn't added
  to that graph at all, and `analyze` fetches analytics/traffic itself
  exactly as before. See
  [architecture.md](architecture.md#how-the-pipeline-is-assembled).

- **Verbose mode — a run you can watch.** `-v`/`-vv` (or `verbose` in tenant
  config) reports every stage and every tool call as it happens, with timings,
  outcomes, and — at `-vv` — truncated prompts, responses, and decisions. It
  matters most for the failures the pipeline deliberately swallows: a degraded
  analytics call or a failed discovery source used to surface only as a
  `tool_errors` entry in the final JSON. Everything goes to stderr, so
  `python src/main.py run --tenant acme -v | jq` is unaffected; secrets are redacted by field
  name and payloads truncated; and a reporter error can never fail a run.
  Implemented by *wrapping*, not by editing: `observe_tools()` proxies each
  client and `observed_node()` wraps each pipeline stage, so no stage — and no
  tenant's `"custom"` class — contains any reporting code. With reporting off
  (the default) the proxies aren't in the call path at all. See
  [configuration.md](configuration.md#watching-a-run-happen-verbose-mode).

- **Output sinks — the result goes where you want it.** `output_sinks` sends a
  finished run to stdout, a file, a JSONL archive, an HTTP endpoint, or a class
  of your own, in any combination. The default is a single `json` sink writing
  the same indented document to stdout the agent has always printed, so nothing
  changes for an existing tenant. Sinks run *after* the graph, at the
  `AgentRunner`/CLI boundary — no stage can see one, and the result shape in
  [output-schema.md](output-schema.md) is untouched. Build failures are fatal
  before the run (a webhook with no url shouldn't be discovered after a pipeline
  has spent real LLM calls); emit failures never are (the result already
  exists). Custom sinks load through the same `load_custom()` every other
  `"custom"` provider now uses, which also grew an optional second `options`
  argument so a provider can carry its own settings and secrets — the original
  `__init__(self, config)` form still works untouched. See
  [configuration.md](configuration.md#where-the-result-goes-output-sinks).

- **A real CLI.** `run`, `check-data`, `show-graph`, `list-tools`,
  `list-specialists`, and `preview-prompt`, built on Typer. Every command is
  explicit — a bare `python src/main.py` prints help and does nothing, and
  `run` is the one command that does work. Commands are one self-contained
  module each with a `register(app)`
  hook and an explicit import list as the registry, so adding one touches
  nothing that already exists. `check-data` reuses the same validators a real
  run uses (rather than reimplementing their rules) and additionally *builds*
  every configured provider, which is where a missing credentials file or an
  unimportable custom class actually shows up; `show-graph` renders from the
  `PipelineSpec` alone, so a purely structural question needs no API key.
  `list-tools` reads a declarative provider catalog that a test pins against the
  builders, so it can't drift. See [cli.md](cli.md).

- **A tenant is a folder.** `userdata/<name>/` holds a tenant's `tenant.json`,
  `plugins/`, `templates/`, `data/`, and `output/`, and a run names it:
  `run --tenant acme`. The workspace root is `--userdata`, else
  `$SEO_AGENT_USERDATA`, else `./userdata`, so a container mounts a volume and
  serves every tenant in it. Every path in a config — and `--input` — resolves
  inside that folder rather than against the process's working directory, which
  is what lets many tenants share one process without reading each other's
  files, and what lets any command run from any directory.

  This replaced **three** different mechanisms for finding a tenant's custom
  code: a file dropped under `src/`, an installed package, and an undocumented
  `PYTHONPATH=code` that the examples actually depended on. Now there is one
  place, `plugins/`, and no `PYTHONPATH` anywhere.

  Plugins are loaded by file location under a per-tenant synthetic package
  rather than by appending to `sys.path` — module names are process-global, so
  the `sys.path` version would let two tenants that each have
  `plugins/analytics.py` collide, first import winning, silently serving one
  tenant's code to another. Tenant names are validated rather than sanitized,
  since in a server they arrive from a request. Extra dependencies mean a new
  image, on purpose: there is no per-tenant environment management. See
  [cli.md](cli.md) and [extending.md](extending.md#where-your-code-goes-the-plugins-folder).

- **Grounding is a contract, not a hope.** `LLMResponse` now carries `grounded`
  — whether grounding *actually happened*, not merely whether it was asked for.
  This fixes real data loss: "grounded, and the search cited nothing" and "this
  provider has no grounding at all" both looked identical from `sources` being
  empty, so a provider that ignored the flag had every link stripped from every
  opportunity while the run still reported success. Links are now verified only
  when grounding genuinely ran; when it didn't, discovery degrades to ungrounded
  handling and the reporter says so rather than quietly discarding data. Gemini's
  behavior is unchanged. Every outbound call is also bounded now — `GeminiClient`
  (120s) and `GoogleSearchConsoleClient` (30s) previously had no timeout at all,
  which on a queue worker is a slot held forever.

- **Async execution.** A run is async end to end — `AgentRunner.arun()`, every
  stage, every tool call — so several tenants' runs proceed concurrently in one
  process on one event loop (`asyncio.gather`) instead of one thread each.
  `GeminiClient` uses google-genai's native coroutine API, and the HTTP clients
  (Cloudflare, `api`-sourced templates, the webhook sink) moved from `requests`
  to `httpx.AsyncClient`.

  The decision that made this non-invasive: **every Protocol accepts a sync *or*
  an async implementation.** The framework awaits an async one and runs a sync
  one in a worker thread ([`agent/utils/async_utils.py`](../src/agent/utils/async_utils.py)),
  decided in one place — the proxies that already wrapped every tool call. So no
  existing `"custom"` class had to change, and none of the examples did.
  `GoogleSearchConsoleClient` stays sync because `googleapiclient` is
  httplib2-based and cannot be otherwise; it runs threaded, correctly, rather
  than pretending. `run()` remains as a thin `asyncio.run(arun(...))` wrapper for
  the CLI and tests.

  A run also has an optional overall deadline now (`run_timeout_seconds`, `0` =
  unbounded) on top of the per-call timeouts — a dozen individually-timely calls
  can still hold a worker slot far longer than intended. See
  [architecture.md](architecture.md#how-a-run-executes-async-and-why-you-can-ignore-that).

- **A service layer.** [`agent/service.py`](../src/agent/service.py)'s
  `AgentService` is the channel-agnostic entry point: `RunRequest` in,
  `RunResult` out. It owns what the CLI used to do inline — resolve the config,
  build the reporter, run, emit to the sinks, keep the state — so an HTTP
  handler, a queue worker, or a scheduler gets the identical sequence instead of
  a near-copy. The CLI is now one adapter among those.

  A failed *run* is a successful *request*: it comes back as a `RunResult` with
  `phase="failed"`, and only an unrunnable request (unknown tenant, unloadable
  config, a webhook sink with no URL) raises. Events can be collected onto the
  result and/or streamed to a callback through a third reporter implementation,
  which is what an SSE endpoint or a job-progress record needs. And nothing
  writes to the process's file descriptors unconditionally any more: `stdout` and
  the sink-failure warning are both request-level choices, defaulting to what a
  CLI wants. Still out of scope, on purpose: the queue, the worker pool, the HTTP
  framework, the scheduler. See
  [architecture.md](architecture.md#calling-the-agent-the-service-layer).

- **A provider registry, and provider-owned settings.** `ToolsManager`'s five
  parallel if/elif ladders are one registry, `kind -> {name -> factory}`, and
  `src/tests/test_providers.py` asserts per kind that its names are the *same
  set* as the catalog `list-tools` reads. Before, a test could only check that
  each catalogued name was accepted: a factory nobody had catalogued was
  invisible, and a catalogued name with no factory reached the user as a
  confusing "Unknown provider". Adding a provider is now one factory and one
  description.

  `llm_provider: "custom"` (via `llm_custom_class`) closes the last gap in the
  `"custom"` mechanism — bringing your own model, gateway, or local LLM no longer
  means forking.

  **Provider settings moved into the provider.** Each kind is now
  `<kind>_provider` plus `<kind>_options`, and nothing provider-specific remains
  at the top level of a config: `gemini_api_key`, `gsc_key_file`,
  `cloudflare_api_token`, the `analytics_*`/`traffic_*` templated fields — all of
  them live with the provider that reads them. Which settings are even meaningful
  depends on the provider selected, so flattening them made every tenant carry
  every provider's fields and left a `custom` class with nowhere to put its own.
  **This is a breaking config change**, and the only one so far: the loader
  rejects an old field and names its new location, so a stale config says exactly
  what to move. The repo's own tenant and all six examples are migrated.

## Next

In order. Two framings shape the list, and both are things the code only partly
lives up to today:

- **Inputs are *signals*, and the vendors shipped here are defaults, not the
  model.** Search Console, Cloudflare and product analytics get someone to a real
  run quickly; a trends feed, a rank tracker, a keyword API, a crawler or an MCP
  server are the same kind of thing. Adding one should be config, not a fork.
- **The deliverable is not always a draft.** Writing an article is one way to
  grow a site; telling someone what to fix on the site they already have is
  another.

1. **A built-in `provider: "mcp"` discovery source.** MCP already works as a
   `"custom"` class ([`examples/06-mcp-discovery/`](../examples/06-mcp-discovery/)),
   but every user writes the same stdio boilerplate.
2. **Signal inputs as a named list.** `Tools` has three fixed slots — `gsc`,
   `traffic`, `analytics` — so swapping Cloudflare for Plausible works while
   *adding* a trends feed does not. Signals get the shape `discovery_sources`
   already has: a named list, any number, any provider. The three built-in slots
   stay as views onto it, so no config breaks.
3. **Stage and pipeline registration.** Config-declared stages, and a pipeline
   spec per agent type rather than one global default.
4. **A `seo_audit` agent type.** Crawl the site, read the sitemap, cross-
   reference the configured signals, and return prioritized recommendations —
   thin or duplicate pages, weak metadata, broken internal links, orphan pages,
   pages ranking 11–20 that deserve work rather than a new article. A separate
   `agent_type` sharing tools, config, the service layer and the result schema
   (`kind: "site_audit"`), not a fourth channel. Findings carry the URLs they
   came from; the crawler is bounded by default (robots.txt, rate limits, page
   and depth caps) because it is the one tool here that can hurt someone else's
   server.
5. **State persistence.** `InMemoryStateStore` promoted to a selectable provider
   — file/JSONL first, since it needs no infrastructure and survives the process.

## Explicitly out of scope for this agent

No queue, no worker pool, no persistence beyond a single in-process run
(state snapshots are in-memory only — see
[`src/state/memory_store.py`](../src/state/memory_store.py)), no approval
gate, no scheduling, no CMS/community posting integration. Those are a
worker/control-plane concern layered on top of `AgentRunner.run()`, not
something this agent should grow into doing itself.
