"""A site audit, built as three tenant-declared pipeline stages.

Nothing in `src/` knows this exists. `tenant.json` names these classes under
`pipelines.site_audit.stages`, and `--agent site_audit` runs them — which is the
whole claim of PLAN.md Step G: "the deliverable is not always a draft" is
something a tenant can act on without forking.

A stage is constructed with `(tools, config)`, or `(tools, config, options)` to
receive its own entry's `options` — the same opt-in every "custom" provider has.
`run(state)` returns only the state keys it changes; LangGraph merges them.
VerifyStage is deliberately a plain `def` to show that a sync stage works too.

**On crawling.** `CrawlStage` here reads a fixture so the example runs offline,
and that is not just convenience: a crawler is the one tool in this system that
can hurt somebody else's server. A real one must obey robots.txt, rate-limit,
cap pages/depth/total time, send an identifying user agent, and never follow
off-site links. A default that could hammer a site is not an acceptable default —
see docs/extending.md.
"""

import json
from pathlib import Path

from jinja2 import Environment


class CrawlStage:
    """Loads the pages this audit is about.

    Stands in for a crawler: same output shape, no network. In a real build this
    is where a bounded fetch loop goes — or, better, a `signal_sources` entry, so
    the crawl is an *input* that any pipeline can read (see
    examples/07-signal-inputs).
    """

    def __init__(self, tools, config, options) -> None:
        self.config = config
        self.options = options

    async def run(self, state: dict) -> dict:
        base = Path(self.config.config_base_dir or ".")
        pages = json.loads((base / self.options["pages_path"]).read_text(encoding="utf-8"))

        working = dict(state.get("working", {}))
        working["pages"] = pages
        return {"phase": "crawl", "working": working}


class FindingsStage:
    """Turns pages into findings, each carrying the URLs it came from.

    Evidence is the point: an audit that asserts problems it cannot point at is
    worse than no audit, the same principle that makes a grounded discovery link
    trustworthy. Every rule below records the exact pages that triggered it.
    """

    RULES = (
        ("missing meta description", "high", lambda p: not p.get("meta_description")),
        ("title over 60 characters", "medium", lambda p: len(p.get("title", "")) > 60),
        ("thin content (under 300 words)", "medium", lambda p: p.get("word_count", 0) < 300),
        ("no h1", "high", lambda p: not p.get("h1")),
        ("not linked from anywhere else on the site", "low", lambda p: p.get("inlinks", 0) == 0),
    )

    def __init__(self, tools, config) -> None:
        self.config = config

    async def run(self, state: dict) -> dict:
        pages = state["working"]["pages"]

        findings = []
        for issue, severity, matches in self.RULES:
            urls = [page["url"] for page in pages if matches(page)]
            if urls:
                findings.append({
                    "issue": issue,
                    "severity": severity,
                    "urls": urls,
                    "evidence": f"{len(urls)} of {len(pages)} crawled pages",
                })

        order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: (order[f["severity"]], -len(f["urls"])))

        working = dict(state["working"])
        working["findings"] = findings
        return {"phase": "findings", "working": working}


class VerifyStage:
    """This pipeline's equivalent of self_qa, and its last stage.

    Two jobs: refuse to report a finding that points at a URL the crawl never saw
    (the audit version of "only trust a link the search actually returned"), and
    write `output`. Sync on purpose — LangGraph runs a plain `def` node in a
    worker thread, so a tenant's stage is not forced to be async.
    """

    def __init__(self, tools, config, options) -> None:
        self.config = config
        self.options = options

    def run(self, state: dict) -> dict:
        working = dict(state["working"])
        pages = working["pages"]
        crawled = {page["url"] for page in pages}

        verified, unverified = [], []
        for finding in working["findings"]:
            if set(finding["urls"]) <= crawled:
                verified.append(finding)
            else:
                unverified.append(finding["issue"])

        report = Environment(autoescape=False).from_string(self.options["report_template"]).render(
            site_url=self.config.site_url,
            findings=verified,
            pages=pages,
            agent_goal=self.config.agent_goal,
        )

        working["findings"] = verified
        working["qa_notes"] = (
            [f"dropped, evidence not in the crawl: {issue}" for issue in unverified] or []
        )

        # A new deliverable is a new `kind`, never a new top-level field — the
        # result shape in docs/output-schema.md is frozen and takes this as-is.
        return {
            "phase": "done",
            "working": working,
            "output": {
                "kind": "site_audit",
                "title": f"Site audit for {self.config.site_url}",
                "content": report,
                "format": "markdown",
                "metadata": {"findings": verified, "pages_crawled": len(pages)},
            },
        }
