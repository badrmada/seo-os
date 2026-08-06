# 07 — Sproutly (signal inputs: adding a data source we've never heard of)

**The story.** Sproutly sells indoor gardening kits and publishes growing guides.
Their SEO work runs on two data sources this project has never heard of: a
**keyword trends export** and their own **rank tracker**. Neither is Search
Console, traffic, or product analytics — and neither should require forking this
repo.

So they go in `signal_sources`, a named list of *every* input the agent reads.

**What this example shows:**

- **`signal_sources`** — inputs as an open list, not three fixed slots.
- **A templated signal** — a trends export mapped by Jinja2, no code.
- **A custom signal** — a rank tracker that needs real computation.
- **`summary` / `facts` / `items`** — prose for the prompt, structure for your
  template.
- **The whole input set in one block** — `search_performance` and `traffic`
  written as entries in the same list.

## The files

```
data/keyword_trends.json    # rising queries (for the templated signal)
data/rankings.json          # tracked keyword positions (for the custom signal)
data/traffic.json           # visitor numbers (for the templated traffic feed)
plugins/rank_tracker.py     # RankTracker — a custom SignalSource
templates/site_article.j2   # the article prompt, as a readable file
tenant.json
input.json
```

`templates/site_article.j2` is the **prompt** — the actual text sent to the model
when it writes, as opposed to the data templates in `tenant.json` that shape
Sproutly's two feeds into facts. It's what lets this example do the thing that
matters here: name its own signals in the wording
(`{{ signals.rank_tracker['items'] }}`) and tell the model to prefer improving a
page that already ranks over starting a new one. A prompt is a paragraph, so it
lives in `templates/` rather than on one escaped line inside `tenant.json`, which
is what `{"file": ...}` is for:

```jsonc
"prompt_templates": { "site_article": { "file": "site_article.j2" } }
```

Any template value can be written that way — see
[configuration.md](../../docs/configuration.md#keeping-a-template-in-its-own-file).

## Everything the agent reads, in one list

```jsonc
"signal_sources": [
  { "name": "search_performance", "provider": "none" },
  { "name": "traffic", "provider": "templated", "options": { "...": "..." } },
  { "name": "keyword_trends", "provider": "templated", "options": { "...": "..." } },
  { "name": "rank_tracker",   "provider": "custom",
    "class": "rank_tracker:RankTracker", "options": { "...": "..." } }
]
```

`search_performance`, `traffic` and `analytics` are **reserved names**: an entry
using one selects that built-in tool, exactly as
`search_performance_provider` / `traffic_provider` /
`analytics_provider` do. Those fields still work and mean the same thing — the
list is just a way to see every input at once. (This example uses the list for
`search_performance` and `traffic`, and the plain field for `analytics`, to show
they mix.)

Every **other** name is a signal of your own. There is no limit and nothing in
this repo knows their names.

## A signal in two flavors

**Templated (`keyword_trends`)** — the data is already JSON, so three Jinja2
templates map it. Only `summary_template` is required:

```jsonc
{
  "name": "keyword_trends",
  "provider": "templated",
  "options": {
    "source": "file",
    "report_path": "data/keyword_trends.json",
    "summary_template": "Rising over the last {{ data.window_days }} days: ...",
    "facts_template":   "{\"window_days\": {{ data.window_days }}, ...}",
    "items_template":   "[{% for row in data.rising %}{...}{% endfor %}]"
  }
}
```

- `summary_template` → text, dropped into the prompt as-is.
- `facts_template` → a JSON **object**, for named values.
- `items_template` → a JSON **array**, for rows.

Templates render against `{"data": <your raw JSON>, "context": <this run>}` — so
a signal can be *about* the run (`{{ context.seed_keyword }}`), unlike an
analytics report, which is just about the site.

**Custom (`rank_tracker`)** — classifying keywords by distance to page one is a
calculation, not a reshape, so it's a small class:

```python
class RankTracker:
    def __init__(self, config, options=None): ...

    def collect(self, context: dict) -> dict:
        return {"summary": "2 tracked keywords sit at positions 11-20 ...",
                "facts": {"tracked": 4, "striking_distance": 2},
                "items": [{"label": "indoor herb garden", "position": 12, "url": "..."}]}
```

One method, `collect(context)`. `def` or `async def` — both work. It lives in this
tenant's `plugins/` folder, found by name, no `PYTHONPATH`. See
[docs/extending.md](../../docs/extending.md).

## Using them in the prompt

Every signal arrives as `signals`, keyed by the name you gave it:

```jinja
{% for name, signal in signals.items() %}
{% if signal.summary %}- {{ name }}: {{ signal.summary }}
{% endif %}
{% endfor %}

{% for row in signals.rank_tracker['items'] %}
- "{{ row.label }}" at position {{ row.position }} — {{ row.url }}
{% endfor %}
```

The loop needs no signal names at all, which is what lets you add one without
touching a template. Naming yours directly is fine too — `signals` always has one
key per configured signal, even on a run where that signal failed or had nothing
to report, so `signals.rank_tracker` never disappears out from under a template.
Misspell one and the config is rejected when you save it, naming the mistake.

## Run it

```bash
python src/main.py check-data --userdata examples --tenant 07-signal-inputs
python src/main.py preview-prompt --userdata examples --tenant 07-signal-inputs
python src/main.py run --userdata examples --tenant 07-signal-inputs
```

Or `make example EXAMPLE=07-signal-inputs`, or in Docker with nothing installed —
the same run three ways is in [Running an example](../README.md#running-an-example).

`preview-prompt` is the one to look at — it's built entirely from your config and
data. Real output (trimmed):

```
Site traffic: 18400 visitors and 41200 pageviews in the last 28 days, mostly organic search.
What our data sources say right now:
- keyword_trends: Rising over the last 30 days: "grow lights for herbs" (5400/mo, 41% up), ...
- rank_tracker: 2 tracked keywords sit at positions 11-20 — close enough to page one that
  improving an existing page usually beats writing a new one. 2 moved up since the last
  check. This run's keyword "indoor herb garden" is at position 12 (was 17).

Rising queries worth covering (4 tracked):
- "grow lights for herbs" — 5400 searches/mo, up 41%
...
Pages already close to page one — prefer improving one of these over starting from scratch:
- "indoor herb garden" at position 12 — https://sproutly.example.com/guides/indoor-herb-garden
```

Note the rank tracker mentioning **this run's** keyword: that's `context` reaching
the signal.

`check-data` builds every signal without running anything — the fastest way to
find a bad path or an unimportable class.

## What happens when a signal breaks

Nothing much, on purpose. Every signal is collected **concurrently** and fails
**independently**: one that raises contributes an empty entry and a
`tool_errors` record, and the run continues with everything else. Try it by
adding a fourth entry:

```jsonc
{ "name": "flaky", "provider": "mock", "options": { "fail": true } }
```

The run still finishes; `discovery.tool_errors` in the output says what happened
and when. A degrade that nothing records would be a bug.

## Go live

Point the templated signal at your real API instead of a file:

```jsonc
"options": {
  "source": "api",
  "api_url": "https://api.your-trends-tool.example/v1/rising",
  "api_headers": { "Authorization": "Bearer ..." },
  "summary_template": "..."
}
```

and give `RankTracker` your real rank-tracking API. Swap `llm_provider` and the
`search_performance` entry for real vendors as in the other examples. Nothing
else changes.
