# Output schema

`AgentRunner.arun()` — the one method a UI or API layer should call, or `run()`
if you have no event loop — **always returns the same top-level keys, whether the
run succeeded or failed**. It never raises past its own boundary: bad input, a
failing search-performance/analytics/traffic/LLM call, a run that overran
`run_timeout_seconds`, or any other exception is caught inside `arun()` and
mapped onto the same `"failed"` shape below, instead of propagating as a raw
traceback. See
[`src/agent/managers/run_manager.py`](../src/agent/managers/run_manager.py)'s
`AgentRunner.arun()` for the try/except that enforces this — it's the one
place this contract is implemented, so it can't drift out of sync with this
document because one stage forgot to handle its own errors.

In practice this means you can build a UI directly against the table below.
You never need a `try/except` around `run()`/`arun()`, and you never need to branch on
"did this tenant have discovery configured" before reading `discovery` — it's
always there, just possibly empty.

## Top level

| Key | Type | Notes |
|---|---|---|
| `run_id` | `string` | Always present, even on failure (generated before anything else runs). |
| `agent_type` | `string` | Constant `"seo_content"`. |
| `phase` | `"done"` \| `"failed"` | The only two terminal values `run()` returns. |
| `input` | `object` | Echoes what you sent in, after defaults are applied — see `agent/managers/run_manager.py`'s `_build_agent_input`. |
| `output` | `object` \| `null` | `null` iff `phase == "failed"`; otherwise the shape below. |
| `discovery` | `object` | Always the 3-key shape below — never `null`, never absent. |
| `usage` | `{tokens: number, cost_usd: number}` | `0`/`0` on failure or if nothing ran yet. |
| `error` | `string` \| `null` | `null` iff `phase == "done"`; otherwise `str(exception)`. |

## `output` (only when `phase == "done"`)

The shape depends on `output.kind`, which is exactly the effective `channel`
for this run — the caller's `input.channel`, or whatever `choose_channel`
decided (see
[architecture.md](architecture.md#discovery-the-agent-finding-its-own-work)):

```jsonc
{
  "kind": "site_article",   // | "external_article" | "comment"
  "title": "...",            // string for the two article kinds; null for "comment"
  "content": "...",           // markdown body, or the short comment text — what
                               // you'd actually publish/post; read this first
  "format": "markdown",
  "metadata": {
    // always present, regardless of kind:
    "word_count": 133,
    "qa_notes": [ "..." ],        // self_qa's heuristic findings; [] means no issues —
                                    // read this before treating a draft as ready to publish
    "originality": "not_checked", // constant today — no plagiarism-detection tool exists yet

    // "site_article" / "external_article" only:
    "target_keyword": "...",
    "meta_description": "...",
    "headings": [ "..." ],
    "internal_links": [ "..." ],

    // "comment" only:
    "in_reply_to": "...",          // truncated to 200 chars
    "mentions_platform": false,
    "disclosure_included": false
  }
}
```

## `discovery` (always this exact shape)

```jsonc
{
  "opportunities": [
    {
      "source": "echooers_ideas",   // discovery_sources registry key that found it
      "topic": "...",
      "signal_strength": 0.8,        // 0-1
      "intent": "discussion",         // "commercial" | "informational" | "mixed" | "discussion"
      "suggested_channel_hint": "engagement_comment",  // a Channel value, or null
      "raw": { },                       // source-specific, kept for context — shape varies by source.
                                          // An "llm" source records how it was grounded here:
                                          // "grounding": "search" | "llm" | "none", the URLs it was
                                          // allowed to cite in "grounding_sources", and
                                          // "grounding_error" if a search failed and it fell back.
                                          // An "mcp" source records "mcp_tool" and "mcp_server".
                                          // These sit directly on "raw" — a source that normalizes
                                          // its own items before returning them would push all of
                                          // it down to "raw"."raw", which is a bug, not a variant.
      "reason": "..."
    }
  ],
  "channel_decision": {                 // null iff no discovery_sources are configured for this
                                          // tenant — i.e. discovery never ran at all
    "chosen": "engagement_comment",
    "reason": "...",
    "fallback": false                    // true = no opportunity suggested a channel;
                                           // config.default_channel was used, not a real decision
  },
  "tool_errors": [
    {
      "tool": "echooers_ideas",   // a source name, or "search_performance"/"analytics"/"traffic"/"llm"
      "node": "discover",           // which stage triggered it — "discover", "analyze", or "draft"
      "error_type": "RuntimeError",
      "message": "...",              // str(exception), truncated to 500 chars
      "occurred_at": "2026-01-01T00:00:00+00:00"  // ISO 8601, UTC
    }
  ]
}
```

For a tenant with `discovery_sources: []` (the default), every run's
`discovery` is exactly `{"opportunities": [], "channel_decision": null,
"tool_errors": []}`.

## On failure (`phase == "failed"`)

`output` is `null`. `discovery` is the empty shape above, even if some
discovery sources had already succeeded before the failure — a failed run
makes no output claim at all. `usage` is `{"tokens": 0, "cost_usd": 0}`.
`error` is a human-readable message (`str(exception)`) — things like
`'input.context_text is required when channel="engagement_comment"'` for a missing
required field, `"Unknown AgentInput field(s): ['seed_keywrod']"` for a
typo'd/unrecognized one (see
[`agent/validators/input_validator.py`](../src/agent/validators/input_validator.py)
— every key in the input is checked against `AgentInput`
([`agent/schemas/io.py`](../src/agent/schemas/io.py)) the same way
`AgentConfigLoader` checks `tenant.json`), or a raw provider error message for
a tool failure. Treat it as
diagnostic text, not guaranteed end-user-facing copy: something to log or show
in a "details" panel, not display verbatim to an end user without review.

## Recommended reading order for a UI

1. `phase` — branch success/failure.
2. `error`, if failed.
3. `output.content` — the thing to show or publish.
4. `output.metadata.qa_notes` — anything worth flagging before publishing.
5. `discovery` — *why* the run turned out this way; a good secondary/
   expandable panel, not the primary view.

## Example: a full success response

```jsonc
{
  "run_id": "b3b2b8b0-...",
  "agent_type": "seo_content",
  "phase": "done",
  "input": {
    "seed_keyword": "",
    "context_text": "",
    "params": { "max_words": 600, "tone": "friendly and practical" },
    "channel": "site_article"
  },
  "output": {
    "kind": "site_article",
    "title": "The Complete Guide to Anonymous Communities",
    "content": "# The Complete Guide to...\n\n...",
    "format": "markdown",
    "metadata": {
      "word_count": 612,
      "qa_notes": [],
      "originality": "not_checked",
      "target_keyword": "anonymous communities",
      "meta_description": "...",
      "headings": ["...", "..."],
      "internal_links": []
    }
  },
  "discovery": { "opportunities": [], "channel_decision": null, "tool_errors": [] },
  "usage": { "tokens": 812, "cost_usd": 0 },
  "error": null
}
```

## Example: a full failure response

```jsonc
{
  "run_id": "9e1e2f3a-...",
  "agent_type": "seo_content",
  "phase": "failed",
  "input": { "channel": "site_article" },
  "output": null,
  "discovery": { "opportunities": [], "channel_decision": null, "tool_errors": [] },
  "usage": { "tokens": 0, "cost_usd": 0 },
  "error": "input.context_text is required when channel=\"engagement_comment\""
}
```
