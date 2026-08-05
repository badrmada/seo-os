"""The one place a tenant-registered class gets turned into an object — shared by
every `"custom"` provider there is: analytics, traffic, discovery sources, output
sinks, and (PLAN.md Step 8) the state store. One contract, one error message, one
thing to document.

`class_path` is always `"module:ClassName"`, and `module` is a file in that
tenant's `plugins/` folder (see agent/config/workspace.py). Nothing else is
searched, and `PYTHONPATH` plays no part.

**Plugins are deliberately not loaded by adding `plugins/` to `sys.path`.** That
is the obvious implementation and it is wrong for a system that runs several
tenants in one process: module names are process-global, so two tenants that each
have a `plugins/analytics.py` would collide, first import winning — silently
serving one tenant's code to another. Instead each tenant's plugins folder gets
its own synthetic package (`_seo_agent_plugins.<tenant>_<hash>`) whose `__path__`
points at that folder, and modules are imported through it. Two tenants therefore
hold genuinely separate modules, and `sys.path` is never touched.

Because that synthetic parent is a real package, plugin files in one tenant's
folder can import each other with a relative import (`from . import helpers`).
"""

import hashlib
import importlib
import importlib.util
import inspect
import re
import sys
import types
from pathlib import Path

from ..config.workspace import plugins_dir_for

_PLUGIN_NAMESPACE = "_seo_agent_plugins"


def load_custom(class_path: str, field_name: str, config, options: dict = None):
    """Import and instantiate a tenant's class.

    Constructor contract, in two versions that both work:

      - `__init__(self, config)` — the original, documented in docs/extending.md.
      - `__init__(self, config, options)` — opt-in, receiving the provider entry's
        own `options` dict so a class can carry its own settings and secrets
        instead of reading them off the generic AgentConfig.

    Which one a class gets is decided by *inspecting its signature*, not by
    calling it with two arguments and retrying on TypeError. The retry approach
    looks simpler but is subtly wrong: a genuine TypeError raised inside a correct
    two-argument `__init__` would be swallowed, the class re-instantiated with one
    argument, and the user shown a confusing second failure instead of their real
    one.
    """
    if not class_path:
        raise ValueError(f'provider="custom" requires {field_name} to be set')
    module_path, _, class_name = class_path.partition(":")
    if not class_name:
        raise ValueError(f'{field_name} must be "module:ClassName", got {class_path!r}')

    module = _import_plugin_module(module_path, field_name, config)
    try:
        cls = getattr(module, class_name)
    except AttributeError:
        raise ValueError(
            f"{field_name}: {module_path!r} has no class {class_name!r}"
        ) from None

    if options is not None and _accepts_options(cls):
        return cls(config, options)
    return cls(config)


def _import_plugin_module(module_path: str, field_name: str, config):
    """Resolve `module_path` inside this tenant's plugins folder.

    A config with no workspace at all — one built directly in Python, as tests and
    embedding applications do — has no plugins folder, so the name falls back to a
    plain import. That path is an implementation detail for programmatic use, not
    a second way for a tenant to ship code: a tenant always has a workspace, and
    `plugins/` is the only place its classes are looked for.
    """
    plugins_dir = plugins_dir_for(config)
    if plugins_dir is None:
        return importlib.import_module(module_path)

    package = _plugin_package(plugins_dir)
    try:
        return importlib.import_module(f"{package}.{module_path}")
    except ModuleNotFoundError as exc:
        # Only the plugin itself being absent is reportable as "no such plugin";
        # a missing *dependency* of a plugin that did load must surface as itself,
        # or a tenant chasing a missing third-party package is told their file
        # doesn't exist.
        if getattr(exc, "name", "") not in (f"{package}.{module_path}", module_path):
            raise
        available = sorted(p.stem for p in plugins_dir.glob("*.py") if not p.name.startswith("_"))
        raise ValueError(
            f"{field_name}: no plugin {module_path!r} in {plugins_dir} "
            f"(available: {', '.join(available) or 'none'})"
        ) from None


def _plugin_package(plugins_dir: Path) -> str:
    """Create (once) a synthetic package rooted at this tenant's plugins folder.

    The name is derived from the folder so each tenant gets its own, and carries a
    path hash so two tenants named alike in different workspace roots still don't
    share modules.
    """
    resolved = plugins_dir.resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:10]
    label = re.sub(r"\W", "_", resolved.parent.name) or "tenant"
    name = f"{_PLUGIN_NAMESPACE}.{label}_{digest}"

    if name in sys.modules:
        return name

    if _PLUGIN_NAMESPACE not in sys.modules:
        namespace = types.ModuleType(_PLUGIN_NAMESPACE)
        namespace.__path__ = []
        namespace.__doc__ = "Synthetic parent for tenant plugin folders; see plugin_loader.py."
        sys.modules[_PLUGIN_NAMESPACE] = namespace

    package = types.ModuleType(name)
    package.__path__ = [str(resolved)]  # makes submodules importable through it
    package.__doc__ = f"Plugins for the tenant at {resolved.parent}."
    sys.modules[name] = package
    return name


def _accepts_options(cls) -> bool:
    """True if cls's constructor takes a second positional argument beyond config."""
    try:
        parameters = list(inspect.signature(cls).parameters.values())
    except (TypeError, ValueError):  # a C-implemented or otherwise unintrospectable class
        return False
    positional = [
        parameter for parameter in parameters
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) >= 2:
        return True
    return any(parameter.kind is parameter.VAR_POSITIONAL for parameter in parameters)
