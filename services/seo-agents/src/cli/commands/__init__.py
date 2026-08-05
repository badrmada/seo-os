"""The command registry.

Adding a command is two steps and touches nothing else:

  1. Write `src/cli/commands/<name>.py` with a function (its docstring becomes the
     command's help, its type-hinted parameters become its flags) and a
     `register(app)` that attaches it.
  2. Add the module to COMMAND_MODULES below.

Registration is explicit rather than discovered by scanning this folder: the
import list is the command list, so what the CLI exposes is readable in one place
and can't change because of a stray file. It is the same reasoning that keeps
tool plugins config-registered rather than folder-scanned (see PLAN.md Step 3).

Order here is the order commands appear in `--help`, so it runs most-used first
rather than alphabetically.
"""

from . import (
    check_data,
    list_specialists,
    list_tenants,
    list_tools,
    preview_prompt,
    run,
    show_graph,
)

COMMAND_MODULES = (
    run,
    check_data,
    list_tenants,
    show_graph,
    list_tools,
    list_specialists,
    preview_prompt,
)


def register_all(app) -> None:
    for module in COMMAND_MODULES:
        module.register(app)
