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
  "seed_keyword": "getting started with acme",
  "params": { "max_words": 700, "tone": "friendly and clear" }
}
```

## Run it

```bash
python src/main.py run --userdata examples --tenant 01-starter-acme
```

Or `make example EXAMPLE=01-starter-acme`, or in Docker with nothing installed —
the same run three ways is in [Running an example](../README.md#running-an-example).

You get a full result object — `phase`, `input`, `output`, `discovery`, `usage`.
The `output.content` is the drafted article; `output.metadata.qa_notes` is the
self-review. The exact fields are documented in
[docs/output-schema.md](../../docs/output-schema.md).

## Heads up: the offline draft

The drafted title comes back as *"The Complete Guide to Getting Started With
Acme"* — your seed keyword, wrapped in generic filler by the **mock AI**. That's
expected: the words become real as soon as you set `llm_provider: "gemini"` (see
any of the other examples for how). The point of this example is the *flow and
the output shape*, not the prose.

The keyword is yours because `search_performance_provider` defaults to `"none"`
— no rank data, so the agent uses the `seed_keyword` you gave it. Connect a rank
source (`"google"`, or `"templated"` over any export) and the agent will instead
target a real striking-distance keyword it found, which is the whole point of
connecting one.

## Next

[02 — PingOwl](../02-saas-blog-pingowl/) adds your own analytics data and a
custom prompt, and shows how to watch your data flow into the prompt.
