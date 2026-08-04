# Configuration reference

Everything the agent does is driven by one file: your `tenant.json`. Every field
below has a generic, product-neutral default, so an empty `{}` still gives you a
complete, working agent — you only set the fields you want to change.

This page is long on purpose. It's meant to be the one place you need: every
field, how to connect each real tool (with setup steps), and — the part most
people get stuck on — several **complete, copy-paste template examples for
different kinds of products**, not just the Echooers one.

**How to read it:**

- New here? Skim [The provider system](#the-provider-system-the-one-idea-to-learn-first)
  first — it's the one concept the rest builds on.
- Connecting your analytics with no code? Jump to
  [Templates, explained properly](#templates-explained-properly-with-examples).
- Connecting Google or Cloudflare? See [Google Search Console](#google-search-console)
  and [Website traffic](#website-traffic) for the setup steps.
- Need real code, not a template? See [extending.md](extending.md).
- Want a full working config to copy? The [examples/](../examples/) folder has
  six runnable ones (SaaS, e-commerce, community, job board, MCP), simple to
  advanced.

`python src/main.py --tenant path/to/tenant.json --input path/to/input.json`
loads your files. Unknown field names are rejected on load, so a typo fails
immediately with a clear message instead of being silently ignored.

## The provider system (the one idea to learn first)

Almost every field name follows one pattern: `<thing>_provider`, plus a few
extra fields that depend on which provider you picked.

A **provider** is just "which implementation do I want for this job?" Every job
offers the same small menu:

| Provider | Means | When to use it |
|---|---|---|
| `mock` | A built-in fake. No network, no keys, same output every time. | Trying things out, tests, offline demos. |
| `templated` | *Your* data (a JSON file or an API), reshaped with a short template. **No code.** | Your data is JSON and you can map it with a snippet — most analytics/traffic cases. |
| `custom` | *Your* Python class. | The logic is real code: a database query, a bespoke API, a multi-step routine. |
| A vendor name (`gemini`, `google`, `cloudflare`) | A real, built-in integration with that vendor. | You use that specific vendor. |
| `llm` *(discovery only)* | The AI model itself finds the opportunities. | You want the agent to discover topics without building an integration. |

So `"analytics_provider": "templated"` means "map my analytics with a template,"
and `"traffic_provider": "cloudflare"` means "get traffic from Cloudflare." Once
you've seen this for one job, every other job reads the same way.

The rest of this page goes job by job.

---

## The AI model (LLM)

The model that writes your drafts.

| Field | Type | Default | Notes |
|---|---|---|---|
| `llm_provider` | `str` | `"mock"` | `"mock"` (offline, no key) or `"gemini"`. |
| `llm_model` | `str` | `"gemini-2.0-flash"` | Model name passed to Gemini. |
| `gemini_api_key` | `str` | `""` | Required when `llm_provider` is `"gemini"`. Get one from [Google AI Studio](https://aistudio.google.com/apikey). |

```jsonc
{ "llm_provider": "gemini", "llm_model": "gemini-pro-latest", "gemini_api_key": "YOUR_GEMINI_API_KEY" }
```

---

## Google Search Console

Real keyword and ranking data for your own site. The agent uses it to find
"striking distance" keywords — ones you *almost* rank for, where a good article
can push you onto page one.

| Field | Type | Default | Notes |
|---|---|---|---|
| `gsc_provider` | `str` | `"mock"` | `"mock"` or `"google"`. |
| `gsc_key_file` | `str` | `"service_account.json"` | Path to your Google **service-account key file** (see below). |

### Setting up the service account (one-time)

`gsc_provider: "google"` authenticates as a Google *service account* — a robot
Google account with its own key file. It does **not** use your personal login.
Two things have to line up: the app needs the key file, and Search Console needs
to trust that service account. Steps:

1. In the [Google Cloud Console](https://console.cloud.google.com/), pick or
   create a project.
2. Enable the **Google Search Console API** for that project (APIs & Services →
   Library → search "Search Console").
3. Create a **service account** (IAM & Admin → Service Accounts → Create).
4. On that service account, create a **key** of type **JSON** and download it.
   This downloaded file is your key file.
5. Put the key file where the app can read it, and point `gsc_key_file` at it.
   By default the app looks for `service_account.json` next to where you run it
   (in `src/`); you can also give an absolute path like
   `"/etc/secrets/gsc.json"`. In a container, this is the file you **mount** in
   and point `gsc_key_file` at.
6. Copy the service account's email address — it looks like
   `something@your-project.iam.gserviceaccount.com`.
7. In [Google Search Console](https://search.google.com/search-console) → your
   property → **Settings → Users and permissions**, add that email as a user.
   Read-only access is enough. **This is the step people forget** — without it,
   the key is valid but Google returns no data for your site.

The access it requests is read-only (`webmasters.readonly`) — the agent never
writes to Search Console.

### `gsc_domain` on the input

Which site to query is set **per run**, in `input.json`, not here — because one
tenant could own several properties. When `gsc_provider` is `"google"`,
`input.gsc_domain` must be a real Search Console property identifier, in one of
two shapes:

- a **domain property**: `"sc-domain:example.com"`
- a **URL-prefix property**: `"https://example.com/"`

It has to match a property the service account was added to in step 7.

---

## Website traffic

Your site's traffic numbers, turned into a one-line `summary` the writer can
reference. You have four ways to provide it.

| Field | Type | Default | Used with |
|---|---|---|---|
| `traffic_provider` | `str` | `"mock"` | `"none"`, `"mock"`, `"cloudflare"`, `"templated"`, `"custom"`. |
| `cloudflare_api_token` | `str` | `""` | `"cloudflare"` |
| `cloudflare_zone_id` | `str` | `""` | `"cloudflare"` |
| `traffic_custom_class` | `str` | `""` | `"custom"` — `"module.path:ClassName"`. |
| `traffic_source` | `str` | `"file"` | `"templated"` — read from a `"file"` or a live `"api"`. |
| `traffic_report_path` | `str` | `""` | `"templated"` + `source="file"`. |
| `traffic_api_url` | `str` | `""` | `"templated"` + `source="api"`. |
| `traffic_api_method` | `str` | `"GET"` | `"templated"` + `source="api"`. |
| `traffic_api_headers` | `dict` | `{}` | `"templated"` + `source="api"` (e.g. an auth header). |
| `traffic_api_timeout_seconds` | `float` | `10.0` | `"templated"` + `source="api"`. |
| `traffic_summary_template` | `str` | `""` | `"templated"` — produces the `summary` text. |

- **`"none"`** — you have no traffic tool. The `summary` stays empty and prompts
  skip it cleanly. Perfectly fine.
- **`"mock"`** — fake numbers, for offline runs.
- **`"cloudflare"`** — a real, built-in Cloudflare integration (setup below).
- **`"templated"`** — any other traffic source (Plausible, GA4, Fathom, a CSV
  export you convert to JSON) via a template. See
  [Templates, explained properly](#templates-explained-properly-with-examples).
- **`"custom"`** — your own code. See [extending.md](extending.md).

### Setting up the Cloudflare token (one-time)

1. In the Cloudflare dashboard: **My Profile → API Tokens → Create Token →
   Create Custom Token**.
2. Give the token these permissions on the zone (domain) you want:
   - **Zone → Zone → Read** — lets it see the zone (read the DNS zone).
   - **Zone → Analytics → Read** — lets it read the traffic analytics.
3. Scope it to the specific zone (or all zones), create it, and copy the token
   into `cloudflare_api_token`.
4. Set `cloudflare_zone_id`: open the domain's **Overview** page in the
   dashboard — the Zone ID is in the right-hand "API" sidebar.

If Cloudflare Web Analytics / Bot Management isn't enabled on the zone, the
richer breakdowns (organic vs. social, human vs. bot) quietly fall back to zero
rather than failing — you still get request totals and the trend.

---

## Product analytics

Your product's own numbers — whatever you track. The agent turns them into two
things for the writer:

- a **`summary`**: one line of plain text ("214 signups this month, $4,820 MRR").
- **`highlights`**: a short list of specific items worth linking to, each a
  `{label, url}` (your top blog posts, your bestselling products, your most
  upvoted ideas).

| Field | Type | Default | Used with |
|---|---|---|---|
| `analytics_provider` | `str` | `"mock"` | `"mock"`, `"templated"`, `"custom"`. |
| `analytics_report_path` | `str` | `"tools/report.json"` | `"templated"` + `source="file"`. |
| `analytics_custom_class` | `str` | `""` | `"custom"` — `"module.path:ClassName"`. |
| `analytics_highlights_limit` | `int` | `3` | How many highlights the model gets. |
| `analytics_source` | `str` | `"file"` | `"file"` or `"api"`. |
| `analytics_api_url` | `str` | `""` | `"templated"` + `source="api"`. |
| `analytics_api_method` | `str` | `"GET"` | `"templated"` + `source="api"`. |
| `analytics_api_headers` | `dict` | `{}` | `"templated"` + `source="api"`. |
| `analytics_api_timeout_seconds` | `float` | `10.0` | `"templated"` + `source="api"`. |
| `analytics_summary_template` | `str` | `""` | `"templated"` — produces the `summary`. |
| `analytics_highlights_template` | `str` | `""` | `"templated"` — produces the `highlights` JSON array. |

How to connect it: **use `"templated"` if your analytics is JSON you can map
with a snippet** (the common case — covered in depth next). Use `"custom"` if it
needs real code, like a database query — see [extending.md](extending.md).

---

## Templates, explained properly (with examples)

This is the feature that lets you plug in **your own** analytics or traffic
**without writing any code**, so it's worth getting comfortable with. It's the
same mechanism for both analytics and traffic.

### The idea

Your data is JSON with *your* field names. The agent expects a fixed shape (a
`summary`, and for analytics a list of `highlights`). A **template** is the
bridge: a short snippet that reads your JSON and writes out the shape the agent
wants. It's [Jinja2](https://jinja.palletsprojects.com/) — the same "fill in the
blanks" idea as a mail merge.

### The two rules you need

1. **Where your data appears.** Inside a template, your raw JSON is available as
   `data`. So if your JSON is `{"totals": {"signups": 214}}`, then
   `{{ data.totals.signups }}` prints `214`. Templates also get one extra value:
   - analytics templates get **`limit`** (how many highlights to include),
   - traffic templates get **`days`** (the time window, e.g. 28).
2. **What each template must produce.**
   - `analytics_summary_template` and `traffic_summary_template` → **plain
     text** (one line).
   - `analytics_highlights_template` → a **JSON array** of `{"label": ...,
     "url": ...}` objects. This one is stricter because you're writing JSON by
     hand; the [highlights walkthrough](#building-the-highlights-array-step-by-step)
     below shows exactly how.

Templates are checked when your config loads — against your **real** data (the
actual file, or a live call to your API). A broken template or a wrong field
name fails right then, with a message naming the problem, not mid-run.

> **Tip: don't know your own field names?**
> [`agent/utils/analytics_schema.py`](../src/agent/utils/analytics_schema.py)'s
> `infer_fields(raw_json)` prints every path in your JSON (like
> `data.totals.signups`) with its type and an example value.

### Example 1 — a SaaS app (analytics from a file)

Your analytics export, saved to a file (`tenant-data/report.json`):

```json
{
  "totals": { "signups_30d": 214, "mrr_usd": 4820, "active_users": 1310 },
  "top_posts": [
    { "title": "How we cut churn in half", "slug": "cut-churn", "reads": 900 },
    { "title": "Pricing lessons from year one", "slug": "pricing-lessons", "reads": 640 }
  ]
}
```

Config:

```jsonc
{
  "analytics_provider": "templated",
  "analytics_source": "file",
  "analytics_report_path": "tenant-data/report.json",
  "analytics_highlights_limit": 3,

  "analytics_summary_template": "{{ data.totals.signups_30d }} new signups in the last 30 days, {{ data.totals.active_users }} active users, ${{ data.totals.mrr_usd }} MRR.",

  "analytics_highlights_template": "[{% for p in data.top_posts[:limit] %}{\"label\": {{ (p.title + \" (\" + p.reads|string + \" reads)\")|tojson }}, \"url\": {{ (\"https://myapp.com/blog/\" + p.slug)|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
}
```

Produces:

```json
{
  "summary": "214 new signups in the last 30 days, 1310 active users, $4820 MRR.",
  "highlights": [
    { "label": "How we cut churn in half (900 reads)", "url": "https://myapp.com/blog/cut-churn" },
    { "label": "Pricing lessons from year one (640 reads)", "url": "https://myapp.com/blog/pricing-lessons" }
  ]
}
```

### Example 2 — an online store (analytics from a live API)

Same idea, but the data comes from an HTTP endpoint instead of a file. Say your
API returns:

```json
{
  "period": "last_28_days",
  "revenue": 18240.5,
  "orders": 342,
  "bestsellers": [
    { "name": "Cast-iron skillet", "handle": "cast-iron-skillet", "units": 120 },
    { "name": "Chef knife", "handle": "chef-knife", "units": 98 }
  ]
}
```

Config:

```jsonc
{
  "analytics_provider": "templated",
  "analytics_source": "api",
  "analytics_api_url": "https://shop.example.com/internal/analytics",
  "analytics_api_headers": { "Authorization": "Bearer YOUR_INTERNAL_API_KEY" },
  "analytics_highlights_limit": 3,

  "analytics_summary_template": "${{ data.revenue|round|int }} in revenue from {{ data.orders }} orders over the {{ data.period|replace('_', ' ') }}.",

  "analytics_highlights_template": "[{% for p in data.bestsellers[:limit] %}{\"label\": {{ (p.name + \" — \" + p.units|string + \" sold\")|tojson }}, \"url\": {{ (\"https://shop.example.com/products/\" + p.handle)|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
}
```

The only difference from Example 1 is `source`, `api_url`, and `headers`. The
`|round|int` and `|replace(...)` bits are Jinja2 **filters** — small transforms
you can chain to clean up values.

### Example 3 — website traffic (Plausible via API)

Traffic only needs a `summary`, so there's just one template and no highlights.
Say [Plausible](https://plausible.io) returns:

```json
{ "results": { "visitors": { "value": 5120 }, "pageviews": { "value": 8730 } } }
```

Config:

```jsonc
{
  "traffic_provider": "templated",
  "traffic_source": "api",
  "traffic_api_url": "https://plausible.io/api/v1/stats/aggregate?site_id=example.com&period=30d&metrics=visitors,pageviews",
  "traffic_api_headers": { "Authorization": "Bearer YOUR_PLAUSIBLE_API_KEY" },
  "traffic_summary_template": "{{ data.results.visitors.value }} visitors and {{ data.results.pageviews.value }} pageviews in the last {{ days }} days."
}
```

Produces `{ "summary": "5120 visitors and 8730 pageviews in the last 28 days." }`.

### Building the highlights array, step by step

The highlights template is the one that trips people up, because you're
producing **JSON text by hand**. Here's the SaaS one from Example 1, unpacked:

```jinja
[
  {% for p in data.top_posts[:limit] %}
    {"label": {{ (p.title + " (" + p.reads|string + " reads)")|tojson }},
     "url":   {{ ("https://myapp.com/blog/" + p.slug)|tojson }}}
    {% if not loop.last %},{% endif %}
  {% endfor %}
]
```

Piece by piece:

- `[ ... ]` — the whole thing must come out as a JSON array, so it's wrapped in
  brackets.
- `{% for p in data.top_posts[:limit] %} ... {% endfor %}` — loop over your
  items. `[:limit]` takes only the first `analytics_highlights_limit` of them.
- `{"label": ..., "url": ...}` — each item becomes one object with exactly these
  two keys. Both are **required**.
- `... |tojson` — **always wrap each value in `|tojson`.** It adds the quotes and
  safely escapes anything weird in your text (quotes, emoji, newlines). Without
  it, a stray character in a title produces broken JSON and the config won't
  load. Because `|tojson` adds the quotes itself, notice there are **no manual
  quotes** around the value.
- `p.reads|string` — `+` only joins strings, so convert a number with `|string`
  before joining it into a label.
- `{% if not loop.last %},{% endif %}` — put a comma between items but not after
  the last one (a trailing comma is invalid JSON).

The real thing is written on one line and needs `\"` for the quotes because it
lives inside a JSON string in your config file — the multi-line version above is
just easier to read. Start from a working example (this one, or Echooers below),
swap in your field names, and keep the structure.

### Templated vs. custom: which one?

| Use `templated` when… | Use `custom` when… |
|---|---|
| Your data is already JSON (file or API). | Your data needs a database query or SDK call. |
| The mapping is "read these fields, format them." | You need real logic, multiple steps, or another API. |
| You want zero code and zero deploys. | A template would be a hack; code is cleaner. |

`custom` is fully documented, with worked examples, in
[extending.md](extending.md).

---

## Opportunity discovery

Turn this on and the agent finds its own topics, threads, and links instead of
only doing what you ask.

| Field | Type | Default | Notes |
|---|---|---|---|
| `discovery_sources` | `list[dict]` | `[]` | Each entry is one source. A non-empty list adds the `discover` + `choose_channel` steps to the pipeline. |

Each entry is `{"name": "...", "provider": "...", ...}`, where the extra fields
depend on the provider:

| Provider | Extra fields | Notes |
|---|---|---|
| `"mock"` | `"fail"` (`bool`, default `false`) | A fixed fixture for testing. `"fail": true` simulates the source erroring. |
| `"llm"` | `"prompt_template"` (optional), `"max_opportunities"` (default `5`), `"grounded"` (default `true`) | The AI model finds opportunities. **Grounded (default) backs it with live Google Search** — real results with real citation URLs, not a guess from training data. Set `"grounded": false` for the old behavior. |
| `"custom"` | `"class"` (`"module.path:ClassName"`) | Your own finder — see [extending.md](extending.md). |

You can list **several** sources; they all run (in parallel when there are 2+)
and their results are pooled. Example — one AI source plus your own Reddit
finder:

```jsonc
{
  "discovery_sources": [
    { "name": "web_trends", "provider": "llm", "max_opportunities": 5 },
    { "name": "reddit", "provider": "custom", "class": "my_tenant.reddit:RedditFinder" }
  ]
}
```

For how discovery scores opportunities and picks a channel, see
[architecture.md](architecture.md#discovery-the-agent-finding-its-own-work).

---

## Brand voice

The one place your actual product is described. These get folded into every
draft (and every `"llm"` discovery prompt), so this is where the agent learns
what your product *is*.

| Field | Type | Default |
|---|---|---|
| `brand_description` | `str` | A generic "a web platform that publishes content..." |
| `agent_goal` | `str` | A generic "increase qualified traffic..." |

Write `brand_description` the way you'd explain your product to a smart stranger
in two or three sentences. Write `agent_goal` as what growth means for you.

---

## Per-run defaults

Used when a specific run doesn't override them.

| Field | Type | Default | Notes |
|---|---|---|---|
| `default_channel` | `str` | `"site_article"` | Used when `input.channel` is omitted and discovery doesn't decide one. |
| `default_max_words` | `int` | `800` | Override per run with `input.params.max_words`. |
| `default_article_tone` | `str` | `"informative"` | Override with `input.params.tone`. |
| `default_comment_tone` | `str` | `"genuine and conversational"` | Override with `input.params.tone`. |

---

## Watching a run happen (verbose mode)

By default a run prints nothing until its final JSON. Verbose mode reports each
stage and each tool call as it happens — which stage is running now, how long each
call took, which tool failed and why.

```bash
python src/main.py -v      # stages and tool calls, with timings
python src/main.py -vv     # also shows prompts, responses, and decisions
```

```text
[  0.00s] > run 76e5ef96  channel=auto seed_keyword="static site seo" sources=3
[  0.03s]   > discover_source [trends]
[  0.03s]     > trends.discover
[  0.03s]     < trends.discover  0ms  found=1
[  0.03s]     ! broken.discover  0ms  error="RuntimeError: source 'broken' configured to fail"
[  0.04s]     > llm.generate  grounded=True
[  2.31s]     < llm.generate  2306ms  tokens=812 sources=3
[  2.31s] < run 76e5ef96  phase=done tokens=812 opportunities=2 tool_errors=1
```

**All of it goes to stderr**, never stdout — stdout still carries only the result
JSON, so `python src/main.py -v | jq` works exactly as before.

The `!` lines are the reason this exists: the agent degrades rather than aborting
when a tool fails, so a failed analytics call or discovery source otherwise only
shows up as a `tool_errors` entry buried in the final JSON.

| Field | Type | Default | Notes |
|---|---|---|---|
| `verbose` | `int` | `0` | `0` silent, `1` stages + tool calls + timings, `2` also payload previews. The `-v`/`-vv` flag always overrides this. |
| `verbose_format` | `str` | `"text"` | `"text"` for humans, or `"json"` for newline-delimited events — one JSON object per event, for a UI or log pipeline. Override with `--verbose-format`. |

Secrets are never printed: API keys, tokens, and auth headers are redacted by
name wherever they appear, and prompts and responses are truncated rather than
dumped whole. Verbose mode never changes a run's behavior or its output — if the
reporter itself hits an error, it stays quiet rather than failing the run.

---

## Self-review thresholds

The quick automated checks that run on every draft. They add advisory notes to
`output.metadata.qa_notes` — they never block a draft.

| Field | Type | Default | Flags when |
|---|---|---|---|
| `qa_article_max_words_overage_pct` | `float` | `0.25` | Body runs more than this fraction over `max_words`. |
| `qa_article_max_avg_sentence_words` | `float` | `30` | Average sentence length (readability proxy) is over this. |
| `qa_comment_max_words` | `int` | `80` | A reply is longer than this. |
| `qa_comment_max_links` | `int` | `1` | A reply has more links than this. |
| `qa_brand_mention_keywords` | `list[str]` | `["our product", "the platform", ...]` | A comment mentions any of these (case-insensitive substring). |
| `qa_disclosure_phrases` | `list[str]` | `["disclosure", "i work on", "i built", ...]` | A brand mention counts as disclosed when one of these is nearby; a mention with none gets flagged. |

Set `qa_brand_mention_keywords` to your product's own vocabulary — the default
only covers generic phrases like "the platform." If a signature feature of your
product is something people say in normal conversation (Echooers' "anonymous,"
say), add it here so a reply that name-drops it still gets the disclosure check.

---

## Prompt templates

| Field | Type | Default |
|---|---|---|
| `prompt_templates` | `dict[str, str]` | One generic template per channel |

You can override the wording for any of the three channels (`site_article`,
`external_article`, `engagement_comment`); any you leave out keeps the default.
These use the same Jinja2 mechanism as the data templates above. Variables you
can reference include `brand_description`, `agent_goal`, `keyword`, `tone`,
`max_words`, `context_text`, `analytics_summary`, `traffic_summary`, and
`highlights` — the built-in defaults in
[`agent/prompts/templates.py`](../src/agent/prompts/templates.py) are the best
reference for what's available per channel.

You control the wording; the system always appends its own "return exactly this
JSON" instruction afterward, so the output stays parseable no matter what you
write. Overrides are validated on config load. See
[architecture.md](architecture.md#prompts-the-system-owns-the-frame-you-own-the-wording).

---

## Worked example: Echooers (all of it together)

[Echooers](https://echooers.com) is an anonymous social platform — no login, no
identity attached to a post. Here's its full config, showing how the pieces
combine in a real product.

```jsonc
{
  // --- Real vendors Echooers actually uses ---
  "llm_provider": "gemini",
  "llm_model": "gemini-pro-latest",
  "gemini_api_key": "YOUR_GEMINI_API_KEY",

  "gsc_provider": "google",
  "gsc_key_file": "service_account.json",

  "traffic_provider": "cloudflare",
  "cloudflare_api_token": "YOUR_CLOUDFLARE_API_TOKEN",
  "cloudflare_zone_id": "YOUR_CLOUDFLARE_ZONE_ID",

  // --- Analytics: Echooers' own idea/upvote JSON, mapped with two templates.
  // data.data.overview.total_ideas etc. are Echooers' own field names. ---
  "analytics_provider": "templated",
  "analytics_source": "file",
  "analytics_report_path": "tools/report.json",
  "analytics_highlights_limit": 3,
  "analytics_summary_template": "{{ data.data.overview.total_ideas }} ideas shared so far, {{ data.data.overview.total_upvotes }} upvotes and {{ data.data.overview.total_views }} views across the community.",
  "analytics_highlights_template": "[{% for i in data.data.top_by_upvotes[:limit] %}{\"label\": {{ (i.content[:200] + \" (\" + i.upvotes|string + \" upvotes, \" + i.views|string + \" views)\")|tojson }}, \"url\": {{ (\"https://echooers.com/idea/\" + i.id)|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]",

  // --- Discovery: a search-backed AI source instead of a bespoke integration ---
  "discovery_sources": [
    { "name": "echooers_ideas", "provider": "llm", "max_opportunities": 5 }
  ],

  // --- Brand voice: the one place "anonymous, no login" lives ---
  "brand_description": "An anonymous social platform, similar in spirit to Twitter/Reddit but with no login or signup: people post, vote, share, and comment freely without tracking or an identity attached, so they never have to fear reputation damage or backlash for what they say.",
  "agent_goal": "Increase qualified traffic to the platform — attract new visitors via search and genuine community discovery, not just serve people already there.",

  "default_article_tone": "informative",
  "default_comment_tone": "genuine and conversational",

  // --- Self-review: "anonymous"/"no login" ARE the product, so mentioning them
  // in a reply needs the same disclosure check as saying "our platform" ---
  "qa_brand_mention_keywords": [
    "our product", "our platform", "our app", "our service",
    "the platform", "the app", "the product", "the service",
    "anonymous", "no login", "no signup", "no tracking"
  ],

  "prompt_templates": {
    "engagement_comment": "You're replying as a real community member.\nProduct: {{ brand_description }}\nReplying to: \"{{ context_text }}\"\nTone: {{ tone }}. Keep it to 2-3 sentences."
  }
}
```

Why each non-default choice was made:

- **`traffic_provider: "cloudflare"`** — Echooers is already on Cloudflare, so
  the built-in client is a better signal than a mock.
- **`analytics_provider: "templated"`** — the analytics is just Echooers' own
  JSON, so two templates map it without any Python.
- **`discovery_sources` with `provider: "llm"`** — an anonymous platform has no
  profile pages to rank the usual way, so the best way to find topics is a
  search-backed AI model, not a specific vendor API.
- **The extra `qa_brand_mention_keywords`** — the anonymity pitch *is* the
  brand, so mentioning it in a reply needs a disclosure just like naming the
  product would.

To adapt this for **your** product: change the brand voice, swap the analytics
templates for your JSON's field names (Examples 1–3 above are closer starting
points for a typical SaaS or store), set your vendor keys, and adjust the
brand-mention keywords. You shouldn't need to touch any code.
</content>
