import json
import subprocess
import sys
from pathlib import Path


class MCPResearchSource:
    """A custom OpportunitySource that gets its opportunities from an MCP server.

    It launches the server as a subprocess and speaks JSON-RPC 2.0 over stdio —
    `initialize`, then `tools/call` — which is exactly what an MCP client does. The
    agent doesn't know or care that an MCP server is behind this; it only sees the
    OpportunitySource interface (`discover(context) -> list[dict]`).

    This hand-rolled client keeps the example dependency-free and runnable on any
    Python. In production you'd use the official `mcp` SDK instead (Python 3.10+),
    which is async — see docs/extending.md for that version.
    """

    def __init__(self, config):
        self._config = config
        # A real deployment would read the server command from an env var or its own
        # config file. Here we launch the bundled stub server next to this example,
        # resolved absolutely so it works from any working directory.
        server = Path(__file__).resolve().parent.parent / "server" / "mock_mcp_server.py"
        self._server_cmd = [sys.executable, str(server)]

    def discover(self, context: dict) -> list[dict]:
        query = context.get("seed_keyword") or self._config.brand_description
        payload = self._call_tool("search_opportunities", {"query": query})
        results = json.loads(payload).get("results", [])
        return [
            {
                "source": "research_mcp",
                "topic": r["topic"],
                "signal_strength": r.get("signal_strength", 0.5),
                "intent": r.get("intent", "informational"),
                "suggested_channel_hint": r.get("channel"),
                "raw": r,
                "reason": r.get("reason", "surfaced via the research MCP server"),
            }
            for r in results
        ]

    # -- a minimal MCP stdio client -------------------------------------------

    def _call_tool(self, name: str, arguments: dict) -> str:
        proc = subprocess.Popen(
            self._server_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,
        )
        try:
            self._request(proc, 1, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "scribe-agent", "version": "0.1.0"},
            })
            self._notify(proc, "notifications/initialized", {})
            result = self._request(proc, 2, "tools/call", {"name": name, "arguments": arguments})
            return result["content"][0]["text"]
        finally:
            proc.stdin.close()
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()

    def _request(self, proc, mid: int, method: str, params: dict) -> dict:
        self._send(proc, {"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server closed before responding to {method!r}")
            msg = json.loads(line)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"MCP error on {method!r}: {msg['error']}")
                return msg["result"]

    def _notify(self, proc, method: str, params: dict) -> None:
        self._send(proc, {"jsonrpc": "2.0", "method": method, "params": params})

    @staticmethod
    def _send(proc, msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
