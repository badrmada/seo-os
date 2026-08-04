#!/usr/bin/env python3
"""A tiny stand-in MCP server, speaking JSON-RPC 2.0 over stdio.

It implements just enough of the Model Context Protocol to be driven by an MCP
client: `initialize`, `tools/list`, and `tools/call` for one tool,
`search_opportunities`. Messages are newline-delimited JSON on stdin/stdout, which
is exactly MCP's stdio transport.

It is deliberately dependency-free and offline (it serves canned data from
opportunities.json next to it) so the example runs on any Python. A real MCP
server would be a full implementation — usually built with the official MCP SDK —
and would actually use the query.
"""
import json
import sys
from pathlib import Path

TOOLS = [
    {
        "name": "search_opportunities",
        "description": "Find content opportunities relevant to a query.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
]


def _load_opportunities() -> list:
    return json.loads((Path(__file__).parent / "opportunities.json").read_text(encoding="utf-8"))


def _handle(msg: dict):
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-research-mcp", "version": "0.1.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params", {})
        if params.get("name") != "search_opportunities":
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"unknown tool {params.get('name')!r}"}}
        query = (params.get("arguments") or {}).get("query", "")
        # A real server would search using `query`; the stub returns canned data.
        payload = {"query": query, "results": _load_opportunities()}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "isError": False,
        }}

    if mid is None:
        return None  # a notification (e.g. notifications/initialized) — no response
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method!r}"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = _handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
