# SEO Agent Plan — remaining work

Everything already shipped is documented in [`docs/roadmap.md`](docs/roadmap.md)
and removed from this file. What follows is only what is left to build.

## START HERE

**Next task: Step I (state persistence).** Steps F, J and G have shipped, and so
did the search-performance rename (unplanned — see below); nothing is
half-finished and the tree is green. Step I is the last one on the list.

**J was taken before G on purpose**, and it paid off: G's deliverable is a tenant
declaring their own stages, which means a tenant writing their own prompts, and
until J those were single escaped JSON lines. `examples/08-custom-pipeline/`'s
audit report is a `.j2` file for exactly that reason — the reverse order would
have shipped it in the form J exists to remove.

```bash
cd services/seo-agents
pip install -r requirements.txt                   # includes pytest, ddgs and mcp
pytest                                            # 402 passing
python src/main.py list-tenants                   # the workspace
python src/main.py check-data --tenant echooers   # the real tenant, no API calls
python src/main.py run --userdata examples --tenant 06-mcp-discovery  # MCP, offline
python src/main.py preview-prompt --userdata examples --tenant 07-signal-inputs  # signals + a template file, offline
python src/main.py run --userdata examples --tenant 08-custom-pipeline  # a site audit, not a draft, offline
```

### Remaining steps, in order

| # | Step | Why in this order |
|---|---|---|
| ~~D~~ | ~~`SearchClient` (pluggable grounding)~~ | **Done** — see docs/roadmap.md. DuckDuckGo is the default; the resolution order is search → the model's own grounding → ungrounded. |
| ~~E~~ | ~~Built-in `provider: "mcp"` discovery source~~ | **Done** — see docs/roadmap.md. Official `mcp` SDK, stdio + streamable HTTP, mapped by `items_template`. |
| ~~F~~ | ~~Signal inputs as a named list~~ | **Done** — see docs/roadmap.md. `signal_sources`, collected concurrently, reaching the prompt as `signals` keyed by name. A crawler or a sitemap reader is a signal like any other. |
| ~~G~~ | ~~Stage and pipeline registration~~ | **Done** — see docs/roadmap.md. `config.pipelines` + `--agent`; `examples/08-custom-pipeline/` is the site audit that proves the bar. |
| ~~H~~ | ~~`seo_audit` agent type~~ | **Dropped** — the `"custom"` mechanism plus G covers it. See below. |
| ~~J~~ | ~~Template values: inline or from a file~~ | **Done, ahead of G** — see docs/roadmap.md. `{"file": "x.j2"}` anywhere a template string is accepted. Taken first because G's whole point is a tenant authoring their own stages and prompts. |
| **I** | **State persistence** | Next, and last. Becomes load-bearing the moment a queue exists. |

Letters are order-of-work, nothing more — they are not renumbered when a step is
dropped, since docs and commits already refer to them.

### Why H was dropped

A built-in `seo_audit` agent type would have been this repo taking a position on
what an audit *is* — which findings matter, how they're ranked, what a crawler
does. That's the position a tenant should hold, and after F and G they can —
`examples/08-custom-pipeline/` is one, built entirely in a tenant folder:

- **Its inputs are signals.** A crawler and a sitemap reader are
  `signal_sources` entries with a `"custom"` class, exactly like a rank tracker.
  Nothing about them needs a new provider kind or a new `Tools` field.
- **Its shape is a pipeline.** G shipped this: a tenant declares their own stages
  and their own spec — including one that ends in a verification stage of their
  own rather than `draft`/`self_qa`.
- **Its output already fits.** `AgentState.agent_type` and the frozen result
  schema take a new `kind` (`"site_audit"`) without a new field, which was
  always the argument for a new kind rather than a fourth channel.

So H was mostly *content* — prompt wording, which findings to look for — wearing
a step's clothes. Two things from it are worth keeping and are recorded here
rather than lost:

- **A crawler is the one tool here that can hurt someone else's server.** Any
  crawler shipped or documented by this project — including an example — obeys
  `robots.txt`, rate-limits, caps pages/depth/total time, sends an identifying
  user agent, and never follows off-site links. A default that could hammer a
  site is not an acceptable default. **Now in `docs/extending.md` ("If your stage
  crawls, bound it"), and the reason `examples/08-custom-pipeline/` ships a
  fixture rather than a working crawler.**
- **Findings must be evidence-backed** — each carrying the URLs and rows it came
  from, the same principle that makes a grounded link trustworthy. An audit that
  asserts problems it cannot point at is worse than no audit. **Also now in
  `docs/extending.md`, and demonstrated by example 08's `VerifyStage`, which drops
  any finding pointing at a URL the crawl never saw.**

If a real use case later shows that every tenant writes the same audit, that is
the moment to reconsider — with their code as the specification.

### What this system is, so the remaining steps don't narrow it

Two framings. Neither is a step any more — F shipped the first, and G plus
`"custom"` is how a tenant gets the second — but both are the standard against
which a remaining step is judged too narrow:

1. **Inputs are *signals*, and the vendors we ship are defaults, not the model.**
   Google Search Console, Cloudflare and product analytics are three signals that
   get someone to a real run quickly. A trends feed, a rank tracker, a keyword
   API, a crawler, a competitor watcher, an MCP server are the same kind of
   thing. Nobody is bound to our three, and adding a fourth must be config, not a
   fork. **Step F did this** — `signal_sources`. A new kind of input goes there;
   it does not become a fourth slot on `Tools`.
2. **The deliverable is not always a draft.** Writing an article is one way to
   grow a site; telling someone what to fix on the site they already have is
   another. `agent_goal` is about growth, and the run decides what to produce.
   **This repo does not need to ship the second deliverable to be true to it** —
   it needs the seams that let a tenant produce one, which is Step G plus the
   `"custom"` mechanism, and a result schema that already takes a new `kind`.
   **Step G did this** — `config.pipelines` and `--agent`, with
   `examples/08-custom-pipeline/` as the audit built on it and no `src/` change.

### Invariants the remaining steps must not break

- **`arun()` / `aexecute()` / `aemit()` are the real entry points**; the sync
  wrappers cannot nest inside a running loop.
- **The sync/async fork lives in exactly one function**,
  `agent/utils/async_utils.py::call()`, reached through the observing proxies. A
  new call site that invokes a client directly must go through it too, or a
  tenant's sync plugin will stall the loop for every concurrent run.
- **`AgentService` owns the sequence around a run.** A new channel adapts to it;
  it does not drive `AgentRunner` itself.
- **A failed run is a successful request.** `RunResult.run["phase"] == "failed"`;
  only an unrunnable *request* raises `RunRequestError`. Step I's store must not
  break this by letting a save failure escape.
- **Nothing writes to process file descriptors unconditionally.** `stdout` and
  `warn_stream` are request-level.
- **A new provider is a factory in `_REGISTRY` plus a name in `CATALOG`**, and
  its settings go in that provider's `options` — never a new top-level config
  field. `src/tests/test_providers.py` fails if the two disagree.
- **The `"custom"` class contract**: `__init__(self, config)` keeps working, with
  `(self, config, options)` as an opt-in, sync or async either way.
- **No further breaking config change without the same treatment** Step C got: a
  named destination per field. There are now three such maps and they are not
  interchangeable — `MOVED_FIELDS` (a setting relocated into a provider's
  `options`), `RENAMED_FIELDS` (same meaning, new name; the message must *not*
  say "move it into options") and `input_validator.py::INPUT_MOVED_FIELDS` (an
  input field that became config). Renames are reported first, since advice about
  an options object that no longer exists is worse than none.
- **`AgentState` and the returned JSON hold** — `docs/output-schema.md`. A new
  deliverable uses a new `kind`, not a new top-level field. A new *working* key on
  `AgentState` must be popped in `AgentRunner._run` alongside `working`, or
  LangGraph materializing it makes it an accidental top-level result field —
  which is exactly how `discover_results` leaked for two steps.
- **A stage is `(tools, config[, options])` and returns only the keys it
  changes**, whether it ships here or comes from a tenant's `plugins/`. A new
  built-in stage joins `pipeline.py::BUILTIN_STAGES`; a new *mode* owes a
  requirement checkable without knowing which stage it is applied to.
- **A new template option is named `*_template`.** That suffix is what
  `agent/config/template_files.py` keys off to accept `{"file": "x.j2"}`, so an
  option named anything else silently supports only inline strings. A config dict
  whose *keys* are a tenant's or a vendor's vocabulary (not this config's
  structure) belongs in that module's `_OPAQUE_MAPS`.
- **A new kind of input is a `signal_sources` entry**, not a new `Tools` field and
  not a new top-level config field. `working.signals` keys stay a function of the
  config rather than of the run, so a prompt template can rely on them.
- **The overlap test is the canary.** `test_two_runs_with_different_configs_overlap`
  is the only thing that catches an accidentally-blocking call in the run path —
  every other test passes with runs silently serialized.

### Things a cold session will otherwise rediscover the hard way

- **Import cycles are the recurring failure here, and `pytest` does not catch
  them.** Three have been introduced and fixed; every one was found by running
  the CLI, never by the suite, because tests import in a lucky order.
  `src/tests/test_imports.py` imports each package cold in a subprocess — **run
  it after any new intra-package import.** The fix is usually a deferred import
  inside the function, as `agent/validators/template_validator.py` already does.
- **A source must not call `normalize_opportunity` itself** — now stated on the
  `OpportunitySource` Protocol in `tools/base.py`, where a new source's author
  will look. `agent/graph/stages/discover.py` normalizes *every* item *every*
  source returns, and `normalize_opportunity` puts the item it is given under
  `raw`, so normalizing first means normalizing twice and everything the source
  recorded lands at `raw.raw.*`. Both built-in sources did this; both are fixed,
  and a `assert "raw" not in opportunity["raw"]` guard sits in
  `test_opportunity_llm.py` and `test_mcp_discovery.py`.
- **A source test that asserts the output shape must go through `DiscoverStage`.**
  This is the general lesson from the above, and the more expensive half. The
  double-normalization survived a whole step because every test in
  `test_opportunity_llm.py` called `source.discover()` directly, saw the
  single-pass result, and passed — while every real run put the grounding audit
  trail somewhere the docs said it wasn't. Both files now have a `_discover`
  helper that runs the real stage.
- **Docs go stale silently.** The reliable check is to extract every
  `python src/main.py …` line from `README.md`, `docs/*.md`, and
  `examples/*/README.md` and actually execute it — that has caught four stale
  command lines that reading did not.
- **Two `.gitignore` files** exist (repo root and `services/seo-agents/`) and
  both need editing when ignore rules change.
- **`userdata/echooers/` is the real tenant** — real Gemini/Search Console/Cloudflare
  credentials, gitignored. `check-data --tenant echooers` validates it without
  spending an API call; `run` spends a real one, so don't run it casually.
- **A step is not done until its config fields are in `docs/configuration.md`
  and its status is in `docs/roadmap.md`**, with tests alongside.
- **A mock that stands in for a *decision* is a trap, and this repo has been
  caught by it once.** `gsc_provider` defaulted to `"mock"`, the mock returned
  striking-distance rows, and `_pick_keyword` prefers those over the caller's
  `seed_keyword` — so every unconnected tenant silently drafted about the
  fixture's keyword while two docs promised otherwise. Every test passed
  throughout, because the old input validator required `gsc_domain` and the tests
  that omitted it never reached the client. **A fixture is the right default for a
  *shape* nothing else provides; it is the wrong default for a decision the
  tenant can already make better** — that case wants a null provider. And a mock
  must be product-neutral: that one shipped one real product's queries and a live
  URL on its domain, so unrelated examples drafted against someone else's
  keywords.
- **A kind named after a vendor eventually blocks someone.** `gsc` was the last
  one, and the only kind with no `templated`/`custom` escape hatch. Name the kind
  after the question it answers; put the vendor's own identifiers in that
  provider's `options`. The give-away that it was wrong: `input.gsc_domain` was
  reaching every signal as `context.site_url` while holding
  `"sc-domain:example.com"`, which is not a URL.
- **Judgement derived from a provider's data belongs outside the provider.**
  Striking-distance classification and scoring lived inside the Google client, so
  a second rank source would have had to reimplement them — in Jinja2, for the
  templated one — and would have disagreed. They now live in
  `tools/clients/search_performance_rows.py`; a provider supplies raw numbers.

---

## Architecture: the three planes

The rule that keeps the remaining steps non-invasive.

1. **Tools plane** — what stages *call*: the LLM, search, discovery sources, and
   every signal input (search performance, analytics and traffic in their own
   fields; everything
   else in `Tools.signals`, from `config.signal_sources`). Lives in `Tools`
   (`agent/graph/tools.py`), built by `ToolsManager`. Stages depend only on the
   Protocols in `tools/base.py`.
2. **Run context plane** — how a run is *observed and persisted*: the reporter,
   the output sinks, the state store (Step I). Orchestration concerns, not things
   a stage calls.
3. **Result plane** — `AgentState` and the returned JSON, documented in
   `docs/output-schema.md` and deliberately frozen. **No step below adds a field
   to it.**

**Design rule:** the run-context plane never enters `Tools` and never enters
`AgentState`. A stage that needs to report something does it through the
observing proxy wrapping its client, never by reaching into run context.

---

## ~~Step D — `SearchClient` (pluggable grounding)~~ — shipped

Kept only for the decisions a later step needs to know about; the full write-up
is in `docs/roadmap.md` and `docs/architecture.md`.

- **`search_provider` defaults to `"duckduckgo"`, not `"none"`.** Grounding is
  the system's capability, not the model's — otherwise "can this agent see the
  real web?" depends on which LLM a tenant picked. DuckDuckGo needs no key, so
  the default costs nobody an account. `"none"` restores the old behavior.
- **The order is search → the model's own grounding → ungrounded**, each falling
  through to the next when it yields nothing. A search outage costs a source its
  grounding, never its results.
- **The model writes the search queries** (one cheap ungrounded call), because a
  real run usually has no seed keyword. `search_queries` on the entry fixes them
  and skips that call; no queries and no seed keyword means no search rather
  than a guessed one.
- **A search-grounded call passes `grounded=False` to the LLM** even on Gemini —
  the facts are in the prompt already, and two searches make "which URLs are
  trustworthy?" ambiguous.
- **Nothing calls `Tools.search` directly yet.** It is in the bundle (and
  wrapped by `ObservedSearchClient`) for Step F/H; today only
  `LLMOpportunitySource` searches, through the instance `ToolsManager` hands it
  at construction. A discovery source's *inner* LLM and search calls report at
  `discover` granularity, since a source is built before a reporter exists.
- **`ddgs` is a new dependency** (with `primp` and `lxml`). It handles the
  engine's bot-challenge flow; a hand-rolled `html.duckduckgo.com` scraper is
  served a challenge page and returns nothing — verified, not assumed.
- **DuckDuckGo rate-limits by IP** — roughly twenty searches in, every request
  raises `DDGSException: No results found` for a while, and `backend="auto"`
  keeps working throughout. Hence `search_options.fallback_backend` (default
  `"auto"`). Anything that adds search calls per run should expect this.
- **A degrade that nothing records is a bug, not a degrade.** The first version
  of Step D swallowed search failures, and a real run came back `phase="done"`
  with silently unverified links and no trace of why. Every opportunity now
  carries `raw.grounding` and `raw.grounding_error`. A later step adding its own
  fallback path owes the same.

---

## ~~Step E — Built-in `provider: "mcp"` discovery source~~ — shipped

Kept only for the decisions a later step needs to know about; the full write-up
is in `docs/roadmap.md` and `docs/configuration.md`.

- **`mcp` (the official SDK) is a new dependency**, and a heavy one — it pulls in
  `starlette`, `uvicorn`, `sse-starlette`, `httpx2` and eleven others, a server
  stack for a client-only feature (16 new pins). Taken deliberately over
  hand-rolled JSON-RPC: the SDK negotiates protocol versions, reads
  `structured_content`, and **validates a tool result against the schema the
  server declares — which means it calls `tools/list` before returning one**. A
  hand-written client passes against a stub that doesn't implement `tools/list`
  and then fails against every real server. `tools.clients.opportunity_mcp`
  imports it **inside the function**, not at module scope: `import mcp` costs
  ~0.8s and `tools_manager` is imported by every CLI command.
- **Both transports, from day one** — `transport: "stdio" | "http"`. Streamable
  HTTP was nearly free once the SDK was in (`streamable_http_client`), and a
  hosted MCP server is at least as common as a locally-launched one.
- **The mapping is a Jinja2 `items_template` rendering to a JSON array string**,
  the same contract as `analytics_options.highlights_template`. Pass-through
  (a bare array, or an object with a `results`/`items`/`opportunities` list)
  covers a server that already speaks this vocabulary, so the common case needs
  no template. A prose answer is an *error*, not zero opportunities.
- **Everything the SDK raises arrives inside an anyio `ExceptionGroup`.**
  Unwrapped by `_only_cause`, or every failure — a command not on PATH, a bad
  payload, a dead server — reaches `discovery.tool_errors` as the identical
  useless string `ExceptionGroup: unhandled errors in a TaskGroup (1
  sub-exception)`. Anything else that wraps a third-party async client owes the
  same.
- **`timeout_seconds` (60) bounds the whole exchange**, not just a read. A server
  that accepts the connection and never answers is the failure mode a
  hand-written client always forgets.
- `examples/06-mcp-discovery/` now runs **both** paths offline against one stub
  server with two deliberately different tool shapes: the built-in on
  `trending_topics` (foreign vocabulary → `items_template`) and the existing
  sync-bridge `"custom"` class on `search_opportunities`, still the proof that a
  sync `discover` works.

---

## ~~Step F — Signal inputs as a named list~~ — shipped

Kept only for the decisions a later step needs to know about; the full write-up
is in `docs/roadmap.md`, `docs/architecture.md` and `docs/configuration.md`.

- **The three built-in slots stayed slots, and the *names* became reserved.**
  (Written at the time; `gsc` has since been renamed `search_performance` — see
  docs/roadmap.md. The reasoning below is unchanged, only the name.)
  `Tools.gsc/.traffic/.analytics` are still real dataclass fields with their own
  Protocols; `Tools.signals` holds everything else. The plan said "views onto the
  signal dict", and that was wrong in one specific way worth remembering: the
  three have genuinely different methods (`search_analytics` returns ranked query
  rows, `report` returns linkable highlights), `dataclasses.replace` in
  `observe_tools` needs real fields, and seven construction sites pass them by
  keyword. Unifying them in `Tools` would have bought nothing and broken all of
  that. What *did* unify is the config: `signal_sources` accepts `gsc`/`traffic`/
  `analytics` as reserved names routing to those kinds, so a tenant can write
  every input as one list. `<kind>_provider`/`<kind>_options` are untouched and
  **nothing needed migrating** — Step C used the one breaking config change this
  system gets.
- **`working.signals` has one key per *configured* signal on every run**, whatever
  happened to that signal — a failure contributes empty values, not a missing key.
  The first version dropped empty signals so `{% if signals %}` would be precise,
  which quietly made a prompt template's available variables depend on whether an
  API answered. Any later step adding a template-visible collection owes the same
  rule: **the keys are a function of the config, not of the run.**
- **That rule is what makes save-time validation of a tenant's template
  possible.** `agent/config/loader.py` passes the tenant's own signal names into
  `prompts.validate_template`, so `{{ signals.rank_tracker.summary }}` is checked
  against the config that builds it and a typo'd name fails while someone is
  editing. The keys *inside* `facts`/`items` are the provider's vocabulary and
  cannot be known, so `templates.py`'s `_AnyKey` accepts any of them — the same
  concession the templated analytics/traffic providers make by validating against
  the tenant's real data instead of a sample.
- **`AnalyzeContextStage` and `AnalyzeStage` now share one `collect_context()`.**
  They were two hand-written versions of the same fetch (concurrent vs.
  sequential), and only one of them would have been generalized — a difference no
  test would have caught until a tenant configured discovery. A stage that
  collects the same thing two ways is a bug waiting for a second implementer.
- **`BUILTIN_SIGNAL_NAMES` lives in `agent/schemas/signal.py`**, a module that
  imports nothing but `typing`, because three packages need it (`managers/
  providers.py`, `managers/tools_manager.py`, `config/loader.py`). Any other home
  makes at least one of them a deferred import to avoid closing a cycle.
- **`ProviderKind.selected()` had to learn about reserved names**, or `list-tools`
  reports a tenant using `{"name": "traffic", "provider": "cloudflare"}` as having
  no traffic provider *and* an uncatalogued signal provider. A config field with
  two spellings needs every reader updated, not just the builder.
- **Explicitly not done:** capability inference. A signal contributes context; it
  does not rewire the pipeline. `AnalyzeStage`'s keyword picking is still
  Search-Console-shaped (striking distance, positions 5–20), deliberately.

---

## ~~Step G — Stage and pipeline registration~~ — shipped

Kept only for the decisions a later step needs to know about; the full write-up is
in `docs/roadmap.md`, `docs/architecture.md` and `docs/configuration.md`.

- **The bar was met, and `examples/08-custom-pipeline/` is the proof** — a site
  audit whose three stages, report template and fixtures live entirely in the
  tenant folder, producing `kind: "site_audit"` with no change to `src/`. If a
  later step makes that example need a repo change to keep working, the step is
  wrong, not the example.
- **A stage's constructor is `(tools, config)`, or `(tools, config, options)`.**
  Deliberately *not* the provider contract — a stage is part of the pipeline, not
  something behind a Protocol. `ChooseChannelStage` and `SelfQaStage` grew an
  unused `tools` argument to make that one shape: a registry that remembers which
  stages want tools is a registry a tenant's stage cannot join.
  `plugin_loader.load_custom_class()` exists for this — resolution without
  instantiation — and anything else that constructs a tenant class differently
  should use it rather than growing a third form of `load_custom`.
- **A mode is gated on a declared requirement, never on a stage's name.**
  `parallel_by_source` requires the class to declare `fanout_over` (a Tools
  mapping) / `fanout_branch` / `fanout_join`; `concurrent_from_start` requires a
  following stage to join into. **A new mode owes a requirement that is checkable
  without knowing which stage it is.** The fan-out branch payload
  (`{"source_name", "context"}`) is the contract a branch class gets — changing it
  breaks every tenant fan-out stage, not just `discover`.
- **List order is the chain; there is no `after:` field.** It was in the original
  sketch and was dropped: this graph is a chain with two declared exceptions, so a
  field whose only legal values restate the list order is a second way to say one
  thing. An arbitrary DAG is a different feature and nothing has asked for one.
- **Structure is validated at config load; classes are resolved when the pipeline
  is built.** Importing a plugin executes a tenant's Python, and a server loading
  a config per request must not run the code of pipelines that request isn't
  using — the same line `load_dict`'s `validate` flag draws for I/O.
  `pipeline.py::validate_pipeline` is the pure half, called by the loader for
  *every* declared pipeline. Step I's provider selection owes the same split if it
  can load tenant code.
- **`channel` is `seo_content` vocabulary, not the framework's.**
  `PipelineSpec.channel_aware` is computed from the stages present, not from the
  agent type, so a tenant pipeline reusing `draft` still resolves a channel and an
  audit doesn't get a meaningless `"site_article"` written into its input and
  handed to every signal.
- **`agent_type` is a request field, not an `AgentInput` field** — it selects
  *which agent*, where the input describes *the job*. An unknown one is a
  `RunRequestError` from `AgentService` (nothing was attempted), and
  `AgentRunner._failed` now reports the configured agent type rather than the
  constant `"seo_content"`.
- **`discover_results` no longer leaks into the result.** LangGraph materializes
  every declared `AgentState` channel, so an `Annotated` key with a reducer is
  present as `[]` even in a graph with no fan-out node — it had been an
  undocumented top-level field in every run's JSON. **Any new `AgentState` working
  key must be popped in `_run` alongside `working`**, or it becomes part of the
  result plane by accident.

---

## ~~Step H — `seo_audit` agent type~~ — dropped

Covered by the `"custom"` mechanism plus Step G; see "Why H was dropped" at the
top of this file for the reasoning and for the two constraints kept from it (a
crawler's default bounds, and evidence-backed findings).

The sketch is left here only because it is the concrete thing to check Step G
against — if a tenant cannot build this on G without touching `src/`, G is not
finished:

- **Inputs**: a crawler and a sitemap reader as `signal_sources` entries with
  `"custom"` classes. The crawler fetches, follows internal links, and records
  status codes, titles, meta descriptions, headings, canonical tags, word counts
  and the internal link graph; the sitemap reader supplies the declared URL set
  to compare against what the crawl actually reaches.
- **Shape**: a tenant-declared pipeline ending in a verification stage of their
  own — the audit equivalent of `self_qa`, checking every finding references a
  real crawled URL — instead of `draft`/`self_qa`.
- **Output**: the frozen result schema with a new `kind`, no new field:

```jsonc
{
  "kind": "site_audit",
  "title": "Site audit for example.com",
  "content": "…readable recommendations, markdown…",
  "format": "markdown",
  "metadata": {
    "findings": [
      { "issue": "…", "severity": "high|medium|low", "urls": [],
        "evidence": "…", "recommendation": "…" }
    ],
    "pages_crawled": 128
  }
}
```

---

## Step I — State persistence

`InMemoryStateStore` already has the right shape (`save`/`load`/`delete`), and
`AgentRunner.arun()` already takes the store as an argument rather than
constructing one, so the seam mostly exists.

- **Promote the interface** to `state/base.py::StateStore`, with
  `InMemoryStateStore` unchanged as the default.
- **Select by provider**, as every other kind is selected: `state_provider` =
  `"memory"` | `"file"` | `"sqlite"` | `"redis"` | `"postgres"` | `"custom"`,
  connection details in that provider's `options`, `"custom"` through
  `load_custom`. A file/JSONL store is the useful first real one — zero
  infrastructure, survives the process.
- **Keep it in the run-context plane.** Not a `Tools` member, not an `AgentState`
  field. Stages never see it; only `AgentRunner` writes to it.

**Contract:**

- `save(self, run_id: str, state: dict) -> None`
- `load(self, run_id: str) -> dict | None`
- `delete(self, run_id: str) -> None`

Four constraints, cheap now and expensive later:

1. **State must stay JSON-serializable.** It happens to hold today (`Channel`
   subclasses `str`, everything else is plain data) and must keep holding: no
   live objects, clients, or file handles in `AgentState`.
2. **A store failure must not fail the run.** Today a `save()` exception
   propagates out of `_run` and is caught by the outermost handler, turning a
   *successful* run into `phase="failed"`. Already wrong for the in-memory store;
   routine with a network-backed one. Wrap it at the call site: degrade, record,
   continue.
3. **Snapshot frequency is a write amplifier.** `save()` runs after every
   super-step — against a remote store that is N round-trips on the critical
   path. The interface should permit batching or async writes, and the terminal
   save must always happen.
4. **Two different persistence concerns — do not conflate them.** This store
   holds *observable run snapshots*. LangGraph's `checkpointer=` on `compile()`
   is a separate mechanism for *resuming* an interrupted graph. This step covers
   the first only; adopt a checkpointer if and when resume is genuinely needed.

Retention and multi-writer coordination are out of scope: keyed by `run_id`,
last-write-wins, single process.

---

## ~~Step J — Template values: inline or from a file~~ — shipped

Kept only for the decisions a later step needs to know about; the full write-up is
in `docs/roadmap.md` and `docs/configuration.md`.

- **Template slots are found by naming convention, not by a list.** A
  `{"file": ...}` object is honored at any key ending in `_template`, plus every
  entry in `prompt_templates`. The alternative — enumerating today's nine options —
  would mean every future template option silently not supporting files until
  someone remembered to add it. **A step adding a template option must name it
  `*_template`**, and gets file support for free by doing so.
- **Everything else is denied, loudly.** A `{"file": ...}` object anywhere that
  isn't a template slot is rejected by path at load time, rather than reaching a
  provider as a dict where a string was expected. This is what makes the allow-list
  safe: a template option that doesn't follow the convention fails with a message
  saying so, instead of quietly reading nothing.
- **`arguments`, `api_headers`, `env` and `headers` are opaque maps** and are not
  walked at all — their keys are an MCP tool's parameter names, HTTP header names
  and environment variable names, so one of them being `file` is ordinary. Anything
  later that adds a config dict whose *keys* come from a tenant or a vendor owes an
  entry in `_OPAQUE_MAPS`; the symptom of forgetting is a spurious rejection, which
  is the failure mode this design chose on purpose.
- **Resolution happens on the raw dict, before `AgentConfig(**data)`.** That
  ordering is the whole reason `prompts.validate_template`, `TemplateValidator`
  and every provider needed no change: by the time a config object exists, every
  template is a string again. **A later step must not move template reading later**
  — validation would then need a second code path for file-loaded templates.
- **Containment is checked after resolving, not textually.** `..` and absolute
  paths are rejected on the string, but a symlink out of `templates/` only shows up
  once resolved. Step I's file-backed store writes into a tenant folder and owes
  the same treatment.
- **`config.template_sources` is loader-owned**, listed in
  `loader.py::LOADER_OWNED_FIELDS` so a config naming it fails as an unknown field.
  Any future field the loader writes rather than reads belongs there too — without
  it, `fields(AgentConfig)` makes every such field silently settable from JSON.

---
