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
- Connecting Google or Cloudflare? See [Search performance](#search-performance-how-your-pages-already-rank)
  and [Website traffic](#website-traffic) for the setup steps.
- Need real code, not a template? See [extending.md](extending.md).
- Want a full working config to copy? The [examples/](../examples/) folder has
  six runnable ones (SaaS, e-commerce, community, job board, MCP), simple to
  advanced.

`python src/main.py run --tenant <name>`
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
| A vendor name (`gemini`, `google`, `cloudflare`, `duckduckgo`) | A real, built-in integration with that vendor. | You use that specific vendor. |
| `llm` *(discovery only)* | The AI model itself finds the opportunities. | You want the agent to discover topics without building an integration. |

So `"analytics_provider": "templated"` means "map my analytics with a template,"
and `"traffic_provider": "cloudflare"` means "get traffic from Cloudflare." Once
you've seen this for one job, every other job reads the same way.

`python src/main.py list-tools --all` prints this menu for every job, with your
tenant's current choice marked. It reads the same registry the agent builds
from, so it can't tell you about a provider that doesn't exist, or omit one that
does.

### Nothing here is mandatory

Gemini, Google Search Console and Cloudflare are **built-in conveniences, not
requirements** — they exist so a first run against real data takes minutes. Drop
any of them, or all of them:

- No Cloudflare? `"traffic_provider": "templated"` maps any traffic tool's JSON,
  `"custom"` runs your own code, and `"none"` turns traffic off entirely.
- No Search Console? Nothing to do — `search_performance_provider` defaults to
  `"none"`, and the agent picks its topic from your seed keyword, your analytics,
  or what discovery found. Have rank data from somewhere else? `"templated"`
  maps any JSON, `"custom"` runs your own code.
- Different model, a local one, or a gateway? `"llm_provider": "custom"` — and
  grounding still works, because searching is the system's job, not the model's.
- Analytics in your own database? `"analytics_provider": "custom"`.
- Don't want the agent searching the web? `"search_provider": "none"`.

A `tenant.json` of `{}` runs. Every job has a working default, and no job
requires a vendor account.

### Each job is two fields

**`<job>_provider`** picks the implementation; **`<job>_options`** holds that
implementation's own settings — including its credentials:

```jsonc
{
  "llm_provider": "gemini",
  "llm_options": { "api_key": "YOUR_GEMINI_API_KEY", "model": "gemini-pro-latest" }
}
```

Settings live with the provider that uses them, never at the top level of the
config, because *which settings are even meaningful depends on which provider you
picked*: `api_token` and `zone_id` mean something to Cloudflare and nothing to a
templated feed. That also gives a `custom` class of yours somewhere to put its
own settings, and it means a credential sits next to the thing that needs it.

Each job's section below lists the option names for every provider it offers.

> **Upgrading an older config?** Two kinds of change, and the loader names the
> destination for both, so a stale config tells you exactly what to fix:
>
> - Settings like `gemini_api_key`, `gsc_key_file`, `cloudflare_api_token`,
>   `analytics_report_path` and `analytics_summary_template` used to sit at the
>   top level. They now belong to their provider's `options`.
> - `gsc_provider` / `gsc_options` were **renamed** to
>   `search_performance_provider` / `search_performance_options` — same meaning,
>   a name describing the job rather than one vendor. Google's own property
>   identifier moved off the run's input (`input.gsc_domain`) into
>   `search_performance_options.gsc_domain`, and the site itself became the
>   top-level `site_url`.

The rest of this page goes job by job.

---

## The AI model (LLM)

The model that writes your drafts.

| Field | Type | Default | Notes |
|---|---|---|---|
| `llm_provider` | `str` | `"mock"` | `"mock"` (offline, no key), `"gemini"`, or `"custom"`. |
| `llm_custom_class` | `str` | `""` | `"module:ClassName"`, when `llm_provider` is `"custom"`. |
| `llm_options` | `dict` | `{}` | The selected provider's settings — see below. |

| `llm_options` for `"gemini"` | Default | Notes |
|---|---|---|
| `api_key` | `""` | Required. Get one from [Google AI Studio](https://aistudio.google.com/apikey). |
| `model` | `"gemini-2.0-flash"` | Any Gemini model name. A run can override it with `input.model`. |
| `timeout_seconds` | `120` | Per call. Generous because a grounded call really searches first. |

```jsonc
{
  "llm_provider": "gemini",
  "llm_options": { "api_key": "YOUR_GEMINI_API_KEY", "model": "gemini-pro-latest" }
}
```

**Bringing your own model.** `"custom"` points at a class of yours implementing
one method, exactly like the other jobs — a different vendor, a local model, a
gateway, or a wrapper that adds retries to Gemini:

```jsonc
{
  "llm_provider": "custom",
  "llm_custom_class": "my_llm:Client",
  "llm_options": { "endpoint": "http://localhost:11434", "model": "llama3" }
}
```

See [extending.md](extending.md) for the class contract (`generate(prompt, *,
model=None, grounded=False) -> LLMResponse`, sync or async).

---

## Web search (how discovery stays grounded)

When discovery is on, the agent **searches the real web before it writes
anything**, and only trusts URLs that came back from that search. This is on by
default and it uses **DuckDuckGo** — no API key, no account, no billing.

| Field | Type | Default | Notes |
|---|---|---|---|
| `search_provider` | `str` | `"duckduckgo"` | `"duckduckgo"`, `"none"`, `"mock"`, or `"custom"`. |
| `search_custom_class` | `str` | `""` | `"module:ClassName"`, when `search_provider` is `"custom"`. |
| `search_options` | `dict` | `{}` | The selected provider's settings — see below. |

| `search_options` for `"duckduckgo"` | Default | Notes |
|---|---|---|
| `backend` | `"duckduckgo"` | Which engine to ask first. `"auto"` lets the library pick; a comma-separated list names specific ones. |
| `fallback_backend` | `"auto"` | Asked only when `backend` raises or finds nothing. Set to `""` for strictly-DuckDuckGo-or-nothing. |
| `region` | `"wt-wt"` | Worldwide. `"us-en"`, `"uk-en"`, `"fr-fr"`, … to localize results. |
| `safesearch` | `"moderate"` | `"on"`, `"moderate"`, or `"off"`. |
| `timelimit` | `""` | `"d"`, `"w"`, `"m"`, `"y"` to restrict to the last day/week/month/year. `""` is no limit. |
| `timeout_seconds` | `10` | Per search. |

**Why a search engine rather than the model's own grounding.** Gemini can search
for itself; a local model, a gateway, or most other vendors can't. Making
grounding the *system's* job instead of the model's means "does this agent find
real pages?" stops depending on which model you picked — and you can change model
without changing what the agent can see.

The order is exact and documented:

1. **A search provider** (the default). The agent writes a few short search
   queries, runs them, puts the real results in the prompt, and treats those URLs
   as the only trustworthy ones. Anything else the model claims is discarded.
2. **The model's own grounding**, if it has any (today: Gemini) — used when
   search is off or found nothing.
3. **Neither.** The model answers from training data, and links come back
   unverified. `-v` says so out loud rather than letting it pass silently.

Each step falls through to the next: a search that errors or returns nothing
costs the run its search grounding, not its results.

**Falling through is never silent.** Every opportunity records which step
produced it — `raw.grounding` is `"search"`, `"llm"`, or `"none"`, with
`raw.grounding_error` when a search failed — so "these links were verified" and
"the search engine was rate-limiting us" are distinguishable in the output rather
than both looking like a successful run.

**Why `fallback_backend` exists.** DuckDuckGo rate-limits by IP: after enough
searches from one address, every request fails for a while. That's fine for the
occasional run and bad as a *default*, so when DuckDuckGo won't answer the client
asks another engine rather than letting a run quietly stop grounding.

**Turning it off.** `"search_provider": "none"` goes straight to step 2 —
Gemini's native grounding, as before. `"grounded": false` on a discovery source
skips 1 *and* 2 for that source.

**Your own engine.** Bing, Serper, Brave, a self-hosted SearxNG, or an internal
index — one method, `search(query, limit=10) -> [{"title", "url", "snippet"}]`,
sync or async:

```jsonc
{
  "search_provider": "custom",
  "search_custom_class": "my_search:Client",
  "search_options": { "api_key": "…" }
}
```

Nothing searches unless `discovery_sources` is configured — a tenant that isn't
using discovery makes no search calls at all.

---

## Which site is this? (`site_url`)

One vendor-neutral field naming the website this agent works on:

```jsonc
{ "site_url": "https://example.com" }
```

Every signal receives it as `context.site_url`, and any tool that needs to know
the site — rather than one vendor's name for it — reads this. It's optional; a
tenant without it still runs.

It is deliberately **not** a provider's identifier. Google Search Console calls
your site `"sc-domain:example.com"`, which is an identifier in *Google's*
namespace and means nothing to anything else, so that lives with the provider
that understands it (below). A run can override `site_url` for itself with
`input.site_url`, for a caller driving several sites through one config.

---

## Search performance (how your pages already rank)

Which queries your site already appears for, where they rank, and which are
close enough to page one to be worth work. The agent uses this to find
"striking distance" keywords — ones you *almost* rank for, where a good article
can push you onto page one.

**This is a job, not a vendor.** Google Search Console is one way to answer it;
Bing Webmaster Tools, a rank tracker's export, or an agency's monthly CSV answer
it just as well, and all of them get the same treatment.

| Field | Type | Default | Notes |
|---|---|---|---|
| `search_performance_provider` | `str` | `"none"` | `"none"`, `"google"`, `"templated"`, `"mock"`, or `"custom"`. |
| `search_performance_custom_class` | `str` | `""` | `"module:ClassName"`, when the provider is `"custom"`. |
| `search_performance_options` | `dict` | `{}` | The selected provider's settings — see below. |

| `options` for… | Keys |
|---|---|
| `"google"` | `gsc_domain` (**required** — your Search Console property), `key_file` (default `"service_account.json"`), `timeout_seconds` (30, per request) |
| `"templated"` | `source` (`"file"` or `"api"`), `report_path`, `api_url`, `api_method`, `api_headers`, `api_timeout_seconds`, plus `rows_template` |
| `"custom"` | whatever your class reads |
| `"none"`, `"mock"` | none |

### Why the default is "none"

Because a fixture must not outrank you. The agent prefers a striking-distance
row *over* your `seed_keyword` — that's the point of having rank data. So when
the default was a fixture, a tenant who asked for `"cron job monitoring"` got a
draft about the fixture's canned keyword instead, silently, while this page
promised the seed keyword would be used.

With `"none"`, there are no rows, and the topic comes from your seed keyword,
then an analytics highlight, then whatever discovery found — all your own
current data. See [`_pick_keyword`](../src/agent/graph/stages/analyze.py).

`"mock"` is still there for offline demos, and its rows are product-neutral.

### Google Search Console

```jsonc
{
  "site_url": "https://example.com",
  "search_performance_provider": "google",
  "search_performance_options": {
    "gsc_domain": "sc-domain:example.com",
    "key_file": "data/service_account.json"
  }
}
```

`gsc_domain` is **required** and is *not* the same as `site_url`. It is a Search
Console **property identifier**, in one of two shapes:

- a **domain property**: `"sc-domain:example.com"`
- a **URL-prefix property**: `"https://example.com/"`

It has to match a property your service account was added to (step 7 below).
It's checked when the client is built, so `check-data` catches a wrong one
without spending an API call.

#### Setting up the service account (one-time)

`"google"` authenticates as a Google *service account* — a robot Google account
with its own key file. It does **not** use your personal login. Two things have
to line up: the app needs the key file, and Search Console needs to trust that
service account. Steps:

1. In the [Google Cloud Console](https://console.cloud.google.com/), pick or
   create a project.
2. Enable the **Google Search Console API** for that project (APIs & Services →
   Library → search "Search Console").
3. Create a **service account** (IAM & Admin → Service Accounts → Create).
4. On that service account, create a **key** of type **JSON** and download it.
   This downloaded file is your key file.
5. Put the key file where the app can read it, and point
   `search_performance_options.key_file` at it. By default the app looks for
   `service_account.json` inside your tenant's folder;
   `"data/service_account.json"` puts it in `data/` alongside your other files,
   and an absolute path like `"/etc/secrets/gsc.json"` also works. In a
   container, this is the file you **mount** in.
6. Copy the service account's email address — it looks like
   `something@your-project.iam.gserviceaccount.com`.
7. In [Google Search Console](https://search.google.com/search-console) → your
   property → **Settings → Users and permissions**, add that email as a user.
   Read-only access is enough. **This is the step people forget** — without it,
   the key is valid but Google returns no data for your site.

The access it requests is read-only (`webmasters.readonly`) — the agent never
writes to Search Console.

### Rank data from anywhere else (`templated`)

If your rank data is JSON you can reshape with a snippet, you need no code.
`rows_template` renders to a **JSON array** of
`{"query", "clicks", "impressions", "ctr", "position"}` objects — the same
"render to a JSON array" rule as analytics' `highlights_template`:

```jsonc
{
  "search_performance_provider": "templated",
  "search_performance_options": {
    "source": "file",
    "report_path": "data/rankings.json",
    "rows_template": "[{% for r in data.rows %}{\"query\": {{ r.term|tojson }}, \"position\": {{ r.rank }}, \"impressions\": {{ r.seen }}, \"clicks\": {{ r.visits }}, \"ctr\": {{ r.ctr }}}{% if not loop.last %},{% endif %}{% endfor %}]"
  }
}
```

**Your template supplies data, never judgement.** You do not compute
`opportunity`, `score` or `reason` — the agent does, from the four numbers
above, using exactly the same logic the Google provider gets. Which keyword is
worth targeting must not vary by where the numbers came from, and reimplementing
striking-distance classification in Jinja2 would be miserable anyway.

`trend` and `top_page` come out `"flat"` and `null` here, because a single
snapshot has no prior period to compare against. Everything else is identical.

### Rank data that needs code (`custom`)

Bing Webmaster Tools, Ahrefs, Semrush, an internal warehouse — one class with one
method, `search_analytics(days=28, row_limit=500)`. See
[extending.md](extending.md).

---

## Website traffic

Your site's traffic numbers, turned into a one-line `summary` the writer can
reference. You have four ways to provide it.

| Field | Type | Default | Used with |
|---|---|---|---|
| `traffic_provider` | `str` | `"mock"` | `"none"`, `"mock"`, `"cloudflare"`, `"templated"`, `"custom"`. |
| `traffic_custom_class` | `str` | `""` | `"custom"` — `"module:ClassName"` — a file in your tenant's `plugins/` folder. |
| `traffic_options` | `dict` | `{}` | The selected provider's settings — see below. |

| `traffic_options` for… | Keys |
|---|---|
| `"cloudflare"` | `api_token`, `zone_id`, `timeout_seconds` (default 15) |
| `"templated"` | `source` (`"file"` or `"api"`), `report_path`, `api_url`, `api_method`, `api_headers`, `api_timeout_seconds`, and `summary_template` — the Jinja2 template producing the `summary` text |
| `"custom"` | whatever your class reads |
| `"mock"`, `"none"` | none |

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
   into `traffic_options.api_token`.
4. Set `traffic_options.zone_id`: open the domain's **Overview** page in the
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
| `analytics_custom_class` | `str` | `""` | `"custom"` — `"module:ClassName"` — a file in your tenant's `plugins/` folder. |
| `analytics_highlights_limit` | `int` | `3` | How many highlights the model gets. Not provider-specific — it's what the *agent* asks for, whichever provider answers. |
| `analytics_options` | `dict` | `{}` | The selected provider's settings — see below. |

| `analytics_options` for… | Keys |
|---|---|
| `"templated"` | `source` (`"file"` or `"api"`), `report_path`, `api_url`, `api_method`, `api_headers`, `api_timeout_seconds`, plus `summary_template` and `highlights_template` |
| `"custom"` | whatever your class reads |
| `"mock"` | none |

How to connect it: **use `"templated"` if your analytics is JSON you can map
with a snippet** (the common case — covered in depth next). Use `"custom"` if it
needs real code, like a database query — see [extending.md](extending.md).

---

## Any other data source (`signal_sources`)

The three sections above are the inputs that get you to a real run quickly. They
are not the *model* of an input. A trends feed, a rank tracker, a keyword API, a
competitor watcher, your own internal dashboard — all the same kind of thing, and
adding one is configuration, not a fork of this project.

`signal_sources` is that open list. Each entry is a named input:

```jsonc
"signal_sources": [
  { "name": "keyword_trends", "provider": "templated", "options": { "...": "..." } },
  { "name": "rank_tracker",   "provider": "custom",
    "class": "rank_tracker:RankTracker", "options": { "...": "..." } }
]
```

| Key | Type | Meaning |
|---|---|---|
| `name` | `str` | **Required, unique.** How this signal reaches your prompt: `{{ signals.keyword_trends }}`. |
| `provider` | `str` | `"mock"`, `"templated"`, or `"custom"`. Defaults to `"mock"`. |
| `class` | `str` | `"custom"` only — `"module:ClassName"`, a file in your tenant's `plugins/` folder. |
| `options` | `dict` | That provider's own settings. |

| `options` for… | Keys |
|---|---|
| `"templated"` | `source` (`"file"` or `"api"`), `report_path`, `api_url`, `api_method`, `api_headers`, `api_timeout_seconds`, plus `summary_template`, `facts_template`, `items_template` |
| `"custom"` | whatever your class reads |
| `"mock"` | `fail` (`bool`, default `false`) — makes this signal raise, to see the degrade path |

Worked end to end in [example 07](../examples/07-signal-inputs/).

### What a signal produces

Three parts, of which only the first is required:

```jsonc
{
  "summary": "2 tracked keywords sit at positions 11-20.",  // text, into the prompt as-is
  "facts":   { "tracked": 4, "striking_distance": 2 },       // named values
  "items":   [ { "label": "indoor herb garden", "position": 12 } ]  // rows
}
```

For `"templated"`, that's three Jinja2 templates: `summary_template` renders to
text, `facts_template` to a JSON **object**, `items_template` to a JSON **array**
(the same rule as `highlights_template` — see
[Templates, explained properly](#templates-explained-properly-with-examples)).
All three render against `{"data": <your raw JSON>, "context": <this run>}`.

That `context` is the one thing signals have that analytics and traffic don't:
`{{ context.seed_keyword }}`, `{{ context.site_url }}`, `{{ context.channel }}`,
`{{ context.context_text }}`. A signal is often *about* what this run is going
after, not just about the site.

For `"custom"`, it's one method — see [extending.md](extending.md#a-signal-input).

### Using signals in a prompt

Every signal arrives as `signals`, keyed by name:

```jinja
{% for name, signal in signals.items() %}
{% if signal.summary %}- {{ name }}: {{ signal.summary }}
{% endif %}
{% endfor %}
```

That loop names no signal, so adding one changes no template. You can also reach
into one you know:

```jinja
{{ signals.rank_tracker.facts.striking_distance }} keywords are close to page one.
{% for row in signals.rank_tracker['items'] %}- {{ row.label }} ({{ row.position }})
{% endfor %}
```

`signals` has **one key per configured signal on every run**, whatever happened
to it — a signal that failed or had nothing to report contributes empty values,
not a missing key. So a template naming your signal keeps working, and a
misspelled name is rejected when you save the config rather than mid-run. (Keys
*inside* `facts`/`items` are your provider's own vocabulary, so those aren't
checked at save time — a wrong one renders empty.)

### The three built-in inputs, in the same list

`search_performance`, `traffic` and `analytics` are **reserved names**. An entry using one
selects that built-in tool instead of adding a fourth signal:

```jsonc
"signal_sources": [
  { "name": "search_performance", "provider": "google",
    "options": { "gsc_domain": "sc-domain:example.com", "key_file": "service_account.json" } },
  { "name": "traffic", "provider": "cloudflare", "options": { "api_token": "...", "zone_id": "..." } },
  { "name": "trends",  "provider": "templated",  "options": { "...": "..." } }
]
```

This is purely so you can see every input in one block. `search_performance_provider` /
`traffic_provider` / `analytics_provider` and their `*_options` still work and
mean exactly what they always did — **nothing needs migrating**. Where both
appear, the `signal_sources` entry wins.

The three keep their own shapes (`search_performance` returns query rows, `analytics` returns
`highlights`), so they reach the prompt as `keyword`, `analytics_summary`,
`highlights` and `traffic_summary` — not as `signals` keys. They also get that
kind's providers: `{"name": "search_performance", "provider": "cloudflare"}` is an
error, because Cloudflare answers a traffic question, not a ranking one.

### When one breaks

Every signal is collected **concurrently** and fails **independently**. One that
raises contributes empty values and a `discovery.tool_errors` entry in the
output; the run continues on everything else. Adding ten signals costs one round
trip, not ten.

`check-data --tenant <name>` builds every configured signal without running
anything — the fastest way to find a bad path or an unimportable class.

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
   - `summary_template` (analytics and traffic) → **plain
     text** (one line).
   - `highlights_template` (analytics) → a **JSON array** of `{"label": ...,
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

### Keeping a template in its own file

A template written inline is a JSON string, which means one long line with
escaped newlines and escaped quotes. That's fine for a one-liner and miserable
for a prompt. **Anywhere a template string is accepted, you can write
`{"file": "name.j2"}` instead**, and the file is read from your tenant's
`templates/` folder:

```jsonc
{
  "prompt_templates": {
    "site_article": { "file": "site_article.j2" }
  },
  "analytics_options": {
    "summary_template":    { "file": "analytics_summary.j2" },
    "highlights_template": { "file": "analytics_highlights.json.j2" }
  }
}
```

```
userdata/acme/
├── tenant.json
└── templates/
    ├── site_article.j2
    ├── analytics_summary.j2
    └── analytics_highlights.json.j2
```

Now the template is a real file: readable, diffable, and editable in something
that understands Jinja2 syntax highlighting.

A plain string keeps working and means exactly what it always did. Those are the
only two forms — there is no `"@file.j2"` prefix and no `summary_template_file`
twin field, because both of those need an escape hatch for a template that
legitimately starts with `@`, and the escape hatch is where the bugs live.

**The rules, all four of them:**

1. **It works for every template option**, not a special list: any option whose
   name ends in `_template`, plus every entry in `prompt_templates`. A provider
   that gains a template option later gets this for free. Put `{"file": ...}`
   anywhere else — a `report_path`, an HTTP header — and the config is rejected
   by name rather than failing strangely later.
2. **Files live in `templates/`, and nowhere else.** Subfolders are fine
   (`{"file": "prompts/article.j2"}`); absolute paths, `..`, and symlinks
   pointing out of the folder are rejected. Your tenant folder is a boundary,
   the same way `plugins/` is.
3. **Files are read when the config loads**, not per render — so a template from
   a file gets the exact same save-time validation an inline one does, and a run
   makes no extra filesystem calls. The tradeoff: editing a template file does
   not affect an already-loaded config. For the CLI that's invisible (one run,
   one load); a long-lived server reloads the config as it would for any other
   config change.
4. **A config built in code has no `templates/` folder**, so `{"file": ...}`
   is a clear error there rather than a read relative to whatever directory the
   process happens to be in.

`check-data` reports which templates came from which file, which is worth
checking after any edit — a template changed in the wrong file renders perfectly
and says the wrong thing:

```console
$ python src/main.py check-data --tenant acme
│ templates │ ok │ prompt_templates.site_article <- site_article.j2 │
```

[`examples/07-signal-inputs/`](../examples/07-signal-inputs/) uses this for its
article prompt.

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
  "analytics_highlights_limit": 3,
  "analytics_options": {
    "source": "file",
    "report_path": "tenant-data/report.json",

    "summary_template": "{{ data.totals.signups_30d }} new signups in the last 30 days, {{ data.totals.active_users }} active users, ${{ data.totals.mrr_usd }} MRR.",

    "highlights_template": "[{% for p in data.top_posts[:limit] %}{\"label\": {{ (p.title + \" (\" + p.reads|string + \" reads)\")|tojson }}, \"url\": {{ (\"https://myapp.com/blog/\" + p.slug)|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
  }
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
  "analytics_highlights_limit": 3,
  "analytics_options": {
    "source": "api",
    "api_url": "https://shop.example.com/internal/analytics",
    "api_headers": { "Authorization": "Bearer YOUR_INTERNAL_API_KEY" },

    "summary_template": "${{ data.revenue|round|int }} in revenue from {{ data.orders }} orders over the {{ data.period|replace('_', ' ') }}.",

    "highlights_template": "[{% for p in data.bestsellers[:limit] %}{\"label\": {{ (p.name + \" — \" + p.units|string + \" sold\")|tojson }}, \"url\": {{ (\"https://shop.example.com/products/\" + p.handle)|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
  }
}
```

The only difference from Example 1 is `source`, `api_url`, and `api_headers`. The
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
  "traffic_options": {
    "source": "api",
    "api_url": "https://plausible.io/api/v1/stats/aggregate?site_id=example.com&period=30d&metrics=visitors,pageviews",
    "api_headers": { "Authorization": "Bearer YOUR_PLAUSIBLE_API_KEY" },
    "summary_template": "{{ data.results.visitors.value }} visitors and {{ data.results.pageviews.value }} pageviews in the last {{ days }} days."
  }
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
| `"llm"` | `"prompt_template"` (optional), `"max_opportunities"` (default `5`), `"grounded"` (default `true`), plus the search settings below | The AI model finds opportunities. **Grounded by default**: it searches the real web first (see [Web search](#web-search-how-discovery-stays-grounded)) and only keeps links that came back from that search. Set `"grounded": false` to skip all grounding for this source. |
| `"mcp"` | an `"options"` object — see [MCP servers](#discovery-from-an-mcp-server) below | A tool on an [MCP](https://modelcontextprotocol.io/) server, over stdio or streamable HTTP. |
| `"custom"` | `"class"` (`"module:ClassName"` — a file in your tenant's `plugins/` folder) | Your own finder — see [extending.md](extending.md). |

A `"llm"` source decides what to search for by asking the model for a few short
queries first (a cheap, ungrounded call). These control that half:

| `"llm"` search field | Default | Notes |
|---|---|---|
| `"search_queries"` | `[]` | Fixed queries. Set them and the query-writing model call is skipped entirely. |
| `"max_search_queries"` | `3` | How many queries to run. They go out concurrently, so three cost about what one does. |
| `"results_per_query"` | `5` | Results requested per query. |
| `"max_search_results"` | `12` | Cap on the merged, de-duplicated list that reaches the prompt. |
| `"query_prompt_template"` | `""` | Your own Jinja2 prompt for writing the queries. Same variables as `prompt_template`, plus `max_queries`. |

With no queries and no `input.seed_keyword`, the agent doesn't search — it falls
through to the model's own grounding rather than guessing at a query.

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

### Discovery from an MCP server

If your research already lives behind an [MCP](https://modelcontextprotocol.io/)
server, `provider: "mcp"` calls one of its tools and turns the answer into
opportunities. You don't write any code for this — no client, no transport, no
`asyncio` bridge.

```jsonc
{
  "discovery_sources": [
    {
      "name": "research",
      "provider": "mcp",
      "options": {
        "command": "npx",
        "args": ["-y", "@acme/research-mcp"],
        "tool_name": "search_opportunities"
      }
    }
  ]
}
```

| Option | Default | Notes |
|---|---|---|
| `"tool_name"` | — | **Required.** The tool on the server to call. |
| `"transport"` | `"stdio"` | `"stdio"` launches the server as a subprocess; `"http"` connects to one somebody else hosts (MCP's streamable HTTP). |
| `"command"` | — | **Required for `"stdio"`.** The program to launch, e.g. `"npx"`. |
| `"args"` | `[]` | Its arguments. |
| `"env"` | `{}` | Extra environment variables for the subprocess — usually the server's API key. Merged onto the normal environment, so `PATH` and friends survive. |
| `"cwd"` | `""` | Working directory, resolved against your tenant folder. |
| `"url"` | — | **Required for `"http"`.** The server's endpoint. |
| `"headers"` | `{}` | Sent with every HTTP request, e.g. `{"Authorization": "Bearer ..."}`. |
| `"arguments"` | see below | The arguments to call the tool with. |
| `"items_template"` | `""` | Jinja2, to map a server whose answer uses its own field names. |
| `"max_opportunities"` | `5` | Cap on how many of the server's results are used. |
| `"timeout_seconds"` | `60` | Bounds the whole exchange. A server that accepts the call and never answers fails the source, not the run. |

**Arguments.** With no `"arguments"` set, the tool is called with a single
`query` — your `input.seed_keyword`, or your `brand_description` when the run has
no keyword (which is the usual case: discovery is exactly when nobody has said
what to look for). To match a different schema, set them yourself; every string
value is a Jinja2 template with `seed_keyword`, `context_text`,
`brand_description`, `agent_goal` and `max_opportunities` available:

```jsonc
"arguments": { "q": "{{ seed_keyword }}", "limit": 10, "freshness": "week" }
```

Non-string values (the `10` above) are passed through untouched, so a server
whose schema says `limit` is a number receives a number.

**Mapping the answer.** If the tool already answers with `topic` /
`signal_strength` / `intent` / `reason` fields — either as a bare JSON array, or
as an object with a `results`, `items`, or `opportunities` list — it works with
no template at all. Otherwise `"items_template"` renders the server's payload
(available as `data`) into a **JSON array string**, the same contract as
`analytics_options.highlights_template`:

```jsonc
"items_template": "[{% for hit in data.hits %}{\"topic\": {{ hit.title | tojson }}, \"signal_strength\": {{ hit.score / 100 }}, \"reason\": {{ hit.why | tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
```

A server that answers in prose rather than JSON is an error, not zero
opportunities — "found nothing" and "answered in English" mean different things.

**When to use `"custom"` instead.** `"mcp"` is one tool call with one mapping. If
you need several calls, to pick the tool at runtime, or to do real work between
calls, write a class — see [extending.md](extending.md#using-an-mcp-server-as-a-tool).

Every opportunity records the server and tool it came from in its `raw` block, so
you can always trace one back to what claimed it.

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

## How long a run may take

| Field | Type | Default | Notes |
|---|---|---|---|
| `run_timeout_seconds` | `float` | `0` (unbounded) | An overall bound on one run. |

Each client already bounds its own calls — the LLM gets 120 seconds, Search
Console 30, an `api`-sourced template whatever its `*_api_timeout_seconds` says.
Those bound **one request**. `run_timeout_seconds` bounds the **run**: a dozen
individually-timely calls, or a `custom` class with no timeout of its own, can
still occupy a slot far longer than you meant.

```jsonc
{ "run_timeout_seconds": 300 }
```

Leave it at `0` for a CLI you're watching — you can always press Ctrl-C. Set it
in a server or worker, where nobody is watching and the slot is shared. A run
that overruns comes back as `"phase": "failed"` with a clear error, in the same
result shape as any other failure — never an exception.

---

## A tenant is a folder

Everything a tenant owns lives in one directory, and a run refers to it by name:

```
userdata/                     the workspace root
├── acme/                     the tenant name
│   ├── tenant.json           this file
│   ├── plugins/              your own classes  (extending.md)
│   ├── templates/            your .j2 files  (see "Keeping a template in its own file")
│   ├── data/                 analytics.json, traffic.json, credentials
│   └── output/               where results land by default
└── globex/
    └── …
```

```bash
python src/main.py run --tenant acme
python src/main.py list-tenants          # what's available
```

The workspace root is `--userdata`, else `$SEO_AGENT_USERDATA`, else the nearest
`userdata/` folder at or above the current directory — so any command works from
anywhere inside your project. A container mounts a volume and sets the
environment variable.

**Every path in your config resolves against your tenant's folder**, so:

```jsonc
{ "analytics_options": { "report_path": "data/analytics.json" } }
```

means that file inside `userdata/acme/`, no matter which directory you run from.
The same goes for a traffic provider's `report_path`, `search_performance_options.key_file`, and an output sink's
`options.path`. Absolute paths and `~` are used as-is.

`--input` works the same way: `--input input.comment.json` means that file in
your tenant's folder, and omitting it uses `input.json` there.

This is what lets many tenants share one process — they'd otherwise share one
working directory, and two tenants both saying `data/analytics.json` would read
the same file.

**Writing a custom class that opens its own files?** Do the same — `config`
carries `config_base_dir`:

```python
self._path = Path(config.config_base_dir or ".") / "data/events.json"
```

---

## Where the result goes (output sinks)

By default a finished run prints one indented JSON document to stdout — exactly
what this agent has always done. `output_sinks` lets you send it somewhere else,
or to several places at once.

```jsonc
{
  "output_sinks": [
    { "name": "stdout",  "provider": "json" },
    { "name": "archive", "provider": "json",
      "options": { "path": "runs.jsonl", "append": true } },
    { "name": "crm",     "provider": "webhook",
      "options": { "url": "https://example.com/hooks/seo",
                   "headers": { "Authorization": "Bearer ..." },
                   "timeout_seconds": 10 } }
  ]
}
```

Sinks run in the order listed. Each one receives the **complete run result** — the
same object stdout would show (`run_id`, `phase`, `output`, `discovery`, `usage`,
`error`), not just the draft, since a consumer usually needs to know which run
produced it and whether it succeeded.

| Provider | Options | Notes |
|---|---|---|
| `json` | `path` (empty = stdout), `indent` (default `2`), `append` | With `append`, writes one compact JSON object per line (JSONL) — good for accumulating many runs. |
| `webhook` | `url` (required), `method` (default `POST`), `headers`, `timeout_seconds` (default `10`) | Auth goes in `headers` — on the sink, not in the general config. No retries: reliable delivery is a queue's job. |
| `custom` | `class` (`"module:ClassName"` — a file in your tenant's `plugins/` folder), plus any `options` you like | One method: `emit(self, output: dict) -> None`. See [extending.md](extending.md#walkthrough-a-custom-output-sink). |

Two behaviors worth knowing, because they're deliberate opposites:

- **A broken sink *config* fails immediately, before the run.** A webhook with no
  `url`, or a custom class that won't import, is caught up front rather than after
  a full pipeline has spent real LLM calls.
- **A sink that fails while *emitting* is never fatal.** By then the result is
  already computed, so a failed webhook delivery doesn't discard a finished run or
  skip the sinks after it. The failure is reported (as a warning on stderr, or as
  an event when verbose mode is on) and the run moves on.

Note that the list **replaces** the default rather than adding to it — if you want
your own sink *and* the usual stdout JSON, list both, as the example above does.

---

## Watching a run happen (verbose mode)

By default a run prints nothing until its final JSON. Verbose mode reports each
stage and each tool call as it happens — which stage is running now, how long each
call took, which tool failed and why.

```bash
python src/main.py run --tenant acme -v      # stages and tool calls, with timings
python src/main.py run --tenant acme -vv     # also shows prompts, responses, and decisions
```

```text
[  0.00s] > run 76e5ef96  channel=auto seed_keyword="static site seo" sources=3
[  0.03s]   > discover_source [trends]
[  0.03s]     > trends.discover
[  2.31s]     < trends.discover  2280ms  found=1
[  0.03s]     ! broken.discover  0ms  error="RuntimeError: source 'broken' configured to fail"
[  2.40s]   > draft
[  2.41s]     > llm.generate  grounded=False
[  5.02s]     < llm.generate  2610ms  tokens=812 sources=0
[  5.03s] < run 76e5ef96  phase=done tokens=812 opportunities=2 tool_errors=1
```

**All of it goes to stderr**, never stdout — stdout still carries only the result
JSON, so `python src/main.py run --tenant acme -v | jq` works exactly as before.

The `!` lines are the reason this exists: the agent degrades rather than aborting
when a tool fails, so a failed analytics call or discovery source otherwise only
shows up as a `tool_errors` entry buried in the final JSON.

One thing the trace doesn't break out: the searches and model calls a discovery
source makes *inside* itself are timed as that single `discover` line (a source is
handed its clients before there's a reporter to wrap them). So a slow
`trends.discover` is usually the web search it did, not the model.

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

## A different deliverable: agent types and pipelines

| Field | Type | Default |
|---|---|---|
| `agent_type` | `string` | `"seo_content"` |
| `pipelines` | `dict[str, object]` | `{}` |

Everything above this point configures **what the agent reads**. This configures
**what it does with it**.

The built-in `seo_content` agent writes an article or a reply: `discover →
choose_channel → analyze → draft → self_qa`, with which of those exist decided by
your `discovery_sources`. That is one way to grow a site. Telling someone what to
fix on the site they already have is another, and so is a content brief, a link
report, or a competitor summary.

**This project deliberately doesn't ship those**, because which findings matter
and what a crawler does are your position to hold, not ours. It ships the seam:

```jsonc
{
  "agent_type": "site_audit",
  "pipelines": {
    "site_audit": {
      "stages": [
        { "name": "crawl",    "class": "audit:CrawlStage",
          "options": { "pages_path": "data/crawl.json" } },
        { "name": "findings", "class": "audit:FindingsStage" },
        { "name": "verify",   "class": "audit:VerifyStage" }
      ]
    }
  }
}
```

```bash
python src/main.py show-graph --tenant acme            # the pipeline you declared
python src/main.py run --tenant acme                   # agent_type from the config
python src/main.py run --tenant acme --agent seo_content   # the built-in one, same config
```

One tenant can have several, and `--agent` (or `RunRequest.agent_type`) picks one
per run. `agent_type` is only the default.

### A stage entry

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | The node name. Must be unique within the pipeline. |
| `class` | unless `name` is built-in | `"module:ClassName"` in this tenant's `plugins/` folder. |
| `mode` | no | `"sequential"` (default), `"concurrent_from_start"`, `"parallel_by_source"`. |
| `options` | no | This stage's own settings, handed to a class that asks for them. |

**List order is the chain.** There is no `after:` field: this pipeline is a chain
with two declared exceptions to it (the two non-sequential modes), so a field
whose only legal values would restate the list order is a second way to say one
thing.

Leaving out `class` uses the built-in stage of that name — `discover`,
`choose_channel`, `analyze_context`, `analyze`, `draft`, `self_qa` — so a
pipeline can mix its own stages with the ones that ship.

### Writing a stage

```python
class FindingsStage:
    def __init__(self, tools, config):        # or (tools, config, options)
        self.config = config

    async def run(self, state):               # a plain `def` works too
        working = dict(state["working"])
        working["findings"] = [...]
        return {"phase": "findings", "working": working}
```

- **Constructed with `(tools, config)`**, plus a third `options` argument if your
  constructor asks for one — the same opt-in every `"custom"` provider has.
  `tools` is the same bundle the built-in stages call (`tools.llm`,
  `tools.signals`, …).
- **`run(state)` returns only the keys it changes.** They're merged into the
  running state before the next stage sees it.
- **The last stage writes `output`**, with its own `kind`:

```python
return {"phase": "done", "output": {
    "kind": "site_audit", "title": "...", "content": "...",
    "format": "markdown", "metadata": {"findings": [...], "pages_crawled": 128},
}}
```

A new deliverable is a **new `kind`, never a new top-level field** — the result
shape in [output-schema.md](output-schema.md) is frozen, so anything reading
`run_id`/`phase`/`output`/`error` keeps working whichever agent produced it.

### The two other modes

`"concurrent_from_start"` runs a stage as a direct child of START, alongside
whatever chain precedes it, joining back in at the **next** stage in the list —
which is how the built-in `analyze_context` overlaps its analytics calls with
discovery. Something must follow it, or its branch would dangle.

`"parallel_by_source"` runs one branch per entry of a `Tools` collection and
merges them, which is how `discover` fans out over several discovery sources. A
stage may use it if its class declares `fanout_over` (a `Tools` attribute),
`fanout_branch` and `fanout_join` — see
[`agent/graph/stages/discover.py`](../src/agent/graph/stages/discover.py).

### What is checked, and when

- **At config load:** that `agent_type` names a pipeline that exists, and that
  every declared pipeline's stage list is sound (names, duplicates, modes, and
  that a stage with no `class` names a built-in). A typo fails while you're
  editing.
- **When the pipeline is built** — at the start of a run, and in `check-data`:
  that every `class` imports. Not at config load, because importing a plugin runs
  your Python, and a server loading configs per request shouldn't run the code of
  pipelines it isn't using. `check-data` builds every stage precisely so this
  isn't left to a real run to find.

### No channel unless you're writing something

`channel` (`site_article` / `external_article` / `engagement_comment`) belongs to
`seo_content` — it picks *which of three things gets drafted*. A pipeline
containing none of the channel-aware stages (`choose_channel`, `analyze`,
`draft`, `self_qa`) has no channel, and none is invented for it: `input.channel`
stays absent rather than quietly becoming `"site_article"`. A pipeline that
*does* reuse `draft` resolves a channel exactly as before.

Worked end to end, offline, in
[example 08](../examples/08-custom-pipeline/) — a site audit whose stages,
templates and fixtures all live in the tenant folder.

---

## Prompt templates

| Field | Type | Default |
|---|---|---|
| `prompt_templates` | `dict[str, str]` | One generic template per channel |

A prompt is the template most worth
[keeping in its own file](#keeping-a-template-in-its-own-file) —
`{"file": "site_article.j2"}` instead of one escaped JSON line.

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
  // --- Real vendors Echooers happens to use. None of these three is required
  // by the agent — see "Nothing here is mandatory" above. ---
  "llm_provider": "gemini",
  "llm_options": {
    "model": "gemini-pro-latest",
    "api_key": "YOUR_GEMINI_API_KEY"
  },

  "search_performance_provider": "google",
  "search_performance_options": {
    "gsc_domain": "sc-domain:echooers.com",
    "key_file": "service_account.json"
  },

  "traffic_provider": "cloudflare",
  "traffic_options": {
    "api_token": "YOUR_CLOUDFLARE_API_TOKEN",
    "zone_id": "YOUR_CLOUDFLARE_ZONE_ID"
  },

  // --- Analytics: Echooers' own idea/upvote JSON, mapped with two templates.
  // data.data.overview.total_ideas etc. are Echooers' own field names. ---
  "analytics_provider": "templated",
  "analytics_highlights_limit": 3,
  "analytics_options": {
    "source": "file",
    "report_path": "tools/report.json",
    "summary_template": "{{ data.data.overview.total_ideas }} ideas shared so far, {{ data.data.overview.total_upvotes }} upvotes and {{ data.data.overview.total_views }} views across the community.",
    "highlights_template": "[{% for i in data.data.top_by_upvotes[:limit] %}{\"label\": {{ (i.content[:200] + \" (\" + i.upvotes|string + \" upvotes, \" + i.views|string + \" views)\")|tojson }}, \"url\": {{ (\"https://echooers.com/idea/\" + i.id)|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
  },

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
  search-backed AI model, not a specific vendor API. Note there's no
  `search_provider` line: DuckDuckGo grounding is already the default, so this
  config searches the real web without saying anything about it.
- **The extra `qa_brand_mention_keywords`** — the anonymity pitch *is* the
  brand, so mentioning it in a reply needs a disclosure just like naming the
  product would.

To adapt this for **your** product: change the brand voice, swap the analytics
templates for your JSON's field names (Examples 1–3 above are closer starting
points for a typical SaaS or store), set your vendor keys, and adjust the
brand-mention keywords. You shouldn't need to touch any code.
