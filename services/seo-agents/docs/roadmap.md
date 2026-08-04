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

## Next

Roughly in priority order:

1. **Real discovery providers beyond `"llm"`, narrowed to what grounding
   doesn't cover.** Grounded search (above) already gets real web results
   with real URLs, so a general "search API" client is no longer the gap.
   What's left: source-specific signal grounding can't easily reproduce —
   e.g. Reddit-specific detail (upvotes, comment counts, a specific
   subreddit) or a dedicated trends API's actual query-volume numbers, if a
   tenant needs that precision rather than "web results about X."
2. **A stage-registration mechanism for genuinely custom pipeline stages** —
   right now the stage registry in `agent/graph/pipeline.py` is a fixed
   dict of the six built-in stages. Worth revisiting once there's a real
   use case for a tenant-specific stage beyond what
   `discover`/`choose_channel`/`analyze_context`/`analyze`/`draft`/`self_qa`
   already cover.

## Explicitly out of scope for this agent

No queue, no worker pool, no persistence beyond a single in-process run
(state snapshots are in-memory only — see
[`src/state/memory_store.py`](../src/state/memory_store.py)), no approval
gate, no scheduling, no CMS/community posting integration. Those are a
worker/control-plane concern layered on top of `AgentRunner.run()`, not
something this agent should grow into doing itself.
</content>
