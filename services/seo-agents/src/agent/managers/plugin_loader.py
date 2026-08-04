"""The one place a tenant-registered class gets turned into an object — shared by
every `"custom"` provider there is: analytics, traffic, discovery sources, output
sinks, and (PLAN.md Step 8) the state store. One contract, one error message, one
thing to document. Extracted from ToolsManager, which still exposes it as
`_load_custom` for the callers and docs that reference it there.

`class_path` is always `"module.path:ClassName"` (see
docs/extending.md#making-your-module-importable).
"""

import importlib
import inspect


def load_custom(class_path: str, field_name: str, config, options: dict = None):
    """Import and instantiate a tenant's class.

    Constructor contract, in two versions that both work:

      - `__init__(self, config)` — the original, documented in docs/extending.md
        and used by every example under examples/. Still fully supported.
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
        raise ValueError(f'{field_name} must be "module.path:ClassName", got {class_path!r}')
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if options is not None and _accepts_options(cls):
        return cls(config, options)
    return cls(config)


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
