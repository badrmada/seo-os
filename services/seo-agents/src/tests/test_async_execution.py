"""Covers async execution (PLAN.md Step A): the sync-or-async Protocol contract,
the fact that two runs genuinely overlap in one process, and the per-run deadline.

The overlap test is the point of the whole step and the thing most likely to
regress silently — a single accidentally-blocking call in the run path (a sync
client invoked directly instead of through async_utils.call, an `asyncio.run`
where an `await` belonged) turns concurrent runs back into sequential ones while
every other test still passes.
"""

import asyncio
import time

import pytest

from agent.config.agent_config import AgentConfig
from agent.graph.tools import Tools
from agent.managers.run_manager import AgentRunner
from agent.utils.async_utils import call, is_async_callable
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.gsc_mock import MockGoogleSearchConsoleClient
from tools.mocks.traffic_mock import MockTrafficClient

# Long enough that a sequential run is unmistakably slower than a concurrent one,
# short enough to keep the suite fast. Every assertion below compares against
# 2 * DELAY (what sequential execution would cost), never against an absolute
# wall-clock budget, so a slow machine can't fail these.
DELAY = 0.2


class SyncSleepingSource:
    """A tenant's existing sync plugin: `__init__(config)` + `def discover(context)`,
    blocking inside. Nothing about it may need to change, and it must not stall the
    event loop while it blocks — that is what async_utils.call's thread hop is for."""

    def __init__(self, name: str = "sync") -> None:
        self.name = name

    def discover(self, context: dict) -> list[dict]:
        time.sleep(DELAY)
        return [{"topic": f"{self.name} topic", "signal_strength": 0.5, "reason": "slept"}]


class AsyncSleepingSource:
    """The opt-in shape: `async def discover`, awaited directly."""

    def __init__(self, name: str = "async") -> None:
        self.name = name

    async def discover(self, context: dict) -> list[dict]:
        await asyncio.sleep(DELAY)
        return [{"topic": f"{self.name} topic", "signal_strength": 0.5, "reason": "awaited"}]


def _tools(discovery_sources=None) -> Tools:
    return Tools(
        gsc=MockGoogleSearchConsoleClient(),
        analytics=MockAppAnalyticsClient(),
        traffic=MockTrafficClient(),
        llm=MockLLMClient(),
        discovery_sources=discovery_sources or {},
    )


def _config(**overrides) -> AgentConfig:
    return AgentConfig(
        discovery_sources=[{"name": "slow", "provider": "custom", "class": "unused"}],
        **overrides,
    )


def _input(keyword: str) -> dict:
    # No gsc_domain: the channel is left for discovery to decide, and with no GSC
    # rows to pick from the seed keyword is what ends up in the draft — which is
    # how the overlap test tells the two concurrent runs' results apart.
    return {"seed_keyword": keyword}


# --- the helper ------------------------------------------------------------


def test_call_awaits_an_async_callable():
    async def double(x):
        return x * 2

    assert asyncio.run(call(double, 21)) == 42


def test_call_runs_a_sync_callable_off_the_event_loop():
    """The guarantee the whole design rests on: a blocking implementation must not
    hold the loop, or one tenant's slow SDK stalls every other tenant's run."""

    async def scenario():
        started = time.perf_counter()
        results = await asyncio.gather(
            call(time.sleep, DELAY),
            call(time.sleep, DELAY),
            call(time.sleep, DELAY),
        )
        return time.perf_counter() - started, results

    elapsed, results = asyncio.run(scenario())
    assert results == [None, None, None]
    assert elapsed < 2 * DELAY


def test_call_handles_a_sync_function_that_returns_an_awaitable():
    """The hand-rolled-bridge shape: a plain `def` whose body returns a coroutine.
    Getting a coroutine object back where a value was expected is a confusing
    failure, so it's awaited rather than returned."""

    async def inner():
        return "value"

    def bridge():
        return inner()

    assert asyncio.run(call(bridge)) == "value"


@pytest.mark.parametrize(
    "fn, expected",
    [
        (lambda: None, False),
        (time.sleep, False),
        (AsyncSleepingSource().discover, True),
        (SyncSleepingSource().discover, False),
    ],
)
def test_is_async_callable(fn, expected):
    assert is_async_callable(fn) is expected


def test_is_async_callable_sees_through_a_callable_object():
    class AsyncCallable:
        async def __call__(self):
            return None

    class SyncCallable:
        def __call__(self):
            return None

    assert is_async_callable(AsyncCallable()) is True
    assert is_async_callable(SyncCallable()) is False


# --- both plugin flavors run in the pipeline -------------------------------


@pytest.mark.parametrize("source", [SyncSleepingSource(), AsyncSleepingSource()])
def test_a_sync_or_async_discovery_source_both_work(source):
    """The compatibility promise: `def discover` and `async def discover` are both
    complete, correct plugins, and neither one's run looks different."""
    runner = AgentRunner(_config(), tools=_tools({"slow": source}))

    result = runner.run(_input("widgets"))

    assert result["phase"] == "done"
    assert result["discovery"]["tool_errors"] == []
    assert len(result["discovery"]["opportunities"]) == 1


# --- the point of the exercise ---------------------------------------------


@pytest.mark.parametrize("source_class", [SyncSleepingSource, AsyncSleepingSource])
def test_two_runs_with_different_configs_overlap(source_class):
    """Two tenants, two configs, two Tools bundles, one process, one event loop —
    finishing in about the time of one run rather than two. Sync *and* async
    plugins both, since the sync branch (asyncio.to_thread) is the one every
    existing tenant plugin takes and the one that would quietly serialize
    everything if the thread hop were ever dropped.
    """

    async def both():
        first = AgentRunner(_config(), tools=_tools({"slow": source_class("first")}))
        second = AgentRunner(_config(), tools=_tools({"slow": source_class("second")}))
        started = time.perf_counter()
        results = await asyncio.gather(
            first.arun(_input("first keyword")),
            second.arun(_input("second keyword")),
        )
        return time.perf_counter() - started, results

    elapsed, (first_result, second_result) = asyncio.run(both())

    assert first_result["phase"] == "done"
    assert second_result["phase"] == "done"
    # Each run's own state stayed its own — overlapping must not mean sharing.
    assert first_result["output"]["metadata"]["target_keyword"] == "first keyword"
    assert second_result["output"]["metadata"]["target_keyword"] == "second keyword"
    assert first_result["run_id"] != second_result["run_id"]
    assert elapsed < 2 * DELAY


def test_analyze_context_runs_analytics_and_traffic_concurrently():
    """AnalyzeContextStage's two calls are independent of each other; they go out
    together rather than one after the other."""
    from agent.graph.stages.analyze import AnalyzeContextStage

    class SlowAnalytics:
        def report(self, limit: int = 5) -> dict:
            time.sleep(DELAY)
            return {"summary": "s", "highlights": []}

    class SlowTraffic:
        def traffic_summary(self, days: int = 28) -> dict:
            time.sleep(DELAY)
            return {"summary": "t"}

    tools = _tools()
    tools.analytics, tools.traffic = SlowAnalytics(), SlowTraffic()
    stage = AnalyzeContextStage(tools, AgentConfig())

    started = time.perf_counter()
    result = asyncio.run(stage.run({"input": {}, "working": {}}))
    elapsed = time.perf_counter() - started

    assert result["analyze_context"]["analytics_summary"] == "s"
    assert result["analyze_context"]["traffic_summary"] == "t"
    assert elapsed < 2 * DELAY


# --- the per-run deadline ---------------------------------------------------


def test_a_run_that_overruns_its_deadline_fails_with_the_documented_shape():
    """A deadline is still a failure like any other: the documented result shape,
    never a raised TimeoutError past the run() boundary."""
    runner = AgentRunner(
        _config(run_timeout_seconds=DELAY / 4),
        tools=_tools({"slow": AsyncSleepingSource()}),
    )

    result = runner.run(_input("widgets"))

    assert result["phase"] == "failed"
    assert "run_timeout_seconds" in result["error"]
    assert result["output"] is None
    assert result["usage"] == {"tokens": 0, "cost_usd": 0}


def test_a_tool_timeout_is_not_relabelled_as_the_run_deadline():
    """A client raising TimeoutError on its own is a different failure from the run
    overrunning — pointing the reader at the wrong timeout costs real debugging
    time, so the two are told apart rather than assumed."""

    class TimingOutSource:
        def discover(self, context: dict) -> list[dict]:
            raise TimeoutError("upstream read timed out")

    runner = AgentRunner(
        _config(run_timeout_seconds=30),
        tools=_tools({"slow": TimingOutSource()}),
    )

    result = runner.run(_input("widgets"))

    # DiscoverStage degrades rather than aborting, so this lands in tool_errors —
    # what matters is that the message is the client's, not the deadline's.
    [error] = result["discovery"]["tool_errors"]
    assert error["error_type"] == "TimeoutError"
    assert "upstream read timed out" in error["message"]
    assert result["error"] is None


def test_no_deadline_by_default():
    """0 (the default) means unbounded — the right default for a CLI someone is
    watching, and the reason this costs nothing when unused."""
    assert AgentConfig().run_timeout_seconds == 0

    runner = AgentRunner(_config(), tools=_tools({"slow": AsyncSleepingSource()}))

    assert runner.run(_input("widgets"))["phase"] == "done"
