#!/usr/bin/env python3
"""A tiny stand-in MCP server, speaking JSON-RPC 2.0 over stdio.

It implements just enough of the Model Context Protocol to be driven by an MCP
client: `initialize`, `tools/list`, and `tools/call` for two tools.

The two tools exist to be *different*, because the example uses each one a
different way:

  - `search_opportunities` already answers in this agent's vocabulary (`topic`,
    `signal_strength`, ...). The custom class in plugins/ calls it.
  - `trending_topics` answers in its own (`title`, `score`, `kind`, `why`) — the
    normal situation with a server somebody else wrote. The built-in
    `provider: "mcp"` calls it and maps it with a Jinja2 `items_template`.

Messages are newline-delimited JSON on stdin/stdout, which is exactly MCP's stdio
transport.

It is deliberately dependency-free and offline (it serves canned data from the
JSON files next to it) so the example runs on any Python. A real MCP server would
be a full implementation — usually built with the official MCP SDK — and would
actually use the query.
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
    },
    {
        "name": "trending_topics",
        "description": "What is rising right now, in this server's own shape.",
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["q"],
        },
    },
]


def _load(filename: str):
    return json.loads((Path(__file__).parent / filename).read_text(encoding="utf-8"))


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
        name = params.get("name")
        args = params.get("arguments") or {}
        # A real server would search using the query; the stub returns canned data.
        if name == "search_opportunities":
            payload = {"query": args.get("query", ""), "results": _load("opportunities.json")}
        elif name == "trending_topics":
            payload = {"q": args.get("q", ""), "hits": _load("trending.json")[: args.get("limit", 10)]}
        else:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"unknown tool {name!r}"}}
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
