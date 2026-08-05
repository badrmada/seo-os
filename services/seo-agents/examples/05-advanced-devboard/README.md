# 05 — DevBoard (advanced: your own code, everything together)

**The story.** DevBoard is a job board for software engineers. Its data needs
**real code**, not just templates: it wants a *growth rate* (this week vs. last
week) that a template can't compute, and it has its own source of trending
searches. So it plugs in two small Python classes — a custom analytics client and
a custom discovery source — alongside a templated traffic feed.

**What this example shows:**

- **Custom Python analytics** — a real calculation (`custom`, not `templated`).
- **A custom discovery source** — your own opportunity finder, with channel hints.
- **Two discovery sources scored together** — they run in parallel and their
  results are pooled into one channel decision.
- **Templated traffic** — one more data feed, via a template.
- **Loading your own module** — how the app finds your code (`PYTHONPATH`).

## The files

```
data/events.json        # daily job-posting counts + top companies (for the custom analytics)
data/traffic.json       # visitor numbers (for the templated traffic feed)
data/trending.json      # trending searches with channel hints (for the custom discovery source)
code/analytics_growth.py   # GrowthAnalytics  — custom AppAnalyticsClient
code/trending_finder.py    # TrendingSearches — custom OpportunitySource
tenant.json
input.auto.json         # no channel — the agent decides
input.article.json      # explicit site_article
```

## The custom code (both are small)

**`code/analytics_growth.py`** computes a 7-day-vs-previous-7-day growth rate —
the kind of thing a template can't do — and returns the standard
`{summary, highlights}` shape:

```python
class GrowthAnalytics:
    def __init__(self, config):
        self._path = Path("data/events.json")

    def report(self, limit: int = 5) -> dict:
        data = json.loads(self._path.read_text())
        counts = [c for _day, c in data["jobs_by_day"]]
        last7, prior7 = sum(counts[-7:]), sum(counts[-14:-7])
        growth = (last7 - prior7) / prior7
        return {"summary": f"{last7} job posts in the last 7 days, {growth:+.0%} vs the previous 7 days, ...",
                "highlights": [ ... top companies as {label, url} ... ]}
```

**`code/trending_finder.py`** turns each trending search into an opportunity,
**carrying a channel hint** the agent can act on:

```python
class TrendingSearches:
    def discover(self, context: dict) -> list[dict]:
        rows = json.loads(Path("data/trending.json").read_text())
        return [{
            "source": "trending_searches",
            "topic": row["query"],
            "signal_strength": min(row["volume"] / 2000, 1.0),
            "suggested_channel_hint": row["channel"],   # <- this drives the channel decision
            "intent": "informational", "raw": row,
            "reason": f'{row["volume"]} monthly searches for "{row["query"]}", and rising.',
        } for row in rows]
```

`tenant.json` points at them by `module:ClassName`:

```jsonc
{
  "analytics_provider": "custom",
  "analytics_custom_class": "analytics_growth:GrowthAnalytics",
  "discovery_sources": [
    { "name": "web_scout", "provider": "mock" },
    { "name": "trending_searches", "provider": "custom", "class": "trending_finder:TrendingSearches" }
  ]
}
```

## Run it (note the `PYTHONPATH`)

Your code lives in `code/`, so tell Python where to find it:

```bash
PYTHONPATH=code python ../../src/main.py run --input input.auto.json
```

`PYTHONPATH=code` is what makes `analytics_growth` and `trending_finder`
importable. (In a real deployment you'd install your code as a package instead —
see [docs/extending.md](../../docs/extending.md#making-your-module-importable).)

If you forget it, the run fails cleanly and tells you exactly why —
`"phase": "failed", "error": "No module named 'analytics_growth'"` — rather than
crashing.

## What happens — two sources, one decision

`input.auto.json` has **no channel**, so the agent runs both discovery sources
(in parallel, because there are two), pools the opportunities, and scores the
channel hints. Real output (trimmed):

```json
"output": { "kind": "site_article", "title": "The Complete Guide to Remote Rust Developer Jobs" },
"discovery": {
  "channel_decision": {
    "chosen": "site_article",
    "reason": "Highest-scoring channel hint across 4 discovered opportunities: {'site_article': 0.95, 'engagement_comment': 0.6, 'external_article': 0.4}.",
    "fallback": false
  },
  "tool_errors": []
}
```

Four opportunities came from **both** sources (`web_scout` + `trending_searches`),
their channel hints were summed, and `site_article` won — a **real decision**
(`"fallback": false`), unlike example 04's offline fallback.

And the custom analytics + templated traffic both show up in the prompt (preview
it with `PYTHONPATH=code python ../../src/main.py preview-prompt --input input.article.json`):

```
Recent activity: 250 job posts in the last 7 days, +25% vs the previous 7 days, across 128 companies hiring.
Site traffic: 51200 visitors and 133400 pageviews in the last 28 days, mostly organic search.
- "Globex — 22 open roles" — https://devboard.example.com/companies/globex
```

## Go live

Swap `llm_provider`/`gsc_provider` for real vendors as in the other examples. Your
two custom classes don't change — point them at your real database or API instead
of the sample files. You could also add a grounded `llm` discovery source next to
your custom one; they'd be scored together exactly as shown above.
</content>
