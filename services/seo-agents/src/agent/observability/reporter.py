"""The one place verbose output is formatted and written — every event in the run
goes through a RunReporter, so there are no scattered print() calls to find and no
second opinion about where output lands.

Three rules this module exists to enforce:

  1. **stderr, never stdout.** stdout carries the run's result JSON (src/main.py
     prints it), so `python src/main.py -v | jq` has to keep working. A reporter
     that wrote to stdout would corrupt the one contract callers depend on.
  2. **Verbose never changes behavior.** event() swallows anything it raises —
     a bug in formatting must not fail a run that otherwise succeeded. This
     mirrors the pipeline's degrade-don't-abort principle.
  3. **Off costs nothing.** NullReporter is the default and every method is a
     no-op, so non-verbose runs pay nothing and no call site needs an
     `if verbose:` guard. Call sites that would build an *expensive* payload
     check `reporter.level` first instead (see observed.py).

Levels: 0 silent (today's exact behavior), 1 lifecycle (stages, tool calls,
timings, errors), 2 adds truncated payload previews.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import contextmanager

from .redaction import redact

# Event kinds, as documented in PLAN.md's reporter contract. Stable strings: the
# --verbose-format=json stream is meant to be consumable by a UI or log pipeline,
# so renaming one of these is a breaking change to that consumer.
RUN_START = "run_start"
RUN_END = "run_end"
STAGE_START = "stage_start"
STAGE_END = "stage_end"
STAGE_ERROR = "stage_error"
TOOL_START = "tool_start"
TOOL_END = "tool_end"
TOOL_ERROR = "tool_error"

# Indent depth per event kind, for the human-readable format — run at the margin,
# stages one in, tool calls two in, so the shape of a run is visible at a glance.
_DEPTH = {
    RUN_START: 0, RUN_END: 0,
    STAGE_START: 1, STAGE_END: 1, STAGE_ERROR: 1,
    TOOL_START: 2, TOOL_END: 2, TOOL_ERROR: 2,
}

# ASCII on purpose: this stream shows up in terminals, CI logs, and pasted bug
# reports, and a mojibake arrow helps nobody.
_MARKER = {
    RUN_START: ">", STAGE_START: ">", TOOL_START: ">",
    RUN_END: "<", STAGE_END: "<", TOOL_END: "<",
    STAGE_ERROR: "!", TOOL_ERROR: "!",
}


class NullReporter:
    """The default. Every call a no-op, so a non-verbose run pays nothing and no
    call site needs a guard. Implements the same surface as StreamReporter —
    including timed() — so a call site never has to check which one it holds."""

    level = 0

    def event(self, kind: str, **fields) -> None:
        pass

    @contextmanager
    def timed(self, start_kind: str, end_kind: str, error_kind: str, **fields):
        yield {}


class StreamReporter:
    """Writes events to a stream (stderr). Subclasses decide the formatting; this
    class owns the parts that must not vary: the run-relative clock, thread-safe
    writes, and the guarantee that a reporting failure never escapes.

    The lock matters — with 2+ discovery sources the pipeline fans out via
    LangGraph's Send and stage/tool events from concurrent branches are emitted
    from different threads. Without it, lines interleave mid-write and the stream
    becomes unreadable exactly when it's most needed.
    """

    def __init__(self, stream=None, level: int = 1) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self.level = level
        self._lock = threading.Lock()
        self._t0 = time.perf_counter()

    def event(self, kind: str, **fields) -> None:
        if self.level < 1:
            return
        try:
            line = self._format(kind, redact(fields), time.perf_counter() - self._t0)
            if line is None:
                return
            with self._lock:
                self._stream.write(line + "\n")
                self._stream.flush()
        except Exception:  # noqa: BLE001 - see module docstring rule 2
            # A reporter that breaks a run is worse than a run with no reporting.
            pass

    def _format(self, kind: str, fields: dict, elapsed: float) -> str:
        raise NotImplementedError

    @contextmanager
    def timed(self, start_kind: str, end_kind: str, error_kind: str, **fields):
        """Emit a start event, run the block, then emit an end event carrying how
        long it took — or an error event if it raised, re-raising unchanged so the
        pipeline's own degrade-don't-abort handling still sees the exception.

        Yields a dict the caller mutates to attach result details that only exist
        after the call (token counts, row counts, whether grounding actually
        happened). Those land on the end event.
        """
        self.event(start_kind, **fields)
        started = time.perf_counter()
        extra: dict = {}
        try:
            yield extra
        except Exception as exc:  # noqa: BLE001 - reported, then re-raised untouched
            self.event(
                error_kind,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
                **fields,
            )
            raise
        self.event(
            end_kind,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            **fields,
            **extra,
        )


class TextReporter(StreamReporter):
    """Human-readable default: one line per event, indented by scope.

        [ 0.00s] > run 5f3c1a2b  channel=auto
        [ 0.00s]   > discover
        [ 0.01s]     > llm.generate  grounded=True
        [ 2.31s]     < llm.generate  2306ms  tokens=812 sources=3
        [ 2.31s]   < discover  2312ms  opportunities=5
    """

    def _format(self, kind: str, fields: dict, elapsed: float) -> str:
        fields = dict(fields)
        label = (
            fields.pop("stage", None)
            or fields.pop("tool", None)
            or fields.pop("run_id", None)
            or kind
        )
        method = fields.pop("method", None)
        if method:
            label = f"{label}.{method}"
        if kind in (RUN_START, RUN_END):
            label = f"run {label}"
        branch = fields.pop("branch", None)
        if branch:
            label = f"{label} [{branch}]"

        parts = [label]
        elapsed_ms = fields.pop("elapsed_ms", None)
        if elapsed_ms is not None:
            parts.append(f"{elapsed_ms}ms")
        detail = " ".join(f"{key}={_render(value)}" for key, value in fields.items() if value != "")
        if detail:
            parts.append(detail)

        indent = "  " * _DEPTH.get(kind, 1)
        return f"[{elapsed:6.2f}s] {indent}{_MARKER.get(kind, '-')} {'  '.join(parts)}"


class JsonReporter(StreamReporter):
    """Newline-delimited JSON, one object per event — the same stream a UI or log
    pipeline would consume, and the shape a future worker/control-plane wants.
    Machine-readable, so nothing is dropped or reformatted for brevity beyond the
    redaction/truncation every reporter applies."""

    def _format(self, kind: str, fields: dict, elapsed: float) -> str:
        payload = {"event": kind, "t": round(elapsed, 4), **fields}
        return json.dumps(payload, default=str)


def _render(value) -> str:
    if isinstance(value, str) and (" " in value or not value):
        return json.dumps(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def build_reporter(level: int = 0, fmt: str = "text", stream=None):
    """The single constructor for a reporter — src/main.py's CLI flags and
    AgentConfig's defaults both come through here, so "how do I get a reporter"
    has exactly one answer. level=0 returns NullReporter, which is why a
    non-verbose run has no reporting overhead at all rather than a disabled one."""
    if not level:
        return NullReporter()
    if fmt == "json":
        return JsonReporter(stream=stream, level=level)
    if fmt == "text":
        return TextReporter(stream=stream, level=level)
    raise ValueError(f'Unknown verbose format {fmt!r}; must be "text" or "json"')
