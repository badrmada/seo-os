# The command line

```bash
python src/main.py --help
```

| Command | What it does |
|---|---|
| `run` | Run the agent once. The only command that does any work — every other one inspects or validates. |
| `check-data` | Validate the config and input, and check every configured tool builds. No LLM call. |
| `show-graph` | Print the pipeline this config produces. |
| `list-tools` | Every pluggable interface and the providers it accepts, with yours marked. |
| `list-specialists` | What *this* tenant has wired: discovery sources, data providers, output sinks. |
| `preview-prompt` | The exact prompt a draft would send, without sending it. |
| `list-tenants` | What's in the workspace — the answer to "what can I pass to --tenant?" |

## A tenant is a name, not a path

Every command takes `--tenant/-t NAME`, where the name is a folder in the
workspace:

```
userdata/            <- the workspace root
├── acme/            <- --tenant acme
│   ├── tenant.json
│   ├── plugins/
│   ├── templates/
│   ├── data/
│   └── output/
└── globex/
```

```bash
python src/main.py list-tenants
python src/main.py run --tenant acme
```

The workspace root comes from `--userdata/-u`, else `$SEO_AGENT_USERDATA`, else
`./userdata`. A container mounts a volume and sets the environment variable.

`--input/-i` is resolved inside the tenant's folder too — `--input
input.comment.json` means that file next to the tenant's config — and defaults to
`input.json` there. An absolute path is used as-is, for an input generated
somewhere else.

## Every command is explicit

There is no implicit default command. A bare invocation prints help and does
nothing — a CLI that silently starts work when you were only looking for its help
isn't one you can explore safely.

```bash
python src/main.py                                    # prints help
python src/main.py run --tenant acme                  # runs the agent
docker run -v ./userdata:/userdata seo-agent run --tenant acme
```

## The commands worth knowing

**`check-data`** is what to run after editing a config. It reuses the same
validators a real run uses — so it can't pass on something a run would reject —
and additionally *builds* every configured provider, which is where a missing
service-account file or an unimportable custom class shows up. It never drafts.

```
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ check             ┃ status ┃ detail                                   ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ tenant config     │   ok   │ loaded; prompt and data templates render │
│ input             │   ok   │ valid; channel: decided by discovery     │
│ discovery sources │   ok   │ 3 built                                  │
│ output sinks      │  FAIL  │ ValueError: webhook requires options.url │
└───────────────────┴────────┴──────────────────────────────────────────┘
```

It exits non-zero if any check fails, so it works in CI.

**`show-graph`** answers "which stages will actually run?" — which depends
entirely on your config, so it needs no API key and builds no tools.

```
  START
   → discover_source × 3    one branch per source (trends, forums, reddit), run concurrently
   → discover_join          merges every branch's opportunities
   → choose_channel
   ⇢ analyze_context        direct child of START, runs alongside the chain above
   → analyze                waits for: analyze_context
   → draft
   → self_qa
   → END
```

`--format mermaid` prints a diagram you can paste into docs.

## Exit codes

`0` on success, `1` on failure — including a run that finishes with
`phase: "failed"`. The result JSON is still written to the configured sinks
either way; only the exit code reflects the outcome, so `run` can be scripted
around.

## Adding a command

Commands are self-contained modules. Adding one touches nothing that exists.

```
src/cli/
├── app.py             the root Typer app
├── context.py         shared: path resolution, config/input loading, reporter
└── commands/
    ├── __init__.py    the registry — the import list is the command list
    └── <name>.py      one command: a function plus register(app)
```

**1. Write the module.** The function's docstring becomes its help text, and its
type-hinted parameters become its flags — Typer derives the whole interface:

```python
# src/cli/commands/list_channels.py
import typer

from ..context import TENANT_OPTION, load_config


def list_channels(
    tenant: str = TENANT_OPTION,
    verbose: bool = typer.Option(False, "--long", "-l", help="Include descriptions."),
) -> None:
    """List the channels this tenant can draft for."""
    config = load_config(tenant)
    for channel in config.prompt_templates:
        typer.echo(channel)


def register(app: typer.Typer) -> None:
    app.command("list-channels")(list_channels)
```

**2. Add it to the registry** in `src/cli/commands/__init__.py`:

```python
from . import check_data, list_channels, list_specialists, ...

COMMAND_MODULES = (run, check_data, show_graph, list_tools, list_specialists,
                   preview_prompt, list_channels)
```

That's it — `--help`, argument parsing, error formatting, and shell completion
all follow. Reuse `context.py`'s shared options (`TENANT_OPTION`, `INPUT_OPTION`,
`VERBOSE_OPTION`, …) so your command spells the common flags the same way
everything else does, and `context.fail()` so errors print as one line rather
than a traceback.

Registration is explicit rather than discovered by scanning the folder: the
import list *is* the command list, so what the CLI exposes is readable in one
place and can't change because of a stray file. Same reasoning as
config-registered tool plugins.

## See also

- [configuration.md](configuration.md) — every config field.
- [configuration.md#watching-a-run-happen-verbose-mode](configuration.md#watching-a-run-happen-verbose-mode) — `-v`/`-vv`.
- [configuration.md#where-the-result-goes-output-sinks](configuration.md#where-the-result-goes-output-sinks) — output sinks.
