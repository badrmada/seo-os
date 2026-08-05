# 06 — Scribe (discovery from an MCP server)

**The story.** Scribe is an AI writing assistant for teams. Its research lives
behind an **MCP server** — a tool server that speaks the
[Model Context Protocol](https://modelcontextprotocol.io/). Scribe wants the
agent to pull content opportunities from that server instead of a built-in
source. Because a discovery source is just a `custom` class, the MCP server plugs
in with no changes to the agent.

**What this example shows:**

- **An MCP server used as a discovery source** — a `custom` `OpportunitySource`
  that is really an **MCP client**.
- **It runs offline, with no dependencies** — a tiny stub MCP server is included.

## The files

```
server/mock_mcp_server.py   # a tiny stub MCP server (JSON-RPC 2.0 over stdio)
server/opportunities.json   # the canned data it serves
code/mcp_discovery.py       # MCPResearchSource — a custom OpportunitySource = MCP client
tenant.json                 # points discovery at that class
input.json                  # no channel — the agent decides from what the server returns
```

## How it fits together

The agent only ever sees the `OpportunitySource` interface
(`discover(context) -> list[dict]`). Inside that method, `MCPResearchSource`:

1. launches the MCP server as a subprocess,
2. speaks JSON-RPC 2.0 over stdio — `initialize`, then `tools/call` for the
   server's `search_opportunities` tool,
3. maps the tool's result into opportunity records (with a channel hint each).

`tenant.json` wires it in like any other custom source:

```jsonc
{
  "discovery_sources": [
    { "name": "research_mcp", "provider": "custom", "class": "mcp_discovery:MCPResearchSource" }
  ]
}
```

## Run it

Your client code is in `code/`, so put it on the import path:

```bash
python src/main.py run --userdata examples --tenant 06-mcp-discovery
```

`input.json` has **no channel**, so the agent runs MCP-backed discovery and picks
the channel from what the server returns. Real output (trimmed):

```json
"discovery": {
  "opportunities": [
    { "source": "research_mcp", "topic": "how to write better meeting notes", "suggested_channel_hint": "site_article", "signal_strength": 0.8 },
    { "source": "research_mcp", "topic": "best ai writing tools 2026", "suggested_channel_hint": "external_article", "signal_strength": 0.55 },
    { "source": "research_mcp", "topic": "does ai writing help or hurt clarity", "suggested_channel_hint": "engagement_comment", "signal_strength": 0.5 }
  ],
  "channel_decision": {
    "chosen": "site_article",
    "reason": "Highest-scoring channel hint across 3 discovered opportunities: {'site_article': 0.8, 'external_article': 0.55, 'engagement_comment': 0.5}.",
    "fallback": false
  },
  "tool_errors": []
}
```

Those opportunities came from the MCP server; the agent scored their channel hints
and chose `site_article`.

## About the stub server

`server/mock_mcp_server.py` is a real (if tiny) MCP server: it answers
`initialize`, `tools/list`, and `tools/call` as newline-delimited JSON-RPC on
stdio, which is MCP's stdio transport. It serves canned data so the example runs
anywhere. You can drive it by hand to see the protocol:

```bash
printf '%s\n%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_opportunities","arguments":{"query":"meeting notes"}}}' \
 | python server/mock_mcp_server.py
```

## Go live

Point the client at a **real** MCP server (a research/search server, your
company's internal MCP, etc.) instead of the bundled stub — usually by reading its
launch command or URL from an env var. In production, use the official
[`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk) (Python
3.10+) rather than the hand-rolled client here; because that SDK is async, you
bridge it into the synchronous `discover()` method with `asyncio.run(...)`. That
version is shown in
[docs/extending.md](../../docs/extending.md#using-an-mcp-server-as-a-tool).
