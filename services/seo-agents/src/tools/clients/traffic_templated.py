from __future__ import annotations

from .templated_json import load_raw, render_text

__all__ = ["load_raw", "render_summary", "TemplatedTrafficClient"]


def render_summary(summary_template: str, raw: dict, days: int) -> dict:
    """Renders a tenant's traffic template against their raw JSON, producing the
    generic {"summary": str} shape. Shared by TemplatedTrafficClient.traffic_summary()
    (runtime) and AgentConfig.from_json (config-save-time validation)."""
    return {"summary": render_text(summary_template, {"data": raw, "days": days})}


class TemplatedTrafficClient:
    """SiteTrafficClient for a tenant whose traffic data is just JSON with its own
    field names (not necessarily Cloudflare's), mapped declaratively via one Jinja2
    template (AgentConfig.traffic_summary_template) instead of a deployed Python
    class — same mechanism as TemplatedAnalyticsClient."""

    def __init__(
        self, source: str, summary_template: str,
        *, report_path: str = "", api_url: str = "", api_method: str = "GET",
        api_headers: dict | None = None, api_timeout_seconds: float = 10.0,
    ):
        self.source = source
        self.summary_template = summary_template
        self.report_path = report_path
        self.api_url = api_url
        self.api_method = api_method
        self.api_headers = api_headers or {}
        self.api_timeout_seconds = api_timeout_seconds

    def traffic_summary(self, days: int = 28) -> dict:
        raw = load_raw(
            self.source, report_path=self.report_path, api_url=self.api_url,
            api_method=self.api_method, api_headers=self.api_headers,
            api_timeout_seconds=self.api_timeout_seconds,
        )
        return render_summary(self.summary_template, raw, days)
