# Roadmap

Today SEO-OS is a runtime you drive from a terminal: an agent is a folder, a run
is a command, and the result is JSON on stdout. It runs on a laptop, on one host
under Compose, and on a cluster as a Job.

**The next release turns that into something you operate instead of type.** An
HTTP API that exposes every command the CLI already has, a queue so one agent can
work through many inputs at once, and a UI to watch a run happen, read what came
out, and edit the agent that produced it.

Four steps, in order. Each one says what it owns, what it deliberately doesn't,
and which decisions have already been taken so they aren't relitigated when the
code gets written.

| # | What | Where it lands |
|---|---|---|
| [1](#1-the-gateway) | **The gateway** — every command over HTTP | [`services/gateway/`](../services/gateway/) |
| [2](#2-many-runs-at-once) | **Many runs at once** — a queue, workers, live progress | the gateway |
| [3](#3-the-ui) | **The UI** — watch, read, edit | [`services/frontend/`](../services/frontend/) |
| [4](#4-the-chart-past-a-single-job) | **The chart past a single Job** | [`helm-charts/seo-os/`](../helm-charts/seo-os/) |
| [—](#not-a-step-release-and-supply-chain) | Release and supply chain | independent of the chain |

The order is a dependency chain read backwards: the UI is last because a UI with
nothing to call is a mock, and the gateway is what everything else is either a
property of or a way to run.

## The shape this is built for

One operator, several agents, many inputs. An agent is a configured folder — a
brand, a site, a product line — and it runs the same pipeline against a stream of
different inputs over time. That is the whole model, and most of what a
multi-tenant SaaS would need is therefore *not* here: no orgs, no per-agent
isolation levels, no quota enforcement, no per-tenant secret vault. The operator
owns every agent, wrote every plugin, and holds every key.

Two consequences worth writing down, because they delete a lot of work:

- **The workspace stays a folder on a volume.** `/userdata/<agent>/` remains the
  source of truth for config, templates, plugins and inputs, exactly as it is
  today. The database holds *runs*, not agents. Nothing materializes a folder from
  a registry, and the runtime keeps the on-disk contract it already has.
- **The image is built ahead of time and plugins are mounted.** One image carries
  the requirements; an agent's own `plugins/` arrives on the volume. There is no
  per-agent image build, and no reason to isolate a run in its own pod — it is the
  operator's own code either way.

## Two language decisions, taken up front

**The backend stays in Python, and FastAPI is the first choice.** The gateway's
core is one line — `await AgentService.aexecute(request)` — over a runtime that is
already async end to end. A Python gateway *imports* that; anything else
re-serializes the entire config and result contract across a process boundary and
then maintains a second implementation of the config model in another language.
FastAPI specifically for ASGI, Starlette's streaming responses (which is what SSE
is), and Pydantic v2 models that generate the OpenAPI document
[step 3](#3-the-ui) types itself from, so the contract is derived rather than
described twice.

One thing that decision does *not* buy for free: the runtime is
`TypedDict`-and-dataclass throughout, not Pydantic, so the gateway's request and
response models are written at its own boundary rather than lifted from
`AgentConfig` and `RunResult`. The import still beats a wire contract — the
argument is about not maintaining the config model twice — but the Pydantic layer
is work step 1 owns rather than inherits.

**The frontend is Next.js** (App Router, TypeScript). The product *is* the live
view of a run, which is a client-side streaming problem, so a server-templated UI
in Python would have meant writing that half in JavaScript anyway. Types come out
of the gateway's OpenAPI schema, generated and checked in CI, so a contract change
breaks the build instead of a page.

### 1. The gateway

`services/gateway/` — a FastAPI application over
[`AgentService.aexecute()`](../services/seo-agents/src/agent/service.py) and over
the same loaders every CLI command already uses. The CLI is one adapter; this is
the second.

**Before any of it: the runtime has to become importable.** Today there is no
`pyproject.toml` — `pythonpath = src` in `pytest.ini` and `WORKDIR /workspace/src`
in the `Dockerfile` are what make `from agent.service import AgentService` work.
A second service in a second directory cannot rely on either. Packaging the
runtime is this step's first commit, and it touches `pytest.ini`, the
`Dockerfile`, the `Makefile` and every documented command line the docs check
executes.

**Every command, plus the things a UI needs.** The inspection endpoints are the
existing commands with their presentation stripped off — they return the data the
Rich tables are currently built from:

| | |
|---|---|
| `GET /v1/agents` | `list-tenants` — every agent, with its plugin and template counts |
| `GET /v1/agents/{name}` | The config, secrets redacted, plus what's wired into it |
| `GET /v1/agents/{name}/check` | `check-data` — does this config work, without spending an API call |
| `GET /v1/agents/{name}/graph` | The `PipelineSpec`, which needs no credentials, for the UI to draw |
| `POST /v1/agents/{name}/preview-prompt` | `preview-prompt` for a given input, without drafting |
| `POST /v1/runs` | Submit a run. `202` with a `run_id`, or `200` with the finished result when the caller asks to wait |
| `GET /v1/runs` | Run history, filtered by agent, phase, time |
| `GET /v1/runs/{run_id}` | The run, live or finished — the [frozen output schema](../services/seo-agents/docs/output-schema.md), not a shape invented here |
| `GET /v1/runs/{run_id}/events` | SSE: stages and tool calls as they happen |
| `POST /v1/runs/{run_id}/cancel` | Recorded as a cancellation, never as a run that stops appearing |
| `/healthz`, `/readyz` | What the chart's probes need |

**And the workspace surface, which is new.** "Upload a template" and "edit this
agent" are file operations on the volume, so the gateway owns a small, explicit
set of them rather than letting the UI invent its own:

| | |
|---|---|
| `GET`/`PUT /v1/agents/{name}/config` | `tenant.json`, validated through the runtime's own loader before it is written |
| `GET`/`PUT /v1/agents/{name}/templates/{file}` | The agent's `templates/` folder |
| `GET`/`PUT /v1/agents/{name}/inputs/{file}` | Named inputs, so one agent keeps a library of them |

Two rules on that surface, both of which are the reason it is the gateway's and
not the UI's. **A write is validated by the runtime's loader, not by a second set
of rules** — a config that would fail a run must fail the `PUT`, and
`check-data`'s answer is the one that counts. And **every path resolves inside the
agent's own folder**, which the workspace already enforces and which becomes a
security property rather than a convenience the moment these arrive from a
request.

**The status-code mapping is the one piece of real design in the translation: a
failed run is a successful request.** A run that comes back `phase: "failed"` is a
`200` carrying a failed run; only an unrunnable request — unknown agent,
unloadable config, a sink with no URL, which the runtime already distinguishes by
raising `RunRequestError` — is a `4xx`. Collapsing those two into `500` is the
mistake this note exists to prevent, and far easier to prevent now than to un-ship
once the UI depends on it.

**Two execution modes, one response shape**: a caller who wants to block gets the
result inline, a caller who doesn't gets a `run_id`. The difference must not become
two code paths producing two different documents. Assigning the id is already
free — `AgentRunner` honours a `run_id` supplied on the input rather than always
minting its own.

**What it owns that the runtime deliberately refuses to grow**: the transport, the
queue ([step 2](#2-many-runs-at-once)), the schedule, and the approval loop — the
runtime drafts and never publishes, and turning "a human approves every word" into
review-and-ship needs somewhere to hold state between the draft and the decision.
That somewhere is a **Postgres this service owns**, external to the chart:
approvals and the durable run record. Redis stays what it already is — the live
state of a run in flight — and is explicitly not the system of record.

Access is a single operator's, so authentication is one API key (or one login),
not an identity model. It still has to exist before this is exposed on an Ingress:
this service reads and writes the folder that holds every key.

### 2. Many runs at once

One agent, many inputs, and a run takes about ninety seconds of waiting on
someone else's API. Today that is one process, one run, a result on stdout.

**A queue and a worker pool, not a pod per run.** The gateway accepts a run and
writes it to a Redis queue; a worker Deployment — the *same image*, a different
subcommand — pulls run ids and executes them. A run holds a socket, not a core, so
one async worker process holds many concurrent runs comfortably; that is what the
runtime's share-nothing design was for. Job-per-run buys per-run isolation nobody
here needs, and costs a pod's startup and a vanished log every time a run fails.

**Behind one `RunDispatcher` interface** — `inproc` and `worker`, selected by
config exactly like every provider in the runtime — because this repo ships a
Compose deployment and people develop on laptops, and a gateway that can only
dispatch into a cluster is undeployable on both. The interface has one rule that
keeps it honest: **a dispatcher takes a request and returns a `run_id`, never a
`RunResult`.** An `inproc` implementation allowed to hand the finished result
straight back is an interface that fits exactly one implementation.

**Progress out is the one thing that needs new code in the runtime.**
`state_provider: "redis"` already makes a snapshot readable from another process
after every super-step — but a snapshot per stage is coarse, and the thing worth
watching is the tool call in flight. The runtime already produces exactly those
events, for `-vv` and for `AgentService`'s streaming callback; in a worker that
callback is in the wrong process. So: **an event-stream reporter (Redis streams),
configured like any other provider**, letting the gateway tail a run instead of
polling snapshots and inventing the granularity it lost. It adds a provider rather
than touching a stage, and it is the *only* change this roadmap asks of
`services/seo-agents/`.

**The result out** is the state store's terminal snapshot, which the worker
already writes. No webhook, no second delivery path: the worker and the gateway
share a Redis and a Postgres, so the run record is updated by whoever finished it.

**Backpressure, because a run spends money.** A concurrency cap per agent and a
global one, and queue depth as the metric that says whether the workers or the
budget are the constraint. A failed run is a recorded run, never a job to retry
silently and bill twice.

**What a run cost** belongs on the run record from the start: tokens in and out
per LLM call, totalled per run. It is additive (`LLMResponse` carries usage), it
is what makes "why was last month expensive" answerable, and retrofitting it means
a history that starts halfway.

### 3. The UI

`services/frontend/` — Next.js, a client of the gateway and of nothing else, which
is why it is last.

- **Runs, live.** The point of the whole thing: the pipeline actually executing —
  which stage is running, which tool call is in flight, which one degraded. A run's
  `tool_errors` and grounding records stop being fields in a JSON blob and become
  the two things visible at a glance: *what did it look at, and what did it fail to
  look at?*
- **The output.** The finished draft, readable, with its self-review notes
  attached — and the raw result JSON one click away for when the rendered view
  isn't enough.
- **History, per agent.** Every run this agent has done, its phase, what it cost,
  what degraded.
- **The graph.** The `PipelineSpec` drawn from the gateway's graph endpoint, with
  the live run lit up on top of it. That endpoint needs no credentials, which is
  what makes a configured pipeline legible before it has ever run.
- **Editing an agent.** The config, its templates, and its library of inputs —
  uploaded and edited in place, round-tripped through the gateway's validation and
  `check-data`, so the answer to "will this work" comes from the runtime's own
  loader rather than a second set of rules in TypeScript that drifts from it.
- **Review.** Drafts arrive with self-review notes and nothing publishes
  automatically; approving, editing and shipping one is a human step that wants an
  interface rather than a `curl`.

**The browser never holds a provider key** — the session is the gateway's, and
every call to an LLM or an analytics API happens in a worker.

It builds against two contracts frozen before it existed: the
[JSON a run returns](../services/seo-agents/docs/output-schema.md) and
[where a run's state is](../services/seo-agents/docs/configuration.md#where-the-runs-state-is-kept-state_provider)
while it is still going. Neither was designed for a UI after the fact, which is the
claim this step is here to test.

### 4. The chart past a single Job

[`helm-charts/seo-os/`](../helm-charts/seo-os/) renders a `Secret` and a `Job`, and
does that well: no credentials, no storage class, no ingress required. It deploys
exactly one workload type, and the steps above add three more.

- **The gateway**: a Deployment, a ClusterIP Service, an Ingress, readiness and
  liveness probes.
- **The workers**: a Deployment, scaled by replica count, mounting the same
  `/userdata` volume the gateway does.
- **The frontend**: a Deployment, a Service, and a share of the Ingress.
- **Redis** as a subchart with an `external.url` escape hatch, now that something
  reads what it writes, and **Postgres external by default** — this chart should
  not become a database operator.
- **The `CronJob`**, still wanted and still the same design: `concurrencyPolicy:
  Forbid`, because two overlapping runs of one agent are two drafts at twice the
  API cost; `startingDeadlineSeconds`, so a cluster down for an hour doesn't
  stampede on recovery. It stays the answer for a runtime deployed with no gateway
  — once the gateway owns scheduling, a schedule is a row in its table.

**One image, not three.** The gateway imports `AgentService`, so its image
contains the runtime either way; the worker *is* the runtime. Shipping them as one
image with subcommands (`run`, `serve`, `worker`) makes version skew impossible by
construction, which matters here because the contract between them is shared
Python — an `AgentConfig` dataclass — rather than a wire format that would notice
the drift. The frontend is the one genuinely separate image.

**The `/userdata` volume becomes shared, and that is the one new infrastructure
requirement.** The gateway writes it (config edits, uploaded templates) and every
worker reads it, so it wants `ReadWriteMany` — or a single-node cluster where
`ReadWriteOnce` is enough, which is worth saying plainly in the chart's README
because it is the thing that will fail first on a multi-node install.

**How the services talk.** Everything internal is a ClusterIP Service and the DNS
name is the contract: `<release>-gateway.<namespace>.svc.cluster.local`. The trap
is the frontend, and it belongs in the chart's README before anyone hits it:
**Next.js needs two base URLs for one API.** Server components call the gateway
over the internal Service name; the browser calls it through the Ingress over a
public hostname. They are not interchangeable, one is unreachable from where the
other runs, and building the internal name into client JavaScript produces a page
that renders perfectly and then fails every fetch.

Ingress: one host, `/` to the frontend and `/api` to the gateway, TLS from
cert-manager.

**Chart quality, which is its own work**: a `values.schema.json` so a typo fails at
`helm install` rather than at `ImagePullBackOff`; `helm template | kubectl apply
--dry-run=server` against a real API server in CI; chart tests; and publishing the
chart as an OCI artifact to GHCR, since installing it today means cloning the
repository, which is not how anyone consumes a chart.

Still explicitly **not** a CRD or an operator. Nothing here needs a reconciliation
loop, and an operator would put a new API in front of one that already exists.

### Not a step: release and supply chain

Independent of the chain above, wanted sooner than step 3, and small:

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
  CI work.
