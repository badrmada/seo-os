from __future__ import annotations

import json
from pathlib import Path

import httpx
import jinja2

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
    """Fetches a tenant's raw JSON, from a file or a live API — the **sync** path,
    kept for config-save-time validation (agent/validators/template_validator.py),
    which runs while a config is being loaded, outside any event loop. Validating a
    template against source="api" means an actual live request, not a fabricated
    sample, so this really does have to fetch.

    At run time the clients below use aload_raw() instead."""
    if source == "file":
        return _load_file(report_path)
    if source == "api":
        with httpx.Client(follow_redirects=True) as client:
            response = client.request(
                api_method, api_url, headers=api_headers or {}, timeout=api_timeout_seconds,
            )
        response.raise_for_status()
        return response.json()
    raise ValueError(f'source must be "file" or "api", got {source!r}')


async def aload_raw(
    source: str, *, report_path: str = "", api_url: str = "", api_method: str = "GET",
    api_headers: dict | None = None, api_timeout_seconds: float = 10.0,
) -> dict:
    """The run-time path: same contract as load_raw, without holding the event loop
    for the duration of an HTTP request.

    The file branch stays a plain blocking read on purpose — a local JSON report is
    a few milliseconds, and a thread hop to save them costs more than it saves.
    That is a measurement, not a rule: if a tenant's report ever becomes large
    enough to matter, this is the line to change."""
    if source == "file":
        return _load_file(report_path)
    if source == "api":
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.request(
                api_method, api_url, headers=api_headers or {}, timeout=api_timeout_seconds,
            )
        response.raise_for_status()
        return response.json()
    raise ValueError(f'source must be "file" or "api", got {source!r}')


def _load_file(report_path: str) -> dict:
    return json.loads(Path(report_path).read_text(encoding="utf-8"))
