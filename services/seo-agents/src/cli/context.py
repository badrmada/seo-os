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

from agent.config.workspace import ROOT_ENV_VAR, TenantWorkspace, UnknownTenantError
from agent.observability import build_reporter

# Shared option definitions, so every command spells these the same way. Typer
# reads them as defaults on the command function's parameters.
TENANT_OPTION = typer.Option(
    ..., "--tenant", "-t",
    help="Tenant name — a folder in the workspace, not a path. See `list-tenants`.",
)
OPTIONAL_TENANT_OPTION = typer.Option(
    None, "--tenant", "-t",
    help="Tenant name — a folder in the workspace, not a path. See `list-tenants`.",
)
USERDATA_OPTION = typer.Option(
    None, "--userdata", "-u",
    help=f"Workspace root holding the tenant folders. Defaults to ${ROOT_ENV_VAR}, then ./userdata.",
)
INPUT_OPTION = typer.Option(
    None, "--input", "-i",
    help="Run input JSON. Defaults to input.json inside the tenant's own folder.",
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


def open_workspace(tenant: str, userdata: str = None) -> TenantWorkspace:
    """Resolve a tenant name to its folder. Every command goes through here, so
    "which tenant" means the same thing everywhere."""
    try:
        return TenantWorkspace.open(tenant, root=userdata)
    except (UnknownTenantError, ValueError) as exc:
        raise fail(str(exc)) from exc


def open_tenant(tenant: str, userdata: str = None):
    """Resolve a tenant and load its config in one step, returning both — commands
    that also need the tenant's folder (to default `--input`, say) get it without
    resolving twice.

    The loader validates prompt/analytics/traffic templates as it goes, so a
    template that can't render fails here rather than mid-run."""
    workspace = open_workspace(tenant, userdata)
    try:
        return workspace, workspace.load_config()
    except Exception as exc:  # noqa: BLE001 - any load failure is a user-facing config error
        raise fail(f"could not load {workspace.config_path}: {exc}") from exc


def load_config(tenant: str, userdata: str = None):
    """open_tenant() for the commands that only need the config."""
    return open_tenant(tenant, userdata)[1]


def load_input(input_path: str, workspace: TenantWorkspace = None) -> dict:
    """Read a run's input JSON.

    A run's inputs live with the tenant they belong to, so `--input` resolves
    inside the tenant's folder: `--input input.comment.json` means that file next
    to the tenant's config, whatever directory you're standing in. Omitted, it
    defaults to `input.json` there. An absolute path is used as-is, which is the
    escape hatch for an input generated somewhere else entirely.
    """
    if not input_path:
        if workspace is None:
            raise fail("no input file given")
        path = workspace.default_input_path
        if not path.is_file():
            raise fail(f"no --input given and no {path.name} in {workspace.dir}")
    else:
        candidate = Path(input_path).expanduser()
        if not candidate.is_absolute() and workspace is not None:
            candidate = workspace.dir / candidate
        path = resolve_existing(str(candidate), "Input")
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
