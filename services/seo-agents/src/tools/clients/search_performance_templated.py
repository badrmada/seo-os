from __future__ import annotations

import json

from .search_performance_rows import enrich_rows
from .templated_json import aload_raw, render_text

__all__ = ["render_rows", "TemplatedSearchPerformanceClient"]


def render_rows(rows_template: str, raw: dict, days: int, row_limit: int) -> list[dict]:
    """Render a tenant's rank data into rows, then classify them centrally.

    `rows_template` must render to a JSON array of
    `{"query", "clicks", "impressions", "ctr", "position"}` objects — the same
    "render to a JSON array" contract as analytics' highlights_template and a
    signal's items_template, so a tenant who has written one already knows this.

    **The template supplies data, never judgement.** It does not produce
    `opportunity`, `score` or `reason`; enrich_rows does, from the four numbers
    below. Asking a tenant to reimplement striking-distance classification and
    scoring in Jinja2 would be miserable to write and would quietly disagree with
    what the Google provider decides — and "which keyword is worth targeting" is
    the one thing here that must not vary by data source.
    """
    rendered = render_text(rows_template, {"data": raw, "days": days, "limit": row_limit})
    try:
        rows = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"search_performance_options.rows_template did not render to valid JSON: {exc}\n"
            f"Rendered output: {rendered!r}"
        ) from exc
    if not isinstance(rows, list):
        raise ValueError(
            "search_performance_options.rows_template must render to a JSON array of "
            f'{{"query", "clicks", "impressions", "ctr", "position"}} objects, got: {rendered!r}'
        )
    return enrich_rows(rows, row_limit=row_limit)


class TemplatedSearchPerformanceClient:
    """search_performance_provider="templated" — a tenant's own rank data (a file
    or a live API) mapped declaratively, no Python required.

    This is what makes the kind genuinely vendor-neutral rather than
    Google-or-nothing: a Search Console CSV export, a Bing Webmaster Tools
    response, an Ahrefs or Semrush payload, or an agency's monthly JSON all become
    one template. Same mechanism as the templated analytics/traffic/signal
    providers.

    `trend` and `top_page` come out "flat"/None here, because a single snapshot
    has no prior period and no page mapping to compare against. That is a real
    difference from the Google provider and it degrades rather than fabricates —
    every other derived field is identical.
    """

    def __init__(
        self, rows_template: str, source: str = "file",
        *, report_path: str = "", api_url: str = "", api_method: str = "GET",
        api_headers: dict | None = None, api_timeout_seconds: float = 10.0,
    ):
        if not rows_template:
            raise ValueError(
                'search_performance_provider="templated" requires '
                "search_performance_options.rows_template"
            )
        self.rows_template = rows_template
        self.source = source
        self.report_path = report_path
        self.api_url = api_url
        self.api_method = api_method
        self.api_headers = api_headers or {}
        self.api_timeout_seconds = api_timeout_seconds

    async def search_analytics(self, days: int = 28, row_limit: int = 500) -> list[dict]:
        raw = await aload_raw(
            self.source, report_path=self.report_path, api_url=self.api_url,
            api_method=self.api_method, api_headers=self.api_headers,
            api_timeout_seconds=self.api_timeout_seconds,
        )
        return render_rows(self.rows_template, raw, days, row_limit)
