from __future__ import annotations

import requests

# provider="webhook" (agent/config/agent_config.py's output_sinks) — hand the
# finished run to an HTTP endpoint: a control plane, a queue, a Slack/Zapier hook,
# a CMS draft endpoint. This is the seam between "the agent produced something" and
# "something happens with it", without this repo growing an integration per
# destination.


class WebhookOutputSink:
    """OutputSink (tools/base.py) POSTing the run result as JSON.

    options:
      - "url" (required): where to send it.
      - "method": default "POST".
      - "headers": sent as-is — this is where an Authorization header or an API
        key belongs. Held on the sink's own options rather than on the generic
        AgentConfig, so the credential lives with the thing that uses it (and is
        redacted by name if verbose mode ever prints it — see
        agent/observability/redaction.py).
      - "timeout_seconds": default 10. Always set, never unbounded: a hung
        endpoint must not hang a finished run.

    A non-2xx response raises, which OutputManager catches, reports, and moves
    past — the run is already complete and successful by the time any sink runs,
    so a failed delivery is never allowed to turn it into a failure. There is no
    retry here on purpose: retrying delivery reliably is a queue's job, and
    pretending to do it with a sleep loop inside a CLI process would be worse than
    not doing it at all.
    """

    def __init__(self, config, options: dict = None) -> None:
        options = options or {}
        self._url = options.get("url", "")
        if not self._url:
            raise ValueError('output sink provider="webhook" requires options.url')
        self._method = options.get("method", "POST")
        self._headers = options.get("headers", {})
        self._timeout = float(options.get("timeout_seconds", 10.0))

    def emit(self, output: dict) -> None:
        response = requests.request(
            self._method,
            self._url,
            json=output,
            headers=self._headers,
            timeout=self._timeout,
        )
        response.raise_for_status()

    def describe(self) -> str:
        """Human-readable destination, for the CLI's list-tools/check-data output.
        The URL only — never the headers, which hold credentials."""
        return f"{self._method} {self._url}"
