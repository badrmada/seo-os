# 02 — PingOwl (developer SaaS: your data + a custom prompt)

**The story.** PingOwl is a cron-job and uptime monitoring service for
developers. Its growth comes from ranking for reliability and monitoring topics,
so it uses the agent to draft **SEO articles for its own blog** — in a terse,
technical, code-first voice, grounded in its real product numbers.

**What this example shows:**

- **Templated analytics from a file** — feeding PingOwl's own metrics into the
  agent with zero code.
- **A custom `site_article` prompt** — a technical voice instead of the generic
  default.
- **How your data flows into the prompt** — the clearest thing to see here.

## The files

- `data/analytics.json` — PingOwl's own analytics export (its field names).
- `tenant.json` — provider choices, brand voice, the two analytics templates, and
  the custom prompt.
- `input.json` — a `site_article` run targeting "cron job monitoring".

## How the data links to the templates

This is the important part. Your data has **its own field names**; two templates
map them into what the agent expects.

**Your data** (`data/analytics.json`, trimmed):

```json
{
  "totals": { "monitors": 3120, "signups_30d": 214, "checks_run_24h": 1900000 },
  "top_posts": [
    { "title": "How to monitor cron jobs the right way", "slug": "monitor-cron-jobs", "reads": 1840 }
  ]
}
```

**The summary template** (in `tenant.json`) reads those fields — `data` is your
JSON:

```jinja
{{ data.totals.signups_30d }} new signups in the last 30 days across {{ data.totals.monitors }} active monitors, {{ data.totals.checks_run_24h }} checks run in the last 24h.
```

**The highlights template** loops over your posts and builds the `{label, url}`
list the agent wants (each value wrapped in `|tojson` so it's safely quoted):

```jinja
[{% for p in data.top_posts[:limit] %}{"label": {{ (p.title + " (" + p.reads|string + " reads)")|tojson }}, "url": {{ ("https://pingowl.example.com/blog/" + p.slug)|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]
```

## See it flow into the prompt

```bash
python ../../src/main.py preview-prompt
```

The rendered prompt (real output) — notice the summary and the post links come
straight from your data, and the wording is your custom template:

```
You are a senior engineer writing for the PingOwl developer blog.
Product: PingOwl is a cron job and uptime monitoring service for developers: ...
Goal: Grow organic signups from developers searching for monitoring and reliability topics.
Target keyword/topic: "anonymous social media app"
Tone: concise and technical. Max words: 900.
Write for a technical audience: concrete, code-first, no marketing fluff. ...
Recent product activity you may reference if it fits naturally: 214 new signups in the last 30 days across 3120 active monitors, 1900000 checks run in the last 24h.
Popular existing posts to link where relevant (add the link to internal_links):
- "How to monitor cron jobs the right way (1840 reads)" — https://pingowl.example.com/blog/monitor-cron-jobs
- "The dead man's switch pattern for backups (1210 reads)" — https://pingowl.example.com/blog/dead-mans-switch
- "Alert fatigue: send fewer, better alerts (980 reads)" — https://pingowl.example.com/blog/alert-fatigue
```

(The `Target keyword/topic` is `"anonymous social media app"` because offline the
keyword comes from the mock Search Console — see [What's real
offline](../README.md#whats-real-offline-and-what-isnt). Everything else is
PingOwl's.)

## Run the full draft

```bash
python ../../src/main.py run
```

## Go live

In `tenant.json`, switch the mocks for real tools:

```jsonc
{
  "llm_provider": "gemini",
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "gsc_provider": "google",              // now targets PingOwl's real striking-distance keywords
  "gsc_key_file": "service_account.json"
}
```

See [docs/configuration.md](../../docs/configuration.md) for the Gemini and
Search Console setup steps. To pull the analytics from a live API instead of a
file, change `analytics_source` to `"api"` and set `analytics_api_url` — the
templates stay exactly the same.
</content>
