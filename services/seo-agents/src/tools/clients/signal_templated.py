from __future__ import annotations

import json

from .templated_json import aload_raw, render_text

__all__ = ["render_signal", "TemplatedSignalSource"]


def render_signal(
    summary_template: str, facts_template: str, items_template: str,
    raw: dict, context: dict,
) -> dict:
    """Renders a tenant's signal templates against their raw JSON, producing the
    generic {"summary", "facts", "items"} shape (agent/schemas/signal.py).

    Same contract as the analytics/traffic templated providers: summary_template
    renders directly to text, while facts_template and items_template must render
    to a JSON object and a JSON array respectively (typically via Jinja2's `tojson`
    filter) — this is where "did it actually render to valid JSON?" is checked, so
    a broken template is a clear error naming the option rather than a confusing
    shape further downstream.

    Only summary_template is required; a signal that just describes itself in prose
    is a complete signal.
    """
    render_context = {"data": raw, "context": context}
    return {
        "summary": render_text(summary_template, render_context) if summary_template else "",
        "facts": _render_json(
            facts_template, render_context, "facts_template", dict, "an object",
        ) or {},
        "items": _render_json(
            items_template, render_context, "items_template", list, "an array",
        ) or [],
    }


def _render_json(template: str, context: dict, option: str, expected: type, described: str):
    if not template:
        return None
    rendered = render_text(template, context)
    try:
        value = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"signal_sources options.{option} did not render to valid JSON: {exc}\n"
            f"Rendered output: {rendered!r}"
        ) from exc
    if not isinstance(value, expected):
        raise ValueError(
            f"signal_sources options.{option} must render to {described}, got: {rendered!r}"
        )
    return value


class TemplatedSignalSource:
    """SignalSource for a tenant whose signal is just JSON with its own field names
    — a trends export, a rank-tracker API, an internal dashboard endpoint — mapped
    declaratively instead of via a deployed Python class (see provider="custom" for
    that heavier path).

    The same file/API loading and Jinja2 mapping as the templated analytics and
    traffic providers, so a tenant who has written one of those already knows how
    to write this. The one addition is `context`: a signal's templates can read the
    run's own context ({{ context.seed_keyword }}, {{ context.site_url }}) as well
    as the fetched data, since unlike an analytics report a signal is often
    *about* whatever this run is going after.
    """

    def __init__(
        self, name: str, source: str, summary_template: str,
        *, facts_template: str = "", items_template: str = "",
        report_path: str = "", api_url: str = "", api_method: str = "GET",
        api_headers: dict | None = None, api_timeout_seconds: float = 10.0,
    ):
        self.name = name
        self.source = source
        self.summary_template = summary_template
        self.facts_template = facts_template
        self.items_template = items_template
        self.report_path = report_path
        self.api_url = api_url
        self.api_method = api_method
        self.api_headers = api_headers or {}
        self.api_timeout_seconds = api_timeout_seconds

    async def collect(self, context: dict) -> dict:
        raw = await aload_raw(
            self.source, report_path=self.report_path, api_url=self.api_url,
            api_method=self.api_method, api_headers=self.api_headers,
            api_timeout_seconds=self.api_timeout_seconds,
        )
        return render_signal(
            self.summary_template, self.facts_template, self.items_template, raw, context,
        )
