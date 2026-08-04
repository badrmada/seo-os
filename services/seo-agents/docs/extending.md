# Extending the agent without forking

The whole point of the provider pattern (see
[architecture.md](architecture.md#the-four-provider-flavors-mock-templated-custom-llm))
is that plugging in your own analytics source, traffic source, or opportunity
finder comes down to three steps:

1. `pip install -r requirements.txt` (plus whatever your own code needs).
2. Write one Python class with the method the interface expects.
3. Point your config at it: `"<field>_provider": "custom"` and
   `"<field>_custom_class": "module.path:ClassName"`.

Nothing under `src/agent/` or `src/tools/` changes. That's the whole deal — the
rest of this page is a worked walkthrough, plus the honest limits.

## How `"custom"` works under the hood

Every `"custom"` provider is loaded by the same one method,
`ToolsManager._load_custom()`
([`src/agent/managers/tools_manager.py`](../src/agent/managers/tools_manager.py)):

```python
def _load_custom(self, class_path: str, field_name: str):
    module_path, _, class_name = class_path.partition(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(self.config)
```

So the contract for your class is just this:

- **Constructor:** `__init__(self, config)`. `config` is the tenant's full
  `AgentConfig`, so you can read any existing field from it
  (`config.brand_description`, and so on). You **can't** add brand-new named
  fields to `AgentConfig` without editing `agent_config.py` — that's the one
  thing that does require touching this repo. If your class needs its own
  settings, have it load them itself: its own environment variables, its own
  small config file, whatever suits your deployment. `config` gives you
  everything the system already knows; it doesn't have to be your class's only
  source of settings.
- **Method:** whatever the target interface requires (table below).
- **Importable:** the module path is resolved with a normal Python import — see
  [Making your module importable](#making-your-module-importable) below.

| Config field | Interface | Method your class needs | Must return |
|---|---|---|---|
| `analytics_custom_class` | `AppAnalyticsClient` | `report(self, limit: int = 5) -> dict` | `{"summary": str, "highlights": [{"label": str, "url": str}, ...]}` |
| `traffic_custom_class` | `SiteTrafficClient` | `traffic_summary(self, days: int = 28) -> dict` | `{"summary": str}` |
| `discovery_sources[i]["class"]` | `OpportunitySource` | `discover(self, context: dict) -> list[dict]` | a list of opportunity dicts (shape below) |

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

## Making your module importable

`_load_custom` uses a plain dotted import, so your class needs to be findable
under that name. Two ways, from quickest to most robust:

1. **Drop a file under `src/`.** `python src/main.py` puts `src/` on the import
   path, so a file at `src/my_tenant/analytics.py` imports as
   `my_tenant.analytics`. This still counts as "no fork" in the way that matters
   — you're adding a new file, not editing anything under `agent/` or `tools/` —
   and it's the fastest way to try something.
2. **Install your own package.** For a real deployment (especially several
   products, or code you want tested and versioned separately), put your class
   in its own installable package and `pip install` it into the same virtualenv.
   Reference it by its real dotted path. This is the version that scales past
   "one product, one checkout."

Either way, the `class_path` in your config is just `"module.path:ClassName"` —
the loader doesn't know or care which route you took.

## Walkthrough: a custom analytics source

Say your analytics lives in Postgres, not a JSON file or a simple REST endpoint.
That's too much of a real query to express as a template reshape (which is what
`"templated"` is for), so `"custom"` is the right fit.

```python
# src/my_tenant/analytics.py
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
  "analytics_custom_class": "my_tenant.analytics:PostgresAnalyticsClient"
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
# src/my_tenant/ga4.py
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
  "traffic_custom_class": "my_tenant.ga4:GA4TrafficClient"
}
```

## Test your class before wiring it into a run

Your class is plain Python, so you can check it in a few lines before touching a
config — no need to run the whole pipeline:

```python
# from src/, with your venv active:  python -c "..."
from agent.config import AgentConfigLoader
from my_tenant.analytics import PostgresAnalyticsClient

config = AgentConfigLoader().load("tenant.json")   # or build a minimal AgentConfig
client = PostgresAnalyticsClient(config)
print(client.report(limit=3))   # eyeball the {summary, highlights} shape
```

If that prints the right shape, the pipeline will accept it — the pipeline only
ever calls that one method.

## Walkthrough: an opportunity source that's itself an agent

A discovery source doesn't have to be one call, or even one AI call — it can run
its own multi-step loop (search, fetch, summarize) before returning results.
Nothing downstream cares; the interface hides all of it. Here's a sketch:

```python
# src/my_tenant/reddit_agent.py
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
    {"name": "reddit_agent", "provider": "custom", "class": "my_tenant.reddit_agent:RedditResearchAgent"}
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
# src/my_tenant/mcp_discovery.py
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
    { "name": "mcp", "provider": "custom", "class": "my_tenant.mcp_discovery:MCPDiscoverySource" }
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
