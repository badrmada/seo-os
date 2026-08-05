"""Turns AgentConfig.output_sinks into concrete OutputSink instances and drives
them once a run has finished — the counterpart to ToolsManager, for the
run-context plane rather than the tools plane.

Deliberately *not* part of Tools and *not* a graph stage: a sink runs after the
graph, at the AgentRunner/CLI boundary, so no stage can see one and
AgentRunner.run()'s return shape (docs/output-schema.md) is untouched by any of
this.
"""

import asyncio
import sys

from tools.sinks.json_sink import JsonOutputSink
from tools.sinks.webhook_sink import WebhookOutputSink

from ..observability import NullReporter
from ..utils.async_utils import call as acall
from .plugin_loader import load_custom

# Sentinel default meaning "the process's own stderr, looked up when it's
# actually written to". A sentinel rather than `sys.stderr` itself because the
# default must stay late-bound (pytest and anything else that swaps the stream
# after import would otherwise be ignored) while still leaving `None` free to
# mean "don't write to a file descriptor at all" — the thing a server needs.
PROCESS_STDERR = object()

# provider name -> (config, options, stdout) -> an object with .emit(output).
# Adding a built-in sink means adding one entry here and one class under
# tools/sinks/. `stdout` is threaded through because a sink that writes to the
# process's stdout must be able to be pointed elsewhere by a host that owns that
# fd (see OutputManager); sinks that don't write there ignore it.
_SINK_FACTORIES = {
    "json": lambda config, options, stdout: JsonOutputSink(config, options, stdout=stdout),
    "webhook": lambda config, options, stdout: WebhookOutputSink(config, options),
}


class OutputManager:
    """Builds every configured sink up front, then emits to them in order.

    Building happens in __init__, before the run, on purpose: a misconfigured sink
    (a webhook with no url, a custom class that won't import) should fail
    immediately rather than after a full pipeline has burned real LLM calls. The
    emit side is the opposite — by then the result exists, so nothing is allowed to
    be fatal.

    `stdout` and `warn_stream` are the two places this class would otherwise write
    to the process's own file descriptors unconditionally. A CLI wants exactly
    that and gets it by default; a server passes its own streams (or None for the
    warning, which then lives only in the reporter's events) rather than having a
    library print into a response it doesn't control.
    """

    def __init__(self, config, reporter=None, *, stdout=None, warn_stream=PROCESS_STDERR) -> None:
        self.config = config
        self.reporter = reporter or NullReporter()
        self._stdout = stdout
        self._warn_stream = warn_stream
        self.sinks = self._build_sinks()

    def _build_sinks(self) -> list:
        sinks = []
        for entry in self.config.output_sinks:
            name = entry.get("name") or entry.get("provider", "output")
            provider = entry.get("provider", "json")
            options = entry.get("options", {})
            if provider == "custom":
                sinks.append((name, load_custom(
                    entry.get("class", ""), f"output_sinks[{name!r}].class", self.config, options,
                )))
                continue
            try:
                factory = _SINK_FACTORIES[provider]
            except KeyError:
                raise ValueError(
                    f"Unknown output sink provider {provider!r} for {name!r}; must be "
                    f'{", ".join(sorted(repr(k) for k in _SINK_FACTORIES))}, or "custom"'
                ) from None
            sinks.append((name, factory(self.config, options, self._stdout)))
        return sinks

    def emit(self, output: dict) -> list:
        """Sync wrapper around aemit(), for callers with no event loop (the CLI).
        Same nesting caveat as AgentRunner.run(): async callers use aemit()."""
        return asyncio.run(self.aemit(output))

    async def aemit(self, output: dict) -> list:
        """Hand the finished result to every sink, in configured order. Returns the
        names of the sinks that failed (empty on full success).

        Each sink is invoked through async_utils.call, so a sink may be `def` or
        `async def` — WebhookOutputSink is async (httpx), JsonOutputSink is a plain
        file/stdout write, and a tenant's existing sync custom sink is unchanged.

        A sink raising is never fatal — the run is already complete, and losing one
        delivery is no reason to discard a finished result or to skip the sinks
        after it. Failures are reported twice over on purpose: as a structured
        reporter event for the verbose/JSON event stream, and — only when verbose
        is off, so it isn't duplicated — as a plain stderr warning, because a
        silently dropped webhook is far worse than a noisy one.
        """
        failed = []
        for name, sink in self.sinks:
            try:
                with self.reporter.timed("tool_start", "tool_end", "tool_error", tool=name, method="emit"):
                    await acall(sink.emit, output)
            except Exception as exc:  # noqa: BLE001 - a sink must never fail a finished run
                failed.append(name)
                warn_to = sys.stderr if self._warn_stream is PROCESS_STDERR else self._warn_stream
                if self.reporter.level < 1 and warn_to is not None:
                    print(f"warning: output sink {name!r} failed: {exc}", file=warn_to)
        return failed

    def describe(self) -> list[dict]:
        """What's configured, for the CLI's list-tools / check-data commands."""
        return [
            {
                "name": name,
                "type": type(sink).__name__,
                "destination": sink.describe() if hasattr(sink, "describe") else "",
            }
            for name, sink in self.sinks
        ]
