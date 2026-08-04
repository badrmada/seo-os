import argparse
import json
from pathlib import Path

from agent.config import AgentConfigLoader
from agent.managers import AgentRunner

# Same defaults as main.py: the tenant config and input are read from ./tenant.json
# and ./input.json in the current working directory, or from whatever --tenant /
# --input paths you pass. Use this to preview how a tenant, input, or
# prompt_templates change renders, without running a full draft.
DEFAULT_TENANT_FILENAME = "tenant.json"
DEFAULT_INPUT_FILENAME = "input.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview the rendered prompt for a tenant config and input, without drafting.",
    )
    parser.add_argument("--tenant", default=DEFAULT_TENANT_FILENAME, help="Path to the tenant config JSON (default: ./tenant.json).")
    parser.add_argument("--input", default=DEFAULT_INPUT_FILENAME, help="Path to the run input JSON (default: ./input.json).")
    return parser.parse_args()


def _resolve_existing(path_str: str, label: str) -> Path:
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

    result = AgentRunner(config).preview_prompt(run_input)
    print(f"--- channel: {result['channel']} ---\n")
    print(result["prompt"])


if __name__ == "__main__":
    main()
