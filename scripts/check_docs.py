#!/usr/bin/env python3
"""Check the documentation against reality.

Three checks, in order of how often they've caught something:

1. **Commands run.** Every `python src/main.py …` line in every markdown file is
   executed. This is the only check that has ever caught a stale command — four
   times, including a `cd` into a directory that never existed. Reading does not
   catch these.
2. **Links resolve.** Every relative link points at a file that exists.
3. **Anchors resolve.** Every `#fragment` on a relative link matches a heading in
   the target file. Renaming a heading silently breaks inbound links otherwise.

Commands needing a tenant we don't ship, credentials, or a placeholder are
skipped and listed, so the skip set stays visible rather than quietly growing.

    python3 scripts/check_docs.py            # everything
    python3 scripts/check_docs.py --no-run   # links and anchors only (fast, no venv)
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "services" / "seo-agents"

SKIP_DIRS = {".git", "vir", ".venv", "venv", "node_modules", ".pytest_cache", "__pycache__"}

# A command is skipped when it names something this repo does not ship: a tenant
# that only exists on someone's machine, a credential, or a literal placeholder.
# Keep this list short — every entry is a line nothing verifies.
SKIP_MARKERS = (
    "<name>", "<tenant>", "acme", "globex", "echooers", "my-",
    "--help", "/srv/", "list_channels", "site_audit",
    "\u2026",  # an ellipsis means the line is prose, not a command

)

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CMD_RE = re.compile(r"python src/main\.py [^\n`\"|]*")


def markdown_files() -> list[Path]:
    return sorted(
        p for p in ROOT.rglob("*.md")
        if not SKIP_DIRS & set(p.relative_to(ROOT).parts)
    )


def anchors_of(path: Path) -> set[str]:
    """GitHub's heading-anchor rules, closely enough: lowercase, drop punctuation
    except hyphens and underscores, spaces to hyphens."""
    found = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if in_fence or not line.startswith("#"):
            continue
        text = line.lstrip("#").strip().lower()
        text = re.sub(r"[^\w\s-]", "", text.replace("&", ""))
        found.add(re.sub(r"\s+", "-", text.strip()))
    return found


def check_links(files: list[Path]) -> list[str]:
    problems = []
    anchor_cache: dict[Path, set[str]] = {}

    for path in files:
        text = path.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            target = target.split(" ")[0].strip()
            if target.startswith(("http://", "https://", "mailto:")):
                continue

            file_part, _, anchor = target.partition("#")
            rel = path.relative_to(ROOT)

            if not file_part:                       # same-page anchor
                dest = path
            else:
                dest = (path.parent / file_part).resolve()
                if not dest.exists():
                    problems.append(f"{rel} -> {target} (no such file)")
                    continue

            if anchor and dest.suffix == ".md":
                if dest not in anchor_cache:
                    anchor_cache[dest] = anchors_of(dest)
                if anchor not in anchor_cache[dest]:
                    problems.append(f"{rel} -> {target} (no such heading)")
    return problems


def commands(files: list[Path]) -> list[tuple[Path, str]]:
    found = []
    for path in files:
        for raw in CMD_RE.findall(path.read_text(encoding="utf-8")):
            cmd = re.sub(r"\s+#.*$", "", raw).strip()
            if cmd and cmd != "python src/main.py":
                found.append((path, cmd))
    return found


def check_commands(files: list[Path]) -> tuple[list[str], int, list[str]]:
    python = SERVICE / "vir" / "bin" / "python"
    if not python.exists():
        python = SERVICE / ".venv" / "bin" / "python"
    if not python.exists():
        return ["no virtualenv found in services/seo-agents (vir/ or .venv/)"], 0, []

    problems, skipped, seen, ran = [], [], set(), 0
    for path, cmd in commands(files):
        if cmd in seen:
            continue
        seen.add(cmd)

        if any(m in cmd for m in SKIP_MARKERS):
            skipped.append(cmd)
            continue

        args = [str(python)] + shlex.split(cmd)[1:]
        result = subprocess.run(args, cwd=SERVICE, capture_output=True, text=True)
        ran += 1
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
            problems.append(
                f"{path.relative_to(ROOT)}: `{cmd}` exited {result.returncode}\n"
                + "\n".join(f"      {line}" for line in tail)
            )
    return problems, ran, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-run", action="store_true", help="skip executing commands")
    args = parser.parse_args()

    files = markdown_files()
    print(f"checking {len(files)} markdown files\n")
    failed = False

    link_problems = check_links(files)
    if link_problems:
        failed = True
        print(f"FAIL  links and anchors ({len(link_problems)})")
        for p in link_problems:
            print(f"      {p}")
    else:
        print("ok    links and anchors")

    if args.no_run:
        print("skip  commands (--no-run)")
    else:
        cmd_problems, ran, skipped = check_commands(files)
        if cmd_problems:
            failed = True
            print(f"\nFAIL  commands ({len(cmd_problems)} of {ran} run)")
            for p in cmd_problems:
                print(f"      {p}")
        else:
            print(f"ok    commands ({ran} run)")
        if skipped:
            print(f"      ({len(skipped)} skipped: need a tenant, credentials, or a placeholder)")

    print("\n" + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
