from __future__ import annotations

import json

from .templated_json import load_raw, render_text

__all__ = ["load_raw", "render_report", "TemplatedAnalyticsClient"]


def render_report(summary_template: str, highlights_template: str, raw: dict, limit: int) -> dict:
    """Renders a tenant's two analytics templates against their raw JSON, producing
    the generic {"summary": str, "highlights": [{label, url}, ...]} shape.

    Shared by TemplatedAnalyticsClient.report() (runtime) and AgentConfig.from_json
    (config-save-time validation) — both must use the exact same logic, so a template
    that validates successfully is guaranteed to behave the same way at run time.

    highlights_template must render to a JSON array string (typically built with
    Jinja2's `tojson` filter inside a `{% for %}` loop over the tenant's own raw
    shape) — this is where the check that it's actually well-formed happens.
    """
    context = {"data": raw, "limit": limit}
    summary = render_text(summary_template, context)
    highlights_json = render_text(highlights_template, context)
    try:
        highlights = json.loads(highlights_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"analytics_highlights_template did not render to valid JSON: {exc}\n"
            f"Rendered output: {highlights_json!r}"
        ) from exc
    if not isinstance(highlights, list) or not all(
        isinstance(item, dict) and "label" in item and "url" in item for item in highlights
    ):
        raise ValueError(
            'analytics_highlights_template must render to a JSON array of '
            f'{{"label": ..., "url": ...}} objects, got: {highlights_json!r}'
        )
    return {"summary": summary, "highlights": highlights}


class TemplatedAnalyticsClient:
    """AppAnalyticsClient for a tenant whose data is just JSON with its own field
    names, mapped declaratively instead of via a deployed Python class (see
    analytics_provider="custom" for that heavier path). The tenant writes two
    Jinja2 templates (AgentConfig.analytics_summary_template/
    analytics_highlights_template) against their raw JSON's own shape — same
    mechanism and validate-at-config-save-time pattern as prompt_templates.
    """

    def __init__(
        self, source: str, summary_template: str, highlights_template: str,
        *, report_path: str = "", api_url: str = "", api_method: str = "GET",
        api_headers: dict | None = None, api_timeout_seconds: float = 10.0,
    ):
        self.source = source
        self.summary_template = summary_template
        self.highlights_template = highlights_template
        self.report_path = report_path
        self.api_url = api_url
        self.api_method = api_method
        self.api_headers = api_headers or {}
        self.api_timeout_seconds = api_timeout_seconds

    def report(self, limit: int = 5) -> dict:
        raw = load_raw(
            self.source, report_path=self.report_path, api_url=self.api_url,
            api_method=self.api_method, api_headers=self.api_headers,
            api_timeout_seconds=self.api_timeout_seconds,
        )
        return render_report(self.summary_template, self.highlights_template, raw, limit)
