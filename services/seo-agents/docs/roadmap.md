# Roadmap

What's actually built vs. what's next — kept honest and short. For *how* the
shipped parts work, see [architecture.md](architecture.md); this page is only
about status and direction.

## Shipped

- **Product-agnostic providers.** LLM, search performance, site traffic, and app analytics
  are all config-driven (`mock`/`templated`/`custom`, plus real vendor
  clients for LLM/Search Console/traffic) — no tenant gets a bespoke Python client baked
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
  `discover`'s sources, `analyze`'s search-performance/analytics/traffic calls, and
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
  "analyze")`) before doing its own channel-dependent keyword-picking
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

- **MCP servers as a built-in discovery source.** `provider: "mcp"` connects to
  an MCP server — stdio or streamable HTTP — calls one of its tools, and turns
  the answer into opportunities. No class, no client, no transport code, no
  `asyncio` bridge. What stays configuration is the only part that was ever
  specific to a server: `tool_name`, `arguments` (Jinja2-rendered against the
  run), and `items_template` for a server answering in its own vocabulary. It is
  built on the official `mcp` SDK rather than hand-rolled JSON-RPC, which is a
  real dependency but buys protocol-version negotiation, structured tool output,
  result-schema validation and the HTTP transport — all things a hand-written
  client gets subtly wrong.

  Bounded and traceable like every other outbound call: `timeout_seconds` covers
  the whole exchange, so a server that accepts a connection and never answers
  costs one source rather than the run; a failure degrades into
  `discovery.tool_errors` naming the actual cause; and every opportunity records
  the server and tool it came from in `raw`.

  `provider: "custom"` remains for what the built-in deliberately isn't —
  several tool calls, choosing the tool at runtime, work in between, or an MCP
  server behind the analytics/traffic/search interfaces.
  [`examples/06-mcp-discovery/`](../examples/06-mcp-discovery/) now runs both
  side by side, offline.

- **Signal inputs as a named list.** `signal_sources` makes every *input* the
  agent reads an open, named list — `{"name", "provider", "options"}`, any number
  of them, `"templated"` for JSON you can map with a snippet and `"custom"` for
  what needs code. Before it, `Tools` had three fixed slots (search performance, `traffic`,
  `analytics`), so swapping Cloudflare for Plausible worked while *adding* a
  trends feed or a rank tracker did not — that took a fork. Now it's config, and
  nothing in this repo knows the name of a signal a tenant adds.

  Each signal returns `{summary, facts, items}` — prose the prompt uses as-is,
  plus structure for a template that knows what it asked for — and reaches every
  prompt as `signals`, keyed by its configured name. The default templates loop
  over it without naming anything, so adding a signal changes no template; a
  tenant's own template may name one, and *is validated against that tenant's
  configured names at save time*, so a typo fails while they're editing rather
  than mid-run.

  Collection is one `asyncio.gather` alongside analytics and traffic, so ten
  signals cost one round trip rather than ten, and each is independently
  degrade-don't-abort: one that fails contributes an empty entry plus a
  `discovery.tool_errors` record and never blocks the others. Crucially,
  `signals` has one key per *configured* signal on every run whatever happened to
  it — the prompt's variables are a function of the config, not of which API
  happened to answer.

  **No config breaks, and nothing needed migrating.** `search_performance_provider` /
  `traffic_provider` / `analytics_provider` and their `*_options` work exactly as
  before; `search_performance`, `traffic` and `analytics` are additionally reserved *names* in
  the list, so the whole input set can be written as one block if you prefer.
  Those three keep their own hand-shaped interfaces rather than being folded into
  `collect()` — their callers predate it and generalizing search performance's
  striking-distance keyword picking is a different job.
  [`examples/07-signal-inputs/`](../examples/07-signal-inputs/) runs a templated
  signal and a custom one, offline.

  Explicitly not done: capability inference. A signal contributes context; it
  does not rewire the pipeline.

- **Search performance, named after the job instead of the vendor.**
  `gsc_provider` was the last kind named after one company, and the only one with
  no escape hatch — `"google"` or a fixture, nothing else. A tenant on Bing
  Webmaster Tools, Ahrefs, or a Search Console *export* had to fork. It is now
  `search_performance_provider`, with the menu every other kind has: `"none"`
  (the default), `"google"`, `"templated"`, `"mock"`, `"custom"`.

  **The default changed from `"mock"` to `"none"`, which fixed a real bug.** The
  agent prefers a striking-distance row over the caller's `seed_keyword` — that
  is the point of having rank data — so defaulting to a fixture meant a config
  asking for "cron job monitoring" silently drafted about the fixture's canned
  keyword instead, while README and configuration.md both promised the seed
  keyword would be used. Worse, that fixture shipped one real product's queries
  and a live URL on its domain, so unrelated examples drafted against someone
  else's keywords. The mock is now product-neutral, and `"none"` means the topic
  comes from the tenant's own data: seed keyword, then an analytics highlight,
  then a discovered opportunity.

  **The site moved out of the run and into the config.** `input.gsc_domain` was
  required on every article run, which made a *Google property identifier*
  mandatory for tenants who had never connected Search Console — and it was
  handed to every signal as `context.site_url`, so a crawler taking it at face
  value would have fetched `"sc-domain:example.com"`. There are now two separate
  things: `site_url` ("https://example.com"), one vendor-neutral top-level field
  every tool can use, and `search_performance_options.gsc_domain`, Google's own
  identifier living with the provider that understands it. A run may override
  `site_url` with `input.site_url`.

  **The scoring is shared, not vendor-locked.** Striking-distance classification,
  trend, intent, scoring and the one-line reason moved out of the Google client
  into `tools/clients/search_performance_rows.py`, so `"templated"` supplies the
  four raw numbers it has and gets an identically-classified answer. A tenant
  never reimplements "which keyword is worth targeting" in Jinja2.

  This is a **breaking config change** — the second, after Step C. The loader
  rejects `gsc_provider`/`gsc_options` and names the replacement, and the input
  validator rejects `gsc_domain` and names both of its new homes.

- **A template can live in its own file.** Anywhere a template string is accepted,
  so is `{"file": "site_article.j2"}`, read from the tenant's already-reserved
  `templates/` folder. Every template in this system is a JSON string, so a real
  one was a single line with escaped newlines and escaped quotes — unreadable,
  undiffable, and un-editable in anything that understands Jinja2, which is a bad
  place to leave the thing tenants edit most.

  **One rule, not nine.** A `{"file": ...}` object is honored at any option whose
  name ends in `_template` plus every entry in `prompt_templates`, rather than at
  a hand-maintained list of today's options — so a provider that gains a template
  option later gets this without touching
  [`agent/config/template_files.py`](../src/agent/config/template_files.py). The
  rejected alternatives are recorded there: a sigil (`"@x.j2"`) needs an escape
  hatch for a template that legitimately starts with `@`, and a
  `*_template_file` twin per option doubles a config surface that already has
  nine template slots.

  **Read at config-load time, not per render**, which is what keeps it invisible
  to everything else: `prompts.validate_template` and `TemplateValidator` catch a
  broken file-loaded template through the exact same code path as an inline one,
  a run makes no filesystem call per prompt, and `AgentConfig` stays plain data
  rather than holding a lazy file reference. Editing a template file doesn't
  affect an already-loaded config — invisible for the CLI, and the same cache
  invalidation any config change already needs for a server.

  Containment is the same boundary `plugins/` and a tenant name get, for the same
  reason — in a server this value arrives from a request: `..`, absolute paths and
  symlinks leaving the folder are rejected, and a missing file names the path
  *and lists what is in the folder*. `check-data` grew a `templates` row naming
  every file a template came from, since "which file is this prompt actually
  coming from" is part of "will this config work" — a template edited in the
  wrong file renders perfectly and says the wrong thing.

- **Stage and pipeline registration — the deliverable is not always a draft.**
  `config.pipelines` maps an agent type to a list of stages, each of which may be
  a tenant's own class from `plugins/`; `--agent <name>` (and
  `RunRequest.agent_type`) selects one per run, landing on `AgentState.agent_type`
  — the seam that field was added for. `_STAGE_FACTORIES` was a fixed dict of six
  and `_default_spec` was the only spec there was; "seo_content" is now one agent
  type among however many a tenant declares, producing exactly its previous three
  shapes.

  **The bar was concrete and is met:
  [`examples/08-custom-pipeline/`](../examples/08-custom-pipeline/) is a site
  audit — crawl, findings, verify — whose stages, template and fixtures all live
  in the tenant folder, producing `kind: "site_audit"` in the frozen result
  schema with nothing in `src/` knowing it exists.** That is why the built-in
  `seo_audit` agent was dropped: which findings matter and what a crawler does are
  a tenant's position to hold.

  **A mode is now available to any stage that meets its requirement, not to a
  stage with the right name.** `build_graph` used to raise unless
  `parallel_by_source` was literally `"discover"` and `concurrent_from_start` was
  literally `"analyze_context"`, which meant no registered stage could ever use
  either. The requirements are real ones now: a fan-out stage's *class* declares
  `fanout_over`/`fanout_branch`/`fanout_join`, and a concurrent stage must be
  followed by something to join into — which build_graph now enforces, where
  before a dangling branch would have run and silently lost its writes into the
  same superstep as END.

  **A pipeline with no channel-aware stage has no channel.** `channel` was
  resolved before the graph and written into every run's input; an audit isn't
  drafting one of three things, so nothing invents a `"site_article"` for it —
  which also stops every signal being told this audit is writing an article.
  Structural validation of a declared pipeline happens at config load; resolving
  its classes deliberately does not, because importing a plugin runs a tenant's
  Python and a server loading a config per request must not run the code of
  pipelines it isn't using. `check-data` grew a `pipeline` row that resolves
  every stage, so that isn't left to a real run to discover.

  Also fixed here, found by the audit example: `discover_results` — an internal
  key of the fan-out — had been leaking into the returned JSON as an
  undocumented top-level field on *every* run, because LangGraph materializes
  every declared channel whether or not a graph uses it.

- **State persistence — a run is watchable from outside the process.**
  `InMemoryStateStore` is now one provider among four: `state_provider` selects
  `memory` (unchanged, still the default), `file` (one atomic `<run_id>.json` per
  run under the tenant's folder), `redis` (one key per run, optional TTL — the one
  that makes a run's progress visible to a *different* process) or `custom`, with
  connection details in `state_options` like every other provider. The interface
  it always had — `save`/`load`/`delete`, plus optional `flush`/`close` — is now
  written down in [`src/state/base.py`](../src/state/base.py), and a store may be
  sync or async like every other pluggable thing here.

  **The store is the one provider whose failures are routine**, so the guard
  around it is the substance of the step rather than the stores themselves. A
  `save()` used to propagate out of `AgentRunner._run` and be caught by the
  outermost handler — turning a run that had produced a good draft into
  `phase="failed"`. Now every call is degrade-record-continue
  ([`state_manager.py`](../src/agent/managers/state_manager.py)): the failure
  lands on `RunResult.state_errors` and the event stream, and a store that is down
  is attempted twice per run rather than once per super-step, since `save` runs
  after each one and a five-second timeout five times over is a run nobody can
  explain. Building the store is the opposite: an unknown provider or a folder
  that can't be created is a `RunRequestError` before anything runs, exactly like
  a misconfigured sink.

  **The terminal snapshot is the result**, not the last raw graph state — so
  something reading a finished run gets the documented JSON
  ([output-schema.md](output-schema.md)) rather than an internal state with
  `working` on it. Explicitly *not* adopted: LangGraph's `checkpointer=`. Resuming
  an interrupted graph is a different feature with different guarantees, and
  nothing has asked for it; conflating the two would have made "we persist state"
  a wrong answer to it later.

## Next

Nothing planned inside the agent itself. Every step this roadmap was steered by
has shipped — *inputs are signals* as `signal_sources`, *the deliverable is not
always a draft* as agent types and pipelines, and a run's state now outlives the
process that produced it. What's next is the layer above (see below), or whatever
a real use case turns out to need.

## Explicitly out of scope for this agent

No queue, no worker pool, no approval gate, no scheduling, no CMS/community
posting integration. Those are a worker/control-plane concern layered on top of
`AgentService.aexecute()`, not something this agent should grow into doing itself
— what it now provides them is a run whose state is durable and readable by
another process ([`src/state/`](../src/state/)), which is the seam a queue needs,
not the queue.
