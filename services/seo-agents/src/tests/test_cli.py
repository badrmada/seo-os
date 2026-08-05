"""Covers the CLI (PLAN.md Steps 6 and 9): that every command runs against a
tenant *workspace*, that a bare invocation shows help rather than doing work,
and that the provider catalog `list-tools` reads can't drift from the builders it
describes."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

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


def make_tenant(root: Path, name: str, config: dict, *, with_input: bool = True) -> Path:
    tenant = root / name
    tenant.mkdir(parents=True, exist_ok=True)
    (tenant / "tenant.json").write_text(json.dumps(config))
    if with_input:
        (tenant / "input.json").write_text(json.dumps(INPUT))
    return root


@pytest.fixture
def workspace(tmp_path):
    """A workspace root holding one tenant, laid out the way a real one is."""
    return make_tenant(tmp_path / "userdata", "acme", TENANT)


def _invoke(args):
    return runner.invoke(app, args)


def _cmd(command, root, *extra, tenant="acme"):
    return _invoke([command, "--userdata", str(root), "--tenant", tenant, *extra])


# --- nothing runs unless you ask it to -------------------------------------

def test_a_bare_invocation_shows_help_and_does_no_work():
    result = _invoke([])

    assert "Usage" in result.stdout
    assert "run" in result.stdout
    assert '"phase"' not in result.stdout


def test_flags_without_a_command_are_rejected(workspace):
    result = _invoke(["--tenant", "acme"])

    assert result.exit_code != 0
    assert '"phase"' not in result.stdout


# --- commands --------------------------------------------------------------

def test_every_registered_command_has_help():
    assert COMMAND_NAMES == {
        "run", "check-data", "list-tenants", "show-graph",
        "list-tools", "list-specialists", "preview-prompt",
    }
    for name in COMMAND_NAMES:
        assert runner.invoke(app, [name, "--help"]).exit_code == 0, name


def test_run_writes_the_result_to_stdout_as_json(workspace):
    assert json.loads(_cmd("run", workspace).stdout)["phase"] == "done"


def test_run_defaults_to_the_tenants_own_input_json(workspace):
    """No --input given: it comes from inside the tenant's folder."""
    assert _cmd("run", workspace).exit_code == 0


def test_input_is_resolved_inside_the_tenant_folder(workspace):
    """A run's inputs live with the tenant, so a bare filename means that file
    next to its config — whatever directory you're standing in."""
    (workspace / "acme" / "input.comment.json").write_text(
        json.dumps({"channel": "engagement_comment", "context_text": "a thread"})
    )
    result = _cmd("run", workspace, "--input", "input.comment.json")

    assert result.exit_code == 0
    assert json.loads(result.stdout)["output"]["kind"] == "comment"


def test_run_output_flag_redirects_to_a_file(tmp_path, workspace):
    destination = tmp_path / "result.json"
    result = _cmd("run", workspace, "-o", str(destination))

    assert result.exit_code == 0
    assert result.stdout.strip() == ""
    assert json.loads(destination.read_text())["phase"] == "done"


def test_run_exits_non_zero_when_the_run_fails(tmp_path):
    root = make_tenant(tmp_path / "userdata", "broken", {
        "llm_provider": "mock",
        "analytics_provider": "custom",
        "analytics_custom_class": "no_such_plugin:Missing",
    })
    result = _cmd("run", root, tenant="broken")

    assert result.exit_code == 1
    assert json.loads(result.stdout)["phase"] == "failed"


def test_show_graph_needs_no_tools_and_names_every_stage(workspace):
    result = _cmd("show-graph", workspace)

    assert result.exit_code == 0
    for stage in ("discover_source", "discover_join", "choose_channel",
                  "analyze_context", "analyze", "draft", "self_qa"):
        assert stage in result.stdout


def test_show_graph_reflects_a_zero_discovery_config(tmp_path):
    root = make_tenant(tmp_path / "userdata", "plain", {"llm_provider": "mock"})
    result = _cmd("show-graph", root, tenant="plain")

    assert "discover" not in result.stdout.split("No discovery sources")[0]
    assert "analyze" in result.stdout


def test_show_graph_mermaid(workspace):
    result = _cmd("show-graph", workspace, "--format", "mermaid")
    assert result.exit_code == 0
    assert "graph TD" in result.stdout


def test_show_graph_rejects_an_unknown_format(workspace):
    assert _cmd("show-graph", workspace, "-f", "svg").exit_code == 1


def test_list_tools_works_without_a_tenant():
    result = _invoke(["list-tools", "--all"])
    assert result.exit_code == 0
    for kind in CATALOG:
        assert kind.kind in result.stdout


def test_list_tools_without_tenant_or_all_says_so():
    result = _invoke(["list-tools"])
    assert result.exit_code == 1


def test_list_specialists_names_the_configured_sources(workspace):
    result = _cmd("list-specialists", workspace)
    assert result.exit_code == 0
    assert "trends" in result.stdout and "forums" in result.stdout


def test_check_data_passes_on_a_valid_setup(workspace):
    result = _cmd("check-data", workspace)
    assert result.exit_code == 0
    assert "All checks passed" in result.stdout


def test_check_data_reports_a_broken_sink_and_exits_non_zero(tmp_path):
    root = make_tenant(tmp_path / "userdata", "acme", {
        **TENANT, "output_sinks": [{"name": "bad", "provider": "webhook", "options": {}}],
    })
    result = _cmd("check-data", root)

    assert result.exit_code == 1
    assert "FAIL" in result.stdout


def test_check_data_never_runs_the_pipeline(workspace):
    """It validates and builds; it must not draft."""
    assert '"phase"' not in _cmd("check-data", workspace).stdout


# --- the workspace ---------------------------------------------------------

def test_list_tenants_shows_every_tenant_in_the_workspace(tmp_path):
    root = tmp_path / "userdata"
    make_tenant(root, "acme", TENANT)
    make_tenant(root, "globex", TENANT)
    (root / "not-a-tenant").mkdir()  # no tenant.json, so not listed

    result = _invoke(["list-tenants", "--userdata", str(root)])

    assert result.exit_code == 0
    assert "acme" in result.stdout and "globex" in result.stdout
    assert "not-a-tenant" not in result.stdout


def test_an_unknown_tenant_lists_the_available_ones(workspace):
    """The most likely first-run mistake, so "no such tenant" alone isn't enough."""
    result = _cmd("run", workspace, tenant="nope")

    assert result.exit_code == 1
    assert "acme" in result.output


@pytest.mark.parametrize("name", ["../etc", "..", "a/b", "/etc", ".hidden", ""])
def test_a_tenant_name_cannot_escape_the_workspace(workspace, name):
    """A tenant name becomes a path segment and, in a server, arrives from a
    request — so it is validated, not sanitized."""
    result = _cmd("run", workspace, tenant=name)
    assert result.exit_code != 0
    assert '"phase"' not in result.stdout


def test_the_workspace_root_can_come_from_the_environment(workspace, monkeypatch):
    monkeypatch.setenv("SEO_AGENT_USERDATA", str(workspace))
    result = _invoke(["run", "--tenant", "acme"])
    assert json.loads(result.stdout)["phase"] == "done"


# --- errors are CLI errors, not tracebacks ---------------------------------

def test_a_missing_workspace_is_a_clean_error(tmp_path):
    result = _invoke(["run", "--userdata", str(tmp_path / "nope"), "--tenant", "acme"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_an_unknown_command_is_rejected():
    assert _invoke(["frobnicate"]).exit_code != 0


# The catalog/builder agreement `list-tools` depends on is pinned in
# src/tests/test_providers.py — it's a property of the provider registry, not of
# the CLI, and asserting it there can be exact (set equality) rather than
# "building each catalogued name doesn't say Unknown".
