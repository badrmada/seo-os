"""The command-line interface (PLAN.md Step 6).

Structure — commands are self-contained modules, so adding one touches nothing
that already exists:

    cli/
    ├── app.py             the root Typer app
    ├── context.py         shared: path resolution, config/input loading, reporter
    └── commands/
        ├── __init__.py    the registry — the import list is the command list
        └── <name>.py      one command: a function plus register(app)

See commands/__init__.py for how to add one.
"""

from .app import app, main

__all__ = ["app", "main"]
