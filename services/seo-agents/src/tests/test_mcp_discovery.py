"""Covers the built-in `provider: "mcp"` discovery source (PLAN.md Step E).

The stdio tests are **end-to-end on purpose**: a real subprocess, the real MCP
SDK, a real JSON-RPC exchange. The whole point of the step is that the transport
and the connect/initialize/call dance are no longer the tenant's problem, and a
test that mocked the SDK would assert we call it the way we think we do rather
than the way it actually works — which is precisely the part that used to go
wrong in every hand-written client.

What is *not* end-to-end is the mapping: `_items` and `_payload` are called
directly, since spawning a subprocess to find out that a prose answer is rejected
tests the subprocess, not the rejection.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

from agent.config.agent_config import AgentConfig
from agent.graph.stages.discover import DiscoverStage
from agent.graph.tools import Tools
from agent.managers.tools_manager import ToolsManager
from tools.clients.opportunity_mcp import MCPOpportunitySource
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.gsc_mock import MockGoogleSearchConsoleClient
from tools.mocks.traffic_mock import MockTrafficClient

# A dependency-free MCP server over stdio — enough of the protocol to be driven by
# a real client (initialize, tools/call), and no more. It is written to tmp_path
# rather than imported from examples/06-mcp-discovery/, so tightening the tests
# never means editing an example that exists to be read.
#
# Its behavior is entirely in the arguments it is launched with, so one script
# covers every case below:
#   --text T       answer with T as the single text block (verbatim, may be prose)
#   --is-error     answer with isError set
#   --hang         accept the tool call and never reply
#   (default)      echo the received arguments back inside a results list
STUB_SERVER = textwrap.dedent('''
    import json, sys, time

    def result(args):
        if "--hang" in sys.argv:
            time.sleep(300)
        if "--text" in sys.argv:
            text = sys.argv[sys.argv.index("--text") + 1]
        else:
            text = json.dumps({
                "results": [{"topic": args.get("query", ""), "signal_strength": 0.7,
                             "intent": "informational", "reason": "from the stub",
                             "arguments": args}],
            })
        return {"content": [{"type": "text", "text": text}],
                "isError": "--is-error" in sys.argv}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            out = {"result": {"protocolVersion": "2024-11-05",
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "stub", "version": "0.1.0"}}}
        elif method == "tools/list":
            # The SDK validates a tool result against the tool's declared schema,
            # so it lists tools before returning one. A real server has this; a
            # hand-written client never discovers that it is needed.
            out = {"result": {"tools": [{
                "name": "search_opportunities",
                "description": "Find content opportunities.",
                "inputSchema": {"type": "object",
                                "properties": {"query": {"type": "string"}}},
            }]}}
        elif method == "tools/call":
            out = {"result": result((msg.get("params") or {}).get("arguments") or {})}
        elif mid is None:
            continue
        else:
            out = {"error": {"code": -32601, "message": "unknown method %r" % method}}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, **out}) + "\\n")
        sys.stdout.flush()
''')


@pytest.fixture
def stub_server(tmp_path):
    path = tmp_path / "stub_mcp_server.py"
    path.write_text(STUB_SERVER, encoding="utf-8")
    return str(path)


def _config(**overrides) -> AgentConfig:
    return AgentConfig(
        brand_description="A homelab community.",
        agent_goal="grow organic traffic",
        **overrides,
    )


def _source(stub_server, *, server_args=(), **options) -> MCPOpportunitySource:
    return MCPOpportunitySource(
        "research", _config(),
        command=sys.executable, args=[stub_server, *server_args],
        tool_name="search_opportunities",
        **options,
    )


def _discover(source, **context) -> list[dict]:
    """What the source itself returns — raw items, not Opportunities. Normalizing
    is DiscoverStage's job (see _through_the_stage), so this is also where a
    failure is still an exception rather than a tool_errors entry."""
    return asyncio.run(source.discover(context))


def _through_the_stage(source, **context) -> dict:
    """The real path an opportunity travels: DiscoverStage calls the source and
    normalizes every item. Anything asserting the *output* shape has to go through
    here, because normalization is where `source` is stamped on and where a
    malformed item is dropped."""
    tools = Tools(
        gsc=MockGoogleSearchConsoleClient(), analytics=MockAppAnalyticsClient(),
        traffic=MockTrafficClient(), llm=MockLLMClient(),
        discovery_sources={source.name: source},
    )
    stage = DiscoverStage(tools, _config())
    return asyncio.run(stage.run({"input": context, "working": {}}))["working"]


# --- end to end, over real stdio -------------------------------------------


def test_a_servers_results_become_opportunities(stub_server):
    working = _through_the_stage(_source(stub_server), seed_keyword="proxmox backups")
    opportunities = working["opportunities"]

    assert working["tool_errors"] == []
    assert len(opportunities) == 1
    assert opportunities[0]["topic"] == "proxmox backups"
    assert opportunities[0]["signal_strength"] == 0.7
    # The registry key wins over anything the server claims, exactly as it does for
    # every other source (agent/schemas/opportunity.py's normalize_opportunity).
    assert opportunities[0]["source"] == "research"


def test_every_opportunity_records_which_server_and_tool_produced_it(stub_server):
    """`raw` reaches the final JSON, so an opportunity can be traced back to the
    thing that claimed it — the audit trail Step D established for grounding.

    Asserted **through DiscoverStage**, because that is the only place the claim
    is worth anything. normalize_opportunity puts the item it is given under
    `raw`, so a source that normalizes its own items before returning them buries
    everything it recorded at `raw.raw.*` — visible only in a real run, never in a
    test that calls discover() directly. That is exactly how it went wrong here.
    """
    raw = _through_the_stage(_source(stub_server), seed_keyword="zfs")["opportunities"][0]["raw"]

    assert raw["mcp_tool"] == "search_opportunities"
    assert stub_server in raw["mcp_server"]
    assert "raw" not in raw, "the item was normalized twice; provenance is now nested"


def test_with_no_arguments_configured_the_seed_keyword_is_the_query(stub_server):
    assert _discover(_source(stub_server), seed_keyword="zfs snapshots")[0]["topic"] == (
        "zfs snapshots"
    )


def test_with_no_seed_keyword_the_brand_description_is_the_query(stub_server):
    """The normal case, not the edge one: discovery is exactly the situation where
    nobody has told the agent what to look for, so an empty query would ask the
    server for nothing at all."""
    assert _discover(_source(stub_server))[0]["topic"] == "A homelab community."


def test_configured_arguments_are_rendered_against_the_run(stub_server):
    source = _source(stub_server, arguments={
        "query": "{{ seed_keyword }} for {{ brand_description }}",
        "limit": 10,
    })
    item = _discover(source, seed_keyword="nas builds")[0]

    assert item["topic"] == "nas builds for A homelab community."
    # Non-string values are passed through untouched rather than stringified — a
    # server whose schema says `limit` is a number must receive a number.
    assert item["arguments"]["limit"] == 10


def test_a_tool_error_is_raised_not_silently_empty(stub_server):
    source = _source(stub_server, server_args=["--is-error", "--text", "rate limited"])
    with pytest.raises(RuntimeError, match="rate limited"):
        _discover(source, seed_keyword="anything")


def test_a_hanging_server_times_out_rather_than_hanging_the_run(stub_server):
    """A server that accepts the call and never answers is the failure mode a
    hand-written client always forgets. Bounded by default, not by configuration."""
    source = _source(stub_server, server_args=["--hang"], timeout_seconds=1.0)
    with pytest.raises(asyncio.TimeoutError):
        _discover(source, seed_keyword="anything")


def test_a_failing_mcp_source_costs_one_source_not_the_run(stub_server):
    """The invariant every discovery source lives under: DiscoverStage records the
    failure on working.tool_errors and carries on (agent/graph/stages/discover.py)."""
    source = _source(stub_server, server_args=["--text", "not json at all"])

    working = _through_the_stage(source, seed_keyword="x")

    assert working["opportunities"] == []
    error = working["tool_errors"][0]
    assert error["tool"] == "research"
    # And the recorded error says what actually happened. The SDK runs its
    # transport in an anyio task group, so without opportunity_mcp's _only_cause
    # every failure lands here as the same useless
    # "ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)".
    assert error["error_type"] == "ValueError"
    assert "did not return JSON" in error["message"]


def test_a_server_that_is_not_installed_says_so(stub_server):
    """The most common first-run failure of all — a `command` that isn't on PATH.
    It must arrive as the FileNotFoundError it is, not wrapped in a task group."""
    source = MCPOpportunitySource(
        "research", _config(), command="definitely-not-an-installed-binary",
        tool_name="search_opportunities",
    )
    with pytest.raises(FileNotFoundError):
        _discover(source, seed_keyword="x")


def test_items_template_maps_a_servers_own_vocabulary(stub_server):
    """The point of the template: a server that answers in its own field names is
    configuration, not a "custom" class."""
    payload = json.dumps({"hits": [
        {"title": "Cheap 10GbE switches", "score": 91, "url": "https://example.test/a"},
        {"title": "Proxmox vs TrueNAS", "score": 64, "url": "https://example.test/b"},
    ]})
    source = _source(
        stub_server, server_args=["--text", payload],
        items_template=(
            "[{% for hit in data.hits %}"
            '{"topic": {{ hit.title | tojson }}, '
            '"signal_strength": {{ hit.score / 100 }}, '
            '"reason": {{ ("scored " ~ hit.score ~ " by the server") | tojson }}, '
            '"link": {{ hit.url | tojson }}}'
            "{% if not loop.last %},{% endif %}"
            "{% endfor %}]"
        ),
    )

    opportunities = _discover(source, seed_keyword="networking")

    assert [o["topic"] for o in opportunities] == [
        "Cheap 10GbE switches", "Proxmox vs TrueNAS",
    ]
    assert opportunities[0]["signal_strength"] == 0.91
    assert opportunities[1]["reason"] == "scored 64 by the server"


def test_max_opportunities_bounds_what_a_server_can_return(stub_server):
    payload = json.dumps({"results": [{"topic": f"topic {n}"} for n in range(20)]})
    source = _source(stub_server, server_args=["--text", payload], max_opportunities=3)

    assert len(_discover(source, seed_keyword="x")) == 3


# --- the server's shape, into ours ------------------------------------------


class FakeResult:
    """A CallToolResult, as much of it as opportunity_mcp reads."""

    def __init__(self, text: str = "", structured=None, is_error: bool = False) -> None:
        self.content = [FakeBlock(text)] if text else []
        self.structured_content = structured
        self.is_error = is_error


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


def _payload(result):
    from tools.clients.opportunity_mcp import _payload as extract

    return extract(result, "search_opportunities")


def test_structured_content_is_preferred_over_text():
    """MCP's own typed output needs no parsing, and a server that sends both is
    telling us which one it means."""
    result = FakeResult(text='{"results": []}', structured={"results": [{"topic": "a"}]})
    assert _payload(result) == {"results": [{"topic": "a"}]}


def test_a_prose_answer_is_an_error_not_an_empty_list():
    """"the server found nothing" and "the server answered in English" look
    identical from an empty list and mean completely different things."""
    with pytest.raises(ValueError, match="did not return JSON"):
        _payload(FakeResult(text="I found three good topics for you!"))


def test_an_empty_answer_is_an_error():
    with pytest.raises(ValueError, match="no content"):
        _payload(FakeResult())


@pytest.mark.parametrize("payload", [
    [{"topic": "a"}],
    {"results": [{"topic": "a"}]},
    {"items": [{"topic": "a"}]},
    {"opportunities": [{"topic": "a"}]},
])
def test_the_common_payload_shapes_need_no_template(payload):
    source = MCPOpportunitySource("s", _config(), command="x", tool_name="t")
    assert source._items(payload, {}) == [{"topic": "a"}]


def test_an_unmappable_payload_names_the_option_that_would_fix_it():
    source = MCPOpportunitySource("s", _config(), command="x", tool_name="t")
    with pytest.raises(ValueError, match="items_template"):
        source._items({"data": {"nested": {"deeply": []}}}, {})


def test_a_template_that_renders_something_other_than_a_json_array_is_rejected():
    source = MCPOpportunitySource(
        "s", _config(), command="x", tool_name="t", items_template="{{ data.results }}",
    )
    with pytest.raises(ValueError, match="must render to a JSON array"):
        source._items({"results": [{"topic": "a"}]}, {})


def test_a_template_rendering_a_json_object_is_rejected():
    source = MCPOpportunitySource(
        "s", _config(), command="x", tool_name="t", items_template='{"topic": "a"}',
    )
    with pytest.raises(ValueError, match="expected a JSON array"):
        source._items({}, {})


# --- built from config ------------------------------------------------------


def test_tools_manager_builds_an_mcp_source_from_its_options(stub_server):
    config = _config(discovery_sources=[{
        "name": "research",
        "provider": "mcp",
        "options": {
            "command": sys.executable,
            "args": [stub_server],
            "tool_name": "search_opportunities",
            "timeout_seconds": 30,
        },
    }])

    sources = ToolsManager(config).build_discovery_sources(llm=None)

    assert isinstance(sources["research"], MCPOpportunitySource)
    assert _discover(sources["research"], seed_keyword="k3s")[0]["topic"] == "k3s"


@pytest.mark.parametrize("options, expected", [
    ({"command": "x"}, "tool_name is required"),
    ({"tool_name": "t"}, "command is required"),
    ({"tool_name": "t", "transport": "http"}, "url is required"),
    ({"tool_name": "t", "transport": "carrier-pigeon"}, "transport"),
])
def test_a_misconfigured_source_fails_when_it_is_built_not_when_it_runs(options, expected):
    """ToolsManager builds every source before the graph starts, so a typo here is
    a startup error naming the option rather than a tool_errors entry ten seconds
    into a run that has already spent an LLM call."""
    config = _config(discovery_sources=[
        {"name": "research", "provider": "mcp", "options": options},
    ])
    with pytest.raises(ValueError, match=expected):
        ToolsManager(config).build_discovery_sources(llm=None)


def test_http_transport_needs_no_command():
    source = MCPOpportunitySource(
        "research", _config(), transport="http",
        url="https://mcp.example.test/mcp", headers={"Authorization": "Bearer t"},
        tool_name="search_opportunities",
    )
    # The transport is built per call and is a single-use async context manager;
    # that it constructs at all is what distinguishes a wired-up URL from a typo.
    assert source._transport() is not None
    assert source._server_label() == "https://mcp.example.test/mcp"
