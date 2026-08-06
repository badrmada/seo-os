# Recipes: wiring in what you already use

Nobody's growth stack is only what ships here. This page covers the integrations
that come up most — a backlink API, a rank tracker you already pay for, publishing
to your CMS, watching runs from your own UI.

Two promises about everything below:

- **Real field names.** Every option here exists. Where a payload is your
  vendor's rather than ours, it's labelled as such — that's the part you map.
- **Nothing here is a shipped integration.** These are recipes *you* write, in
  your own agent's folder. None of them needed a change to the runtime, which is
  the whole claim this page exists to back up.

New to the model? [concepts.md](concepts.md) first — this page assumes
capabilities, providers and signals.

## Which kind of integration is mine?

Almost every integration is one of six kinds. Getting this right first saves you
reading the wrong section:

| What you have | It's a | Configured as |
|---|---|---|
| Data that should inform a run (backlinks, trends, a competitor watcher, your dashboard) | **signal** | `signal_sources[]` |
| Data about how your pages currently rank | **search performance** | `search_performance_provider` |
| Something that finds opportunities worth acting on | **discovery source** | `discovery_sources[]` |
| Somewhere the finished result should land (CMS, Slack, a queue) | **sink** | `output_sinks[]` |
| Somewhere to watch runs from another process | **state store** | `state_provider` |
| A different deliverable entirely (an audit, a brief, a link report) | **skill** | `pipelines` + `plugins/` |

If in doubt: anything *feeding* a run is a signal, and it is almost always the
right answer. It's the open-ended capability — a named list of any length, with
no field to add and no fork required.

---

## 1. Backlinks (Ahrefs, Majestic, Moz, anything)

A backlink API is a signal. The runtime has never heard of your vendor and
doesn't need to.

### The no-code version

If the API returns JSON and one request is enough, `templated` needs no Python at
all:

```jsonc
"signal_sources": [
  {
    "name": "backlinks",
    "provider": "templated",
    "options": {
      "source": "api",
      "api_url": "https://api.example-backlinks.com/v3/site/backlinks?target=example.com",
      "api_headers": { "Authorization": "Bearer YOUR_TOKEN" },
      "api_timeout_seconds": 15,

      "summary_template": "{{ data.metrics.live }} live backlinks from {{ data.metrics.refdomains }} referring domains; {{ data.metrics.new_30d }} new in the last 30 days.",
      "facts_template": "{\"live\": {{ data.metrics.live }}, \"refdomains\": {{ data.metrics.refdomains }}}",
      "items_template": "[{% for l in data.backlinks[:10] %}{\"label\": {{ l.anchor|tojson }}, \"url\": {{ l.url_from|tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
    }
  }
]
```

**`data` is your vendor's payload, verbatim.** `metrics.live`, `backlinks[].anchor`
and the rest are that API's field names, not ours — swap in whatever yours
actually returns. The three templates are the entire contract:

| Template | Renders to | Required |
|---|---|---|
| `summary_template` | plain text, dropped into the prompt as-is | yes |
| `facts_template` | a JSON **object** as a string | no |
| `items_template` | a JSON **array** as a string | no |

That `|tojson` filter on every string is not optional decoration — an anchor text
containing a quote produces invalid JSON without it.

Keep the templates in files once they grow past a line. Any option ending in
`_template` accepts `{"file": "backlinks_items.j2"}`, resolved from your agent's
`templates/` folder.

### When you need code

Pagination, an SDK, signing, or two calls that have to be combined — that's a
`custom` class. One method:

```python
# userdata/acme/plugins/backlinks.py
import httpx

class BacklinkSignal:
    def __init__(self, config, options=None):
        options = options or {}
        self._token = options["api_token"]
        self._target = options.get("target") or config.site_url

    async def collect(self, context: dict) -> dict:
        rows, cursor = [], None
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(5):                      # bound it; never "while True"
                r = await client.get(
                    "https://api.example-backlinks.com/v3/site/backlinks",
                    params={"target": self._target, "cursor": cursor, "limit": 100},
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                r.raise_for_status()
                page = r.json()
                rows += page["backlinks"]
                cursor = page.get("next_cursor")
                if not cursor:
                    break

        dofollow = [b for b in rows if not b.get("nofollow")]
        return {
            "summary": f"{len(rows)} backlinks from {len({b['domain'] for b in rows})} domains, {len(dofollow)} dofollow.",
            "facts": {"total": len(rows), "dofollow": len(dofollow)},
            "items": [{"label": b["anchor"], "url": b["url_from"]} for b in dofollow[:10]],
        }
```

```jsonc
"signal_sources": [
  { "name": "backlinks", "provider": "custom",
    "class": "backlinks:BacklinkSignal",
    "options": { "api_token": "…", "target": "example.com" } }
]
```

Four things that apply to every custom signal:

- **`def` or `async def`, your choice.** Use `async` when your client has a native
  coroutine API; a blocking client should be a plain `def` and gets run off the
  event loop so it never stalls other runs.
- **`context`** carries what the run knows so far — `seed_keyword`, `site_url`,
  `context_text`, `channel`. Every key is optional; `channel` is `""` before the
  agent has decided one. Ignoring `context` entirely is fine.
- **Don't swallow errors to be polite.** Signals are collected concurrently and
  fail independently: raising costs you that signal (an empty entry plus a
  `tool_errors` record), never the run. A failure nothing records is worse than
  one that does.
- **Returning less is fine.** `{"summary": "..."}` is a complete signal, a bare
  string is read as the summary, and `None` means "nothing to report".

### Using it in a prompt

Every signal arrives under the name you gave it:

```jinja
{{ signals.backlinks.summary }}
{{ signals.backlinks.facts.dofollow }} of those are dofollow.
```

Or write the loop that names nothing, and adding a fifth signal changes no
template:

```jinja
{% for name, signal in signals.items() %}
{% if signal.summary %}- {{ name }}: {{ signal.summary }}
{% endif %}
{% endfor %}
```

`signals` has **one key per configured signal on every run**, whatever happened to
it — a source that failed contributes empty values, not a missing key. So a
template naming yours keeps working, and a misspelled name is caught when you save
the config rather than mid-run.

→ [signal_sources reference](../services/seo-agents/docs/configuration.md#any-other-data-source-signal_sources)
· [example 07](../services/seo-agents/examples/07-signal-inputs/) runs this offline

---

## 2. Rank data from a tracker that isn't Search Console

Rankings have their own capability rather than being a signal, because the agent
*reasons* over them — it picks which keyword to target from positions and
impressions. Any source works:

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

`rows_template` renders a JSON array of
`{query, clicks, impressions, ctr, position}`. Set `"source": "api"` with
`api_url` / `api_headers` to pull it live instead of from a file.

**The rule that matters here: your template supplies data, never judgement.** You
do not compute which keyword is worth targeting, or score it, or explain it — the
agent does that from those five numbers, with the identical logic the Google
provider gets. Which keyword wins must not depend on where the numbers came from.

Two consequences: reimplementing striking-distance classification in Jinja2 would
be miserable and you don't have to; and `trend` comes out `"flat"`, because one
snapshot has no prior period to compare against.

**Not JSON?** `templated` reads JSON only. A CSV export needs a `custom` class
with one method, `search_analytics(days=28, row_limit=500)`, returning those same
row objects. Bing Webmaster Tools, Semrush, an internal warehouse — same method.

→ [search performance reference](../services/seo-agents/docs/configuration.md#search-performance-how-your-pages-already-rank)

---

## 3. Several signals at once

There's no limit and no ordering to worry about. A trends export, a rank tracker
and a backlink API together:

```jsonc
"signal_sources": [
  { "name": "backlinks", "provider": "custom", "class": "backlinks:BacklinkSignal",
    "options": { "api_token": "…" } },
  { "name": "trends", "provider": "templated",
    "options": { "source": "file", "report_path": "data/trends.json",
                 "summary_template": "Rising: {{ data.rising|join(', ') }}." } },
  { "name": "rank_tracker", "provider": "custom", "class": "rank_tracker:RankTracker",
    "options": { "api_key": "…", "site": "example.com" } }
]
```

**They're collected concurrently.** Ten signals cost one round trip, not ten. One
that's down contributes empty values and a `discovery.tool_errors` entry; the run
continues on the rest.

You can also fold the built-in inputs into the same list, purely so every input is
visible in one block — `search_performance`, `traffic` and `analytics` are
reserved names that route to those capabilities:

```jsonc
"signal_sources": [
  { "name": "traffic", "provider": "cloudflare", "options": { "api_token": "…", "zone_id": "…" } },
  { "name": "backlinks", "provider": "custom", "class": "backlinks:BacklinkSignal" }
]
```

Nothing needs migrating to do this — `traffic_provider` and friends still work and
mean exactly what they always did.

---

## 4. An MCP server as a discovery source

If your research already lives behind an MCP server, it's a config entry. No
client, no transport code, no `asyncio` bridge:

```jsonc
"discovery_sources": [
  { "name": "research", "provider": "mcp",
    "options": {
      "command": "npx",
      "args": ["-y", "@acme/research-mcp"],
      "env": { "RESEARCH_API_KEY": "…" },
      "tool_name": "search_opportunities",
      "max_opportunities": 5
    } }
]
```

Hosted instead of launched? `"transport": "http"` with `"url"` and `"headers"`.

**The answer maps itself when it can.** A tool that already answers with
`topic` / `signal_strength` / `intent` / `reason` — as a bare array, or an object
with a `results`, `items` or `opportunities` list — needs no template. Otherwise
`items_template` renders the payload (as `data`) into a JSON array string, same
contract as everywhere else.

**Arguments** default to a single `query` — your seed keyword, or your
`brand_description` when the run has none, which is the usual case for discovery.
Match a different schema explicitly, and every string value is a Jinja2 template:

```jsonc
"arguments": { "q": "{{ seed_keyword }}", "limit": 10, "freshness": "week" }
```

Non-string values pass through untouched, so a schema wanting a number gets one.

Use a `custom` class instead when you need several calls, runtime tool selection,
or real work between calls. `timeout_seconds` (60) bounds the whole exchange — a
server that accepts the connection and never answers fails the *source*, not the
run.

→ [MCP reference](../services/seo-agents/docs/configuration.md#discovery-from-an-mcp-server)
· [example 06](../services/seo-agents/examples/06-mcp-discovery/) runs offline against a stub server

---

## 5. Publishing: CMS, Slack, a queue

A sink receives the **complete run result** — `run_id`, `phase`, `output`,
`discovery`, `usage`, `error` — not just the draft, because a consumer usually
needs to know which run produced it and whether it succeeded.

### A plain webhook

```jsonc
"output_sinks": [
  { "name": "stdout", "provider": "json" },
  { "name": "archive", "provider": "json", "options": { "path": "runs.jsonl", "append": true } },
  { "name": "queue", "provider": "webhook",
    "options": { "url": "https://example.com/hooks/seo",
                 "headers": { "Authorization": "Bearer …" },
                 "timeout_seconds": 10 } }
]
```

The list **replaces** the default rather than adding to it — if you want your own
sink *and* the usual stdout JSON, list both, as above.

### Slack and Discord need a custom sink

Worth stating plainly, because it's the first thing people try: the built-in
`webhook` sink POSTs the run result as the request body, verbatim. Slack's
incoming webhooks expect `{"text": …}`, so pointing one at Slack sends it JSON it
won't render. Shape it yourself:

```python
# userdata/acme/plugins/slack_sink.py
import httpx

class SlackSink:
    def __init__(self, config, options=None):
        self._url = (options or {})["webhook_url"]

    def emit(self, output: dict) -> None:
        if output.get("phase") != "done":
            return
        draft = output["output"]
        httpx.post(self._url, json={
            "text": f"*{draft.get('title') or draft['kind']}*\n"
                    f"{draft['content'][:400]}…\n"
                    f"_{draft['metadata'].get('word_count', 0)} words · run {output['run_id']}_"
        }, timeout=10).raise_for_status()
```

```jsonc
{ "name": "slack", "provider": "custom", "class": "slack_sink:SlackSink",
  "options": { "webhook_url": "https://hooks.slack.com/services/…" } }
```

A CMS sink works the same way — `emit` posts `draft["title"]` and
`draft["content"]` as an **unpublished** post. Keep it unpublished: a human
reviews before anything goes live, and a sink is not an approval workflow.

### Two deliberate opposites

- **A broken sink *config* fails immediately, before the run.** A webhook with no
  `url`, or a class that won't import, is caught up front rather than after a
  pipeline has spent real LLM calls.
- **A sink that fails while *emitting* is never fatal.** The result is already
  computed, so a dead webhook doesn't discard a finished run or skip the sinks
  after it. It's reported and the run moves on.

There are **no retries**. Reliable delivery is a queue's job, and a queue is the
layer above this one.

→ [output sinks reference](../services/seo-agents/docs/configuration.md#where-the-result-goes-output-sinks)

---

## 6. Watching runs from your own UI or worker

A sink answers "where does the finished result go?". This answers a different
question: **where is this run right now?**

```jsonc
{ "state_provider": "file",  "state_options": { "path": "state" } }
```

```jsonc
{ "state_provider": "redis",
  "state_options": { "url": "redis://localhost:6379/0",
                     "key_prefix": "seo-agent:run:",
                     "ttl_seconds": 604800 } }
```

A snapshot is written after every step, keyed by `run_id`, so a progress endpoint
or a dashboard can read a run that hasn't returned yet. Use `redis` when several
processes need to see the same runs; `file` needs no infrastructure and survives
the process.

Three things to build against:

- **The terminal snapshot is the *result*** — exactly the documented JSON — while
  the ones before it are raw in-progress states. Both carry `run_id` and `phase`,
  so a reader never has to branch on which it's holding.
- **A store that's down degrades the run, it doesn't fail it.** Failures land in
  `RunResult.state_errors`, so a store that's been dead a week doesn't look
  identical to a working one.
- **`ttl_seconds` matters on Redis.** Nothing else expires snapshots; retention is
  deliberately out of scope.

Building a UI on this? [output-schema.md](../services/seo-agents/docs/output-schema.md)
is the frozen contract — including what a failed run looks like.

---

## 7. A different deliverable: your own skill

An audit, a content brief, a link report — a pipeline of your own stages, living
in your agent's folder:

```jsonc
{
  "agent_type": "site_audit",
  "pipelines": {
    "site_audit": {
      "stages": [
        { "name": "crawl",    "class": "audit:CrawlStage", "options": { "pages_path": "data/crawl.json" } },
        { "name": "findings", "class": "audit:FindingsStage" },
        { "name": "verify",   "class": "audit:VerifyStage" }
      ]
    }
  }
}
```

Leave out `class` to reuse a built-in specialist by name, so a skill can mix its
own stages with the ones that ship. `--agent` picks one per run when an agent
declares several.

**[example 08](../services/seo-agents/examples/08-custom-pipeline/) is a complete
working one** — copy that folder rather than starting from this snippet.

Two constraints inherited from it:

- **If your stage crawls, bound it.** Obey `robots.txt`, rate-limit, cap pages,
  depth and total time, send an identifying user agent, never follow off-site
  links. A crawler is the one tool here that can hurt someone else's server.
- **Findings must be evidence-backed** — each carrying the URLs and rows it came
  from. Example 08's `VerifyStage` drops any finding pointing at a URL the crawl
  never saw. An audit that asserts problems it can't point at is worse than none.

→ [pipelines reference](../services/seo-agents/docs/configuration.md#a-different-deliverable-agent-types-and-pipelines)
· [writing a stage](../services/seo-agents/docs/extending.md#walkthrough-a-pipeline-stage-of-your-own)

---

## Before you ship a recipe

Three checks, cheapest first. None of them spends an LLM call:

```bash
# 1. Does the class import, does the template render, is the credential there?
python src/main.py check-data --tenant acme

# 2. Does my data actually reach the prompt?
python src/main.py preview-prompt --tenant acme

# 3. Which specialists will run, given this config?
python src/main.py show-graph --tenant acme
```

`check-data` reuses the same validators a real run uses *and* builds every
configured provider, which is where a missing key file or an unimportable class
shows up. It exits non-zero, so it works in CI.

`preview-prompt` is the one people skip and shouldn't: it renders everything your
templates produce without calling a model, which is the fastest way to see a
signal arriving empty.

And when a run does happen, `-v` follows every stage and tool call live, `-vv`
adds prompts and responses. Verbose output goes to stderr, so `… -v | jq` still
works.

→ [cli.md](../services/seo-agents/docs/cli.md)

## See also

| | |
|---|---|
| The model behind all of this | [concepts.md](concepts.md) |
| Every config field | [configuration.md](../services/seo-agents/docs/configuration.md) |
| Full walkthroughs for every custom class | [extending.md](../services/seo-agents/docs/extending.md) |
| Eight runnable examples | [examples/](../services/seo-agents/examples/) |
