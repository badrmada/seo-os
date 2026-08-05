"""Resolving a tenant's file paths against *its own* config, not the process's
working directory.

Every path a tenant writes in its config — a templated provider's
`report_path`, `gsc_options.key_file`, an output sink's `options.path` — used to
be interpreted relative to wherever the command happened to be run from. That is
survivable for one CLI user who `cd`s into the right folder, and wrong the moment
the agent is called from a server, a queue worker, or anything with one working
directory shared by every tenant: two tenants that both say `data/analytics.json`
would read the same file.

So a config loaded from a file carries `config_base_dir` (that file's own
directory), and every relative path resolves against it. A config built in code
with no base directory keeps the old behavior, so nothing that constructs an
`AgentConfig()` directly changes.
"""

from pathlib import Path


def resolve_path(config, path: str) -> str:
    """Turn a tenant-supplied path into one that means the same thing from any
    working directory.

    - Empty stays empty (an unset optional path).
    - Absolute paths pass through untouched.
    - `~` is expanded.
    - Relative paths resolve against `config.config_base_dir` when the config
      came from a file; against the process CWD (the old behavior) when it
      didn't.
    """
    if not path:
        return path
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    base = getattr(config, "config_base_dir", "")
    if not base:
        return str(candidate)
    return str(Path(base) / candidate)
