"""Covers output sinks (PLAN.md Step 5): the built-in json/webhook sinks, the
shared custom-class loader they now share with every other provider, and the two
rules that make sinks safe to put after a finished run — build failures are fatal
*before* the run, emit failures are never fatal after it."""

import io
import json

import pytest

from agent.config.agent_config import AgentConfig
from agent.managers.output_manager import OutputManager
from agent.managers.plugin_loader import load_custom
from agent.observability import build_reporter
from tools.sinks import JsonOutputSink, WebhookOutputSink

RESULT = {"run_id": "abc123", "phase": "done", "output": {"title": "A draft"}}


# --- the default is exactly the old behavior -------------------------------

def test_default_config_emits_one_indented_json_document_to_stdout(capsys):
    """The agent has always printed json.dumps(result, indent=2). A zero-config
    tenant must keep getting precisely that, now that a sink produces it."""
    OutputManager(AgentConfig()).emit(RESULT)

    printed = capsys.readouterr().out
    assert printed == json.dumps(RESULT, indent=2) + "\n"


def test_sinks_write_to_stdout_not_stderr(capsys):
    OutputManager(AgentConfig()).emit(RESULT)
    captured = capsys.readouterr()
    assert captured.out
    assert captured.err == ""


# --- json sink -------------------------------------------------------------

def test_json_sink_writes_a_file(tmp_path):
    path = tmp_path / "nested" / "result.json"
    JsonOutputSink(AgentConfig(), {"path": str(path)}).emit(RESULT)
    assert json.loads(path.read_text()) == RESULT


def test_json_sink_append_accumulates_one_run_per_line(tmp_path):
    path = tmp_path / "runs.jsonl"
    sink = JsonOutputSink(AgentConfig(), {"path": str(path), "append": True})
    sink.emit(RESULT)
    sink.emit(RESULT)

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["phase"] == "done" for line in lines)


def test_json_sink_overwrite_does_not_accumulate(tmp_path):
    path = tmp_path / "result.json"
    sink = JsonOutputSink(AgentConfig(), {"path": str(path)})
    sink.emit(RESULT)
    sink.emit(RESULT)
    assert len(path.read_text().splitlines()) == len(json.dumps(RESULT, indent=2).splitlines())


# --- webhook sink ----------------------------------------------------------

def test_webhook_sink_requires_a_url():
    with pytest.raises(ValueError, match="options.url"):
        WebhookOutputSink(AgentConfig(), {})


def test_webhook_sink_describe_never_leaks_credentials():
    sink = WebhookOutputSink(AgentConfig(), {
        "url": "https://example.com/hook",
        "headers": {"Authorization": "Bearer supersecret"},
    })
    assert "supersecret" not in sink.describe()


# --- build-time vs emit-time failure ---------------------------------------

def test_a_broken_sink_config_fails_before_the_run_not_after():
    """Building happens in __init__, so a bad sink is caught before a pipeline
    spends real LLM calls."""
    config = AgentConfig(output_sinks=[{"name": "bad", "provider": "webhook", "options": {}}])
    with pytest.raises(ValueError):
        OutputManager(config)


def test_unknown_sink_provider_fails_fast():
    config = AgentConfig(output_sinks=[{"name": "x", "provider": "ftp"}])
    with pytest.raises(ValueError, match="Unknown output sink provider"):
        OutputManager(config)


def test_a_failing_sink_is_reported_but_never_fatal(tmp_path, capsys):
    """The run is already complete when sinks run — one failing must not raise,
    and must not skip the sinks configured after it."""
    archive = tmp_path / "runs.jsonl"
    config = AgentConfig(output_sinks=[
        {"name": "broken", "provider": "custom", "class": f"{__name__}:ExplodingSink"},
        {"name": "archive", "provider": "json", "options": {"path": str(archive)}},
    ])

    failed = OutputManager(config).emit(RESULT)

    assert failed == ["broken"]
    assert json.loads(archive.read_text()) == RESULT
    assert "broken" in capsys.readouterr().err  # warned, even with verbose off


def test_sink_failures_are_reported_as_events_when_verbose():
    stream = io.StringIO()
    config = AgentConfig(output_sinks=[
        {"name": "broken", "provider": "custom", "class": f"{__name__}:ExplodingSink"},
    ])

    OutputManager(config, reporter=build_reporter(1, "json", stream=stream)).emit(RESULT)

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [e["event"] for e in events] == ["tool_start", "tool_error"]
    assert events[-1]["tool"] == "broken"


def test_verbose_on_does_not_duplicate_the_stderr_warning(capsys):
    config = AgentConfig(output_sinks=[
        {"name": "broken", "provider": "custom", "class": f"{__name__}:ExplodingSink"},
    ])
    OutputManager(config, reporter=build_reporter(1, "json", stream=io.StringIO())).emit(RESULT)
    assert "warning:" not in capsys.readouterr().err


# --- the shared custom-class loader ----------------------------------------

def test_custom_sink_receives_its_own_options():
    config = AgentConfig(output_sinks=[
        {"name": "mine", "provider": "custom", "class": f"{__name__}:RecordingSink",
         "options": {"label": "hello"}},
    ])
    manager = OutputManager(config)
    manager.emit(RESULT)

    sink = manager.sinks[0][1]
    assert sink.label == "hello"
    assert sink.received == [RESULT]


def test_the_original_one_argument_constructor_still_works():
    """docs/extending.md documents __init__(self, config), and every class under
    examples/ relies on it. Adding options must not break them."""
    instance = load_custom(f"{__name__}:LegacyOneArgSink", "x", AgentConfig(), {"ignored": True})
    assert instance.config is not None


def test_a_type_error_from_inside_a_two_arg_constructor_is_not_swallowed():
    """The signature is inspected rather than the call retried on TypeError —
    otherwise a real error inside a correct constructor would be hidden and the
    user shown a confusing second failure instead."""
    with pytest.raises(TypeError, match="deliberate"):
        load_custom(f"{__name__}:ExplodingConstructor", "x", AgentConfig(), {})


def test_custom_class_path_must_name_a_class():
    with pytest.raises(ValueError, match="module:ClassName"):
        load_custom("just_a_module", "some_field", AgentConfig(), {})


def test_custom_provider_requires_a_class_path():
    with pytest.raises(ValueError, match="requires"):
        load_custom("", "some_field", AgentConfig(), {})


# --- fixtures --------------------------------------------------------------

class ExplodingSink:
    def __init__(self, config, options=None):
        pass

    def emit(self, output: dict) -> None:
        raise RuntimeError("sink is down")


class RecordingSink:
    def __init__(self, config, options=None):
        self.label = (options or {}).get("label", "")
        self.received = []

    def emit(self, output: dict) -> None:
        self.received.append(output)


class LegacyOneArgSink:
    """The pre-options constructor shape — must keep working untouched."""

    def __init__(self, config):
        self.config = config

    def emit(self, output: dict) -> None:
        pass


class ExplodingConstructor:
    def __init__(self, config, options=None):
        raise TypeError("deliberate failure inside a correct two-argument constructor")
