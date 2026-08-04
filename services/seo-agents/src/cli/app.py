"""The root command. Commands live one-per-module under commands/ and attach
themselves through `register(app)`; this file owns only the app itself.

Every command is explicit — `python src/main.py` with no arguments prints help
rather than doing anything. Running the agent is `python src/main.py run`. There
is no implicit default command: a CLI that silently executes work when you were
only looking for its help is a CLI you can't explore safely.
"""

from __future__ import annotations

import typer

from .commands import register_all

app = typer.Typer(
    name="seo-agent",
    help="Run and inspect the SEO growth agent.",
    add_completion=True,
    no_args_is_help=True,  # bare invocation shows help; `run` is what runs the agent
    pretty_exceptions_show_locals=False,  # a traceback with every local dumped is
                                          # noise in a CLI, and can print secrets
)

register_all(app)

COMMAND_NAMES = frozenset(command.name for command in app.registered_commands)


def main(argv: list[str] = None) -> None:
    app(args=argv)
