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

Commands run against a scratch workspace built from the quickstart's own
instructions (see SCRATCH_TENANTS), so `--tenant acme` is checked rather than
skipped and CI needs no `userdata/` of its own. What is still skipped is named
per command, so the skip set stays visible rather than quietly growing.

    python3 scripts/check_docs.py            # everything
    python3 scripts/check_docs.py --no-run   # links and anchors only (fast, no venv)
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "services" / "seo-agents"

SKIP_DIRS = {".git", "vir", ".venv", "venv", "node_modules", ".pytest_cache", "__pycache__"}

# `userdata/` is gitignored — a real tenant holds real credentials — so a fresh
# clone and CI have no tenants at all. That used to mean every `--tenant acme`
# line in the docs was skipped, and `list-tenants` failed outright with nothing
# to list: this check passed on a laptop and failed in CI, for the same commit.
#
# These are not fixtures standing in for the docs' tenants. They are what the
# quickstart tells the reader to create — an empty `tenant.json` meaning "use the
# built-in fake for everything", plus the input files the docs name — which is
# the only reason running these commands proves anything.
SCRATCH_TENANTS: dict[str, dict[str, str]] = {
    "acme": {
        "tenant.json": "{}",
        "input.json": '{ "channel": "site_article", "seed_keyword": "your topic here" }',
        # A reply needs the conversation it is replying to — `context_text` is
        # required for this channel, exactly as README's second input shows.
        "input.comment.json": (
            '{ "channel": "engagement_comment",'
            ' "context_text": "Why does anonymous feedback make people more honest?" }'
        ),
    },
    # README's "a different one", so `list-tenants` has more than one to list and
    # the workspace isn't a single-tenant special case.
    "globex": {
        "tenant.json": "{}",
        "input.json": '{ "channel": "site_article", "seed_keyword": "another product" }',
    },
}

# A tenant that only exists on one machine, or a literal placeholder. Matched
# against the *value* of `--tenant`, never as a substring of the line: "acme" as
# a substring also skipped `01-starter-acme`, an example this repo does ship.
SKIP_TENANTS = {"echooers", "<name>", "<tenant>"}

# An agent type only one example declares, so it doesn't exist for `acme`.
SKIP_AGENTS = {"site_audit"}

# An ellipsis means the line is prose describing a command, not a command.
ELLIPSIS = "\u2026"

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


def option(args: list[str], flag: str) -> str | None:
    """The value of `--flag`, or None. Only the long `--flag value` form the docs
    actually use; this is not an argument parser."""
    if flag in args:
        i = args.index(flag) + 1
        if i < len(args):
            return args[i]
    return None


def skip_reason(cmd: str) -> str | None:
    """Why this documented line can't be executed here — or None, meaning run it.

    Every reason names the specific thing that is missing, because the skip list
    is the part of this check nobody is verifying: a reason that reads "needs a
    tenant" hides a command that has been broken for a year.
    """
    if ELLIPSIS in cmd:
        return "prose, not a command"
    try:
        args = shlex.split(cmd)[2:]          # drop `python src/main.py`
    except ValueError:
        return "not a parseable command line"

    tenant = option(args, "--tenant")
    if tenant in SKIP_TENANTS:
        return f"--tenant {tenant} is private to one machine, or a placeholder"

    agent = option(args, "--agent")
    if agent in SKIP_AGENTS:
        return f"--agent {agent} is declared by one example, not by this tenant"

    userdata = option(args, "--userdata")
    if userdata and not (SERVICE / userdata).exists():
        return f"--userdata {userdata} is an illustrative path, not one that exists"

    return None


@contextlib.contextmanager
def scratch_workspace():
    """The tenants the quickstart tells a reader to create, in a temp folder.

    Commands that pass their own `--userdata` (the examples) are unaffected —
    that flag wins over the environment variable set here.
    """
    with tempfile.TemporaryDirectory(prefix="check-docs-") as tmp:
        root = Path(tmp) / "userdata"
        for tenant, files in SCRATCH_TENANTS.items():
            (root / tenant).mkdir(parents=True)
            for name, body in files.items():
                (root / tenant / name).write_text(body + "\n", encoding="utf-8")
        yield root


def check_commands(files: list[Path]) -> tuple[list[str], int, list[str]]:
    python = SERVICE / "vir" / "bin" / "python"
    if not python.exists():
        python = SERVICE / ".venv" / "bin" / "python"
    if not python.exists():
        return ["no virtualenv found in services/seo-agents (vir/ or .venv/)"], 0, []

    problems, skipped, seen, ran = [], [], set(), 0
    with scratch_workspace() as workspace:
        env = {**os.environ, "SEO_AGENT_USERDATA": str(workspace)}
        for path, cmd in commands(files):
            if cmd in seen:
                continue
            seen.add(cmd)

            reason = skip_reason(cmd)
            if reason:
                skipped.append(f"{cmd}\n        ({reason})")
                continue

            args = [str(python)] + shlex.split(cmd)[1:]
            result = subprocess.run(
                args, cwd=SERVICE, capture_output=True, text=True, env=env
            )
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
            # Printed in full, with each reason. A skip count on its own is how a
            # command quietly stops being checked.
            print(f"\nskip  commands ({len(skipped)})")
            for s in skipped:
                print(f"      {s}")

    print("\n" + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
