from __future__ import annotations

import asyncio
import json

from .templated_json import render_text

# provider="mcp" (agent/config/agent_config.py's discovery_sources) — a discovery
# source backed by a tool on an MCP server (https://modelcontextprotocol.io/).
#
# This has always been possible through provider="custom", and docs/extending.md
# still shows how — but every tenant who did it wrote the same three things: the
# transport setup, the connect/initialize/tools-call dance, and a loop turning the
# server's JSON into Opportunity dicts. Only the third is actually theirs. So the
# first two are built in here and the third stays configuration: `items_template`,
# the same Jinja2 mapping "templated" analytics and traffic already use.
#
# What is deliberately *not* built in: anything that assumes what the server's
# tool is called, what arguments it takes, or what shape it answers in. MCP
# standardizes the transport, not the vocabulary — so `tool_name`, `arguments` and
# `items_template` are all the tenant's, and this class only knows how to reach
# the server and how to hand what comes back to normalize_opportunity.

# The keys a pass-through payload may hide its list under, when no items_template
# is configured. Not an attempt at a general shape-guesser — just the three names
# servers actually use, so the common case needs no template at all.
_LIST_KEYS = ("results", "items", "opportunities")

DEFAULT_TIMEOUT_SECONDS = 60.0


class MCPOpportunitySource:
    """OpportunitySource (tools/base.py) backed by one tool call against an MCP
    server. `name` is the discovery_sources registry key this instance was built
    for (agent/managers/tools_manager.py) — it becomes Opportunity.source, so a
    tenant with two MCP servers configured can tell their opportunities apart.

    Transport is "stdio" (launch the server as a subprocess — what an `npx`-style
    server wants) or "http" (MCP's streamable HTTP, for a server somebody else
    hosts). One connection per discover() call: a discovery source runs once per
    run, and a pooled connection would outlive the run that owns it.
    """

    def __init__(
        self,
        name: str,
        config,
        *,
        transport: str = "stdio",
        command: str = "",
        args=(),
        env: dict | None = None,
        cwd: str = "",
        url: str = "",
        headers: dict | None = None,
        tool_name: str = "",
        arguments: dict | None = None,
        items_template: str = "",
        max_opportunities: int = 5,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.name = name
        self.config = config
        self.transport = transport
        self.command = command
        self.args = list(args or ())
        self.env = dict(env or {})
        self.cwd = cwd
        self.url = url
        self.headers = dict(headers or {})
        self.tool_name = tool_name
        self.arguments = dict(arguments or {})
        self.items_template = items_template
        self.max_opportunities = max_opportunities
        self.timeout_seconds = float(timeout_seconds)

        # Validated here rather than at the first call, so a typo in the config is
        # a startup error naming the option, not a run that reaches `discover` and
        # fails there. ToolsManager builds every source before the graph runs.
        if self.transport not in ("stdio", "http"):
            raise ValueError(
                f'discovery source {name!r}: options.transport must be "stdio" or "http", '
                f"got {self.transport!r}"
            )
        if not self.tool_name:
            raise ValueError(f"discovery source {name!r}: options.tool_name is required")
        if self.transport == "stdio" and not self.command:
            raise ValueError(
                f'discovery source {name!r}: options.command is required for transport="stdio"'
            )
        if self.transport == "http" and not self.url:
            raise ValueError(
                f'discovery source {name!r}: options.url is required for transport="http"'
            )

    async def discover(self, context: dict) -> list[dict]:
        """Async because the MCP SDK is — this is the case docs/extending.md calls
        "no bridge to write". The whole exchange is bounded by `timeout_seconds`:
        a discovery source is an outbound call to somebody else's process, and a
        server that accepts a connection and then never answers would otherwise
        hang the run forever. Timing out raises, which DiscoverStage records as a
        tool error and degrades past — one source contributing nothing.
        """
        try:
            payload = await asyncio.wait_for(self._call_tool(context), self.timeout_seconds)
        except BaseExceptionGroup as group:
            raise _only_cause(group) from None
        items = self._items(payload, context)[: self.max_opportunities]

        # Which server and tool each item came from. `raw` is free-form and
        # already reaches the final JSON, so this costs nothing in the result
        # schema (docs/output-schema.md) and means an opportunity can be traced
        # back to the thing that claimed it — the same reason
        # LLMOpportunitySource records `grounding`.
        #
        # Normalizing is deliberately left to the caller. Every item every source
        # returns already goes through normalize_opportunity in
        # agent/graph/stages/discover.py, and normalizing an Opportunity a second
        # time nests it inside its own `raw` — so a source that does it too puts
        # everything here at `raw.raw.*` instead of `raw.*` in the real output.
        return [
            {**item, "mcp_tool": self.tool_name, "mcp_server": self._server_label()}
            for item in items
            if isinstance(item, dict)
        ]

    # --- the MCP exchange ----------------------------------------------------

    async def _call_tool(self, context: dict) -> object:
        # Deferred import: `import mcp` costs the better part of a second (it
        # pulls in a whole server stack for a client-only feature), and
        # agent/managers/tools_manager.py is imported by every CLI command,
        # including the ones that build nothing. Paying that only when a tenant
        # actually configures an MCP source keeps `list-tenants` fast.
        from mcp import Client

        async with Client(self._transport()) as client:
            result = await client.call_tool(self.tool_name, self._arguments(context))

        if result.is_error:
            raise RuntimeError(
                f"MCP tool {self.tool_name!r} returned an error: {_result_text(result)[:500]}"
            )
        return _payload(result, self.tool_name)

    def _transport(self):
        """A fresh transport per call. Both of these are single-use async context
        managers, which is why they're built here rather than in __init__."""
        if self.transport == "http":
            from mcp.client.streamable_http import (
                create_mcp_http_client,
                streamable_http_client,
            )

            return streamable_http_client(
                self.url,
                http_client=create_mcp_http_client(headers=self.headers or None),
            )

        from mcp import StdioServerParameters
        from mcp.client.stdio import get_default_environment, stdio_client

        return stdio_client(
            StdioServerParameters(
                command=self.command,
                args=self.args,
                # Merged onto the default environment rather than replacing it:
                # a configured `env` is almost always one API key, and a server
                # launched with PATH and HOME stripped away fails in ways that
                # look nothing like the cause.
                env={**get_default_environment(), **self.env},
                cwd=self.cwd or None,
            )
        )

    def _server_label(self) -> str:
        return self.url if self.transport == "http" else " ".join([self.command, *self.args])

    def _arguments(self, context: dict) -> dict:
        """The tool's arguments, with every string value rendered as Jinja2 against
        what the run knows — so `{"query": "{{ seed_keyword }}"}` reaches the
        server as a real query rather than a literal template.

        No `arguments` configured means the near-universal case: one string
        argument named `query`, holding the seed keyword or, when the run has none
        (which is the norm — discovery is exactly the case where nobody said what
        to look for), the brand description.
        """
        render_context = {
            "seed_keyword": context.get("seed_keyword", ""),
            "context_text": context.get("context_text", ""),
            "brand_description": self.config.brand_description,
            "agent_goal": self.config.agent_goal,
            "max_opportunities": self.max_opportunities,
        }
        if not self.arguments:
            return {"query": render_context["seed_keyword"] or render_context["brand_description"]}
        return {
            key: render_text(value, render_context) if isinstance(value, str) else value
            for key, value in self.arguments.items()
        }

    # --- the server's shape, into ours ---------------------------------------

    def _items(self, payload, context: dict) -> list:
        """The server's JSON, as a list of opportunity-shaped dicts.

        `items_template` is a Jinja2 template rendered against
        {"data": <the server's payload>, ...} that must produce a **JSON array
        string** — the same contract as analytics_options.highlights_template, and
        written the same way (a `{% for %}` loop over `tojson`). It is how a
        server whose tool answers in its own vocabulary is mapped without writing
        a class.

        With no template, the payload is passed through: MCP servers written for
        this kind of use commonly already answer with topic/signal_strength/etc.,
        and normalize_opportunity is tolerant of the rest. Anything else is an
        error naming the template, rather than a silent zero opportunities.
        """
        if self.items_template:
            rendered = render_text(
                self.items_template,
                {
                    "data": payload,
                    "seed_keyword": context.get("seed_keyword", ""),
                    "context_text": context.get("context_text", ""),
                    "max_opportunities": self.max_opportunities,
                },
            )
            try:
                items = json.loads(rendered)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"discovery source {self.name!r}: items_template must render to a JSON "
                    f"array, got {rendered[:200]!r} ({exc})"
                ) from exc
            if not isinstance(items, list):
                raise ValueError(
                    f"discovery source {self.name!r}: items_template rendered "
                    f"{type(items).__name__}, expected a JSON array"
                )
            return items

        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in _LIST_KEYS:
                if isinstance(payload.get(key), list):
                    return payload[key]
        raise ValueError(
            f"discovery source {self.name!r}: MCP tool {self.tool_name!r} answered with "
            f"{type(payload).__name__}, which is neither a JSON array nor an object with a "
            f"{'/'.join(_LIST_KEYS)} list — set options.items_template to map it"
        )


def _only_cause(group: BaseExceptionGroup) -> BaseException:
    """The one real exception inside an anyio ExceptionGroup, or the group itself.

    The SDK runs its transport in an anyio task group, so *every* failure arrives
    wrapped — a server that isn't installed, a tool that doesn't exist, a payload
    that won't parse. Left alone, all of them reach
    agent/graph/stages/discover.py's tool_errors as
    `ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`, which
    names neither the source nor the cause and is the same string every time. The
    wrapper is an implementation detail of how the SDK is built, not information
    about what went wrong, so it is unwrapped whenever there is exactly one thing
    underneath. A genuinely concurrent failure (more than one leaf) keeps its
    group — there the shape *is* the information.
    """
    while len(group.exceptions) == 1:
        inner = group.exceptions[0]
        if not isinstance(inner, BaseExceptionGroup):
            return inner
        group = inner
    return group


def _result_text(result) -> str:
    """Every text block of a CallToolResult, joined. Non-text content (images,
    embedded resources) is skipped rather than stringified: a tool answering with
    an image is not answering with opportunities, and the empty string that
    results says so more clearly than a repr would."""
    return "\n".join(
        block.text for block in (result.content or []) if getattr(block, "text", None)
    )


def _payload(result, tool_name: str):
    """The tool's answer as plain JSON data.

    `structured_content` first — it is MCP's own typed output and needs no
    parsing. Otherwise the text blocks, which is how the great majority of servers
    answer today: JSON in a text block. A tool that answers in prose has nothing
    an opportunity can be built from, so that is an error rather than an empty
    list, which would look identical to "the server found nothing".
    """
    if result.structured_content is not None:
        return result.structured_content

    text = _result_text(result).strip()
    if not text:
        raise ValueError(f"MCP tool {tool_name!r} returned no content")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"MCP tool {tool_name!r} did not return JSON: {text[:200]!r} ({exc})"
        ) from exc
