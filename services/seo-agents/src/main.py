import argparse
import json
from pathlib import Path

from agent.config import AgentConfigLoader
from agent.graph import Tools
from agent.managers import AgentRunner
from state.memory_store import InMemoryStateStore

# Default file names, resolved relative to the current working directory (where you
# run the command) — not to this file's location. So from the project root,
# `python src/main.py` looks for ./tenant.json and ./input.json. Pass --tenant /
# --input to point at any other path; a relative path there is resolved against the
# working directory the same way. Nothing is baked into the script — the run always
# reads the files you actually provide, and fails clearly if they're missing.
DEFAULT_TENANT_FILENAME = "tenant.json"
DEFAULT_INPUT_FILENAME = "input.json"

# Optional in-process override: import this module and set TOOLS to a Tools(...)
# instance to bypass ToolsManager's config-driven provider selection. None (the
# default) means "build the tools from the tenant config."
TOOLS: Tools = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the growth agent once against a tenant config and a run input.",
    )
    parser.add_argument(
        "--tenant",
        default=DEFAULT_TENANT_FILENAME,
        help="Path to the tenant config JSON (default: ./tenant.json in the current directory).",
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_FILENAME,
        help="Path to the run input JSON (default: ./input.json in the current directory).",
    )
    return parser.parse_args()


def _resolve_existing(path_str: str, label: str) -> Path:
    """Turn whatever path was given (a default name or a --flag value) into a full
    absolute path, and fail with a clear message if the file isn't there — rather
    than surfacing a raw FileNotFoundError deeper in the run."""
    path = Path(path_str).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"{label} file not found: {path}")
    return path


def main() -> None:
    args = _parse_args()
    tenant_path = _resolve_existing(args.tenant, "Tenant config")
    input_path = _resolve_existing(args.input, "Input")

    config = AgentConfigLoader().load(str(tenant_path))
    run_input = json.loads(input_path.read_text(encoding="utf-8"))

    store = InMemoryStateStore()  # in-memory only; a single, in-process run
    result = AgentRunner(config, tools=TOOLS).run(run_input, state_store=store)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
