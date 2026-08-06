# Contributing to SEO-OS

Issues and pull requests are welcome. This page is mostly about one question —
**does your change belong in this repo at all?** — because for a large class of
useful work, the answer is no, and that's by design rather than a brush-off.

## Where does my change go?

| You want to | Goes in | PR needed? |
|---|---|---|
| Connect a tool, add a data source, publish somewhere, change the voice, produce a different deliverable | **your own agent's folder** — `tenant.json`, `plugins/`, `templates/` | **No.** That's the whole point. |
| Add another provider for a capability that already exists | `src/tools/` + the registry | Yes |
| Add a new *kind* of capability | `src/tools/base.py` + registry + docs | Yes, and read [the bar](#adding-a-new-capability-kind) first |
| Fix or improve the docs | wherever it's wrong | Yes, always welcome |

**If you're wiring in a vendor, start with [docs/recipes.md](docs/recipes.md).**
Backlink APIs, rank trackers, MCP servers, CMS publishing and progress UIs all
work today without touching this repo. A PR that hardcodes your vendor into the
runtime will get a suggestion to do it as a `custom` class instead — not because
the contribution isn't wanted, but because the version in your folder ships
immediately, survives upgrades, and doesn't ask everyone else to carry your
dependency.

The exception worth raising an issue about: **if the extension point doesn't
quite reach your case.** That's a real bug in the seams, and it's the most
valuable thing you can report.

## Setup

```bash
cd services/seo-agents
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                     # 438 passing; the Redis ones skip without a server
```

Useful while working, none of which spends an API call:

```bash
python src/main.py list-tools --all                                    # every capability and provider
python src/main.py check-data --userdata examples --tenant 01-starter-acme
python src/main.py run --userdata examples --tenant 08-custom-pipeline  # offline, end to end
```

## Adding a provider

A provider is a class satisfying one interface in
[`src/tools/base.py`](services/seo-agents/src/tools/base.py) (or
`tools/llm/base.py`, `state/base.py`). Every method may be `def` or `async def`.

Three rules, each enforced by a test:

1. **A new provider is a factory in `_REGISTRY` plus a name in `CATALOG`.**
   `src/tests/test_providers.py` fails if the two disagree — so a provider that
   works but isn't catalogued, or is catalogued but can't be built, doesn't merge.
2. **Its settings go in that provider's `options`, never a new top-level config
   field.** Which settings are meaningful depends on which provider is selected;
   top-level fields imply otherwise.
3. **Name the capability after the question it answers, not after a vendor.** This
   was learned the expensive way: `gsc` had to become `search_performance`,
   because a vendor-named capability eventually blocks whoever doesn't use that
   vendor — and it was the only one with no `templated`/`custom` escape hatch.

## Adding a new capability kind

A high bar, on purpose. A new kind means every agent now has one more thing to
reason about, and the alternative is usually `signal_sources` — which exists so
that a new *input* is never a new capability.

Before proposing one, check it isn't one of these:

- **A new input** → a `signal_sources` entry. No new `Tools` field.
- **A new deliverable** → a pipeline with your own stages, and a new `kind` in the
  result. Not a new capability, and not a new top-level result field.
- **A new destination** → an `output_sinks` entry.

If it genuinely is a new kind, read
[extending.md](services/seo-agents/docs/extending.md#adding-a-new-provider-kind-not-just-a-new-instance),
which walks the four places it has to appear.

## Invariants

These are what the shipped work cost to learn. Most are enforced; the ones that
aren't are the expensive ones.

- **A failed run is a successful request.** `phase: "failed"` in a returned
  result; only an unrunnable *request* raises. Nothing in the run-context plane —
  sinks, state store, reporter — may decide a run's outcome.
- **The result schema is frozen.** A new deliverable is a new `kind`, never a new
  top-level field. It's the contract a UI is built on.
- **The sync/async fork lives in exactly one function**
  (`agent/utils/async_utils.py::call()`). A new call site that invokes a client
  directly will stall the event loop for every concurrent run.
- **A new template option must be named `*_template`.** That suffix is what makes
  `{"file": "x.j2"}` work; an option named anything else silently supports inline
  strings only.
- **A degrade that nothing records is a bug, not a degrade.** If you add a
  fallback path, it owes an entry somewhere a non-verbose caller can read.
- **Import cycles are the recurring failure here, and `pytest` doesn't catch
  them** — tests import in a lucky order. Three have shipped and been fixed, every
  one found by running the CLI. Run `src/tests/test_imports.py` (it imports each
  package cold in a subprocess) after any new intra-package import.
- **`test_two_runs_with_different_configs_overlap` is the canary.** It's the only
  test that catches an accidentally-blocking call in the run path; everything else
  passes with runs silently serialized.

The full list, with the story behind each, is in
[`services/seo-agents/PLAN.md`](services/seo-agents/PLAN.md).

## Docs are part of the change

A change isn't done until:

- its config fields are in `docs/configuration.md`,
- its status is in [`docs/roadmap.md`](docs/roadmap.md) — the repo-level one, not
  the service's,
- and any command line you added has been **executed, not proofread**.

That last one isn't pedantry, and it's automated:

```bash
python3 scripts/check_docs.py            # runs every documented command, checks every link and anchor
python3 scripts/check_docs.py --no-run   # links and anchors only — fast, no virtualenv needed
```

It runs in CI on every PR. Extracting every `python src/main.py …` line and
actually executing it has caught stale commands four separate times, including a
`cd` into a directory that never existed — none of which reading ever caught. The
anchor check found two more the day it was written: links pointing at headings
that had been renamed out from under them.

**It runs `python src/main.py …` lines and nothing else.** The `make …` and
`docker run …` forms the examples also document are checked by hand when you
change them — running them here would mean building an image inside the docs
workflow, which [`images.yml`](.github/workflows/images.yml) already does. So if
you touch the [Makefile](services/seo-agents/Makefile) or a documented `docker
run` line, run it yourself; nothing else will. `make` on its own lists every
target, and `make test` is the same command CI runs.

Commands run against a **scratch workspace** the script builds itself — the
`acme` and `globex` tenants exactly as the quickstart tells a reader to create
them (`SCRATCH_TENANTS` in the script). That's there because `userdata/` is
gitignored, so CI has no tenants: without it every `--tenant acme` line went
unchecked and `list-tenants` failed outright, which is how this check used to
pass on a laptop and fail in CI on the same commit. A command that still can't be
run is **printed with the reason** rather than counted, since a silent skip is
how a documented command stops being checked without anyone noticing.

## How a change lands

Fork, branch, open a pull request against `main`. `main` takes no direct pushes —
not from contributors, not from the maintainer.

**A first-time contributor's PR waits for someone to press "approve and run"
before CI starts.** That is a GitHub setting, not a judgement about your patch:
a workflow triggered by a stranger's branch runs that branch's code, so the first
one gets a human glance. Subsequent PRs from you start automatically.

Then three checks have to go green, and they are required — nothing merges
around them:

| Check | Is |
|---|---|
| `tests / seo-agents` | `pytest` on 3.11 |
| `docs / check` | `check_docs.py` — every documented command executed, every link resolved |
| `image (seo-agents)` | the runtime image builds, and `--help` runs inside it |

They all hang off [`images.yml`](.github/workflows/images.yml), which is the one
entry point: the build declares `needs: [tests, docs]`, so a red suite stops the
image rather than racing it. **Your PR builds the image but never publishes it.**
Publishing happens from `main` and `v*` tags only — a fork's PR pushing to this
project's registry is exactly the thing that arrangement prevents, which is also
why the GHCR login step is skipped on pull requests.

Review is by a code owner (currently one person, so expect days rather than
hours), and merges are **squash-only** — one commit per change on `main`, which
is what makes the history readable and the release tags mean something. Your
branch is deleted on merge; nothing is lost, the commit is on `main`.

If a check fails on something that looks unrelated to your change, say so in the
PR rather than pushing empty commits at it. That is a bug in the check, and it is
worth fixing for everyone.

## A note on crawlers

If you contribute anything that fetches other people's sites — an example, a
stage, a signal — it must obey `robots.txt`, rate-limit, cap pages, depth and
total time, send an identifying user agent, and never follow off-site links. A
default that could hammer someone's server is not an acceptable default. It's why
`examples/08-custom-pipeline/` ships a fixture instead of a working crawler.
