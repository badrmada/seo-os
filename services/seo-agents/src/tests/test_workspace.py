"""Covers the tenant workspace (PLAN.md Step 9 Part 1b): one predefined folder
layout per tenant, and plugins loaded from it without touching sys.path."""

import json
import sys

import pytest

from agent.config.workspace import (
    TenantWorkspace,
    UnknownTenantError,
    list_tenants,
    resolve_root,
    validate_name,
)
from agent.managers.plugin_loader import load_custom

PLUGIN_TEMPLATE = '''
class Analytics:
    def __init__(self, config):
        self.config = config

    def report(self, limit: int = 5) -> dict:
        return {{"summary": "{marker}", "highlights": []}}
'''


def make_tenant(root, name, config=None, plugins=None):
    tenant = root / name
    (tenant).mkdir(parents=True, exist_ok=True)
    (tenant / "tenant.json").write_text(json.dumps(config or {"llm_provider": "mock"}))
    for filename, source in (plugins or {}).items():
        (tenant / "plugins").mkdir(exist_ok=True)
        (tenant / "plugins" / filename).write_text(source)
    return TenantWorkspace(root=root, name=name)


# --- the layout ------------------------------------------------------------

def test_a_tenant_is_a_folder_and_everything_derives_from_it(tmp_path):
    workspace = make_tenant(tmp_path / "userdata", "acme")

    assert workspace.dir == tmp_path / "userdata" / "acme"
    assert workspace.config_path.name == "tenant.json"
    assert workspace.plugins_dir.name == "plugins"
    assert workspace.templates_dir.name == "templates"
    assert workspace.data_dir.name == "data"
    assert workspace.output_dir.name == "output"


def test_config_base_dir_becomes_the_tenant_folder(tmp_path):
    """So Part 1's path resolution carries over: "data/analytics.json" in a config
    already means the right file."""
    workspace = make_tenant(tmp_path / "userdata", "acme")
    assert workspace.load_config().config_base_dir == str(workspace.dir)


def test_list_tenants_only_counts_folders_holding_a_config(tmp_path):
    root = tmp_path / "userdata"
    make_tenant(root, "acme")
    make_tenant(root, "globex")
    (root / "scratch").mkdir()

    assert list_tenants(root) == ["acme", "globex"]


def test_opening_an_unknown_tenant_names_the_available_ones(tmp_path):
    root = tmp_path / "userdata"
    make_tenant(root, "acme")

    with pytest.raises(UnknownTenantError, match="acme"):
        TenantWorkspace.open("globex", root=str(root))


# --- the name is a boundary, not a hint ------------------------------------

@pytest.mark.parametrize("name", ["../etc", "..", "a/b", "/etc", "", ".hidden", "a b"])
def test_invalid_tenant_names_are_rejected(name):
    """A tenant name becomes a path segment and, in a server, arrives from a
    request. Rejected outright rather than sanitized into something that looks
    fine and points elsewhere."""
    with pytest.raises(ValueError):
        validate_name(name)


@pytest.mark.parametrize("name", ["acme", "acme-2", "acme_2", "acme.eu", "05-advanced"])
def test_ordinary_tenant_names_are_accepted(name):
    assert validate_name(name) == name


# --- the workspace root ----------------------------------------------------

def test_root_precedence_is_flag_then_env_then_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEO_AGENT_USERDATA", raising=False)
    assert resolve_root() == (tmp_path / "userdata").resolve()

    monkeypatch.setenv("SEO_AGENT_USERDATA", str(tmp_path / "from-env"))
    assert resolve_root() == (tmp_path / "from-env").resolve()

    assert resolve_root(str(tmp_path / "from-flag")) == (tmp_path / "from-flag").resolve()


# --- plugins ---------------------------------------------------------------

def test_a_plugin_loads_from_the_tenants_own_folder(tmp_path):
    workspace = make_tenant(
        tmp_path / "userdata", "acme",
        plugins={"analytics.py": PLUGIN_TEMPLATE.format(marker="acme data")},
    )
    config = workspace.load_config()

    client = load_custom("analytics:Analytics", "analytics_custom_class", config)

    assert client.report()["summary"] == "acme data"


def test_two_tenants_can_use_the_same_plugin_filename(tmp_path):
    """The reason plugins are not loaded by appending to sys.path. Module names
    are process-global, so with sys.path the first import would win and one
    tenant would silently be served the other's code."""
    root = tmp_path / "userdata"
    acme = make_tenant(root, "acme", plugins={"analytics.py": PLUGIN_TEMPLATE.format(marker="acme data")})
    globex = make_tenant(root, "globex", plugins={"analytics.py": PLUGIN_TEMPLATE.format(marker="globex data")})

    acme_client = load_custom("analytics:Analytics", "f", acme.load_config())
    globex_client = load_custom("analytics:Analytics", "f", globex.load_config())

    assert acme_client.report()["summary"] == "acme data"
    assert globex_client.report()["summary"] == "globex data"


def test_loading_a_plugin_never_touches_sys_path(tmp_path):
    before = list(sys.path)
    workspace = make_tenant(
        tmp_path / "userdata", "acme",
        plugins={"analytics.py": PLUGIN_TEMPLATE.format(marker="x")},
    )
    load_custom("analytics:Analytics", "f", workspace.load_config())

    assert sys.path == before


def test_plugins_in_one_tenant_can_import_each_other(tmp_path):
    """The synthetic package is a real package, so a relative import works."""
    workspace = make_tenant(tmp_path / "userdata", "acme", plugins={
        "helpers.py": "MARKER = 'from a helper'\n",
        "analytics.py": (
            "from .helpers import MARKER\n\n"
            "class Analytics:\n"
            "    def __init__(self, config):\n        pass\n\n"
            "    def report(self, limit: int = 5) -> dict:\n"
            "        return {'summary': MARKER, 'highlights': []}\n"
        ),
    })

    client = load_custom("analytics:Analytics", "f", workspace.load_config())
    assert client.report()["summary"] == "from a helper"


def test_a_missing_plugin_says_what_is_available(tmp_path):
    workspace = make_tenant(
        tmp_path / "userdata", "acme",
        plugins={"analytics.py": PLUGIN_TEMPLATE.format(marker="x")},
    )
    with pytest.raises(ValueError, match="available: analytics"):
        load_custom("typo:Analytics", "analytics_custom_class", workspace.load_config())


def test_a_missing_class_in_a_real_plugin_says_so(tmp_path):
    workspace = make_tenant(
        tmp_path / "userdata", "acme",
        plugins={"analytics.py": PLUGIN_TEMPLATE.format(marker="x")},
    )
    with pytest.raises(ValueError, match="no class 'Nope'"):
        load_custom("analytics:Nope", "analytics_custom_class", workspace.load_config())


def test_a_plugins_own_missing_dependency_is_not_reported_as_a_missing_plugin(tmp_path):
    """Otherwise a tenant chasing an uninstalled third-party package is told
    their file doesn't exist."""
    workspace = make_tenant(tmp_path / "userdata", "acme", plugins={
        "analytics.py": "import a_package_that_is_not_installed\n\nclass Analytics: pass\n",
    })
    with pytest.raises(ModuleNotFoundError, match="a_package_that_is_not_installed"):
        load_custom("analytics:Analytics", "f", workspace.load_config())
