"""CLI entrypoint. The commands themselves live in cli/ — see cli/commands/ to add
one; this file only starts the app.

`python src/main.py` prints help. `python src/main.py run --tenant t.json --input
i.json` runs the agent. Every command is explicit — see docs/cli.md.

The in-process Tools override that used to live here as `main.TOOLS` moved to the
command that uses it — set `cli.commands.run.TOOLS` instead.
"""

from cli import main

if __name__ == "__main__":
    main()
