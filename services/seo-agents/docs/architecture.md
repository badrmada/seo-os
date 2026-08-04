# Architecture

This is the deep dive on how the agent is built and why. If you just want to run
it, start with the [README](../README.md). For the full list of config fields,
see [configuration.md](configuration.md). To plug in your own tool without
forking, see [extending.md](extending.md).

## The big picture

The agent is a small **pipeline**: a fixed sequence of steps, each doing one job
and passing its work to the next. One call to `AgentRunner.run(input_data)`
builds the pipeline, runs it once, and returns a finished result. There's no
queue and no background worker — one call in, one draft out.

Two design ideas shape everything below, so it's worth stating them up front:

1. **Every step talks to tools through an interface, never a specific vendor.**
   A step that needs traffic data calls "the traffic tool" — it doesn't know or
   care whether that's Cloudflare, a fake, or your own code. This is what lets
   the same pipeline run offline with fakes or in production with real vendors,
   with no change to the steps themselves.
2. **The pipeline's shape is decided from config, not hardcoded.** A product
   that hasn't turned on discovery gets a shorter pipeline. The steps that
   handle discovery don't run as empty no-ops — they simply aren't part of that
   product's pipeline at all.

## What one run looks like

Here's the full pipeline. The exact steps depend on whether discovery is turned
on (that's the diamond at the top):

```mermaid
flowchart TD
    IN([AgentInput: your request]) --> V{discovery<br/>turned on?}

    V -- no --> AN

    V -- yes --> DI[discover<br/>find opportunities]
    V -- yes --> AC[analyze_context<br/>gather your data early]
    DI --> CH[choose_channel<br/>article or comment?]

    CH --> AN[analyze<br/>pick keyword / finalize data]
    AC --> AN

    AN --> DR[draft<br/>write it with the AI model]
    DR --> QA[self_qa<br/>run automated checks]
    QA --> OUT([result: the finished draft])
```

Read it top to bottom. Three things are worth calling out:

1. **No discovery? The pipeline is just three steps:** `analyze → draft →
   self_qa`. You supply the channel and topic up front, like any normal content
   pipeline. The `discover`, `choose_channel`, and `analyze_context` steps
   aren't skipped — they were never added to this product's pipeline in the
   first place.

2. **With discovery on, the agent can pick the channel itself.** If you didn't
   set `channel` in your input, `choose_channel` looks at what `discover` found
   and decides whether this run should be an article or a comment. If you *did*
   set `channel`, that always wins — discovery only fills in a blank.

3. **Two things happen at once, on purpose.** When discovery is on, gathering
   your analytics/traffic data (`analyze_context`) doesn't need to wait for
   discovery to finish — the two run side by side. `analyze` then waits for both
   before doing its final, channel-dependent work (like picking a keyword). This
   just saves time; the result is identical either way. The wiring behind that
   is explained in [How the pipeline is
   assembled](#how-the-pipeline-is-assembled) below.

## The steps, in plain terms

| Step | Job | Runs when |
|---|---|---|
| `discover` | Call every configured opportunity source and collect what they find. | Discovery is on. |
| `choose_channel` | Score the opportunities and pick the channel — unless you already picked one. | Discovery is on. |
| `analyze_context` | Fetch analytics and traffic data early, in parallel with discovery. | Discovery is on. |
| `analyze` | Finalize the data. For article channels, pull Search Console rows and pick the best keyword to target. | Always. |
| `draft` | Build a prompt from your brand voice, goal, and data, and ask the AI model to write the draft. | Always. |
| `self_qa` | Run quick automated checks on the draft and attach the notes. Produce the final `output`. | Always. |

Each step reads some state, does its work, and writes new state for the next
step. The precise list of which key each step reads and writes lives in the
docstrings on [`agent/schemas/io.py`](../src/agent/schemas/io.py)'s `AgentState`
— that file is the source of truth.

## How the pipeline is assembled

The pipeline runs on [LangGraph](https://github.com/langchain-ai/langgraph), a
small library for wiring steps into a graph. Each step is a plain Python object
with a `run(state) -> dict` method. Whatever it returns gets merged into the
shared state before the next step runs. There's no branching or approval logic
*inside* the pipeline — those belong to a control layer built on top, not to the
agent.

The interesting part is how the graph gets built.
[`agent/graph/pipeline.py`](../src/agent/graph/pipeline.py)'s `build_graph()`
does **not** hardcode a list of steps. Instead it reads a small **spec** — a
plain list of step names — and wires up exactly those steps. Today there's one
spec, and it's built from your config:

```python
def _default_spec(config) -> PipelineSpec:
    stages = []
    if config.discovery_sources:
        mode = "parallel_by_source" if len(config.discovery_sources) > 1 else "sequential"
        stages.append(PipelineStage("discover", mode=mode))
        stages.append(PipelineStage("choose_channel"))
        stages.append(PipelineStage("analyze_context", mode="concurrent_from_start"))
    stages += [PipelineStage("analyze"), PipelineStage("draft"), PipelineStage("self_qa")]
    return PipelineSpec(stages=tuple(stages))
```

The payoff: adding a new step to the pipeline means adding it to this spec and a
registry of steps — not rewriting the graph-building code by hand. Most steps
run one after another, but a step can ask for a different shape through its
`mode`:

- **`parallel_by_source`** (used by `discover` when you have 2+ sources) — run
  every opportunity source at the same time instead of one after another, then
  merge their results. With 0 or 1 source there's nothing to parallelize, so it
  stays sequential. Same per-source logic either way; only the timing differs.

- **`concurrent_from_start`** (used by `analyze_context`) — start this step
  immediately, in parallel with the `discover → choose_channel` chain, instead
  of after it. `analyze` then waits for *both* branches to finish before it
  runs. In LangGraph terms this is an "AND-join": you tell it one step depends
  on a **list** of predecessors, and it waits for all of them. (Getting this
  wrong — wiring each predecessor separately — would let `analyze` fire as soon
  as *either* branch finished, which could corrupt the shared state. The single
  list form is what guarantees it waits for both.)

## Tools: the swappable-vendor pattern

This is the core idea that makes the agent product-agnostic.

Every step depends only on an **interface** (a Python `Protocol` from
[`tools/base.py`](../src/tools/base.py)) — never on a concrete class like
"CloudflareClient." The `Tools` object
([`agent/graph/tools.py`](../src/agent/graph/tools.py)) simply bundles one
implementation of each interface:

```python
@dataclass
class Tools:
    gsc: GSCClient                              # keyword / ranking data
    analytics: AppAnalyticsClient               # your product's own analytics
    traffic: SiteTrafficClient                  # your website's traffic
    llm: LLMClient                              # the AI model that writes
    discovery_sources: dict[str, OpportunitySource]  # the opportunity finders
```

There's exactly **one** place that decides which concrete tool to use for each
interface: `ToolsManager`
([`agent/managers/tools_manager.py`](../src/agent/managers/tools_manager.py)).
It reads your config's `*_provider` fields and builds the matching tool. Because
nothing under `agent/graph/` ever imports Cloudflare, Gemini, or Google directly,
a step's logic never changes based on which vendor (or fake, or template, or
your own code) is actually behind the interface.

| Interface | What it provides | Available providers |
|---|---|---|
| `LLMClient` | Turn a prompt into text | `mock`, `gemini` |
| `GSCClient` | Search Console rows (query, position, clicks) | `mock`, `google` |
| `AppAnalyticsClient` | Your analytics → `{summary, highlights}` | `mock`, `templated`, `custom` |
| `SiteTrafficClient` | Your traffic → `{summary}` | `none`, `mock`, `cloudflare`, `templated`, `custom` |
| `OpportunitySource` | Content opportunities → a list of `Opportunity` | `mock`, `llm`, `custom` |

Analytics, traffic, and opportunities are deliberately **free-form**. What a
product tracks (ideas and upvotes, or orders and revenue, or articles and reads)
and what counts as a good opportunity (a rising search term, a hot thread, a
stale but relevant idea) don't share one fixed shape across products. So the
system never assumes a fixed vocabulary — only the concrete tool, which
understands its own data, converts raw numbers into the generic shape above.

## The four provider flavors: `mock`, `templated`, `custom`, `llm`

Every swappable tool follows the same small menu of options. Learn it once and
it applies everywhere:

- **`mock`** — a built-in fake: deterministic, no network, product-neutral.
  It's what a zero-config run gets, and it's what lets the test suite run
  without touching any real API.

- **`templated`** *(analytics, traffic)* — you hand the agent your own data
  (from a file or a live API) plus a short **template** that reshapes it into
  the generic interface shape. No Python required. This is what Echooers uses
  for its analytics; see [configuration.md](configuration.md)'s worked example.

- **`custom`** *(analytics, traffic, discovery)* — you point the config at your
  own Python class (`"module.path:ClassName"`). Use this when the logic is real
  code — a database query, a bespoke API call, or a multi-step research routine
  — rather than a simple reshape. This is the main extension point;
  [extending.md](extending.md) is the full walkthrough, including how to do it
  **without forking**.

- **`llm`** *(discovery only)* — the AI model itself is the opportunity finder.
  It's prompted to surface topics, threads, and links worth pursuing and to
  return them as structured data. **By default it's grounded in live Google
  Search** (`generate(..., grounded=True)`): for `GeminiClient` this attaches
  Google Search, so the model actually searches the web instead of guessing from
  its training data — and the real citation URLs it used come back on
  `LLMResponse.sources`. This is what Echooers uses instead of building a
  dedicated Reddit or trends integration.

Two providers are the exception to "everything is `mock`/`templated`/`custom`":
`cloudflare` (traffic) and `google` (Search Console). These are real, reusable
integrations that do genuine computation — bot-score bucketing, API pagination —
which is exactly why they're proper Python classes and not templates.

## Discovery: the agent finding its own work

Without discovery, the agent is **reactive** — it does exactly what you asked:
a channel, a keyword or domain, or a specific thread to reply to.

Turn on `discovery_sources` and it becomes **proactive**. The `discover` step
calls every configured source, normalizes whatever comes back into a common
`Opportunity` shape, and — if you left `channel` unset — `choose_channel` picks
the channel based on what was found:

```python
class Opportunity(TypedDict):
    source: str                       # which discovery source found it
    topic: str
    signal_strength: float            # 0-1, comparable across sources
    intent: Literal["commercial", "informational", "mixed", "discussion"]
    suggested_channel_hint: str | None  # a channel value, or None
    raw: dict                         # the source's own payload, kept for prompt context
    reason: str                       # human-readable "why this is worth doing"
```

**How the channel gets chosen.** `choose_channel` adds up `signal_strength` for
each suggested channel across all opportunities, and picks the highest total. If
no opportunity suggests any channel, it falls back to `config.default_channel` —
and marks that in the output as `fallback: true`, so you can tell a real
decision from a default (see [output-schema.md](output-schema.md)).

**An explicit channel always wins.** If your input names a `channel`, discovery
never overrides it. Discovery only fills in a blank. This is also what keeps
every existing caller behaving identically after a product turns discovery on.

**The results are validated, not trusted.** A discovery source can compute
whatever it likes internally, but every item that crosses back into the pipeline
is coerced by
[`agent/schemas/opportunity.py`](../src/agent/schemas/opportunity.py)'s
`normalize_opportunity` — for *every* source (`mock`, `llm`, and your own
`custom` classes alike). A malformed item (a non-numeric score, a junk intent,
no topic) is dropped on its own rather than crashing the step and throwing away
every other opportunity from that source. For the grounded `llm` source, there's
an extra check: a `link` is discarded unless it's actually one of the citation
URLs the search returned — a made-up link is treated as a hallucination, not a
fact.

## Error handling: degrade, don't crash

One external call failing should never bring down the whole run, and the process
should never crash because of it. Every step that calls something external wraps
that call individually and does one of two things: **degrade** (keep going with
a safe default) or **fail cleanly** (stop without crashing) — depending on
whether there's anything meaningful left to do.

- **`discover`** wraps each source separately. One source failing contributes
  zero opportunities and one recorded error — it doesn't stop the other sources
  and doesn't fail the step.
- **`analyze` / `analyze_context`** wrap the Search Console, analytics, and
  traffic calls independently. Any of them failing degrades to an empty default
  plus a recorded error. There's always *some* usable topic to draft from, even
  with all three down.
- **`draft`** is the one place degrading doesn't make sense — there's no
  fallback article for a failed AI call. So it fails cleanly: it marks the run
  `failed` with a clear error and skips writing a draft, rather than crashing.
- **`self_qa`** notices a failed run and passes it through unchanged, instead of
  trying to check a draft that doesn't exist.
- **`AgentRunner.run()`** is the final safety net for anything that slips
  through (bad input, a failure before the pipeline even starts). It catches it
  and returns the same top-level `failed` shape every other path produces. **A
  caller never needs a `try/except` around `run()`.**

The upshot: whether a tool degraded or the run failed, `tool_errors` (surfaced
as `discovery.tool_errors` in the output) collects one entry per failure across
every step that ran. Even a failed run tells you what *did* succeed, not just
that something broke. The shared helper is
[`agent/utils/tool_errors.py`](../src/agent/utils/tool_errors.py)'s
`record_tool_error()`:

```python
class ToolError(TypedDict):
    tool: str          # which tool failed — a discovery source name, or "gsc"/"analytics"/"traffic"/"llm"
    node: str          # which step triggered it
    error_type: str    # the exception's class name
    message: str       # the error message, truncated
    occurred_at: str   # ISO 8601 timestamp
```

## Prompts: the system owns the frame, you own the wording

[`agent/prompts/builder.py`](../src/agent/prompts/builder.py) renders your
prompt template (or the built-in default from
[`agent/prompts/templates.py`](../src/agent/prompts/templates.py)) and then
**always** appends a fixed, system-owned instruction: "return only this exact
JSON shape." Your template never controls that final part. That's what keeps the
output reliably parseable no matter how creative your wording gets.

Every template you override is checked against a sample context when the config
loads (see [configuration.md](configuration.md)), so a broken template fails
when you save your config — not in the middle of a run.

## Where to go next

- [configuration.md](configuration.md) — every config field, with the full
  Echooers example explained.
- [extending.md](extending.md) — writing your own tool and wiring it in without
  forking.
- [output-schema.md](output-schema.md) — the exact JSON a run returns, for
  building a UI on it.
- [roadmap.md](roadmap.md) — what's built and what's next.
</content>
