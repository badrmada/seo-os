class TemplateValidator:
    """Validates a tenant's "templated" analytics/traffic provider config at load
    time — called by agent/config/loader.py's AgentConfigLoader. Fails fast on a
    broken Jinja2 template or unreachable source instead of failing mid-run."""

    def validate_analytics(self, config, path: str) -> None:
        # Deferred import: tools/ sits below agent/ in this project's layering
        # everywhere else, and this is the one place agent/config needs a tools/
        # module — only when analytics_provider="templated" is actually used.
        from tools.clients.analytics_templated import load_raw, render_report

        from ..config.paths import resolve_path

        if not config.analytics_summary_template or not config.analytics_highlights_template:
            raise ValueError(
                f'analytics_provider="templated" in {path} requires analytics_summary_template '
                "and analytics_highlights_template to both be set"
            )
        try:
            raw = load_raw(
                config.analytics_source, report_path=resolve_path(config, config.analytics_report_path),
                api_url=config.analytics_api_url, api_method=config.analytics_api_method,
                api_headers=config.analytics_api_headers,
                api_timeout_seconds=config.analytics_api_timeout_seconds,
            )
        except Exception as exc:
            raise ValueError(f"Could not load analytics data to validate templates in {path}: {exc}") from exc
        try:
            render_report(
                config.analytics_summary_template, config.analytics_highlights_template,
                raw, config.analytics_highlights_limit,
            )
        except ValueError as exc:
            raise ValueError(f"Invalid analytics template(s) in {path}: {exc}") from exc

    def validate_traffic(self, config, path: str) -> None:
        from tools.clients.traffic_templated import load_raw, render_summary

        from ..config.paths import resolve_path

        if not config.traffic_summary_template:
            raise ValueError(
                f'traffic_provider="templated" in {path} requires traffic_summary_template to be set'
            )
        try:
            raw = load_raw(
                config.traffic_source, report_path=resolve_path(config, config.traffic_report_path),
                api_url=config.traffic_api_url, api_method=config.traffic_api_method,
                api_headers=config.traffic_api_headers,
                api_timeout_seconds=config.traffic_api_timeout_seconds,
            )
        except Exception as exc:
            raise ValueError(f"Could not load traffic data to validate template in {path}: {exc}") from exc
        try:
            render_summary(config.traffic_summary_template, raw, days=28)
        except ValueError as exc:
            raise ValueError(f"Invalid traffic_summary_template in {path}: {exc}") from exc
