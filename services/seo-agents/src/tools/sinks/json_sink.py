from __future__ import annotations

import json
import sys
from pathlib import Path

from agent.config.paths import resolve_path

# provider="json" (agent/config/agent_config.py's output_sinks) — the default sink,
# and deliberately the exact behavior this agent has always had: json.dumps(result,
# indent=2) to stdout. A zero-config tenant's output is byte-for-byte what it was
# before sinks existed.


class JsonOutputSink:
    """OutputSink (tools/base.py) writing the run result as indented JSON.

    options:
      - "path": file to write to. Empty (the default) means stdout.
      - "indent": json.dumps indent, default 2. null for a single compact line.
      - "append": with a path, append one JSON object per line (JSONL) instead of
        overwriting — for accumulating many runs into one file.

    With no path, the result goes to `stdout` — the stream handed in at
    construction, defaulting to the process's own `sys.stdout` so the CLI's output
    is byte-for-byte what it has always been. A server passes its own stream (a
    log, a buffer): "print to whatever fd this process happens to have" is not a
    decision a library gets to make for its host. Note that verbose mode
    (agent/observability/) writes to *stderr* precisely so that this sink's stdout
    output stays clean and pipeable to jq.
    """

    def __init__(self, config, options: dict = None, *, stdout=None) -> None:
        options = options or {}
        self._stdout = stdout
        # Resolved against the tenant's own config directory, not the process's
        # working directory — see agent/config/paths.py.
        self._path = resolve_path(config, options.get("path", ""))
        self._indent = options.get("indent", 2)
        self._append = bool(options.get("append", False))

    def emit(self, output: dict) -> None:
        payload = json.dumps(output, indent=None if self._append else self._indent, default=str)
        if not self._path:
            stream = self._stdout if self._stdout is not None else sys.stdout
            stream.write(payload + "\n")
            stream.flush()
            return
        path = Path(self._path).expanduser()
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if self._append else "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")

    def describe(self) -> str:
        """Human-readable destination, for the CLI's list-tools/check-data output."""
        if not self._path:
            return "stdout"
        return f"{self._path} ({'append' if self._append else 'overwrite'})"
