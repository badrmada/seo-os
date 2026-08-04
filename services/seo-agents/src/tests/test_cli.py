"""Covers the CLI (PLAN.md Step 6): that every command runs, that a bare
invocation shows help rather than doing work, and that the provider catalog
`list-tools` reads can't drift from the builders it describes."""

import json

import pytest
from typer.testing import CliRunner

from agent.config.agent_config import AgentConfig
from agent.managers import ToolsManager
from agent.managers.providers import CATALOG
from cli.app import COMMAND_NAMES, app

runner = CliRunner()

TENANT = {
    "llm_provider": "mock", "gsc_provider": "mock",
    "traffic_provider": "mock", "analytics_provider": "mock",
    "discovery_sources": [
        {"name": "trends", "provider": "mock"},
        {"name": "forums", "provider": "mock"},
    ],
}
INPUT = {"seed_keyword": "static site seo", "gsc_domain": "sc-domain:example.com"}


@pytest.fixture
def tenant_files(tmp_path):
    tenant = tmp_path / "tenant.json"
    run_input = tmp_path / "input.json"
    tenant.write_text(json.dumps(TENANT))
    run_input.write_text(json.dumps(INPUT))
    return str(tenant), str(run_input)


def _invoke(args):
    return runner.invoke(app, args)


# --- nothing runs unless you ask it to -------------------------------------

def test_a_bare_invocation_shows_help_and_does_no_work():
    """Looking up the help must never execute the agent — every command is
    explicit, and `run` is the only one that does work."""
    result = _invoke([])

    assert "Usage" in result.stdout
    assert "run" in result.stdout
    assert '"phase"' not in result.stdout  # no run result was produced


def test_flags_without_a_command_are_rejected(tenant_files):
    tenant, run_input = tenant_files
    result = _invoke(["--tenant", tenant, "--input", run_input])

    assert result.exit_code != 0
    assert '"phase"' not in result.stdout


# --- commands --------------------------------------------------------------

def test_every_registered_command_has_help():
    assert COMMAND_NAMES == {
        "run", "check-data", "show-graph", "list-tools", "list-specialists", "preview-prompt",
    }
    for name in COMMAND_NAMES:
        result = runner.invoke(app, [name, "--help"])
        assert result.exit_code == 0, name


def test_run_writes_the_result_to_stdout_as_json(tenant_files):
    tenant, run_input = tenant_files
    result = _invoke(["run", "--tenant", tenant, "--input", run_input])
    assert json.loads(result.stdout)["phase"] == "done"


def test_run_output_flag_redirects_to_a_file(tmp_path, tenant_files):
    tenant, run_input = tenant_files
    destination = tmp_path / "result.json"

    result = _invoke(["run", "--tenant", tenant, "--input", run_input, "-o", str(destination)])

    assert result.exit_code == 0
    assert result.stdout.strip() == ""
    assert json.loads(destination.read_text())["phase"] == "done"


def test_run_exits_non_zero_when_the_run_fails(tmp_path):
    """AgentRunner never raises — a failed run is phase="failed" with the full
    result shape. But a CLI that exits 0 on failure can't be scripted around."""
    tenant = tmp_path / "tenant.json"
    tenant.write_text(json.dumps({
        "llm_provider": "mock",
        "analytics_provider": "custom",
        "analytics_custom_class": "no.such.module:Missing",
    }))
    run_input = tmp_path / "input.json"
    run_input.write_text(json.dumps(INPUT))

    result = _invoke(["run", "--tenant", str(tenant), "--input", str(run_input)])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["phase"] == "failed"


def test_show_graph_needs_no_tools_and_names_every_stage(tenant_files):
    tenant, _ = tenant_files
    result = _invoke(["show-graph", "--tenant", tenant])

    assert result.exit_code == 0
    for stage in ("discover_source", "discover_join", "choose_channel",
                  "analyze_context", "analyze", "draft", "self_qa"):
        assert stage in result.stdout


def test_show_graph_reflects_a_zero_discovery_config(tmp_path):
    tenant = tmp_path / "tenant.json"
    tenant.write_text(json.dumps({"llm_provider": "mock"}))

    result = _invoke(["show-graph", "--tenant", str(tenant)])

    assert "discover" not in result.stdout.split("No discovery sources")[0]
    assert "analyze" in result.stdout


def test_show_graph_mermaid(tenant_files):
    tenant, _ = tenant_files
    result = _invoke(["show-graph", "--tenant", tenant, "--format", "mermaid"])
    assert result.exit_code == 0
    assert "graph TD" in result.stdout


def test_show_graph_rejects_an_unknown_format(tenant_files):
    tenant, _ = tenant_files
    assert _invoke(["show-graph", "--tenant", tenant, "-f", "svg"]).exit_code == 1


def test_list_tools_works_without_a_tenant_config():
    result = _invoke(["list-tools", "--all"])
    assert result.exit_code == 0
    for kind in CATALOG:
        assert kind.kind in result.stdout


def test_list_specialists_names_the_configured_sources(tenant_files):
    tenant, _ = tenant_files
    result = _invoke(["list-specialists", "--tenant", tenant])
    assert result.exit_code == 0
    assert "trends" in result.stdout and "forums" in result.stdout


def test_check_data_passes_on_a_valid_setup(tenant_files):
    tenant, run_input = tenant_files
    result = _invoke(["check-data", "--tenant", tenant, "--input", run_input])
    assert result.exit_code == 0
    assert "All checks passed" in result.stdout


def test_check_data_reports_a_broken_sink_and_exits_non_zero(tmp_path):
    tenant = tmp_path / "tenant.json"
    tenant.write_text(json.dumps({
        **TENANT, "output_sinks": [{"name": "bad", "provider": "webhook", "options": {}}],
    }))
    run_input = tmp_path / "input.json"
    run_input.write_text(json.dumps(INPUT))

    result = _invoke(["check-data", "--tenant", str(tenant), "--input", str(run_input)])

    assert result.exit_code == 1
    assert "FAIL" in result.stdout


def test_check_data_never_runs_the_pipeline(tenant_files):
    """It validates and builds; it must not draft. A drafted run would print the
    result JSON, so its absence is the check."""
    tenant, run_input = tenant_files
    result = _invoke(["check-data", "--tenant", tenant, "--input", run_input])
    assert '"phase"' not in result.stdout


# --- errors are CLI errors, not tracebacks ---------------------------------

def test_a_missing_file_is_a_clean_error(tmp_path):
    result = _invoke(["run", "--tenant", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_an_unknown_command_is_rejected():
    assert _invoke(["frobnicate"]).exit_code != 0


# --- the provider catalog can't drift from the builders --------------------

@pytest.mark.parametrize("kind", [k for k in CATALOG if not k.is_list], ids=lambda k: k.kind)
def test_every_catalogued_provider_is_accepted_by_its_builder(kind):
    """list-tools reads agent/managers/providers.py; the builders are still
    if/elif ladders. This asserts they agree, so a provider added to one without
    the other fails here rather than misleading a user."""
    builder = getattr(ToolsManager(AgentConfig()), f"build_{kind.kind}")
    for name in kind.providers:
        manager = ToolsManager(AgentConfig(**{kind.config_field: name}))
        try:
            getattr(manager, f"build_{kind.kind}")()
        except Exception as exc:  # noqa: BLE001 - only the "unknown provider" case matters
            # Other failures are fine and expected here (a "custom" provider with
            # no class configured, a vendor client with no credentials) — the only
            # thing being asserted is that the name itself is recognized.
            assert "Unknown" not in str(exc), f"{kind.kind} builder rejects catalogued {name!r}"
    assert builder is not None


@pytest.mark.parametrize("kind", [k for k in CATALOG if not k.is_list], ids=lambda k: k.kind)
def test_an_uncatalogued_provider_name_is_rejected(kind):
    manager = ToolsManager(AgentConfig(**{kind.config_field: "definitely-not-a-provider"}))
    with pytest.raises(Exception, match="Unknown|not a provider|definitely-not"):
        getattr(manager, f"build_{kind.kind}")()
