# Roadmap

Today SEO-OS is a runtime you drive from a terminal: a tenant is a folder, a run
is a command, and the result is JSON on stdout. It runs on a laptop, on one host
under Compose, and on a cluster as a Job.

**The next release turns that into something other software can call.** An LLM
first — the agent as an MCP server, which is the shortest path from "a person
types a command" to "a model does the work". Then an HTTP API, a cluster that
dispatches many runs for many tenants, the tracking to know what they cost, and a
UI to watch one happen.

Seven steps, in order, and the order is the argument. Each one says what it owns,
what it deliberately doesn't, and which decisions have already been taken so they
aren't relitigated when the code gets written.

| # | What | Where it lands |
|---|---|---|
| [1](#1-mcp-the-whole-thing-callable-by-an-llm) | **An MCP server** — an LLM drives the agent directly | [`services/seo-agents/`](../services/seo-agents/), one more command |
| [2](#2-the-gateway-the-api) | **The API**, FastAPI | [`services/gateway/`](../services/gateway/) |
| [3](#3-dispatch-and-the-data-flow-between-gateway-and-workers) | **Dispatch** — how a request becomes a running pod, and what flows back | the gateway, the chart |
| [4](#4-multi-tenant-from-a-folder-to-an-account) | **Multi-tenancy** — identity, isolation, quotas | the gateway |
| [5](#5-tracking-and-observability) | **Tracking and observability** — logs, traces, metrics, cost | everywhere |
| [6](#6-the-chart-past-a-single-job) | **The chart past a single Job** — every service, and how they talk | [`helm-charts/seo-os/`](../helm-charts/seo-os/) |
| [7](#7-the-frontend) | **The UI**, Next.js | [`services/frontend/`](../services/frontend/) |
| [—](#not-a-step-release-and-supply-chain) | Release and supply chain | independent of the chain |

## The order, and why it's this order

Steps 2–7 are a dependency chain read backwards. The frontend is the visible one
and it is last, because a UI with nothing to call is a mock. The gateway is what
everything else is either a property of (dispatch, tenancy, observability) or a
way to run (the chart).

**Step 1 is not part of that chain, which is exactly why it goes first.** It is
the cheapest possible way to put an LLM in front of this system — no HTTP surface,
no auth, no database, no cluster, and no new dependency — and it tests the claim
the other six are built on before any of them is written.

Multi-tenancy and observability are steps rather than adjectives on purpose: both
are cheap to design in and expensive to retrofit, and both are the kind of work
that never happens if left as "we'll add it later". They sit before the frontend
so the UI is built against a gateway that already knows who is asking and already
records what it did.

Where a step leans on something that already works, it links to it rather than
re-describing it: what SEO-OS does today is documented in [docs/](README.md) and
in each service's own pages.

## What these steps ask of the runtime

The runtime is finished for now — nothing below is a feature inside the agent. The
claim each step tests is that it needs no change in `services/seo-agents/`, and so
far **exactly two** have surfaced. Both are additive, both are shaped like things
that already exist there, and both are named where they arise:

- an **MCP server command**, which is a CLI command module like the seven that
  are already there ([step 1](#1-mcp-the-whole-thing-callable-by-an-llm));
- an **event-stream reporter**, which is a provider like every other
  ([step 3](#3-dispatch-and-the-data-flow-between-gateway-and-workers)).

Everything else is around the runtime, and the shape of it is one sentence: a run
has to become something you can request, dispatch, watch, pay for and approve —
without the runtime learning what any of that is.

## Two language decisions, taken up front

They belong here because four of the seven steps assume them.

**The backend stays in Python, and FastAPI is the first choice.** The gateway's
core is one line — `await AgentService.aexecute(request)` — over a runtime that is
already async end to end and already Pydantic-shaped. A Python gateway *imports*
that; anything else re-serializes the entire config and result contract across a
process boundary and then maintains a second implementation of the config model in
another language, which is a lot of work to buy nothing. FastAPI specifically, for
four things this system actually needs: ASGI, so the gateway waits on Redis,
Postgres and the Kubernetes API on one loop the same way a run waits on its tools;
Starlette's streaming responses, which is what SSE and MCP-over-HTTP both are;
Pydantic v2 models that generate the OpenAPI document [step 7](#7-the-frontend)
types itself from, so the contract is derived rather than described twice; and
dependency injection, which is where per-request tenant resolution and auth
belong. The known cost is CPU-bound work blocking the loop, and there is none here
— every expensive thing this service does happens in another process.

**The frontend is Next.js.** The product *is* the live view of a run, which is a
client-side streaming problem, so a server-templated UI in Python would have meant
writing that half in JavaScript anyway and getting one language for the other
half. App Router gives the split this actually wants: run history and agent config
render server-side against the gateway's internal Service address, and the live
run subscribes to SSE from the browser through the Ingress. TypeScript types come
out of the gateway's OpenAPI schema, generated and checked in CI, so a contract
change breaks the build instead of a page. And it is one more `Dockerfile` and one
more line in the image matrix, which is the whole reason that matrix exists.

### 1. MCP: the whole thing callable by an LLM

**Yes, and it is close to free**, which is why it goes first rather than fifth.
Three facts decide this step before any design does:

- **`mcp` is already a dependency.** This repo is already an MCP *client* — the
  `provider: "mcp"` discovery source connects to a server over stdio or streamable
  HTTP using the official SDK. Serving the protocol adds no package: the SDK,
  `starlette`, `uvicorn` and `sse-starlette` are all in `requirements.txt` today.
- **`AgentService` is already the channel-agnostic entry point**, built so the CLI
  is one adapter over it rather than the only way in.
- **Every CLI command is already one self-contained module** with a `register(app)`
  hook. The list of tools an LLM wants is very nearly the list of commands that
  exists.

**Where it lives: in the runtime, as one more command.** `python src/main.py mcp`
starts a **stdio** server; the tool definitions live in their own module that the
gateway later imports and mounts at `/mcp` as **streamable HTTP**. One definition
of every tool, two transports, no duplicated schema.

The tension is worth stating rather than glossing: transport belongs above the
runtime, and MCP is a transport. What makes stdio the exception is that it has no
port, no deployment, no lifetime of its own and no second process — it is a
program the client spawns, which is precisely what the CLI already is. The moment
it wants a port it becomes the gateway's, where transports belong.

**The tools, deliberately few.** Every tool description sits in the caller's
context on every single call, so this list is a budget, not a catalogue:

| Tool | Over | What it costs |
|---|---|---|
| `list_agents` | `list-tenants` | nothing |
| `describe_agent` | the config plus what's wired into it | nothing |
| `check_agent` | `check-data` — builds every provider, resolves every plugin | nothing: **no API call** |
| `show_pipeline` | the `PipelineSpec` | nothing, and no credentials |
| `preview_prompt` | `preview-prompt` | nothing |
| `start_run` | `AgentService.aexecute()` | **money** |
| `get_run` | the state store | nothing |

`list_runs` and `approve_draft` wait for the gateway's database
([steps 2](#2-the-gateway-the-api) and [4](#4-multi-tenant-from-a-folder-to-an-account)).
Six of the seven cost nothing to call, which is the property that makes this
pleasant to hand to a model: it can look before it spends.

**Six design problems, and the decisions.**

1. **A run takes about ninety seconds; a tool call is request/response.** So
   `start_run` returns a `run_id` immediately and `get_run` polls, with `wait:
   true` for short or mocked runs. That is the same two-modes decision the
   gateway's HTTP surface has to make, and it gets made once, here, and shared. For
   a waiting caller, MCP **progress notifications** carry stage and tool-call
   events — which are the events the reporter already produces, and the same stream
   [step 3](#3-dispatch-and-the-data-flow-between-gateway-and-workers) needs for
   SSE. One event source, three consumers.
2. **Structured output, not a wall of JSON.** MCP tools return structured content
   against an output schema, and the result schema is already frozen. So: the
   result as structured content, and as *text* a short summary — phase, channel,
   word count, what degraded. Returning a 40 KB result as text spends the caller's
   context, which is the one resource this interface is here to be careful with.
3. **Resources and prompts, not only tools.** Agents and finished runs as MCP
   *resources* (`seo-os://agents/<name>`, `seo-os://runs/<id>`) so a model can read
   without spending a tool call, plus two or three prompts ("draft an article for
   this agent about X", "audit this site"). Tools alone make this usable from an
   agent loop; prompts and resources are what make it usable from a chat client.
4. **Consent, and the fact that one of these tools spends money.** Tool
   annotations set honestly — read-only hints on the six that are, `openWorldHint`
   on `start_run`, and a description that says plainly that it costs — because
   that is what clients surface when they ask a human to approve a call. Plus a
   server-side guard that does not depend on the client behaving: a `dry_run` that
   forces mock providers, and a per-session ceiling on runs started. An LLM that
   can start runs in a loop is a budget incident, not a hypothesis.
5. **Tenancy is the transport's problem, and it sets the order.** Over stdio the
   client is the local user, the workspace is whatever `--userdata` points at, and
   the containment that already exists (validated tenant names, per-tenant plugin
   packages, every config path resolved inside the folder) is the whole security
   model — the same trust as the CLI, because it *is* the CLI's trust boundary.
   Over HTTP, "which agent may this caller run?" has no answer until
   [step 4](#4-multi-tenant-from-a-folder-to-an-account) exists. **So stdio ships
   now and MCP-over-HTTP does not ship before auth**, written down here because
   enabling that transport is one line in the SDK and would otherwise happen by
   accident.
6. **It closes the loop in both directions.** This system consumes MCP servers as
   discovery sources; serving one means an SEO-OS can be another's tool. That is
   genuinely useful and genuinely a footgun, so `start_run` carries a depth guard —
   a run whose discovery source is an SEO-OS whose discovery source is an SEO-OS
   should stop at a documented depth rather than at a credit card limit.

**Why it's first.** It is the shortest path from here to "an LLM can drive this"
— no HTTP surface, no auth, no database, no cluster, no UI, no new dependency —
and it is usable the day it lands, from any MCP client, against a real tenant. It
also **tests the claim the next four steps rest on**: that `AgentService` is
genuinely channel-agnostic and a second adapter needs no changes underneath it. If
that is wrong, learning it from a small command is enormously cheaper than
learning it from FastAPI plus Next.js.

Delivered with it: a `make mcp` target, the `claude mcp add` line, and the
`docker run -i` form (the image already exists and already mounts `/userdata`), so
the three ways to run it are documented the way the examples already document
three ways to run the CLI.

### 2. The gateway, the API

`services/gateway/` — a FastAPI application over
[`AgentService.aexecute()`](../services/seo-agents/src/agent/service.py). The CLI
is one adapter over it, [step 1](#1-mcp-the-whole-thing-callable-by-an-llm) is the
second, and this is the third — the one that brings a port, a database and a
lifetime. It arrives with its own `Dockerfile` (one line in the image matrix), its
own `pytest` suite in the same gated workflow, and its own `Makefile` targets.

**The HTTP surface**, a first cut:

| | |
|---|---|
| `POST /v1/runs` | Submit a run. `202` with a `run_id`, or `200` with the finished result when the caller asks to wait |
| `GET /v1/runs/{run_id}` | The run, live or finished — the [frozen output schema](../services/seo-agents/docs/output-schema.md), not a shape invented here |
| `GET /v1/runs` | List, filtered by tenant, agent type, phase, time |
| `GET /v1/runs/{run_id}/events` | SSE: stages and tool calls as they happen |
| `POST /v1/runs/{run_id}/cancel` | See [step 3](#3-dispatch-and-the-data-flow-between-gateway-and-workers) on what cancel means |
| `GET /v1/agents/{name}/check` | `check-data` over HTTP — does this config work, without spending an API call |
| `GET /v1/agents/{name}/graph` | The `PipelineSpec`, which needs no credentials, for the frontend to draw |
| `POST /v1/runs/{run_id}/approve` | The approval loop, once there's somewhere to hold a decision |
| `/mcp` | [Step 1](#1-mcp-the-whole-thing-callable-by-an-llm)'s tools over streamable HTTP — the same module, and not before auth |
| `/healthz`, `/readyz`, `/metrics` | What the chart's probes and [step 5](#5-tracking-and-observability) need |

**The status-code mapping is the one piece of real design in the translation: a
failed run is a successful request.** A run that comes back `phase: "failed"` is a
`200` carrying a failed run; only an unrunnable request — unknown tenant,
unloadable config, a sink with no URL, which the runtime already distinguishes by
raising `RunRequestError` — is a `4xx`. Collapsing those two into `500` is the
mistake this note exists to prevent, and it is far easier to prevent now than to
un-ship once clients depend on it.

**Two execution modes, one response shape**, and the same pair
[step 1](#1-mcp-the-whole-thing-callable-by-an-llm) settles: a caller who wants to
block gets the result inline, a caller who doesn't gets a `run_id`. The difference
must not become two code paths producing two different documents.

**What it owns that the runtime deliberately refuses to grow**: the transport,
auth ([step 4](#4-multi-tenant-from-a-folder-to-an-account)), the queue
([step 3](#3-dispatch-and-the-data-flow-between-gateway-and-workers)), the
schedule, and the approval loop — the runtime drafts and never publishes, and
turning "a human approves every word" into review-and-ship needs somewhere to hold
state between the draft and the decision. That somewhere is a **Postgres this
service owns**: approvals, the durable run record, the tenant registry. Redis stays
what it already is — the live state of a run in flight — and is explicitly not the
system of record.

Why none of this is in the runtime: a queue that can't be swapped out is worse than
no queue, and folding a web framework in would make every tenant who only wants a
CLI carry one.

### 3. Dispatch, and the data flow between gateway and workers

The gateway accepts a run. Something else has to execute it, and everything
interesting about this system's plumbing lives in the gap between those two
sentences. Today there is no gap: one process, one run, a result on stdout.

**Who executes a run.** Two candidates, and the answer is that it is the wrong
question to answer only once:

- **A Job per run**, created by the gateway through the Kubernetes API with RBAC
  on `batch/v1`. Each run gets its own pod, its own limits and its own blast radius
  — which matters a great deal, because a tenant's `plugins/` is Python this
  process imports and executes.
- **A worker Deployment** pulling run ids from Redis and running them in-process.
  No RBAC, no per-run pod startup, and it works where a cluster isn't.

**Both, behind one `RunDispatcher` interface in the gateway** — `kubernetes` and
`inproc`, selected by config exactly like every provider in the runtime. This is
not fence-sitting: this repo ships a Compose deployment and people develop on
laptops, and a gateway that can only dispatch into a cluster is undeployable on
both. `kubernetes` is the chart's default and the recommended one; `inproc` is
Compose's and development's, documented as giving up isolation — which is why
[step 4](#4-multi-tenant-from-a-folder-to-an-account) says a tenant with plugins
must not be dispatched that way.

**The four channels between the two halves**, each of which is a decision:

1. **The request in.** A Job's args stay the CLI's, and the run id is the *only*
   thing passed — the request itself is a record the worker reads. The alternative,
   a Secret or ConfigMap rendered per run, churns cluster objects at run rate and
   walks into the 1 MiB limit for no benefit.
2. **Progress out**, and this is the one that needs new code. `state_provider:
   "redis"` already makes a snapshot readable from another process after every
   super-step, which is the seam that was built for this — but a snapshot per stage
   is coarse, and the thing worth watching is the tool call in flight. The runtime
   already produces exactly those events, for `-vv` and for `AgentService`'s
   streaming callback; in a Job that callback is in the wrong pod. So: **an
   event-stream reporter (Redis streams), configured like any other provider**,
   letting the gateway tail a run instead of polling snapshots and inventing the
   granularity it lost. That is one of the two changes this roadmap asks of the
   runtime, it adds a provider rather than touching a stage, and
   [step 1](#1-mcp-the-whole-thing-callable-by-an-llm)'s progress notifications are
   its third consumer.
3. **The result out.** `output_sinks: webhook` posting the finished result to the
   gateway, authenticated with a per-run token, idempotent by `run_id` — *plus* the
   state store's terminal snapshot as the backstop, because a webhook missed while
   the gateway restarted must not be a lost run. Belt and braces is cheap here; a
   run costs real money and cannot be replayed for free.
4. **Control.** Cancel is `delete job` in one implementation and a cancellation
   token in the other, and both must land the run as a *recorded* cancellation
   rather than a run that stops appearing. Bounding is already solved twice over:
   `run_timeout_seconds` inside, `activeDeadlineSeconds` outside.

**Backpressure, because a run spends money.** The queue needs a per-tenant
concurrency cap and a global one, and queue depth is the metric that says whether
the cluster or the budget is the constraint. `backoffLimit: 0` stays: a failed run
is a recorded run, not a pod to run again and bill twice.

### 4. Multi-tenant, from a folder to an account

A tenant is a folder, and that isolation is real: names validated rather than
sanitized precisely because in a server they arrive from a request, plugins loaded
under a per-tenant synthetic package so two tenants with the same module name
can't shadow each other, every config path resolved inside the folder. What is
missing is everything above the filesystem:

- **Identity.** A request carries a subject; a subject belongs to an org; an org
  owns tenants. API keys first, because that is what a script and an MCP client
  need, OIDC after — and roles that distinguish who may *run* from who may
  *approve*, since the approval loop is meaningless if those are one permission.
- **Where the tenant of record lives.** Today: a laptop folder, or a Secret in a
  release. Neither survives fifty tenants. The gateway owns a tenant registry in
  its Postgres and **materializes `/userdata/<name>` per run** from it, which keeps
  the on-disk contract the runtime already has, so the runtime still learns nothing.
  The chart's existing mechanisms stay available for the cases they suit — an RWX
  volume, an init container syncing from object storage, a Secret mounted as the
  folder.
- **Secrets, and the `${VAR}` question reopening.** The runtime does no
  interpolation, so `tenant.json` *is* the credential and the whole file is a
  Secret. Fine for one tenant, wrong for fifty: rotating one key means rewriting
  configs. This is where env-var interpolation gets decided rather than deferred
  again, alongside external-secrets and per-tenant secret references.
- **Three isolation levels, named honestly**, because "multi-tenant" hides which
  one you actually bought: *process* (an in-proc worker — none worth the word,
  since tenant plugin code shares an interpreter, which is why a tenant with
  plugins must be dispatched as a Job), *pod* (a Job per run — the default), and
  *namespace* (per tenant, with its own quota, RBAC and NetworkPolicy — the escape
  hatch for a tenant whose code you would rather not reason about).
- **Quotas.** Per-tenant concurrency, rate limits, and a monthly spend cap, which
  depends on the cost accounting in [step 5](#5-tracking-and-observability) — an
  agent that discovers its own work can spend money without anyone asking it to,
  and an LLM holding [step 1](#1-mcp-the-whole-thing-callable-by-an-llm)'s
  `start_run` tool can do it faster.
- **What this does to the chart.** One tenant per release stops being how tenants
  are declared as soon as a registry exists. The chart keeps declaring them only
  for the standalone Job/CronJob case, which stays supported for anyone who wants
  the runtime and no gateway at all.

### 5. Tracking and observability

The runtime is already unusually honest per run: `-v`/`-vv` reports every stage and
tool call with timings and outcomes, failures the pipeline deliberately swallows
surface as `tool_errors`, and every opportunity records whether grounding actually
happened and why it didn't. All of that is per run, on stderr, and invisible in
aggregate. Three different things follow, and conflating them is the usual mistake:

- **Logs.** Structured JSON on stdout from every service, one event per stage and
  per tool call, with `run_id`, `tenant` and `agent_type` on every line. The events
  already exist — this is a formatter and a sink, not new instrumentation.
- **Traces.** OpenTelemetry, one trace per run, a span per stage and per tool call.
  The interesting requirement is that the trace **crosses the dispatch boundary**:
  trace context travels with the run record so a Job's spans hang under the request
  that asked for it, or you have two disconnected halves of every run. The wrapping
  that makes this nearly free already exists — the proxies that wrap every client
  and every stage — so an OTel exporter is another reporter and no stage changes.
- **Metrics.** Prometheus from the gateway (requests, queue depth, dispatch
  latency, runs by phase) and per run (duration, tool errors by provider, grounding
  fallbacks, tokens and cost). Run pods are short-lived, so scraping them doesn't
  work: the gateway records run metrics as it ingests results, which avoids a
  pushgateway entirely.

**Cost accounting is a feature, not a metric.** Tokens in and out per LLM call,
per run, per tenant, with an estimated cost — because
[step 4](#4-multi-tenant-from-a-folder-to-an-account)'s spend caps are
unimplementable without it, and because "why was last month expensive" is a
question every operator of this eventually asks. It likely wants `LLMResponse` to
carry usage, which is additive.

**Where it goes**: an OTLP endpoint and a scrape annotation, both chart values.
Nothing in this repo runs a collector — that is the cluster's job, and pretending
otherwise means shipping a second-rate one.

**And the boundary that keeps this shippable**: prompts and drafts are tenant
content. `-vv` payloads stay in the pod unless someone opts in, secret redaction by
field name already exists and must apply to every exporter, and a reporter error
can never fail a run — a property the current implementation has and any new
exporter has to keep.

### 6. The chart past a single Job

[`helm-charts/seo-os/`](../helm-charts/seo-os/) renders a `Secret` and a `Job`, and
does that well: no credentials, no storage class, no ingress required. It deploys
exactly one workload type, and the steps above add three more.

**The deployments to add**, in the order they become real:

- **The gateway**: a Deployment, a ClusterIP Service, an Ingress, readiness and
  liveness probes, and a ServiceAccount with a **Role — not a ClusterRole** — on
  `batch/v1` Jobs and `pods/log` in its own namespace only. It is a
  cluster-privileged component that also terminates HTTP, and the smallest possible
  grant is the entire point.
- **The frontend**: a Deployment, a Service, and a share of the Ingress.
- **The worker Deployment**, only if the `inproc` dispatcher is chosen.
- **Redis** as a subchart with an `external.url` escape hatch, now that something
  reads what it writes, and **Postgres external by default** — this chart should
  not become a database operator.
- **The CronJob**, still wanted and still the same design: `concurrencyPolicy:
  Forbid`, because two overlapping runs of one tenant are two drafts at twice the
  API cost; `startingDeadlineSeconds`, so a cluster down for an hour doesn't
  stampede on recovery; small history limits. It stays the answer for a runtime
  deployed with no gateway — once the gateway owns scheduling, a schedule is a row
  in its table.

**How the services talk, which is where a deployment like this usually goes
wrong.** Everything internal is a ClusterIP Service and the DNS name is the
contract: `<release>-gateway.<namespace>.svc.cluster.local`. The trap is the
frontend, and it belongs in the chart's README before anyone hits it: **Next.js
needs two base URLs for one API.** Server components call the gateway over the
internal Service name; the browser calls it through the Ingress over a public
hostname. They are not interchangeable, one of them is unreachable from where the
other runs, and building the internal name into client JavaScript produces a page
that renders perfectly and then fails every fetch.

Ingress: one host, `/` to the frontend and `/api` to the gateway, TLS from
cert-manager. Run pods get no Ingress and no Service — nothing calls into a run.

**NetworkPolicy, default-deny, with the allowed edges written down** — this matters
more here than in most systems, because a run pod executes a tenant's own Python
next to that tenant's API keys:

| Edge | |
|---|---|
| frontend → gateway | allow |
| gateway → Redis, Postgres, the Kubernetes API | allow |
| run pod → Redis (state, events), gateway (the result webhook) | allow |
| run pod → the internet | allow — LLM, search and analytics APIs are the job |
| run pod → Postgres, the Kubernetes API, other tenants' pods | **deny** |

**Chart quality, which is its own work**: a `values.schema.json` so a typo fails at
`helm install` rather than at `ImagePullBackOff`; `helm template | kubectl apply
--dry-run=server` against a real API server in CI; chart tests; and publishing the
chart as an OCI artifact to GHCR, since installing it today means cloning the
repository, which is not how anyone consumes a chart.

Still explicitly **not** a per-tenant CRD or an operator. Nothing here needs a
reconciliation loop, a Job is already the Kubernetes-native spelling of "run this
once", and an operator would put a new API in front of one that already exists.

### 7. The frontend

`services/frontend/` — Next.js (App Router, TypeScript), a client of the gateway
and of nothing else, which is why it is last.

- **Runs, live.** The point of the whole thing: the pipeline actually executing —
  which stage is running, which tool call is in flight, which one degraded. A run's
  `tool_errors` and grounding records stop being fields in a JSON blob and become
  the two things visible at a glance: *what did it look at, and what did it fail to
  look at?*
- **The graph.** The `PipelineSpec` drawn from the gateway's graph endpoint, with
  the live run lit up on top of it. That endpoint needs no credentials, which is
  what makes a configured pipeline legible before it has ever run.
- **Agents.** What each has wired in, editable, and checkable without spending an
  API call — the config editor round-trips through the gateway's `check-data`, so
  validation is the runtime's own loader rather than a second set of rules in
  TypeScript that drifts from it.
- **Review.** Drafts arrive with self-review notes attached and nothing publishes
  automatically; approving, editing and shipping one is a human step that wants an
  interface rather than a `curl`.
- **Cost and history**, from [step 5](#5-tracking-and-observability): what this
  tenant spent, what degraded, and how often grounding fell back.

Two rules it is built under. **Types are generated from the gateway's OpenAPI
document and checked in CI**, so a contract change breaks a build instead of a
page. And **the browser never holds a tenant's provider keys** — the session is the
gateway's, and every call to an LLM or an analytics API happens in a pod.

It builds against two contracts frozen before it existed: the
[JSON a run returns](../services/seo-agents/docs/output-schema.md) and
[where a run's state is](../services/seo-agents/docs/configuration.md#where-the-runs-state-is-kept-state_provider)
while it is still going. Neither was designed for a UI after the fact, which is the
claim this step is here to test.

### Not a step: release and supply chain

Independent of the chain above, wanted sooner than step 7, and small:

- **Cut `v0.1.0`, and make `latest` mean the last release.** Compose and the chart
  both default to `latest`, so today a default install runs whatever landed on
  `main` most recently — for a repository people install from, the default should be
  a release. `main` becomes `edge`. **The flip and the first tag have to land
  together**: flipping `latest` to tags-only while the repo has no tags freezes the
  existing `latest` at whatever is in GHCR and silently stops updating it.
- The same change carries `Chart.yaml`'s `appVersion` off its `"latest"`
  placeholder, publishing the chart as an OCI artifact, and the `VERSION` /
  `REVISION` / `CREATED` build args that only `make build` passes and CI never does.
- **Signed images (cosign), an SBOM, and a `pip-audit` job** — the remainder of the
  CI work, and the part that matters most once a tenant's plugins are running next
  to their keys.
