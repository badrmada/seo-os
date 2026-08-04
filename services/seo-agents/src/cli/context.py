"""What every command needs and none of them should re-implement: resolving the
tenant/input paths, loading them with a clean error instead of a traceback, and
turning the shared verbosity flags into a reporter.

Commands stay thin because this is here. A new command gets the same path
handling, the same error style, and the same -v behavior for free.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from agent.config import AgentConfigLoader
from agent.observability import build_reporter

DEFAULT_TENANT_FILENAME = "tenant.json"
DEFAULT_INPUT_FILENAME = "input.json"

# Shared option definitions, so every command spells these the same way. Typer
# reads them as defaults on the command function's parameters.
TENANT_OPTION = typer.Option(
    DEFAULT_TENANT_FILENAME, "--tenant", "-t",
    help="Tenant config JSON. Relative paths resolve against the current directory.",
)
INPUT_OPTION = typer.Option(
    DEFAULT_INPUT_FILENAME, "--input", "-i",
    help="Run input JSON. Relative paths resolve against the current directory.",
)
VERBOSE_OPTION = typer.Option(
    0, "--verbose", "-v", count=True,
    help="Follow the run on stderr: -v for stages and tool calls, -vv to also show payloads.",
)
QUIET_OPTION = typer.Option(
    False, "--quiet", "-q",
    help="Force verbose off, overriding a tenant config that enables it.",
)
VERBOSE_FORMAT_OPTION = typer.Option(
    None, "--verbose-format",
    help="Verbose output format: 'text' (default) or 'json' for newline-delimited events.",
)


def fail(message: str) -> typer.Exit:
    """Print an error the way a CLI should — one red line on stderr, no traceback —
    and exit non-zero. Returned rather than raised so call sites read as
    `raise fail(...)`, which keeps control flow obvious to both readers and linters."""
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(1)


def resolve_existing(path_str: str, label: str) -> Path:
    path = Path(path_str).expanduser().resolve()
    if not path.is_file():
        raise fail(f"{label} file not found: {path}")
    return path


def load_config(tenant: str):
    """Load a tenant config, surfacing a bad one as a clean CLI error. The loader
    validates prompt/analytics/traffic templates as it goes, so a template that
    can't render fails here rather than mid-run."""
    path = resolve_existing(tenant, "Tenant config")
    try:
        return AgentConfigLoader().load(str(path))
    except Exception as exc:  # noqa: BLE001 - any load failure is a user-facing config error
        raise fail(f"could not load {path}: {exc}") from exc


def load_input(input_path: str) -> dict:
    path = resolve_existing(input_path, "Input")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - malformed JSON is a user-facing error
        raise fail(f"could not parse {path}: {exc}") from exc


def make_reporter(config, verbose: int, quiet: bool, verbose_format: str):
    """Resolve the verbosity precedence in one place: --quiet wins over everything,
    then an explicit -v/-vv, then the tenant config's own `verbose` setting.

    -v can't express "off" (count options start at 0, which is also "not passed"),
    which is exactly why --quiet exists — otherwise a tenant with verbose enabled
    in config would have no way to silence a single run.
    """
    if quiet:
        return build_reporter(0)
    level = verbose or getattr(config, "verbose", 0)
    return build_reporter(level, verbose_format or getattr(config, "verbose_format", "text"))
