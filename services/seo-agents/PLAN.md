# SEO Agent Plan — remaining work

Everything already shipped is documented in [`docs/roadmap.md`](docs/roadmap.md)
and removed from this file. What follows is only what is left to build.

## START HERE

**Next task: Step G (stage and pipeline registration).** Step F shipped, and so
did the search-performance rename (unplanned — see below); nothing is
half-finished and the tree is green.

```bash
cd services/seo-agents
pip install -r requirements.txt                   # includes pytest, ddgs and mcp
pytest                                            # 357 passing
python src/main.py list-tenants                   # the workspace
python src/main.py check-data --tenant echooers   # the real tenant, no API calls
python src/main.py run --userdata examples --tenant 06-mcp-discovery  # MCP, offline
python src/main.py preview-prompt --userdata examples --tenant 07-signal-inputs  # signals, offline
```

### Remaining steps, in order

| # | Step | Why in this order |
|---|---|---|
| ~~D~~ | ~~`SearchClient` (pluggable grounding)~~ | **Done** — see docs/roadmap.md. DuckDuckGo is the default; the resolution order is search → the model's own grounding → ungrounded. |
| ~~E~~ | ~~Built-in `provider: "mcp"` discovery source~~ | **Done** — see docs/roadmap.md. Official `mcp` SDK, stdio + streamable HTTP, mapped by `items_template`. |
| ~~F~~ | ~~Signal inputs as a named list~~ | **Done** — see docs/roadmap.md. `signal_sources`, collected concurrently, reaching the prompt as `signals` keyed by name. A crawler or a sitemap reader is a signal like any other. |
| **G** | **Stage and pipeline registration** | Next. What a tenant-specific stage needs — and, with `"custom"`, what makes a site audit a tenant's build rather than a step here. |
| ~~H~~ | ~~`seo_audit` agent type~~ | **Dropped** — the `"custom"` mechanism plus G covers it. See below. |
| I | State persistence | Becomes load-bearing the moment a queue exists. |
| J | Template values: inline or from a file | Independent of G and I; the smallest of the three and doable any time. |

Letters are order-of-work, nothing more — they are not renumbered when a step is
dropped, since docs and commits already refer to them.

### Why H was dropped

A built-in `seo_audit` agent type would have been this repo taking a position on
what an audit *is* — which findings matter, how they're ranked, what a crawler
does. That's the position a tenant should hold, and after F and G they can:

- **Its inputs are signals.** A crawler and a sitemap reader are
  `signal_sources` entries with a `"custom"` class, exactly like a rank tracker.
  Nothing about them needs a new provider kind or a new `Tools` field.
- **Its shape is a pipeline.** Once G lands, a tenant declares their own stages
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
  site is not an acceptable default. This belongs in `docs/extending.md` next to
  the signal walkthrough, whether or not anyone builds the audit.
- **Findings must be evidence-backed** — each carrying the URLs and rows it came
  from, the same principle that makes a grounded link trustworthy. An audit that
  asserts problems it cannot point at is worse than no audit.

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
   Judge G by whether an audit could be built on it without touching `src/`.

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
  deliverable uses a new `kind`, not a new top-level field.
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

## Step G — Stage and pipeline registration

`_STAGE_FACTORIES` in `agent/graph/pipeline.py` is a fixed dict of six stages,
and `_default_spec` is the only spec there is. Both have to open up, and with H
dropped this is now the step that carries the whole "the deliverable is not
always a draft" framing: **the test of this step is whether a tenant could build
a site audit on it without touching `src/`.**

- **Config-declared stages**: `{"name", "class", "mode", "after"}`, resolved
  through `load_custom`.
- **A spec per agent type**, not one global default: `agent_type -> PipelineSpec`,
  with `"seo_content"` producing exactly today's three shapes.
- **Generalize the mode/stage-name coupling.** `build_graph` raises unless
  `parallel_by_source` is literally `"discover"` and `concurrent_from_start` is
  literally `"analyze_context"`. Those checks must key off a stage's declared
  requirements, not its name, before any registered stage can use those modes.
- Preserve the existing `discover → choose_channel → analyze → draft → self_qa`
  flow and all three current graph shapes for default configs.
- **A tenant-declared agent type must be selectable at run time**, since that is
  what makes a second deliverable reachable at all: `--agent <name>` on the CLI,
  the same field on the service request, landing on `AgentState.agent_type` — the
  seam that field was added for ("constant `seo_content`; a seam for when other
  agent types exist").
- **A custom stage writes `output` with its own `kind`**, not a new top-level
  field — the result plane stays frozen (`docs/output-schema.md`). Worth an
  example: a stage returning `{"kind": "site_audit", …}` proves the schema takes
  a non-draft deliverable, which is the claim H was going to test.
- **Explicitly not doing:** a capability-inference engine that derives graph shape
  from tool metadata. Revisit only with a real use case.

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

## Step J — Template values: inline or from a file

**The problem.** Every template in this system is a JSON string, so a real one is
a single line with escaped newlines and escaped quotes. `userdata/echooers/`'s
`highlights_template` is one 300-character line; `examples/07-signal-inputs/`'s
`prompt_templates.site_article` is worse. They are unreadable, undiffable, and
un-editable in anything that understands Jinja2 — and prompt wording is the thing
a tenant edits most.

The workspace already has the answer half-built: `templates/` is a documented
tenant folder (`agent/config/workspace.py`, "reserved; not read yet") and
`list-tenants` already counts what's in it. This step makes it real.

**Decided: an object, `{"file": "..."}`, accepted anywhere a template string is.**

```jsonc
{
  "prompt_templates": {
    "site_article": { "file": "site_article.j2" }
  },
  "analytics_options": {
    "summary_template":    { "file": "analytics_summary.j2" },
    "highlights_template": { "file": "analytics_highlights.json.j2" }
  }
}
```

A plain string keeps working and means exactly what it does today. The two forms
are the *only* two.

Why the object rather than the two obvious alternatives:

- **Not a sigil** (`"@templates/x.j2"`, `"file:x.j2"`). Every sigil is a value a
  template could legitimately start with, so it needs an escape hatch, and the
  escape hatch is the bug — a tenant whose summary genuinely starts with `@` gets
  a confusing file-not-found instead of their text. `isinstance(value, str)`
  versus `dict` has no ambiguity to resolve.
- **Not a sibling field** (`summary_template_file`). Templates appear in at least
  nine places today (`prompt_templates.<channel>`, the analytics/traffic/signal
  `*_template` options, the discovery source's `prompt_template`/
  `query_prompt_template`/`items_template`, MCP `arguments` values). Doubling
  each one doubles the config surface and the docs, and every new template option
  after this would have to remember to add its twin.

**One rule, one resolver.** *Anywhere* a template string is accepted, so is
`{"file": …}`. A helper in `agent/config/` resolves it; nothing else learns about
it. If a new provider adds a template option, it gets this for free — which is
the whole reason to do it uniformly rather than per-option.

**Resolution and containment:**

- Relative to the tenant's `templates/` folder (`TenantWorkspace.templates_dir`),
  and nowhere else. Not `config_base_dir`, not the CWD, not an absolute path.
- **Reject `..`, absolute paths, and symlinks escaping the folder** — the same
  containment `validate_name` gives a tenant name and `plugin_loader` gives a
  plugin, and for the same reason: in a server this value arrives from a request
  or a database row, so "read any file the process can read" is the failure mode
  to design out, not to discover.
- A config with no workspace (built in code, as tests do) has no `templates/`
  folder, so `{"file": …}` is a clear error there rather than a CWD-relative read.
- Missing or unreadable file → a config error naming the path and listing what
  *is* in the folder, the treatment `plugin_loader` already gives a missing
  plugin. "No such file" alone sends someone looking in the wrong directory.

**Read at config-load time, not per render.** By the time `AgentConfig` exists,
every template is a string again. This matters for three reasons:

1. `prompts.validate_template` and `TemplateValidator` keep working untouched —
   save-time validation still catches a bad template, including one loaded from a
   file, with no new code path.
2. A run makes no filesystem call per prompt, and a server resolving a tenant
   config per request pays the read once.
3. **`AgentConfig` stays plain data** — no paths to re-resolve, no file handles.
   Step I's "state must stay JSON-serializable" has the same shape, and a config
   holding a lazy file reference would quietly break anyone caching one.

The tradeoff is honest and worth stating in the docs: editing a template file
does not affect a config already loaded. For the CLI that is invisible (one run,
one load); for a long-lived server it means the same cache invalidation any
config change already needs.

**Also:** `check-data` should report the templates it loaded and from where —
that command exists to answer "will this config work", and "which file is this
prompt actually coming from" is now part of that question. `list-tenants`'
existing `templates` column stops being decorative.

**Explicitly not doing:** template inheritance or `{% include %}` across files
(Jinja2 can, but it needs a loader with its own containment rules — revisit if
anyone actually wants it), a template registry shared between tenants, and any
change to what the templates *render against*. This step is about where the text
comes from, nothing else.
