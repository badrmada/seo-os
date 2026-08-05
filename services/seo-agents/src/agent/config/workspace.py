"""A tenant is a folder. That's the whole idea.

    userdata/                     the workspace root
    ├── acme/                     the tenant name — the only thing a run needs
    │   ├── tenant.json           config
    │   ├── plugins/              your own classes
    │   ├── templates/            your own templates (reserved; not read yet)
    │   ├── data/                 analytics.json, traffic.json, credentials
    │   └── output/               where results land by default
    └── globex/…

    python src/main.py run --tenant acme

Everything a tenant owns lives under its own directory, so "which tenant" is one
name rather than a set of paths, and two tenants can never read each other's
files. This replaces three different mechanisms that previously existed for
finding a tenant's custom code — a file dropped under `src/`, an installed
package, and an undocumented `PYTHONPATH=code` that the examples actually relied
on — with one predefined folder.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Where the workspace root comes from, in order. One concept with a normal
# override chain — a container mounts a volume and sets the environment variable.
ROOT_ENV_VAR = "SEO_AGENT_USERDATA"
DEFAULT_ROOT = "userdata"

CONFIG_FILENAME = "tenant.json"
INPUT_FILENAME = "input.json"
PLUGINS_DIRNAME = "plugins"
TEMPLATES_DIRNAME = "templates"
DATA_DIRNAME = "data"
OUTPUT_DIRNAME = "output"

# A tenant name becomes a path segment, and in a server it arrives from a request.
# So it is validated, not sanitized: "../../etc" must be rejected outright rather
# than quietly rewritten into something that looks fine and points elsewhere.
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class UnknownTenantError(Exception):
    """Raised when a tenant name doesn't correspond to a workspace directory."""


def resolve_root(explicit: str = None) -> Path:
    """--userdata → $SEO_AGENT_USERDATA → the nearest `userdata/` at or above the
    current directory.

    The upward search is what stops the workspace root from being the one thing
    still tied to where you're standing. Everything *inside* a tenant already
    resolves against the tenant's folder; without this, `list-tenants` would find
    your tenants from the project root and silently find nothing one directory
    down. Same idea as git locating `.git`.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    from_env = os.environ.get(ROOT_ENV_VAR)
    if from_env:
        return Path(from_env).expanduser().resolve()
    return _find_upwards(Path.cwd()) or (Path.cwd() / DEFAULT_ROOT).resolve()


def _find_upwards(start: Path) -> Path | None:
    """The nearest `userdata/` directory at or above `start`, if any."""
    for directory in (start, *start.parents):
        candidate = directory / DEFAULT_ROOT
        if candidate.is_dir():
            return candidate.resolve()
    return None


def validate_name(name: str) -> str:
    """Reject anything that isn't a plain directory name. This is the boundary
    that stops a tenant name from escaping the workspace — `..`, an absolute
    path, or a nested path would all otherwise resolve outside it."""
    if not name or not _VALID_NAME.match(name) or ".." in name:
        raise ValueError(
            f"Invalid tenant name {name!r}: must be a single directory name using "
            "letters, digits, dot, dash, or underscore, and start with a letter or digit"
        )
    return name


@dataclass(frozen=True)
class TenantWorkspace:
    """One tenant's directory, and everything derived from it."""

    root: Path
    name: str

    @classmethod
    def open(cls, name: str, root: str = None) -> "TenantWorkspace":
        """Resolve a tenant by name, failing with a message that lists what *is*
        there — a mistyped tenant name is the most likely first-run error, and
        "no such tenant" alone doesn't help anyone."""
        workspace = cls(root=resolve_root(root), name=validate_name(name))
        if not workspace.dir.is_dir():
            available = ", ".join(list_tenants(workspace.root)) or "none"
            raise UnknownTenantError(
                f"No tenant {name!r} in {workspace.root} (available: {available})"
            )
        if not workspace.config_path.is_file():
            raise UnknownTenantError(
                f"Tenant {name!r} has no {CONFIG_FILENAME} ({workspace.config_path})"
            )
        return workspace

    @property
    def dir(self) -> Path:
        return self.root / self.name

    @property
    def config_path(self) -> Path:
        return self.dir / CONFIG_FILENAME

    @property
    def default_input_path(self) -> Path:
        return self.dir / INPUT_FILENAME

    @property
    def plugins_dir(self) -> Path:
        return self.dir / PLUGINS_DIRNAME

    @property
    def templates_dir(self) -> Path:
        return self.dir / TEMPLATES_DIRNAME

    @property
    def data_dir(self) -> Path:
        return self.dir / DATA_DIRNAME

    @property
    def output_dir(self) -> Path:
        return self.dir / OUTPUT_DIRNAME

    def load_config(self, *, validate: bool = True):
        """Load this tenant's config. `config_base_dir` becomes the tenant
        directory, so every relative path in it — and every path a plugin of its
        own resolves — is anchored here (see agent/config/paths.py)."""
        from .loader import AgentConfigLoader

        return AgentConfigLoader().load(str(self.config_path), validate=validate)

    def describe(self) -> dict:
        """What exists in this workspace, for the CLI's list-tenants/check-data."""
        return {
            "name": self.name,
            "path": str(self.dir),
            "plugins": len(list(self.plugins_dir.glob("*.py"))) if self.plugins_dir.is_dir() else 0,
            "templates": len(list(self.templates_dir.iterdir())) if self.templates_dir.is_dir() else 0,
            "has_input": self.default_input_path.is_file(),
        }


def list_tenants(root: Path) -> list[str]:
    """Every directory under the root holding a tenant.json, sorted."""
    if not root.is_dir():
        return []
    return sorted(
        entry.name for entry in root.iterdir()
        if entry.is_dir() and (entry / CONFIG_FILENAME).is_file() and _VALID_NAME.match(entry.name)
    )


def plugins_dir_for(config) -> Path | None:
    """The plugins folder belonging to whatever config this is, or None when the
    config has no workspace (one built in code, e.g. in tests or an embedding
    application). Derived from config_base_dir rather than passed around, so no
    call site has to thread a workspace through to reach it."""
    base = getattr(config, "config_base_dir", "")
    if not base:
        return None
    candidate = Path(base) / PLUGINS_DIRNAME
    return candidate if candidate.is_dir() else None
