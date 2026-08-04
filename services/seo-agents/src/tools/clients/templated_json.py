from __future__ import annotations

import json
from pathlib import Path

import jinja2
import requests

# Shared by tools/clients/analytics_templated.py and tools/clients/traffic_templated.py — both let a
# tenant map their own raw JSON (a file or a live API) into a generic shape via
# Jinja2 templates, so this loading/rendering logic is the same either way; only
# what each one's template renders *to* differs.

_ENV = jinja2.Environment(
    trim_blocks=True, lstrip_blocks=True, undefined=jinja2.StrictUndefined,
)


def render_text(template_str: str, context: dict) -> str:
    """Render a tenant's Jinja2 template. Raises ValueError (not a jinja2 exception)
    on bad syntax or a missing variable, so callers don't need to know Jinja2's
    exception types."""
    try:
        return _ENV.from_string(template_str).render(**context)
    except jinja2.TemplateError as exc:
        raise ValueError(f"template error: {exc}") from exc


def load_raw(
    source: str, *, report_path: str = "", api_url: str = "", api_method: str = "GET",
    api_headers: dict | None = None, api_timeout_seconds: float = 10.0,
) -> dict:
    """Fetches a tenant's raw JSON, from a file or a live API — used both at run time
    and at config-save-time validation, so validating a template against source="api"
    means an actual live request, not a fabricated sample."""
    if source == "file":
        return json.loads(Path(report_path).read_text(encoding="utf-8"))
    if source == "api":
        response = requests.request(
            api_method, api_url, headers=api_headers or {}, timeout=api_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
    raise ValueError(f'source must be "file" or "api", got {source!r}')
