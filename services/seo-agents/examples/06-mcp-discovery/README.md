# 06 — Scribe (discovery from an MCP server)

**The story.** Scribe is an AI writing assistant for teams. Its research lives
behind an **MCP server** — a tool server that speaks the
[Model Context Protocol](https://modelcontextprotocol.io/). Scribe wants the
agent to pull content opportunities from that server instead of a built-in
source.

**What this example shows** — the two ways to do that, side by side:

- **`provider: "mcp"` — no code at all.** The built-in source connects, calls a
  tool, and maps the answer with a Jinja2 template. This is what you should reach
  for first.
- **`provider: "custom"` — a class you write.** For when one tool call and one
  mapping aren't enough. The class here is also an **MCP client**, hand-rolled so
  you can see the protocol underneath.
- **It runs offline, with no external server** — a tiny stub MCP server is
  included, serving two tools that deliberately answer in *different shapes*.

## The files

```
server/mock_mcp_server.py   # a tiny stub MCP server (JSON-RPC 2.0 over stdio)
server/opportunities.json   # canned data for its search_opportunities tool
server/trending.json        # canned data for its trending_topics tool
plugins/mcp_discovery.py    # MCPResearchSource — a custom OpportunitySource = MCP client
tenant.json                 # wires up both sources
input.json                  # no channel — the agent decides from what the server returns
```

## The built-in source (start here)

The stub's `trending_topics` tool answers in **its own vocabulary** — `title`,
`score`, `kind`, `why` — which is the normal situation with a server somebody
else wrote. No class is needed; `items_template` maps it:

```jsonc
{
  "name": "trending",
  "provider": "mcp",
  "options": {
    "command": "python3",
    "cwd": ".",
    "args": ["server/mock_mcp_server.py"],
    "tool_name": "trending_topics",
    "arguments": { "q": "{{ seed_keyword }}", "limit": 5 },
    "items_template": "[{% for hit in data.hits %}{\"topic\": {{ hit.title | tojson }}, \"signal_strength\": {{ hit.score / 100 }}, \"suggested_channel_hint\": {{ hit.kind | tojson }}, \"reason\": {{ hit.why | tojson }}}{% if not loop.last %},{% endif %}{% endfor %}]"
  }
}
```

Everything that used to be your problem — launching the server, `initialize`,
`tools/call`, the async bridge, timeouts — is the provider's. What stays yours is
the only part that was ever specific to your server: which tool, which arguments,
and what its answer means. Full option list in
[configuration.md](../../docs/configuration.md#discovery-from-an-mcp-server).

A **real** server replaces `command`/`args` with something like
`"command": "npx", "args": ["-y", "@acme/research-mcp"]`, or drops them entirely
for `"transport": "http"` and a `"url"`. (`python3` here is only because the stub
happens to be a Python script; you'd rarely launch an MCP server that way.)

## The custom source (when one call isn't enough)

`MCPResearchSource` in `plugins/` calls the stub's other tool,
`search_opportunities`, which already answers in this agent's vocabulary. As a
class it could do things the built-in cannot: several tool calls, picking the
tool from `list_tools()`, or real work in between.

```jsonc
{ "name": "research_mcp", "provider": "custom", "class": "mcp_discovery:MCPResearchSource" }
```

It is also deliberately **synchronous** — a plain `def discover` that bridges to
its own I/O inside. That still works: a sync source runs in a worker thread with
no event loop of its own. A real class should use the official
[`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk) with
`async def discover` and no bridge at all — that version is in
[docs/extending.md](../../docs/extending.md#using-an-mcp-server-as-a-tool). The
SDK is already installed; the built-in provider uses it.

## Run it

```bash
python src/main.py run --userdata examples --tenant 06-mcp-discovery
```

Or `make example EXAMPLE=06-mcp-discovery`, or in Docker with nothing installed —
the same run three ways is in [Running an example](../README.md#running-an-example).

`input.json` has **no channel**, so the agent runs both MCP-backed sources (in
parallel, as it does whenever 2+ sources are configured) and picks the channel
from what they return. Real output (trimmed):

```json
"discovery": {
  "opportunities": [
    { "source": "research_mcp", "topic": "how to write better meeting notes", "suggested_channel_hint": "site_article", "signal_strength": 0.8 },
    { "source": "research_mcp", "topic": "best ai writing tools 2026", "suggested_channel_hint": "external_article", "signal_strength": 0.55 },
    { "source": "research_mcp", "topic": "does ai writing help or hurt clarity", "suggested_channel_hint": "engagement_comment", "signal_strength": 0.5 },
    { "source": "trending", "topic": "meeting notes templates that people actually reuse", "suggested_channel_hint": "site_article", "signal_strength": 0.72 },
    { "source": "trending", "topic": "why teams abandon their note-taking tool after a month", "suggested_channel_hint": "engagement_comment", "signal_strength": 0.61 }
  ],
  "channel_decision": {
    "chosen": "site_article",
    "reason": "Highest-scoring channel hint across 5 discovered opportunities: {'site_article': 1.52, 'engagement_comment': 1.11, 'external_article': 0.55}.",
    "fallback": false
  },
  "tool_errors": []
}
```

Both sources' hints were pooled and scored together, and `site_article` won.

Each opportunity from the built-in source also records where it came from:

```json
"raw": {
  "topic": "meeting notes templates that people actually reuse",
  "signal_strength": 0.72,
  "mcp_tool": "trending_topics",
  "mcp_server": "python3 server/mock_mcp_server.py"
}
```

## About the stub server

`server/mock_mcp_server.py` is a real (if tiny) MCP server: it answers
`initialize`, `tools/list`, and `tools/call` as newline-delimited JSON-RPC on
stdio, which is MCP's stdio transport. It serves canned data so the example runs
anywhere. You can drive it by hand to see the protocol:

```bash
printf '%s\n%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"trending_topics","arguments":{"q":"meeting notes"}}}' \
 | python3 examples/06-mcp-discovery/server/mock_mcp_server.py
```

Note it implements `tools/list` as well as the two tools. That isn't decoration:
the MCP SDK validates a tool's result against the schema the server declares, so
it lists tools before returning one. A server that skips `tools/list` fails
against a real client — which is the sort of thing you find out by using the SDK
rather than hand-rolling a client.
