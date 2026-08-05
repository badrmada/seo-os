# 04 — HomelabHub (community: discovery + the agent picks the channel)

**The story.** HomelabHub is a community forum for self-hosting and home-lab
enthusiasts. It grows by **joining relevant conversations** elsewhere — helpfully,
never spammy. Rather than being told exactly what to write each time, it lets the
agent **discover** what's worth engaging with and **decide the channel itself**.

**What this example shows:**

- **Discovery turned on** (`discovery_sources`) — the agent finds opportunities
  instead of only reacting.
- **The agent choosing the channel** — you leave `channel` out and it decides.
- **A comment with a disclosure rule** — a custom `engagement_comment` prompt plus
  brand-mention keywords so any product mention gets a disclosure.

## The files

- `tenant.json` — discovery on (one source), `default_channel` set to
  `engagement_comment`, a custom comment prompt, and `qa_brand_mention_keywords`.
- `input.discover.json` — **no channel** — the agent decides.
- `input.comment.json` — an explicit reply to a specific post.

## Two ways to run it

### A) Let the agent decide

```bash
python src/main.py run --userdata examples --tenant 04-community-homelabhub --input input.discover.json
```

There's no `channel` in the input, so the agent runs discovery, then picks. Real
output (trimmed):

```json
"discovery": {
  "opportunities": [
    { "source": "community_trends", "topic": "self hosting privacy (mock opportunity ...)", "suggested_channel_hint": null }
  ],
  "channel_decision": {
    "chosen": "engagement_comment",
    "reason": "No discovered opportunity suggested a channel; used config.default_channel.",
    "fallback": true
  }
}
```

`"fallback": true` is honest bookkeeping: **offline**, the mock discovery source
returns a generic opportunity with no channel hint, so the agent falls back to
your `default_channel`. **In production** with a grounded `llm` source (below),
opportunities come back with real channel hints and the decision looks like this
instead:

```jsonc
"channel_decision": {
  "chosen": "engagement_comment",
  "reason": "Highest-scoring channel hint across 3 discovered opportunities: {'engagement_comment': 0.8, ...}.",
  "fallback": false   // a real decision, not a default
}
```

### B) Reply to a specific post

```bash
python src/main.py run --userdata examples --tenant 04-community-homelabhub --input input.comment.json
```

Here you give the exact `context_text` to reply to. The self-review confirms the
reply disclosed its affiliation and didn't over-mention the product:

```json
"metadata": { "disclosure_included": true, "mentions_platform": false, "qa_notes": [] }
```

> **Note on the comment text offline.** The mock AI returns a fixed placeholder
> reply (it doesn't read your brand voice), so offline the wording won't sound
> like HomelabHub. Preview the prompt to see *your* instructions and disclosure
> rule — `python src/main.py preview-prompt --userdata examples --tenant 04-community-homelabhub --input input.comment.json` — and
> switch to `llm_provider: "gemini"` for real, on-brand replies.

## The disclosure setup

Because a community reply can quietly become an ad, two settings work together:

- The custom `engagement_comment` prompt tells the model to disclose if it
  mentions HomelabHub.
- `qa_brand_mention_keywords` lists the phrases that count as a brand mention
  (`"homelabhub"`, `"self-hosting community"`, …). If a reply mentions one
  **without** a nearby disclosure, the self-review flags it.

## Go live

```jsonc
{
  "llm_provider": "gemini",
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "discovery_sources": [
    { "name": "community_trends", "provider": "llm", "max_opportunities": 5 }
  ]
}
```

The `llm` discovery source is **grounded in live Google Search by default**, so
it surfaces real conversations with real links. See
[docs/configuration.md](../../docs/configuration.md#opportunity-discovery).
</content>
