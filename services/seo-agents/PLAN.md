# SEO Agent Plan — remaining work

Everything already shipped is documented in [`docs/roadmap.md`](docs/roadmap.md)
and removed from this file. What follows is only what is left to build.

## START HERE

**Next task: Step F (signal inputs as a named list).** Step E shipped; nothing is
half-finished and the tree is green.

```bash
cd services/seo-agents
pip install -r requirements.txt                   # includes pytest, ddgs and mcp
pytest                                            # 279 passing
python src/main.py list-tenants                   # the workspace
python src/main.py check-data --tenant echooers   # the real tenant, no API calls
python src/main.py run --userdata examples --tenant 06-mcp-discovery  # MCP, offline
```

### Remaining steps, in order

| # | Step | Why in this order |
|---|---|---|
| ~~D~~ | ~~`SearchClient` (pluggable grounding)~~ | **Done** — see docs/roadmap.md. DuckDuckGo is the default; the resolution order is search → the model's own grounding → ungrounded. |
| ~~E~~ | ~~Built-in `provider: "mcp"` discovery source~~ | **Done** — see docs/roadmap.md. Official `mcp` SDK, stdio + streamable HTTP, mapped by `items_template`. |
| **F** | **Signal inputs as a named list** | Next. The generalization every new kind of input needs — including the crawler and sitemap Step H runs on. |
| G | Stage and pipeline registration | Prerequisite for a second agent type; also what a tenant-specific stage needs. |
| H | `seo_audit` agent type | Sits on F (its signals) and G (its pipeline). |
| I | State persistence | Last. Becomes load-bearing the moment a queue exists. |

F and H are new; the old "stage registration" and "state persistence" steps are
now G and I. Letters are order-of-work, nothing more.

### What this system is, so the remaining steps don't narrow it

Two framings the code partly contradicts today, and that F and H exist to fix.
They are also the answer to "why is there a plan step for this at all":

1. **Inputs are *signals*, and the vendors we ship are defaults, not the model.**
   Google Search Console, Cloudflare and product analytics are three signals that
   get someone to a real run quickly. A trends feed, a rank tracker, a keyword
   API, a crawler, a competitor watcher, an MCP server are the same kind of
   thing. Nobody is bound to our three, and adding a fourth must be config, not a
   fork.
2. **The deliverable is not always a draft.** Writing an article is one way to
   grow a site; telling someone what to fix on the site they already have is
   another. `agent_goal` is about growth, and the run decides what to produce.

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
  named destination per field in `agent/config/loader.py::MOVED_FIELDS`.
- **`AgentState` and the returned JSON hold** — `docs/output-schema.md`. A new
  deliverable uses a new `kind`, not a new top-level field.
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
- **`userdata/echooers/` is the real tenant** — real Gemini/GSC/Cloudflare
  credentials, gitignored. `check-data --tenant echooers` validates it without
  spending an API call; `run` spends a real one, so don't run it casually.
- **A step is not done until its config fields are in `docs/configuration.md`
  and its status is in `docs/roadmap.md`**, with tests alongside.

---

## Architecture: the three planes

The rule that keeps the remaining steps non-invasive.

1. **Tools plane** — what stages *call*: the LLM and every signal input (GSC,
   analytics, traffic, discovery sources, and whatever Step F adds). Lives in
   `Tools` (`agent/graph/tools.py`), built by `ToolsManager`. Stages depend only
   on the Protocols in `tools/base.py`.
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

## Step F — Signal inputs as a named list

**The problem.** `Tools` has three fixed slots — `gsc`, `traffic`, `analytics` —
and `AnalyzeStage` reaches for them by name (`self.tools.gsc.search_analytics(…)`).
So "swap Cloudflare for Plausible" works, but "add a trends feed" does not: there
is no slot for it, and adding one means editing this repo. That contradicts the
first framing above, and it is the thing standing between the agent and any new
kind of input — including the crawler and sitemap Step H needs.

`discovery_sources` is already the right shape. Signals get the same one.

```jsonc
"signal_sources": [
  { "name": "gsc",     "provider": "google",     "options": { } },
  { "name": "traffic", "provider": "cloudflare", "options": { } },
  { "name": "trends",  "provider": "custom", "class": "trends:Client", "options": { } }
]
```

**Decided: the built-in slots stay.** `gsc_provider` / `traffic_provider` /
`analytics_provider` and their `*_options` keep working and are read as three
implicit entries named `gsc`/`traffic`/`analytics`. `Tools.gsc/.traffic/
.analytics` remain as *views* onto the signal dict, so `AnalyzeStage`, every
existing tenant config, and every tenant's `"custom"` class are untouched. **No
second breaking config change** — Step C used the one this system gets.

**Contract for a generic signal**, mirroring how deliberately free-form
`AppAnalyticsClient` and `SiteTrafficClient` already are:

- `collect(self, context: dict) -> dict`, returning
  `{"summary": str, "facts": dict, "items": list[dict]}` — all optional but
  `summary`. `summary` is prose the prompt uses as-is; `facts`/`items` are for a
  stage or a template that knows what it asked for.
- `context` carries what the run knows so far (seed keyword, site URL, channel),
  same as `discover(context)`.

Then:

- Signals land in `Tools.signals[name]`, and a generic context bag reaches the
  prompt keyed by name, so a stage never has to learn a new signal's name.
- Each signal is independently degrade-don't-abort, like every existing tool
  call, and is wrapped by the observing proxies for verbose mode.
- Collection is concurrent (`asyncio.gather`), which is most of what async
  execution bought and what makes N signals affordable.

**Explicitly not doing:** a capability-inference engine that decides graph shape
from signal metadata. A signal contributes context; it does not rewire the
pipeline.

Watch out for: `AnalyzeStage`'s keyword picking is Search-Console-shaped
(striking distance, positions 5–20). Generalizing *that* is not part of this
step — a signal that isn't GSC contributes context, and the fallback chain
already handles GSC being absent.

---

## Step G — Stage and pipeline registration

`_STAGE_FACTORIES` in `agent/graph/pipeline.py` is a fixed dict of six stages,
and `_default_spec` is the only spec there is. A second agent type (Step H) needs
both to open up.

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
- **Explicitly not doing:** a capability-inference engine that derives graph shape
  from tool metadata. Revisit only with a real use case.

---

## Step H — `seo_audit` agent type

**Decided: a separate agent type, not a fourth channel.** `AgentState.agent_type`
exists as exactly this seam ("constant `seo_content`; a seam for when other agent
types exist"). The two agents share `Tools`, `AgentConfig`, `AgentService`, the
sinks, verbose mode and the output schema; they do not share a pipeline, and
neither decides on the other's behalf. The tenant picks:

```bash
python src/main.py run --tenant acme --agent seo_audit
```

**What it produces.** Not a draft — a prioritized list of what to fix on a site
that already exists: thin or duplicate pages, missing or weak metadata, broken or
missing internal links, orphan pages, cannibalizing URLs, and pages ranking 11–20
that deserve work rather than a new article.

**It fits the frozen result shape** — that is the point of picking a new `kind`
rather than a new field:

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

**New signals it needs** (Step F, `provider: "crawler"` / `"sitemap"`):

- a **crawler** — fetch, follow internal links, record status codes, titles, meta
  descriptions, headings, canonical tags, word counts, internal link graph.
- a **sitemap reader** — the declared URL set, to compare against what the crawl
  actually reaches (orphans, and URLs in the sitemap that 404).

**A crawler is the one tool here that can hurt someone else's server**, so it is
bounded by default, not by configuration: obey `robots.txt`, rate-limit, cap
pages and depth and total time, send an identifying user agent, and never follow
off-site links. A default that could hammer a site is not an acceptable default.

**Findings must be evidence-backed** — each carries the URLs and the signal rows
it came from, the same principle that makes a grounded link trustworthy. An audit
that asserts problems it cannot point at is worse than no audit.

**Also needed:** its own prompt template and its own verification stage (the audit
equivalent of `self_qa` — every finding must reference at least one real crawled
URL), plus `input.site_url` and crawl bounds on the input.

**Explicitly not doing:** a full technical-SEO suite. No JS rendering, no
Lighthouse/Core Web Vitals, no backlink analysis in the first version. Crawl,
sitemap, and the signals already configured are enough to say something useful.

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
