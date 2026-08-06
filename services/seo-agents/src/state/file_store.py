"""One JSON file per run, in a folder — the first durable store, and the one that
needs no infrastructure at all.

Why a file per run rather than an append-only JSONL log, which is the other
obvious shape: the interface is `save`/`load`/`delete` **keyed by run_id**, and a
log answers none of those three without scanning itself. The thing a log is
genuinely good at — an accumulating archive of finished runs — already exists as
the `json` output sink with `"append": true` (docs/configuration.md), so building
a second, worse one here would only split the answer to "where do my runs go?"
across two features.
"""

import json
import os
import re
from pathlib import Path

# A run_id becomes a filename, and a run_id comes from the *caller*
# (AgentInput.run_id, or a uuid4 when it's omitted). So it is checked rather than
# trusted: no separators, no leading dot, nothing that could climb out of the
# folder or land on a dotfile. The bound is there for the same reason — a 4KB
# "run id" is not a run id.
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class FileStateStore:
    """Snapshots as `<directory>/<run_id>.json`, replaced in place as a run
    progresses.

    Writes are atomic: a temporary file in the same folder, then `os.replace`. A
    snapshot is overwritten several times per run, and a reader watching the run
    happen must never catch a half-written file — which is exactly what a plain
    `open(...).write()` hands them, and only under load, which is the worst way to
    find out.
    """

    def __init__(self, directory: str) -> None:
        # Resolved and created up front, so a path that can't be written fails
        # while the store is being built rather than mid-run — the same line
        # OutputManager draws for a sink (build early, degrade late).
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, run_id: str, state: dict) -> None:
        path = self._path_for(run_id)
        payload = json.dumps(state, ensure_ascii=False)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def load(self, run_id: str) -> dict | None:
        path = self._path_for(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, run_id: str) -> None:
        self._path_for(run_id).unlink(missing_ok=True)

    def describe(self) -> str:
        return str(self.directory)

    def _path_for(self, run_id: str) -> Path:
        if not _SAFE_RUN_ID.fullmatch(run_id or ""):
            raise ValueError(
                f"run_id {run_id!r} can't be a filename: a file-backed state store "
                "accepts letters, digits, '.', '_' and '-' only (a run with no "
                "run_id of its own gets a uuid4, which always qualifies)"
            )
        path = (self.directory / f"{run_id}.json").resolve()
        # Checked after resolving, not on the string: `..` and separators are
        # already gone above, but a symlink pointing out of the folder only shows
        # up once the path is real (the same treatment template files get — see
        # agent/config/template_files.py).
        if path.parent != self.directory:
            raise ValueError(f"run_id {run_id!r} resolves outside {self.directory}")
        return path
