# Extending the agent without forking

The whole point of the provider pattern (see
[architecture.md](architecture.md#the-four-provider-flavors-mock-templated-custom-llm))
is that plugging in your own analytics source, traffic source, or opportunity
finder comes down to three steps:

1. `pip install -r requirements.txt` (plus whatever your own code needs).
2. Write one Python class with the method the interface expects.
3. Point your config at it: `"<field>_provider": "custom"` and
   `"<field>_custom_class": "module:ClassName"` (a file in your `plugins/` folder).

Nothing under `src/agent/` or `src/tools/` changes. That's the whole deal — the
rest of this page is a worked walkthrough, plus the honest limits.

## How `"custom"` works under the hood

Every `"custom"` provider is loaded by the same one function,
`load_custom()`
([`src/agent/managers/plugin_loader.py`](../src/agent/managers/plugin_loader.py)):

```python
def load_custom(class_path, field_name, config, options=None):
    module_path, _, class_name = class_path.partition(":")
    module = _import_plugin_module(module_path, field_name, config)  # from plugins/
    cls = getattr(module, class_name)
    if options is not None and _accepts_options(cls):
        return cls(config, options)
    return cls(config)
```

So the contract for your class is just this:

- **Constructor:** either `__init__(self, config)` or
  `__init__(self, config, options)` — the loader inspects your signature and
  calls whichever you wrote. `config` is the tenant's full `AgentConfig`, so you
  can read any existing field from it (`config.brand_description`, and so on).
- **Your own settings:** if your class needs configuration of its own, take the
  two-argument form and put it in that provider's `"options"` object in the
  tenant JSON — it travels with the provider that uses it, which is also where
  its secrets belong. (Older classes that take only `config` keep working
  unchanged; they load their own settings from env vars or their own file
  instead, and every example under [`examples/`](../examples/) still does it that
  way.)
- **Method:** whatever the target interface requires (table below).
- **Where it lives:** a `.py` file in your tenant's `plugins/` folder, and the
  module name is that filename — see
  [the plugins folder](#where-your-code-goes-the-plugins-folder) below.
- **Files your class opens itself:** anchor them to `config.config_base_dir`,
  the folder holding the tenant config, rather than to the working directory:

  ```python
  self._path = Path(config.config_base_dir or ".") / "data/events.json"
  ```

  The system already does this for every path *declared in config*
  ([configuration.md](configuration.md#how-file-paths-in-your-config-are-resolved));
  a path hardcoded inside your class is the one case it can't reach. Getting
  this right is what lets your class work regardless of where the command runs
  from — including from a server running several tenants at once, where the
  working directory is shared and means nothing.

| Config field | Interface | Method your class needs | Must return |
|---|---|---|---|
| `analytics_custom_class` | `AppAnalyticsClient` | `report(self, limit: int = 5) -> dict` | `{"summary": str, "highlights": [{"label": str, "url": str}, ...]}` |
| `traffic_custom_class` | `SiteTrafficClient` | `traffic_summary(self, days: int = 28) -> dict` | `{"summary": str}` |
| `discovery_sources[i]["class"]` | `OpportunitySource` | `discover(self, context: dict) -> list[dict]` | a list of opportunity dicts (shape below) |
| `output_sinks[i]["class"]` | `OutputSink` | `emit(self, output: dict) -> None` | nothing — it's a side effect |

The return shapes are exactly what the `"templated"` provider produces, and the
same ones documented in [configuration.md](configuration.md) — `"custom"` is
just "build that shape with code instead of a template." For discovery, each
opportunity dict is `{"source", "topic", "signal_strength" (0–1), "intent",
"suggested_channel_hint", "raw", "reason"}` — but you don't have to get it
perfect: every item is normalized and validated for you, and a malformed one is
dropped rather than crashing the run.

The `context` dict passed to `discover()` carries what the run already knows —
`context.get("seed_keyword")` and `context.get("context_text")` — so your finder
can steer toward the caller's topic when there is one.

(There's no `"custom"` option for `llm_provider` or `gsc_provider` today — those
two already ship with one real vendor client each. To add another, see
[Adding a new provider *kind*](#adding-a-new-provider-kind-not-just-a-new-instance)
below.)

## Where your code goes: the `plugins/` folder

Your class goes in **one place**: the `plugins/` folder inside your tenant.

```
userdata/
└── acme/                 <- your tenant, referred to as --tenant acme
    ├── tenant.json
    ├── plugins/
    │   └── analytics.py  <- your class lives here
    └── data/
```

```jsonc
{ "analytics_provider": "custom",
  "analytics_custom_class": "analytics:PostgresAnalyticsClient" }
```

The module name is the filename, without `.py`. No `PYTHONPATH`, no dropping
files into `src/`, no installing anything — it works from any directory, because
the folder is found relative to your `tenant.json` rather than to wherever you
ran the command.

A few things worth knowing:

- **Plugin files in one tenant can import each other** with a relative import:
  `from .helpers import something`.
- **Two tenants may both have `plugins/analytics.py`** and they stay completely
  separate. Each tenant's folder is loaded as its own package rather than being
  added to `sys.path`, so nothing collides even with many tenants in one process.
- **Need a third-party library?** Install it in the image your deployment runs
  (see below) and import it normally from your plugin file. A whole package of
  your own works the same way — `pip install` it, then have a file in `plugins/`
  import from it:

  ```python
  # userdata/acme/plugins/analytics.py
  from mycompany_seo_tools import PostgresAnalyticsClient  # noqa: F401
  ```

### Extra dependencies mean a new image

There is no per-tenant environment management, on purpose. One deployment is one
image with one set of installed packages. If your class needs libraries the image
doesn't ship, add them to `requirements.txt` and build an image that has them —
anything needing genuinely different packages is a different deployment. This is
what keeps plugin loading a single, predictable mechanism instead of a dependency
resolver.

## Walkthrough: a custom analytics source

Say your analytics lives in Postgres, not a JSON file or a simple REST endpoint.
That's too much of a real query to express as a template reshape (which is what
`"templated"` is for), so `"custom"` is the right fit.

```python
# userdata/acme/plugins/analytics.py
import psycopg2

class PostgresAnalyticsClient:
    def __init__(self, config):
        # config is the tenant's full AgentConfig. Read anything it already has,
        # or load your own settings however you like (an env var, here):
        import os
        self._dsn = os.environ["ANALYTICS_DB_DSN"]

    def report(self, limit: int = 5) -> dict:
        with psycopg2.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ideas")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT title, url FROM ideas ORDER BY upvotes DESC LIMIT %s", (limit,)
            )
            highlights = [{"label": title, "url": url} for title, url in cur.fetchall()]
        return {"summary": f"{total} ideas shared so far.", "highlights": highlights}
```

Config:

```jsonc
{
  "analytics_provider": "custom",
  "analytics_custom_class": "analytics:PostgresAnalyticsClient"
}
```

That's the entire integration. `agent/graph/stages/analyze.py` never changes —
it only ever calls `self.tools.analytics.report(limit=...)` against whatever
implements the interface.

## Walkthrough: a custom traffic source

Traffic is even simpler — one method, and it only returns a `summary`. Say you
use Google Analytics 4, which needs its official SDK rather than a plain JSON
fetch (so `"templated"` doesn't fit):

```python
# userdata/acme/plugins/ga4.py
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Metric, RunReportRequest

class GA4TrafficClient:
    def __init__(self, config):
        self._property_id = "properties/123456789"  # your GA4 property
        self._client = BetaAnalyticsDataClient()     # reads GOOGLE_APPLICATION_CREDENTIALS

    def traffic_summary(self, days: int = 28) -> dict:
        report = self._client.run_report(RunReportRequest(
            property=self._property_id,
            date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
            metrics=[Metric(name="sessions"), Metric(name="totalUsers")],
        ))
        row = report.rows[0].metric_values
        sessions, users = row[0].value, row[1].value
        return {"summary": f"{sessions} sessions from {users} users over the last {days} days."}
```

```jsonc
{
  "traffic_provider": "custom",
  "traffic_custom_class": "ga4:GA4TrafficClient"
}
```

## Test your class before wiring it into a run

Your class is plain Python, so you can check it in a few lines before touching a
config — no need to run the whole pipeline:

```python
# from src/, with your venv active:  python -c "..."
from agent.config.workspace import TenantWorkspace
from agent.managers.plugin_loader import load_custom

config = TenantWorkspace.open("acme", root="../userdata").load_config()
client = load_custom("analytics:PostgresAnalyticsClient", "analytics_custom_class", config)
print(client.report(limit=3))   # eyeball the {summary, highlights} shape
```

Going through `load_custom` rather than importing your file directly is the point
— it resolves the module out of your tenant's `plugins/` folder exactly as a real
run does, so "it imports here" and "it imports in a run" can't diverge.

If that prints the right shape, the pipeline will accept it — the pipeline only
ever calls that one method.

## Walkthrough: a custom output sink

A sink is the simplest interface here — one method, no return value. It runs once,
after the run finishes, and receives the complete result. Say you want each
finished draft to land in your CMS as an unpublished post:

```python
# userdata/acme/plugins/cms_sink.py
import requests

class CmsDraftSink:
    def __init__(self, config, options=None):
        # Two-argument form: settings come from this sink's own "options" in the
        # tenant JSON, so its API token lives with the sink that uses it.
        options = options or {}
        self._url = options["cms_url"]
        self._token = options["cms_token"]

    def emit(self, output: dict) -> None:
        if output.get("phase") != "done":
            return                      # nothing worth publishing from a failed run
        draft = output["output"]
        requests.post(
            self._url,
            json={"title": draft["title"], "body": draft["content"], "status": "draft"},
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10,
        ).raise_for_status()
```

```jsonc
{
  "output_sinks": [
    { "name": "stdout", "provider": "json" },
    { "name": "cms", "provider": "custom",
      "class": "cms_sink:CmsDraftSink",
      "options": { "cms_url": "https://cms.example.com/api/posts",
                   "cms_token": "..." } }
  ]
}
```

Your `emit` can raise — a sink failure is reported and skipped, never fatal, since
the run is already complete by then. But a failure in your **constructor** is
fatal, and deliberately so: sinks are built before the run starts, so a bad
setting fails immediately instead of after a pipeline has spent real LLM calls.

## Walkthrough: an opportunity source that's itself an agent

A discovery source doesn't have to be one call, or even one AI call — it can run
its own multi-step loop (search, fetch, summarize) before returning results.
Nothing downstream cares; the interface hides all of it. Here's a sketch:

```python
# userdata/acme/plugins/reddit_agent.py
class RedditResearchAgent:
    """A small agent-as-a-tool: it searches Reddit, reads the top threads, and asks
    an AI model to turn them into opportunities — a multi-step loop hiding behind
    the same interface a single prompt (provider="llm") or a static fixture
    (provider="mock") would use."""

    def __init__(self, config):
        self._config = config
        self._llm = ...  # build (or reuse) whatever AI client you need

    def discover(self, context: dict) -> list[dict]:
        query = context.get("seed_keyword") or self._config.brand_description
        threads = self._search_reddit(query)                    # your own HTTP calls
        summaries = [self._summarize(t) for t in threads[:5]]   # your own AI calls
        return [
            {
                "source": "reddit_agent",
                "topic": s["topic"],
                "signal_strength": s["score"],
                "intent": "discussion",
                "suggested_channel_hint": "engagement_comment",
                "raw": s,
                "reason": s["why"],
            }
            for s in summaries
        ]

    def _search_reddit(self, query): ...
    def _summarize(self, thread): ...
```

```jsonc
{
  "discovery_sources": [
    {"name": "reddit_agent", "provider": "custom", "class": "reddit_agent:RedditResearchAgent"}
  ]
}
```

This is the exact same mechanism as the built-in `"llm"` provider
([`tools/clients/opportunity_llm.py`](../src/tools/clients/opportunity_llm.py))
— `"llm"` is simply the single-prompt case that's common enough to ship
built-in. Reach for `"custom"` once you need multiple steps, external APIs, or
logic beyond one prompt.

## Using an MCP server as a tool

Nothing in the `"custom"` contract cares *how* your class produces its result, so
a class can be an [MCP](https://modelcontextprotocol.io/) client: it connects to
an MCP server, calls the server's tools, and returns the interface's shape. A
discovery source is the most natural fit (an MCP server exposing search/research
tools maps cleanly onto "find opportunities"), but analytics and traffic work the
same way.

There's one thing to know: the interface methods here are **synchronous**
(`discover(self, context) -> list[dict]`), while the official
[`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk) is
**async**. You bridge the two by running the async calls with `asyncio.run(...)`
inside the sync method:

```python
# userdata/acme/plugins/mcp_discovery.py
import asyncio, os, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPDiscoverySource:
    def __init__(self, config):
        self._config = config
        # AgentConfig can't gain new fields without editing the repo, so the class
        # loads its own MCP connection details (env vars here, or your own file):
        self._server = StdioServerParameters(
            command=os.environ.get("MCP_CMD", "npx"),
            args=os.environ.get("MCP_ARGS", "-y @acme/research-mcp").split(),
        )

    def discover(self, context: dict) -> list[dict]:
        return asyncio.run(self._discover(context))          # bridge async -> sync

    async def _discover(self, context):
        async with stdio_client(self._server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(
                    "search_opportunities",
                    {"query": context.get("seed_keyword") or self._config.brand_description},
                )
                items = json.loads(res.content[0].text)      # shape depends on the server
        return [
            {
                "source": "mcp", "topic": it["topic"],
                "signal_strength": it.get("signal_strength", 0.6),
                "intent": "informational",
                "suggested_channel_hint": it.get("channel"),
                "raw": it, "reason": it.get("reason", "surfaced via MCP"),
            }
            for it in items["results"]
        ]
```

```jsonc
{
  "discovery_sources": [
    { "name": "mcp", "provider": "custom", "class": "mcp_discovery:MCPDiscoverySource" }
  ]
}
```

Worth knowing:

- **`asyncio.run` per call is fine** — `discover()` runs once per `run()`. (If you
  reused one instance across many runs and wanted a persistent session, you'd
  manage a long-lived event loop instead — not needed for the one-shot model.)
- **Connection details and secrets** live in your class (an env var or its own
  file), since you can't add fields to `AgentConfig` without touching the repo.
- **The result is still normalized** — a slightly-off item is dropped, not fatal.

A **complete, runnable** version — including a tiny dependency-free stub MCP
server so you can see the whole flow offline — is in
[`examples/06-mcp-discovery/`](../examples/06-mcp-discovery/). That example uses a
hand-rolled JSON-RPC-over-stdio client (so it runs on any Python and shows what
the SDK does underneath); prefer the official `mcp` SDK above for real work.

## Adding a new provider *kind* (not just a new instance)

Everything above adds a new **instance** of an interface that already exists
(analytics, traffic, a discovery source) — no repo changes. Adding a genuinely
new *kind* of pluggable thing — a fifth interface, or a new LLM/GSC vendor
(since those two don't have a `"custom"` slot today) — does mean touching this
repo:

1. Add the interface to [`tools/base.py`](../src/tools/base.py).
2. Add a field for it to
   [`agent/graph/tools.py`](../src/agent/graph/tools.py)'s `Tools` dataclass.
3. Add a `build_x()` method to
   [`agent/managers/tools_manager.py`](../src/agent/managers/tools_manager.py)'s
   `ToolsManager`, plus the matching `*_provider` field(s) in
   [`agent/config/agent_config.py`](../src/agent/config/agent_config.py).
4. Use it from whichever step needs it (`agent/graph/stages/`).

This is genuinely rare — the four interfaces that exist today ("keyword data,"
"product analytics," "traffic," and "content opportunities") already cover a lot
of ground. If you find yourself here, it's worth opening an issue first, in case
what you need actually fits one of the existing four with a `"custom"` class.

## See also

- [architecture.md](architecture.md) — why the system is shaped this way.
- [configuration.md](configuration.md) — every config field, including every
  provider option.
</content>
