class TemplateValidator:
    """Validates a tenant's "templated" analytics/traffic provider config at load
    time — called by agent/config/loader.py's AgentConfigLoader. Fails fast on a
    broken Jinja2 template or unreachable source instead of failing mid-run.

    Reads the provider's own `analytics_options` / `traffic_options`, the same
    place ToolsManager builds the real client from, so "it validated" and "it
    runs" can't mean different things.
    """

    def validate_analytics(self, config, path: str) -> None:
        # Deferred import: tools/ sits below agent/ in this project's layering
        # everywhere else, and this is the one place agent/config needs a tools/
        # module — only when analytics_provider="templated" is actually used.
        from tools.clients.analytics_templated import load_raw, render_report

        from ..config.paths import resolve_path

        options = config.analytics_options or {}
        if not options.get("summary_template") or not options.get("highlights_template"):
            raise ValueError(
                f'analytics_provider="templated" in {path} requires analytics_options.summary_template '
                "and analytics_options.highlights_template to both be set"
            )
        try:
            raw = load_raw(
                options.get("source", "file"),
                report_path=resolve_path(config, options.get("report_path", "")),
                api_url=options.get("api_url", ""),
                api_method=options.get("api_method", "GET"),
                api_headers=options.get("api_headers", {}),
                api_timeout_seconds=options.get("api_timeout_seconds", 10.0),
            )
        except Exception as exc:
            raise ValueError(f"Could not load analytics data to validate templates in {path}: {exc}") from exc
        try:
            render_report(
                options["summary_template"], options["highlights_template"],
                raw, config.analytics_highlights_limit,
            )
        except ValueError as exc:
            raise ValueError(f"Invalid analytics template(s) in {path}: {exc}") from exc

    def validate_traffic(self, config, path: str) -> None:
        from tools.clients.traffic_templated import load_raw, render_summary

        from ..config.paths import resolve_path

        options = config.traffic_options or {}
        if not options.get("summary_template"):
            raise ValueError(
                f'traffic_provider="templated" in {path} requires traffic_options.summary_template to be set'
            )
        try:
            raw = load_raw(
                options.get("source", "file"),
                report_path=resolve_path(config, options.get("report_path", "")),
                api_url=options.get("api_url", ""),
                api_method=options.get("api_method", "GET"),
                api_headers=options.get("api_headers", {}),
                api_timeout_seconds=options.get("api_timeout_seconds", 10.0),
            )
        except Exception as exc:
            raise ValueError(f"Could not load traffic data to validate template in {path}: {exc}") from exc
        try:
            render_summary(options["summary_template"], raw, days=28)
        except ValueError as exc:
            raise ValueError(f"Invalid traffic_options.summary_template in {path}: {exc}") from exc
