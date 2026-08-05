"""Every package must import on its own, first, in a fresh interpreter.

Three circular imports have been introduced and fixed while building this out
(`agent.graph` ↔ `agent.observability`, `agent.config` ↔ `agent.validators`, and
one more), and the test suite caught none of them — because by the time any test
imports the module under test, something else has already pulled the cycle's
other half into `sys.modules` in a lucky order. Only running the CLI found them.

These packages re-export through their `__init__`, so any new intra-package
import can close a loop. Each case below is a subprocess so the import genuinely
starts cold.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]

PACKAGES = [
    "agent.config",
    "agent.graph",
    "agent.managers",
    "agent.observability",
    "agent.prompts",
    "agent.schemas",
    "agent.validators",
    "cli",
    "cli.commands",
    "state.memory_store",
    "tools.base",
    "tools.sinks",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_package_imports_standalone_in_a_cold_interpreter(package):
    result = subprocess.run(
        [sys.executable, "-c", f"import {package}"],
        cwd=SRC, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"importing {package} first fails:\n{result.stderr}"
