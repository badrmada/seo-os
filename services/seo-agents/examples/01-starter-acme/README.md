# 01 — Acme (the simplest possible setup)

**The story.** Acme is a placeholder product. This example exists to show the two
files a run needs and the shape of what comes back — nothing else. Start here to
get the mechanics, then move on to a real scenario.

## The two files

**`tenant.json`** — how the agent behaves. Here it's almost empty: no providers
are set, so every tool defaults to the built-in mock. The only real settings are
the brand voice.

```json
{
  "brand_description": "Acme is a placeholder product ...",
  "agent_goal": "Grow qualified organic traffic to acme.example.com."
}
```

**`input.json`** — what to write this run. A basic site article:

```json
{
  "channel": "site_article",
  "gsc_domain": "sc-domain:acme.example.com",
  "seed_keyword": "getting started with acme",
  "params": { "max_words": 700, "tone": "friendly and clear" }
}
```

## Run it

```bash
python src/main.py run --userdata examples --tenant 01-starter-acme
```

You get a full result object — `phase`, `input`, `output`, `discovery`, `usage`.
The `output.content` is the drafted article; `output.metadata.qa_notes` is the
self-review. The exact fields are documented in
[docs/output-schema.md](../../docs/output-schema.md).

## Heads up: the offline keyword

The drafted title comes back as *"The Complete Guide to Anonymous Social Media
App"* — not about Acme. That's expected: with `gsc_provider` on mock, the agent
reads a **fixed sample keyword** from the built-in fake Search Console, and the
**mock AI** writes generic filler around it. Both become real as soon as you set
`gsc_provider: "google"` and `llm_provider: "gemini"` (see any of the other
examples for how). The point of this example is the *flow and the output shape*,
not the words.

## Next

[02 — PingOwl](../02-saas-blog-pingowl/) adds your own analytics data and a
custom prompt, and shows how to watch your data flow into the prompt.
