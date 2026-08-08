"""A newsletter, built as two tenant-declared stages on top of one built-in one.

Nothing in `src/` knows this example exists. `tenant.json` names these classes
under `pipelines.newsletter.stages`, and `--agent newsletter` runs them — the
same mechanism example 08 uses for a site audit, with one difference worth the
extra example: the first stage here is **built-in**. `analyze_context` ships with
the project and already collects analytics and every configured signal
concurrently, degrading rather than aborting when one fails, so a pipeline that
needs "everything this run knows" reuses it instead of re-implementing it. A
declared pipeline is a list of stages, not a list of *your* stages.

Which is what makes this deliverable a newsletter rather than a summary of one:
an issue is only worth sending if it is built out of what actually happened —
what you published, what your list read, what people started asking — and all
three of those are already signals. `signal_sources` was designed to feed a
draft; nothing about it assumed the deliverable was a draft.

**On what goes in an email.** ComposeStage drops any linked item that points off
your own domain. A newsletter is the one output here that arrives in someone's
inbox with your name on it, so a link that a data feed put in front of you is not
the same thing as a link you are willing to send to 1,284 people — see
ComposeStage below. And nothing in this repo sends anything: the run ends with a
result, `output_sinks` hands it to whatever you point it at, and a human decides
whether that hop happens. See this example's README.
"""

from urllib.parse import urlparse

from jinja2 import Environment


def _host(url: str) -> str:
    return urlparse(url or "").netloc.lower()


class CurateStage:
    """Signals in, sections out.

    Reads `state["analyze_context"]` — what the built-in first stage collected —
    and decides what this issue is about. That decision is the tenant's whole
    editorial position, which is exactly why it lives here and not in `src/`:
    "the three guides we published, then what the list actually read, then what
    people are newly asking" is one publication's answer, not the runtime's.

    Note what it does with `tool_errors`. `analyze_context` reports a signal that
    failed rather than hiding it, and a stage reading that output has to carry
    the report forward — a degrade nobody records is indistinguishable from a
    quiet month.

    Constructed with `(tools, config)`: a stage asks for its entry's `options` by
    taking a third argument, and this one has nothing to configure. ComposeStage
    below takes all three.
    """

    def __init__(self, tools, config) -> None:
        self.config = config

    def run(self, state: dict) -> dict:
        context = state.get("analyze_context") or {}
        signals = context.get("signals", {})

        published = signals.get("content_updates", {}).get("items", [])
        trending = signals.get("keyword_trends", {}).get("items", [])
        most_read = context.get("analytics_highlights", [])

        sections = [
            {
                "title": "New since the last issue",
                "source": "signal:content_updates",
                "linked": True,
                "items": [
                    {"label": row.get("label", ""), "url": row.get("url", ""),
                     "note": row.get("note", "")}
                    for row in published
                ],
            },
            {
                "title": "What everyone read last month",
                "source": "analytics highlights",
                "linked": True,
                "items": [
                    {"label": row.get("label", ""), "url": row.get("url", ""), "note": ""}
                    for row in most_read
                ],
            },
            {
                # No URLs in this one, and that is a section, not a defect: what
                # people are searching for is worth telling your list even when
                # you have not written the answer yet. It is also next issue's
                # commissioning list.
                "title": "What people started asking this month",
                "source": "signal:keyword_trends",
                "linked": False,
                "items": [
                    {"label": row.get("label", ""), "url": "",
                     "note": f"{row.get('change_pct', 0)}% more searches"}
                    for row in trending
                ],
            },
        ]

        working = dict(state.get("working", {}))
        working["sections"] = [section for section in sections if section["items"]]
        working["audience_summary"] = context.get("analytics_summary", "")
        working["tool_errors"] = [
            *working.get("tool_errors", []), *context.get("tool_errors", []),
        ]
        return {"phase": "curate", "working": working}


class ComposeStage:
    """VERIFY, then write. This pipeline's `self_qa`, and its last stage.

    The check is one rule: **a linked item must point at this tenant's own
    domain.** Every other example verifies something before it ships it — a
    discovery link has to have come from a real search result, an audit finding
    has to name a page the crawl actually saw — and this is the same rule aimed
    at the case where being wrong is most expensive. The items here arrive from
    signal feeds, which is to say from systems this repo has never seen; a
    syndicated listing, a partner URL or a stale redirect in one of them becomes,
    unchecked, a link you personally mailed to your entire list.

    Dropped items are reported in `qa_notes`, never silently removed — the whole
    point of a human approving this is that they can see what was taken out.

    The check runs *before* `max_items_per_section` trims the list, not after. A
    limit applied first would quietly decide which links get verified: the
    off-domain item in this example's fixture is fourth, so cutting to three
    first would have produced a clean run that had checked nothing.
    """

    def __init__(self, tools, config, options) -> None:
        self.tools = tools
        self.config = config
        self.options = options

    def run(self, state: dict) -> dict:
        working = dict(state["working"])
        site_host = _host(self.config.site_url)
        limit = int(self.options.get("max_items_per_section", 3))

        sections, qa_notes = [], []
        for section in working["sections"]:
            if not section["linked"]:
                sections.append({**section, "items": section["items"][:limit]})
                continue
            kept = []
            for item in section["items"]:
                if _host(item["url"]) == site_host:
                    kept.append(item)
                else:
                    qa_notes.append(
                        f'dropped, links off {site_host}: "{item["label"]}" ({item["url"]})'
                    )
            if kept:
                sections.append({**section, "items": kept[:limit]})

        lead = sections[0]["items"][0] if sections else {"label": "this month's issue"}
        env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
        subject = env.from_string(self.options["subject_template"]).render(
            lead=lead, site_url=self.config.site_url,
        )
        body = env.from_string(self.options["newsletter_template"]).render(
            subject=subject,
            sections=sections,
            audience_summary=working["audience_summary"],
            brand_description=self.config.brand_description,
            site_url=self.config.site_url,
        )

        working["sections"] = sections
        working["qa_notes"] = qa_notes

        # A new deliverable is a new `kind`, never a new top-level field — the
        # result shape in docs/output-schema.md is frozen and takes this as-is.
        # `requires_approval` is a note to whatever reads this result, not
        # something the runtime enforces: nothing here sends email, and nothing
        # here checks the flag for you.
        return {
            "phase": "done",
            "working": working,
            "output": {
                "kind": "newsletter",
                "title": subject,
                "content": body,
                "format": "markdown",
                "metadata": {
                    "subject": subject,
                    "sections": [
                        {"title": s["title"], "source": s["source"], "items": len(s["items"])}
                        for s in sections
                    ],
                    "items": sum(len(s["items"]) for s in sections),
                    "dropped": qa_notes,
                    "requires_approval": True,
                },
            },
        }
