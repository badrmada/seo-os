"""Covers PLAN.md Step F — signal inputs as a named list: config.signal_sources
and the providers behind it, the concurrent degrade-don't-abort collection in
analyze, and the generic context bag reaching the prompt keyed by name.

The load-bearing tests here are the two that go through a *real run* rather than
calling a piece directly:
`test_a_configured_signal_reaches_the_drafted_prompt_keyed_by_its_name` and
`test_the_same_signals_arrive_with_and_without_a_discovery_pipeline`. The point of
the step is that a signal nobody wrote code for lands in the prompt, and a test
that only asserts SignalSource.collect() returns a dict proves none of it — the
same lesson the double-normalization bug taught discovery (see tools/base.py's
OpportunitySource), where every test passed while every real run put the data
somewhere the docs said it wasn't.
"""

import asyncio
import json
import time

import pytest

from agent.config.agent_config import AgentConfig
from agent.config.loader import AgentConfigLoader
from agent.graph.pipeline import build_graph
from agent.graph.stages.analyze import AnalyzeContextStage, AnalyzeStage
from agent.graph.tools import Tools
from agent.managers.run_manager import AgentRunner
from agent.managers.tools_manager import ToolsManager
from agent.observability import build_reporter, observe_tools
from agent.schemas.signal import empty_signal, normalize_signal
from tools.llm.mocks.mock_client import MockLLMClient
from tools.mocks.analytics_mock import MockAppAnalyticsClient
from tools.mocks.gsc_mock import MockGoogleSearchConsoleClient
from tools.mocks.signal_mock import MockSignalSource
from tools.mocks.traffic_mock import MockTrafficClient

# Same reasoning as test_async_execution.py's: every timing assertion below
# compares against 2 * DELAY (what sequential collection would cost), never an
# absolute budget, so a slow machine can't fail them.
DELAY = 0.2


_UNSET = object()


class StubSignal:
    """A signal that answers with whatever it was given — the shape a tenant's own
    class takes, minus the fetching. The default is a sentinel, not None, because
    None is itself one of the return shapes under test."""

    def __init__(self, result=_UNSET, *, fail: bool = False) -> None:
        self.result = {"summary": "stub summary"} if result is _UNSET else result
        self.fail = fail
        self.contexts: list[dict] = []

    def collect(self, context: dict) -> dict:
        self.contexts.append(context)
        if self.fail:
            raise RuntimeError("signal exploded")
        return self.result


class SyncSleepingSignal:
    """A tenant's blocking client: a plain `def collect`. It must not hold the
    event loop while it sleeps, or N signals cost N round-trips instead of one —
    which is the whole reason collection is a gather."""

    def collect(self, context: dict) -> dict:
        time.sleep(DELAY)
        return {"summary": "slept"}


def _tools(signals=None, **overrides) -> Tools:
    return Tools(
        gsc=overrides.get("gsc") or MockGoogleSearchConsoleClient(),
        analytics=overrides.get("analytics") or MockAppAnalyticsClient(),
        traffic=overrides.get("traffic") or MockTrafficClient(),
        llm=MockLLMClient(),
        discovery_sources=overrides.get("discovery_sources") or {},
        signals=signals or {},
    )


def _analyze(signals=None, config=None, state=None) -> dict:
    """AnalyzeStage on the no-discovery path, where it collects context itself."""
    stage = AnalyzeStage(_tools(signals), config or AgentConfig())
    state = state or {"input": {"channel": "site_article", "seed_keyword": "kettles"}, "working": {}}
    return asyncio.run(stage.run(state))["working"]


def _tenant(tmp_path, plugins: dict = None, files: dict = None, **config) -> AgentConfig:
    tenant = tmp_path / "acme"
    tenant.mkdir(parents=True, exist_ok=True)
    for filename, source in (plugins or {}).items():
        (tenant / "plugins").mkdir(exist_ok=True)
        (tenant / "plugins" / filename).write_text(source)
    for filename, content in (files or {}).items():
        (tenant / filename).parent.mkdir(parents=True, exist_ok=True)
        (tenant / filename).write_text(content)
    return AgentConfig(config_base_dir=str(tenant), **config)


# --- the shape a signal returns --------------------------------------------


def test_a_full_result_passes_through_unchanged():
    signal = normalize_signal(
        {"summary": "up 12%", "facts": {"change_pct": 12}, "items": [{"label": "a"}]}
    )
    assert signal == {"summary": "up 12%", "facts": {"change_pct": 12}, "items": [{"label": "a"}]}


def test_only_a_summary_is_a_complete_signal():
    """summary is the one field a signal must produce; facts/items are for a
    template that knows what it asked for."""
    assert normalize_signal({"summary": "just prose"}) == {
        "summary": "just prose", "facts": {}, "items": [],
    }


@pytest.mark.parametrize(
    ("raw", "expected_summary"),
    [(None, ""), ("a bare summary", "a bare summary")],
)
def test_the_two_forgiven_shorthands(raw, expected_summary):
    """None means "ran, nothing to report"; a bare string is the obvious mistake to
    forgive, since summary is the only required field."""
    assert normalize_signal(raw)["summary"] == expected_summary


@pytest.mark.parametrize(
    "raw",
    [42, {"summary": 42}, {"summary": "s", "facts": []}, {"summary": "s", "items": {}}],
)
def test_a_shape_nothing_can_read_raises_rather_than_flattening(raw):
    """Unlike an opportunity — one of many, dropped individually — a signal *is*
    the whole contribution, so a malformed one has to be reported. A degrade
    nothing records is a bug."""
    with pytest.raises(ValueError):
        normalize_signal(raw)


def test_non_dict_items_are_dropped_rather_than_failing_the_signal():
    signal = normalize_signal({"summary": "s", "items": [{"label": "keep"}, "drop", 7]})
    assert signal["items"] == [{"label": "keep"}]


def test_nothing_to_report_and_a_failure_look_the_same_to_a_template():
    assert normalize_signal(None) == empty_signal()


# --- collection: concurrent, and degrade-don't-abort -----------------------


def test_a_signal_lands_in_working_keyed_by_its_name():
    working = _analyze({"trends": StubSignal({"summary": "interest is up"})})

    assert working["signals"] == {
        "trends": {"summary": "interest is up", "facts": {}, "items": []},
    }


def test_a_signal_is_told_what_the_run_knows_so_far():
    signal = StubSignal()
    _analyze(
        {"trends": signal},
        state={
            "input": {
                "channel": "site_article", "seed_keyword": "kettles",
                "gsc_domain": "sc-domain:example.com", "context_text": "a thread",
            },
            "working": {},
        },
    )

    assert signal.contexts == [{
        "seed_keyword": "kettles",
        "context_text": "a thread",
        "site_url": "sc-domain:example.com",
        "channel": "site_article",
    }]


def test_one_signal_failing_costs_only_that_signal():
    working = _analyze({"good": StubSignal({"summary": "fine"}), "bad": StubSignal(fail=True)})

    assert working["signals"]["good"] == {"summary": "fine", "facts": {}, "items": []}
    errors = [e for e in working["tool_errors"] if e["tool"] == "bad"]
    assert len(errors) == 1
    assert errors[0]["node"] == "analyze"
    assert "exploded" in errors[0]["message"]


def test_a_malformed_result_is_recorded_as_a_tool_error_not_a_crash():
    working = _analyze({"weird": StubSignal(42)})

    assert working["signals"]["weird"] == empty_signal()
    assert [e["tool"] for e in working["tool_errors"]] == ["weird"]


@pytest.mark.parametrize(
    ("name", "signal"),
    [("quiet", StubSignal(None)), ("broken", StubSignal(fail=True)), ("weird", StubSignal(42))],
)
def test_every_configured_signal_gets_a_key_whatever_happened_to_it(name, signal):
    """The keys of working.signals are a function of the *config*, not of what the
    run managed to fetch — which is what lets a tenant template say
    `{{ signals.rank_tracker.facts.tracked }}` and mean the same thing every time,
    and what makes validating that template at save time sound."""
    working = _analyze({name: signal})

    assert working["signals"] == {name: empty_signal()}


def test_a_failing_signal_never_blocks_the_built_in_ones():
    working = _analyze({"bad": StubSignal(fail=True)})

    assert working["analytics_summary"]
    assert working["traffic_summary"]


def test_signals_are_collected_concurrently_not_one_after_another():
    """The affordability of N signals is the entire reason this is a gather. A
    sync client is the case that regresses silently — it has to reach the loop
    through async_utils.call, or three signals cost three round-trips."""
    signals = {name: SyncSleepingSignal() for name in ("a", "b", "c")}

    started = time.perf_counter()
    working = _analyze(signals)
    elapsed = time.perf_counter() - started

    assert len(working["signals"]) == 3
    assert elapsed < 2 * DELAY, f"three signals took {elapsed:.2f}s; they ran sequentially"


def test_signals_are_collected_concurrently_with_analytics_and_traffic():
    """Not just with each other: analyze_context's whole job is that the
    channel-independent inputs go out together."""

    class SleepingAnalytics:
        def report(self, limit: int = 5) -> dict:
            time.sleep(DELAY)
            return {"summary": "slow analytics", "highlights": []}

    stage = AnalyzeStage(
        _tools({"a": SyncSleepingSignal()}, analytics=SleepingAnalytics()), AgentConfig(),
    )
    state = {"input": {"channel": "engagement_comment", "context_text": "hi"}, "working": {}}

    started = time.perf_counter()
    asyncio.run(stage.run(state))
    elapsed = time.perf_counter() - started

    assert elapsed < 2 * DELAY, f"analytics and one signal took {elapsed:.2f}s; they serialized"


def test_tool_errors_keep_config_order_regardless_of_which_failed_first():
    """gather preserves argument order, so this stays readable even though the
    failures race in wall-clock time."""
    working = _analyze({"first": StubSignal(fail=True), "second": StubSignal(fail=True)})

    assert [e["tool"] for e in working["tool_errors"]] == ["first", "second"]


# --- both graph shapes agree ------------------------------------------------


def test_the_analyze_context_node_collects_signals_too():
    stage = AnalyzeContextStage(_tools({"trends": StubSignal({"summary": "up"})}), AgentConfig())

    context = asyncio.run(stage.run({"input": {}, "working": {}}))["analyze_context"]

    assert context["signals"] == {"trends": {"summary": "up", "facts": {}, "items": []}}


def test_analyze_folds_the_precomputed_signals_in_without_recollecting():
    stage = AnalyzeStage(_tools({"trends": StubSignal(fail=True)}), AgentConfig())
    state = {
        "input": {"channel": "engagement_comment", "context_text": "hi"},
        "working": {},
        "analyze_context": {
            "analytics_summary": "s", "analytics_highlights": [], "traffic_summary": "t",
            "signals": {"trends": {"summary": "precomputed", "facts": {}, "items": []}},
            "tool_errors": [],
        },
    }

    working = asyncio.run(stage.run(state))["working"]

    # The signal raises if called; getting a value back proves it wasn't.
    assert working["signals"]["trends"]["summary"] == "precomputed"
    assert working["tool_errors"] == []


def test_an_analyze_context_built_by_hand_without_signals_still_works():
    """A caller driving the stages directly predates this field; omitting it must
    not crash them."""
    stage = AnalyzeStage(_tools(), AgentConfig())
    state = {
        "input": {"channel": "engagement_comment", "context_text": "hi"},
        "working": {},
        "analyze_context": {
            "analytics_summary": "s", "analytics_highlights": [], "traffic_summary": "t",
            "tool_errors": [],
        },
    }

    assert asyncio.run(stage.run(state))["working"]["signals"] == {}


def test_the_same_signals_arrive_with_and_without_a_discovery_pipeline():
    """The two graph shapes run different code to get here (AnalyzeContextStage as
    its own node vs. AnalyzeStage inline), and a signal must not be able to tell
    them apart — this is what collect_context being one function buys."""
    def signals():
        return {"trends": StubSignal({"summary": "up 12%", "facts": {"pct": 12}})}

    def run(discovery_sources):
        config = AgentConfig(discovery_sources=discovery_sources)
        tools = _tools(
            signals(),
            discovery_sources={"d": _MockSource()} if discovery_sources else {},
        )
        state = {
            "run_id": "r", "phase": "queued", "working": {}, "usage": {"tokens": 0},
            "input": {"channel": "site_article", "seed_keyword": "kettles",
                      "gsc_domain": "sc-domain:example.com", "params": {}},
        }
        return asyncio.run(build_graph(tools, config).ainvoke(state))

    without = run([])
    with_discovery = run([{"name": "d", "provider": "mock"}])

    assert without["working"]["signals"] == with_discovery["working"]["signals"]
    assert without["working"]["signals"]["trends"]["facts"] == {"pct": 12}


class _MockSource:
    def discover(self, context: dict) -> list[dict]:
        return [{"topic": "a topic", "signal_strength": 0.6, "suggested_channel_hint": "site_article"}]


# --- reaching the prompt ----------------------------------------------------


def test_a_configured_signal_reaches_the_drafted_prompt_keyed_by_its_name():
    """End to end through a real run: config -> ToolsManager -> collection ->
    prompt. The name is the tenant's, and nothing in this repo knows it."""
    config = AgentConfig(
        signal_sources=[{"name": "competitor_watch", "provider": "mock"}],
    )
    llm = _RecordingLLM()
    built = ToolsManager(config).build_all()
    tools = Tools(
        gsc=built.gsc, analytics=built.analytics, traffic=built.traffic, llm=llm,
        signals=built.signals,
    )

    result = AgentRunner(config, tools=tools).run(
        {"seed_keyword": "pour over kettles", "gsc_domain": "sc-domain:example.com"}
    )

    assert result["phase"] == "done"
    prompt = llm.prompts[-1]
    assert "competitor_watch" in prompt
    # MockSignalSource steers off the seed keyword, so this proves the *run's*
    # context reached the signal and the signal's answer reached the prompt —
    # not just that the name was interpolated.
    assert "interest in pour over kettles is up 12%" in prompt


class _RecordingLLM(MockLLMClient):
    """MockLLMClient, plus the prompts it was sent — what the drafted prompt
    actually contained is the only thing this test is about."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, model: str = None, grounded: bool = False):
        self.prompts.append(prompt)
        return super().generate(prompt, model=model, grounded=grounded)


def test_a_tenant_template_can_reach_into_a_signals_facts():
    """The bag is passed whole rather than unpacked into named variables, so a
    template that knows its own signal can use its structure, not just its prose."""
    from agent.prompts.builder import build_article_prompt

    prompt = build_article_prompt(
        "site_article", "kettles", {}, "", [], "",
        _config_with_template("{{ signals.trends.facts.change_pct }}% and rising"),
        signals={"trends": {"summary": "s", "facts": {"change_pct": 12.0}, "items": []}},
    )

    assert "12.0% and rising" in prompt


def _config_with_template(template: str) -> AgentConfig:
    config = AgentConfig()
    config.prompt_templates = {**config.prompt_templates, "site_article": template}
    return config


def test_a_template_naming_a_configured_signal_validates_at_config_load_time():
    """A tenant's signal name is in their config, so it can be — and is — checked
    against exactly the signals that config builds."""
    AgentConfigLoader().load_dict({
        "signal_sources": [{"name": "rank_tracker", "provider": "mock"}],
        "prompt_templates": {
            "site_article": "{{ signals.rank_tracker.summary }}",
        },
    })


def test_a_template_naming_a_signal_that_is_not_configured_fails_at_save_time():
    """The whole point of checking against the tenant's own names: a typo is
    caught while they're editing, not on the run that needed to work."""
    with pytest.raises(ValueError, match="rank_trakcer|prompt_templates"):
        AgentConfigLoader().load_dict({
            "signal_sources": [{"name": "rank_tracker", "provider": "mock"}],
            "prompt_templates": {"site_article": "{{ signals.rank_trakcer.summary }}"},
        })


def test_a_reserved_name_is_not_offered_to_a_template_as_a_signal():
    """`{"name": "traffic"}` fills Tools.traffic and reaches the prompt as
    traffic_summary — it never becomes a signals key, so a template must not be
    validated as though it did."""
    with pytest.raises(ValueError, match="prompt_templates"):
        AgentConfigLoader().load_dict({
            "signal_sources": [{"name": "traffic", "provider": "mock"}],
            "prompt_templates": {"site_article": "{{ signals.traffic.summary }}"},
        })


def test_a_templates_use_of_a_signals_own_facts_and_items_is_accepted():
    """The keys inside facts/items are the provider's vocabulary, not something
    this system can know — the same reason the templated data providers are
    validated against real data instead of a sample. Accepting any key still
    checks the syntax and the signal name, and a wrong key degrades at run time to
    an empty value in a prompt rather than a failed run."""
    AgentConfigLoader().load_dict({
        "signal_sources": [{"name": "trends", "provider": "mock"}],
        "prompt_templates": {
            "site_article": (
                "{{ signals.trends.facts.anything_at_all }}"
                "{% for row in signals.trends['items'] %}{{ row.whatever }}{% endfor %}"
            ),
        },
    })


def test_no_signals_configured_leaves_the_prompt_exactly_as_it_was():
    """The guard every existing tenant relies on: adding this field changed
    nothing for a config that doesn't use it."""
    from agent.prompts.builder import build_article_prompt

    with_none = build_article_prompt("site_article", "kettles", {}, "", [], "", AgentConfig())

    assert "data sources currently show" not in with_none


# --- configuration ----------------------------------------------------------


def test_a_signal_that_is_not_one_of_the_three_lands_in_tools_signals():
    config = AgentConfig(signal_sources=[{"name": "trends", "provider": "mock"}])

    tools = ToolsManager(config).build_all()

    assert list(tools.signals) == ["trends"]
    assert isinstance(tools.signals["trends"], MockSignalSource)


def test_a_reserved_name_selects_the_built_in_slot_instead_of_adding_a_signal():
    """One list for every input, without a second breaking config change: an entry
    named "traffic" is the same choice `traffic_provider` makes."""
    config = AgentConfig(signal_sources=[{"name": "traffic", "provider": "none"}])

    tools = ToolsManager(config).build_all()

    assert tools.signals == {}
    assert type(tools.traffic).__name__ == "NullTrafficClient"


def test_a_reserved_name_entry_wins_over_the_legacy_field():
    config = AgentConfig(
        traffic_provider="mock",
        signal_sources=[{"name": "traffic", "provider": "none"}],
    )

    assert type(ToolsManager(config).build_traffic()).__name__ == "NullTrafficClient"


def test_the_legacy_fields_keep_working_untouched():
    config = AgentConfig(traffic_provider="none", analytics_provider="mock")

    tools = ToolsManager(config).build_all()

    assert type(tools.traffic).__name__ == "NullTrafficClient"
    assert isinstance(tools.analytics, MockAppAnalyticsClient)


def test_a_reserved_name_gets_the_built_in_kinds_providers_not_the_signal_ones():
    """"templated" means something different for traffic than for a signal, and a
    generic-signal provider name must not be silently accepted for a slot that has
    no such implementation."""
    config = AgentConfig(signal_sources=[{"name": "gsc", "provider": "templated"}])

    with pytest.raises(ValueError, match="Unknown gsc provider"):
        ToolsManager(config).build_gsc()


def test_an_unknown_signal_provider_names_the_entry_it_came_from():
    config = AgentConfig(signal_sources=[
        {"name": "trends", "provider": "mock"},
        {"name": "rankings", "provider": "nope"},
    ])

    with pytest.raises(ValueError, match="rankings"):
        ToolsManager(config).build_signal_sources()


def test_an_entry_with_no_name_is_rejected():
    """A signal reaches the prompt keyed by its name; an unnamed one has no way to
    get there at all."""
    with pytest.raises(ValueError, match=r'signal_sources\[0\] has no "name"'):
        ToolsManager(AgentConfig(signal_sources=[{"provider": "mock"}])).build_signal_sources()


def test_a_duplicate_name_is_rejected_rather_than_silently_shadowing():
    config = AgentConfig(signal_sources=[
        {"name": "trends", "provider": "mock"},
        {"name": "trends", "provider": "mock"},
    ])

    with pytest.raises(ValueError, match="duplicate signal source name 'trends'"):
        ToolsManager(config).build_signal_sources()


def test_a_mock_signal_can_be_told_to_fail(tmp_path):
    config = AgentConfig(signal_sources=[
        {"name": "trends", "provider": "mock", "options": {"fail": True}},
    ])

    signal = ToolsManager(config).build_signal_sources()["trends"]

    with pytest.raises(RuntimeError):
        signal.collect({})


# --- the templated provider -------------------------------------------------


TRENDS_JSON = json.dumps({
    "window": 30,
    "queries": [{"q": "pour over kettle", "volume": 4800}, {"q": "gooseneck kettle", "volume": 2600}],
})


def test_a_templated_signal_maps_a_tenants_own_json(tmp_path):
    config = _tenant(
        tmp_path, files={"data/trends.json": TRENDS_JSON},
        signal_sources=[{
            "name": "trends", "provider": "templated",
            "options": {
                "source": "file",
                "report_path": "data/trends.json",
                "summary_template": "Top query: {{ data.queries[0].q }} over {{ data.window }} days.",
                "facts_template": '{"window_days": {{ data.window }}}',
                "items_template": (
                    "[{% for q in data.queries %}{\"label\": {{ q.q|tojson }}, "
                    "\"value\": {{ q.volume }}}{% if not loop.last %},{% endif %}{% endfor %}]"
                ),
            },
        }],
    )

    signal = ToolsManager(config).build_signal_sources()["trends"]
    result = asyncio.run(signal.collect({}))

    assert result["summary"] == "Top query: pour over kettle over 30 days."
    assert result["facts"] == {"window_days": 30}
    assert result["items"] == [
        {"label": "pour over kettle", "value": 4800},
        {"label": "gooseneck kettle", "value": 2600},
    ]


def test_a_templated_signal_can_read_the_runs_own_context(tmp_path):
    """Unlike an analytics report, a signal is often *about* what this run is going
    after — so its templates get the context, not just the fetched data."""
    config = _tenant(
        tmp_path, files={"data/trends.json": TRENDS_JSON},
        signal_sources=[{
            "name": "trends", "provider": "templated",
            "options": {
                "source": "file", "report_path": "data/trends.json",
                "summary_template": "Rankings for {{ context.seed_keyword }}.",
            },
        }],
    )

    signal = ToolsManager(config).build_signal_sources()["trends"]

    assert asyncio.run(signal.collect({"seed_keyword": "kettles"}))["summary"] == (
        "Rankings for kettles."
    )


def test_a_templated_signal_that_does_not_render_to_json_says_which_option(tmp_path):
    config = _tenant(
        tmp_path, files={"data/trends.json": TRENDS_JSON},
        signal_sources=[{
            "name": "trends", "provider": "templated",
            "options": {
                "source": "file", "report_path": "data/trends.json",
                "summary_template": "ok", "items_template": "not json at all",
            },
        }],
    )

    signal = ToolsManager(config).build_signal_sources()["trends"]

    with pytest.raises(ValueError, match="items_template"):
        asyncio.run(signal.collect({}))


def test_a_templated_signals_report_path_resolves_against_the_tenant_folder(tmp_path):
    config = _tenant(
        tmp_path, files={"data/trends.json": TRENDS_JSON},
        signal_sources=[{
            "name": "trends", "provider": "templated",
            "options": {"source": "file", "report_path": "data/trends.json",
                        "summary_template": "ok"},
        }],
    )

    signal = ToolsManager(config).build_signal_sources()["trends"]

    assert signal.report_path == str(tmp_path / "acme" / "data" / "trends.json")


# --- a tenant's own class ---------------------------------------------------


CUSTOM_SIGNAL = '''
class Signal:
    def __init__(self, config, options=None):
        self.options = options or {}

    def collect(self, context):
        return {"summary": f"from {self.options.get('endpoint')}"}
'''


def test_a_signal_can_be_a_tenants_own_class(tmp_path):
    config = _tenant(
        tmp_path, plugins={"my_signal.py": CUSTOM_SIGNAL},
        signal_sources=[{
            "name": "trends", "provider": "custom", "class": "my_signal:Signal",
            "options": {"endpoint": "https://example.test"},
        }],
    )

    signal = ToolsManager(config).build_signal_sources()["trends"]

    assert signal.options == {"endpoint": "https://example.test"}
    assert signal.collect({})["summary"] == "from https://example.test"


def test_a_custom_signal_error_names_the_entry(tmp_path):
    config = _tenant(
        tmp_path, plugins={"my_signal.py": CUSTOM_SIGNAL},
        signal_sources=[{"name": "trends", "provider": "custom", "class": "missing:Signal"}],
    )

    with pytest.raises(ValueError, match=r"signal_sources\['trends'\]"):
        ToolsManager(config).build_signal_sources()


# --- verbose mode -----------------------------------------------------------


def test_a_signal_is_reported_under_its_own_name(capsys):
    reporter = build_reporter(2, "json")
    tools = observe_tools(_tools({"trends": StubSignal({"summary": "up", "items": [{"a": 1}]})}), reporter)

    asyncio.run(AnalyzeStage(tools, AgentConfig()).run(
        {"input": {"channel": "engagement_comment", "context_text": "hi"}, "working": {}}
    ))

    events = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()]
    collected = [e for e in events if e.get("tool") == "trends" and e["event"] == "tool_end"]
    assert len(collected) == 1
    assert collected[0]["items"] == 1
    assert collected[0]["summary"] == "up"


def test_the_proxy_survives_the_shorthand_return_shapes():
    """The proxy runs before normalize_signal, so it sees a bare string or None
    exactly as the client returned it."""
    reporter = build_reporter(2, "json")
    tools = observe_tools(_tools({"a": StubSignal("bare string"), "b": StubSignal(None)}), reporter)

    working = asyncio.run(AnalyzeStage(tools, AgentConfig()).run(
        {"input": {"channel": "engagement_comment", "context_text": "hi"}, "working": {}}
    ))["working"]

    assert working["signals"]["a"]["summary"] == "bare string"
    assert working["signals"]["b"] == empty_signal()
