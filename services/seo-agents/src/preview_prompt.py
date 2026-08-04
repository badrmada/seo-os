"""Kept so `python src/preview_prompt.py --tenant t.json --input i.json` keeps
working. The command itself now lives at cli/commands/preview_prompt.py, and is
also reachable as `python src/main.py preview-prompt`."""

import sys

from cli import main

if __name__ == "__main__":
    main(["preview-prompt", *sys.argv[1:]])
