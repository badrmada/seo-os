"""Covers the service layer (PLAN.md Step B): one channel-agnostic entry point
that owns everything around the pipeline — resolve config, build the reporter,
run, emit to sinks — and *returns* the outcome instead of printing it.

The two properties worth stating up front, because everything else follows:

  - **A failed run is a successful request.** It comes back as a RunResult whose
    `run["phase"] == "failed"`, never as an exception. Only a request that could
    not be started at all (unknown tenant, broken sink config) raises.
  - **Nothing is written to the process's file descriptors unless asked for.** A
    server hands in its own streams (or none) and reads the result off the
    RunResult; that is the difference between a library and a CLI.
"""

import asyncio
import io
import json

import pytest

from agent.config.agent_config import AgentConfig
from agent.service import AgentService, RunRequest, RunRequestError

INPUT = {"seed_keyword": "static site seo"}


def _config(**overrides) -> AgentConfig:
    """All-mock, and no sinks unless a test asks for them — so a service test
    can't accidentally write to the terminal running the suite."""
    return AgentConfig(**{"output_sinks": [], **overrides})


def _tenant(root, name, config=None) -> None:
    tenant = root / name
    tenant.mkdir(parents=True, exist_ok=True)
    (tenant / "tenant.json").write_text(json.dumps(config or {"llm_provider": "mock"}))


# --- the request ------------------------------------------------------------


def test_a_request_needs_exactly_one_source_of_config():
    with pytest.raises(RunRequestError):
        RunRequest(input=INPUT)  # neither
    with pytest.raises(RunRequestError):
        RunRequest(tenant="acme", config=_config(), input=INPUT)  # both


# --- the happy path ---------------------------------------------------------


def test_execute_returns_the_documented_run_shape():
    result = AgentService().execute(RunRequest(config=_config(), input=INPUT))

    assert result.ok
    assert result.run["phase"] == "done"
    assert result.run_id
    assert result.run["output"]["content"]
    assert result.failed_sinks == []
    assert result.events == []  # not requested


def test_a_tenant_name_is_resolved_in_the_workspace(tmp_path):
    root = tmp_path / "userdata"
    _tenant(root, "acme", {"llm_provider": "mock", "output_sinks": []})

    result = AgentService().execute(
        RunRequest(tenant="acme", userdata=str(root), input=INPUT)
    )

    assert result.run["phase"] == "done"


def test_an_unknown_tenant_is_a_request_error_not_a_failed_run(tmp_path):
    with pytest.raises(RunRequestError):
        AgentService().execute(
            RunRequest(tenant="nope", userdata=str(tmp_path / "userdata"), input=INPUT)
        )


def test_a_broken_sink_config_fails_before_the_run(tmp_path):
    """A webhook with no url must fail now, not after a full pipeline has spent
    real LLM calls."""
    request = RunRequest(
        config=_config(output_sinks=[{"name": "hook", "provider": "webhook", "options": {}}]),
        input=INPUT,
    )

    with pytest.raises(RunRequestError):
        AgentService().execute(request)


# --- a failed run is still a result -----------------------------------------


def test_a_failed_run_comes_back_as_a_result_not_an_exception():
    # engagement_comment with no context_text: rejected by the input validator,
    # which is a run failure (AgentRunner's boundary), not a request error.
    request = RunRequest(config=_config(), input={"channel": "engagement_comment"})

    result = AgentService().execute(request)

    assert result.ok is False
    assert result.run["phase"] == "failed"
    assert result.run["error"]
    assert result.run["output"] is None


# --- events belong to the run -----------------------------------------------


def test_collected_events_come_back_on_the_result_and_nothing_is_printed(capsys):
    result = AgentService().execute(
        RunRequest(config=_config(), input=INPUT, collect_events=True)
    )

    kinds = [event["event"] for event in result.events]
    assert kinds[0] == "run_start"
    assert kinds[-1] == "run_end"
    assert {"stage_start", "stage_end", "tool_start", "tool_end"} <= set(kinds)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_on_event_receives_events_while_the_run_happens():
    """What an SSE endpoint or a progress row needs — the events as they occur,
    not a list at the end."""
    seen = []
    result = AgentService().execute(
        RunRequest(config=_config(), input=INPUT, on_event=seen.append)
    )

    assert [event["event"] for event in seen] == [event["event"] for event in result.events]
    assert seen[0]["event"] == "run_start"


def test_an_on_event_callback_that_raises_never_fails_the_run():
    """Same rule as every other reporter: a broken progress feed must not turn a
    good run into a failed one."""

    def explode(event):
        raise RuntimeError("subscriber went away")

    result = AgentService().execute(
        RunRequest(config=_config(), input=INPUT, on_event=explode)
    )

    assert result.run["phase"] == "done"


# --- the process's file descriptors are the caller's, not ours --------------


def test_the_stdout_sink_writes_where_the_request_says(capsys):
    stream = io.StringIO()
    request = RunRequest(
        config=_config(output_sinks=[{"name": "stdout", "provider": "json"}]),
        input=INPUT,
        stdout=stream,
    )

    result = AgentService().execute(request)

    assert json.loads(stream.getvalue())["run_id"] == result.run_id
    assert capsys.readouterr().out == ""  # not the process's stdout


def test_a_failing_sink_is_reported_on_the_result_and_can_stay_off_stderr(capsys):
    """A dropped delivery must never be silent — but "not silent" means on the
    result, not necessarily on someone's terminal."""
    request = RunRequest(
        config=_config(output_sinks=[
            {"name": "hook", "provider": "webhook",
             "options": {"url": "http://127.0.0.1:1/nope", "timeout_seconds": 0.5}},
        ]),
        input=INPUT,
        warn_stream=None,
    )

    result = AgentService().execute(request)

    assert result.failed_sinks == ["hook"]
    assert result.run["phase"] == "done"  # a lost delivery never fails a finished run
    assert capsys.readouterr().err == ""


# --- overrides are per-run --------------------------------------------------


def test_output_sinks_override_does_not_leak_into_the_next_run(tmp_path):
    """A request is not a config edit: the tenant's own sinks must be back the
    next time it runs."""
    root = tmp_path / "userdata"
    _tenant(root, "acme", {
        "llm_provider": "mock",
        "output_sinks": [{"name": "archive", "provider": "json",
                          "options": {"path": "output/runs.jsonl", "append": True}}],
    })
    service = AgentService()
    target = tmp_path / "elsewhere.json"

    service.execute(RunRequest(
        tenant="acme", userdata=str(root), input=INPUT,
        output_sinks=[{"name": "one_off", "provider": "json",
                       "options": {"path": str(target)}}],
    ))
    service.execute(RunRequest(tenant="acme", userdata=str(root), input=INPUT))

    assert target.is_file()                               # the override was used
    assert (root / "acme" / "output" / "runs.jsonl").is_file()  # and then forgotten


def test_quiet_beats_a_config_that_turns_verbose_on(capsys):
    request = RunRequest(config=_config(verbose=2), input=INPUT, quiet=True)

    AgentService().execute(request)

    assert capsys.readouterr().err == ""


def test_the_run_deadline_can_be_set_per_request():
    """A deadline is a property of *this* run — a worker's bound, not something a
    tenant has to have written into its config."""
    # Short enough that even the all-mock pipeline overruns it, which is the point:
    # the request's value is what takes effect, not the config's default of 0.
    request = RunRequest(config=_config(), input=INPUT, run_timeout_seconds=0.001)

    result = AgentService().execute(request)

    assert result.ok is False
    assert "run_timeout_seconds" in result.run["error"]


# --- many runs, one process -------------------------------------------------


def test_several_requests_run_concurrently_in_one_process():
    """The reason aexecute() is the real entry point: a server gathers requests
    rather than serializing them."""

    async def both():
        service = AgentService()
        return await asyncio.gather(
            service.aexecute(RunRequest(
                config=_config(), input={**INPUT, "seed_keyword": "first"}, collect_events=True,
            )),
            service.aexecute(RunRequest(
                config=_config(), input={**INPUT, "seed_keyword": "second"}, collect_events=True,
            )),
        )

    first, second = asyncio.run(both())

    assert first.ok and second.ok
    assert first.run_id != second.run_id
    # Each run's events are its own, not one interleaved pile.
    assert {event["run_id"] for event in first.events if "run_id" in event} == {first.run_id}
    assert {event["run_id"] for event in second.events if "run_id" in event} == {second.run_id}
