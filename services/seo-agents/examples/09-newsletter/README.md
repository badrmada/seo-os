# 09 — a newsletter, assembled from the signals

Example 08 proved the deliverable doesn't have to be a draft. This one goes
further: the deliverable doesn't have to be *for search engines* at all.

Sproutly already feeds this agent three things — what they published, what their
list actually read, and what people started searching for. That is a monthly
issue. Nobody needs to write it from scratch, and nothing new needs to be
connected: `signal_sources` was built to inform a draft, but nothing about it
assumed the deliverable was a draft.

So this pipeline reads the same inputs as every other example and returns
`kind: "newsletter"` — a subject line and a ready-to-read issue, with every link
in it checked.

## The files

```
plugins/newsletter.py       two stages: curate -> compose (the first stage is built in)
templates/newsletter.j2     the issue's wording — a template file, not an escaped line
data/content.json           what went up since the last issue
data/audience.json          the list: size, open rate, most-read guides
data/keyword_trends.json    what people started searching for
tenant.json
input.json
```

## Declaring the pipeline

```jsonc
{
  "agent_type": "newsletter",
  "pipelines": {
    "newsletter": {
      "stages": [
        { "name": "analyze_context" },
        { "name": "curate",  "class": "newsletter:CurateStage" },
        { "name": "compose", "class": "newsletter:ComposeStage",
          "options": {
            "max_items_per_section": 3,
            "newsletter_template": { "file": "newsletter.j2" },
            "subject_template": "Sproutly Weekly — {{ lead.label }}"
          } }
      ]
    }
  }
}
```

**The first stage ships with the project.** `analyze_context` is a built-in — the
one that collects analytics and every configured signal concurrently, and
degrades to an empty value plus a `tool_errors` entry when one of them fails
instead of killing the run. A stage entry with no `"class"` names a built-in, so
"everything this run knows" costs one line rather than a re-implementation. That
is the difference from [example 08](../08-custom-pipeline/), which declares all
three of its stages: **a declared pipeline is a list of stages, not a list of
*your* stages.**

`CurateStage` then takes `(tools, config)` and `ComposeStage` takes
`(tools, config, options)` — a stage receives its own entry's `options` by asking
for a third argument, and curate has nothing to configure.

## What the stages do

**`curate` — signals in, sections out.** It reads `state["analyze_context"]` and
decides what the issue is about:

| Section | Comes from |
|---|---|
| New since the last issue | the `content_updates` signal |
| What everyone read last month | the analytics highlights (`{label, url}` already) |
| What people started asking this month | the `keyword_trends` signal |

That ordering is Sproutly's editorial position, which is exactly why it lives in
their folder and not in `src/`. The third section carries no links, and that's a
section rather than a defect: what people are searching for is worth telling your
list before you've written the answer — it's also next month's commissioning
list.

**`compose` — verify, then write.** One rule, and it is the reason this example
exists separately from 08:

> **A linked item must point at your own domain.** Anything else is dropped and
> reported in `qa_notes`.

Every deliverable here verifies something before it ships — a discovered link has
to have come from a real search result, an audit finding has to name a page the
crawl actually saw. A newsletter is where being wrong costs the most: the items
arrive from feeds this repo has never seen, and an unchecked syndicated listing,
partner URL or stale redirect becomes a link you personally mailed to 1,284
people. `data/content.json` ships with exactly that — a marketplace listing sitting
fourth in the feed — so a real run drops something:

```
"dropped": [
  "dropped, links off sproutly.example.com: \"Sproutly starter kit\" (https://listings.marketplace-example.com/sproutly-starter-kit)"
]
```

Note the order: the check runs **before** `max_items_per_section` trims each
section. Trimming first would have cut that fourth item and produced a clean run
that had verified nothing.

## Sending it — and the human in the middle

The result is the same frozen schema every run returns
([output-schema.md](../../docs/output-schema.md)) with `output.kind =
"newsletter"`, so it can go straight to your email provider with no glue code:
[`output_sinks`](../../docs/configuration.md#where-the-result-goes-output-sinks)
already POSTs a finished run wherever you point it.

```jsonc
"output_sinks": [
  { "name": "stdout", "provider": "json" },
  { "name": "esp",    "provider": "webhook",
    "options": {
      "url": "https://api.your-esp.example.com/v1/drafts",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    } }
]
```

**Point it at the endpoint that creates a draft, not the one that sends.** Every
other deliverable in this repo lands somewhere a person looks before anyone else
does; a newsletter is the one that can reach thousands of inboxes, under your
name, with no further step. Approval is not a formality here — it is the last
place a wrong link, a wrong price or a wrong tone can still be stopped, and it
costs one person one minute.

That is why this example ships **no sink at all**: it prints to stdout, and going
further is a line you add deliberately. `output.metadata.requires_approval` is
there for whatever consumes the result — it is a note to your own system, not a
gate this repo enforces. Nothing here sends email, and nothing here checks that
flag for you.

If your provider wants a shape of its own, a
[custom sink](../../docs/extending.md#walkthrough-a-custom-output-sink) is one
method — `emit(self, output)` — in the same `plugins/` folder as these stages.

## Run it

```bash
python src/main.py show-graph --userdata examples --tenant 09-newsletter
python src/main.py check-data --userdata examples --tenant 09-newsletter
python src/main.py run --userdata examples --tenant 09-newsletter
```

Or `make example EXAMPLE=09-newsletter`, or in Docker with nothing installed —
the same run three ways is in [Running an example](../README.md#running-an-example).

`show-graph` prints `analyze_context -> curate -> compose` and, below it, "no
channel-aware stages here, so this run has no channel". A newsletter isn't a
site article, an external article or a reply, and none is invented for it.

The whole issue above is real offline — every heading, link, number and topic
comes from this tenant's own files. **No model is called at any point in this
pipeline**, which is worth sitting with: the interesting part of a newsletter was
never the prose, it was knowing what to put in it. If you do want the model to
write the intro paragraph, `self.tools.llm.generate(...)` in `ComposeStage` is
the same client every built-in stage uses.

## Going live

| Swap | To |
|---|---|
| `data/content.json` | your CMS's "published since" endpoint — the same signal with `"source": "api"` |
| `data/audience.json` | your ESP's campaign stats, via `analytics_options` `"source": "api"` |
| `data/keyword_trends.json` | a trends export or a rank tracker ([example 07](../07-signal-inputs/) has both) |
| stdout | the `esp` webhook sink above, pointed at your provider's **draft** endpoint |

The same config still runs the built-in agent, because a tenant is not limited to
one deliverable — the same signals that made this month's issue can write the
guide it links to next month:

```bash
python src/main.py run --userdata examples --tenant 09-newsletter --agent seo_content
```
