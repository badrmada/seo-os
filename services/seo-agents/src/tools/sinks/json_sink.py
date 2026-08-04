from __future__ import annotations

import json
from pathlib import Path

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

    stdout is written through print() rather than a captured stream so it stays on
    the same file descriptor the CLI has always used. Note that verbose mode
    (agent/observability/) writes to *stderr* precisely so that this sink's stdout
    output stays clean and pipeable to jq.
    """

    def __init__(self, config, options: dict = None) -> None:
        options = options or {}
        self._path = options.get("path", "")
        self._indent = options.get("indent", 2)
        self._append = bool(options.get("append", False))

    def emit(self, output: dict) -> None:
        payload = json.dumps(output, indent=None if self._append else self._indent, default=str)
        if not self._path:
            print(payload)
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
