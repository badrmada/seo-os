# 08 — a different deliverable: a site audit

Every other example writes something. This one doesn't: it reads a site's pages
and reports **what to fix on the pages that already exist**.

Nothing in `src/` knows this example exists. The stages are three classes in
`plugins/`, named by `tenant.json`, and the result is the same frozen schema
every other run returns — with a different `kind`.

That is the whole point of config-declared pipelines. Writing an article is one
way to grow a site and telling someone what to fix is another, but which findings
matter and what a crawler does are *your* position to hold, not this repo's.

## The files

```
plugins/audit.py            three stages: crawl -> findings -> verify
templates/audit_report.j2   the report wording (a template file, not an escaped line)
data/crawl.json             the pages, standing in for a crawler
tenant.json
input.json
```

## Declaring the pipeline

```jsonc
{
  "agent_type": "site_audit",
  "pipelines": {
    "site_audit": {
      "stages": [
        { "name": "crawl",    "class": "audit:CrawlStage",
          "options": { "pages_path": "data/crawl.json" } },
        { "name": "findings", "class": "audit:FindingsStage" },
        { "name": "verify",   "class": "audit:VerifyStage",
          "options": { "report_template": { "file": "audit_report.j2" } } }
      ]
    }
  }
}
```

List order is the chain. Each stage is `{"name", "class", "mode", "options"}`;
`class` is `"module:ClassName"` in this tenant's `plugins/`, and can be left out
when `name` is one of the built-in stages (`analyze`, `draft`, …) — so a pipeline
can mix its own stages with the ones that ship.

## Writing a stage

```python
class FindingsStage:
    def __init__(self, tools, config):   # (tools, config, options) to get your own options
        self.config = config

    async def run(self, state):          # a plain `def` works too
        return {"phase": "findings", "working": {...}}
```

`run(state)` returns **only the keys it changes**; LangGraph merges them into the
running state for the next stage. `VerifyStage` here is deliberately sync, to
show that a stage isn't forced to be async.

Three things this example is careful about, and a real audit should be too:

- **Findings carry their evidence.** Every finding lists the exact URLs it came
  from. An audit that asserts problems it cannot point at is worse than no audit
   — the same principle that makes a grounded discovery link trustworthy.
- **The last stage verifies before it reports.** `VerifyStage` drops any finding
  referencing a URL the crawl never saw. It's this pipeline's `self_qa`.
- **A new deliverable is a new `kind`, not a new field.** The result is the
  schema in [output-schema.md](../../docs/output-schema.md), with
  `output.kind = "site_audit"` and the findings under `output.metadata`.

## About the "crawler"

`CrawlStage` reads a fixture, so this runs offline. That is not only convenience:
**a crawler is the one tool here that can hurt someone else's server.** A real
one obeys `robots.txt`, rate-limits, caps pages/depth/total time, sends an
identifying user agent, and never follows off-site links. A default that could
hammer a site is not an acceptable default.

A real crawl is also better expressed as a `signal_sources` entry
([example 07](../07-signal-inputs/)) than as a stage — then it's an *input* any
pipeline can read, not something one pipeline owns.

## Run it

```bash
python src/main.py show-graph --userdata examples --tenant 08-custom-pipeline
python src/main.py check-data --userdata examples --tenant 08-custom-pipeline
python src/main.py run --userdata examples --tenant 08-custom-pipeline
```

Or `make example EXAMPLE=08-custom-pipeline`, or in Docker with nothing installed —
the same run three ways is in [Running an example](../README.md#running-an-example).

The same config still runs the built-in agent, because a tenant is not limited to
one deliverable:

```bash
python src/main.py run --userdata examples --tenant 08-custom-pipeline --agent seo_content
```
