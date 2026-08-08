# Examples

Nine worked examples for fictional products, each a complete, **runnable** agent
you can copy and adapt. Together they're a curriculum: 01 through 09 install one
new capability at a time, ending with two agents whose deliverable isn't a draft
at all — one of them not even aimed at search.

Every one runs **fully offline with no API keys** — the built-in mock model, no
rank source connected, data read from small local files. Each README then shows
the exact lines to change to **go live** (Gemini, Google Search Console,
Cloudflare, grounded discovery).

## The path

**Tier 1 — the mechanics.** Twenty minutes, and you can configure an agent for
your own product.

| | Learn | Why here |
|---|---|---|
| **[01 · Acme](01-starter-acme/)** | The two files, running one, reading the result. | Everything else assumes this. Nothing but the mechanics — no real scenario to distract you. |
| **[02 · PingOwl](02-saas-blog-pingowl/)** *developer SaaS* | Templated analytics from a file; a custom `site_article` prompt. | The first real product. Your data reaching a prompt **with no code** is the single highest-value thing here. |
| **[03 · Roast & Co.](03-ecommerce-roast-co/)** *e-commerce* | Highlights that are product links; `external_article`; the self-review catching a real issue. | Same template mechanism, aimed at a different outcome — and a different channel, so you see channels matter. |

**Tier 2 — the agent starts deciding.** This is where it stops being a generator.

| | Learn | Why here |
|---|---|---|
| **[04 · HomelabHub](04-community-homelabhub/)** *community forum* | Discovery on; the agent **picks the channel itself**; disclosure checks on comments. | The first example where you don't say what to write. Leave `channel` out and watch it decide — and explain why. |
| **[05 · DevBoard](05-advanced-devboard/)** *job board* | **Custom Python** analytics and a custom discovery source; templated traffic; two discovery sources scored together. | The jump from template to code, motivated properly: DevBoard needs a week-over-week *growth rate*, which no template can compute. |

**Tier 3 — capabilities we've never heard of.** Where "no fork required" gets
proven rather than claimed.

| | Learn | Why here |
|---|---|---|
| **[06 · Scribe](06-mcp-discovery/)** *AI writing SaaS* | Discovery from an **MCP server**, both ways: `provider: "mcp"` with no code, and a `custom` client — side by side against one stub server. | Your existing tool server becomes a capability. Runs offline; the stub server is included. |
| **[07 · Sproutly](07-signal-inputs/)** *indoor gardening* | **`signal_sources`** — a trends export and a rank tracker, one templated and one custom. Its article prompt is a **template file**. | The open-ended capability. This is the pattern for a backlink API, a competitor watcher, anything. Start here for [recipes.md](../../../docs/recipes.md). |
| **[08 · a site audit](08-custom-pipeline/)** | **A different deliverable** — three stages of your own via `pipelines` + `--agent`. No draft, no channel, `kind: "site_audit"`. | Nothing in `src/` knows this example exists. It's the proof that a skill is a folder. |
| **[09 · a newsletter](09-newsletter/)** *indoor gardening* | **A deliverable that isn't for search at all** — an issue assembled from the signals, with a built-in stage first and two of its own after it. Every link verified against your domain. | Where the signals you already configured pay for themselves twice, and where "a human approves it" stops being a slogan: this one goes to inboxes. |

*(All brands and `example.com` domains are fictional.)*

## Which one is closest to what I need?

| If you want to… | Copy |
|---|---|
| feed the agent your own metrics without writing code | **02**, then **03** for links in the output |
| let it find its own opportunities | **04** |
| compute something a template can't | **05** |
| plug in an MCP server | **06** |
| add a data source that isn't rankings, traffic or analytics | **07** |
| produce something other than an article | **08** |
| send something to your own audience instead of publishing it | **09** |

## Running an example

**Three ways, one run.** They differ in what you need installed, not in what
happens — every one of them ends in the same `run` command against the same
folder. All three are run **from `services/seo-agents/`**.

**Python**, the direct form. Install once (see the service
[README](../README.md#1-install)), then `--userdata examples` makes this folder
the workspace and `--tenant` names the example inside it:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/main.py run --userdata examples --tenant 02-saas-blog-pingowl
```

**Docker**, with nothing installed but Docker. There is no `--userdata` flag here
because the image already sets `SEO_AGENT_USERDATA=/userdata`, so mounting this
folder there *is* the flag:

```bash
docker run --rm -v "$PWD/examples:/userdata" \
  ghcr.io/badrmada/seo-os/seo-agents:latest run --tenant 02-saas-blog-pingowl
```

**Make**, which is the two above with the arguments filled in — the same targets
CI calls, so there is one definition of "run an example" rather than three:

```bash
make example EXAMPLE=02-saas-blog-pingowl
make build && make example EXAMPLE=02-saas-blog-pingowl ENGINE=docker
```

`ENGINE=docker` runs a *local* image, so `make build` comes first unless you have
already pulled one; without either, the Makefile stops and names both fixes
rather than letting Docker fail at the registry.

`make` on its own lists every target. See the
[Makefile](../Makefile) for `build`, `push`, `test` and `run`.

> **Which of these is checked, and how.** The `python src/main.py …` lines
> throughout these READMEs are **executed on every push** by
> [`scripts/check_docs.py`](../../../scripts/check_docs.py) — that is why they can
> be trusted literally. The `make` and `docker run` forms are **not** run
> automatically and are verified by hand when they change: running them in CI
> would mean building an image inside the docs workflow, which is
> [`images.yml`](../../../.github/workflows/images.yml)'s job. Treat a
> `python src/main.py` line as tested and the other two as maintained.

Two notes on the Docker form specifically. The mount must be **writable** —
Python writes `__pycache__` beside a tenant's `plugins/`. And examples that need
more than a config work unchanged: **06** spawns its MCP server as a subprocess
inside the container, and **05/07/08/09** load their plugins from the mounted
folder, because a tenant is a folder either way.

Three variations worth knowing (shown with `python`; each works the same way in
the other two forms):

- **Preview the prompt instead of drafting** — exactly what would be sent to the
  model. Offline, this is the clearest way to watch your data flow through your
  templates:

  ```bash
  python src/main.py preview-prompt --userdata examples --tenant 07-signal-inputs
  ```

- **See which specialists will run** — the pipeline is built from config, so it
  differs per example:

  ```bash
  python src/main.py show-graph --userdata examples --tenant 05-advanced-devboard
  ```

- **Use a different input file** — some examples ship more than one:

  ```bash
  python src/main.py run --userdata examples --tenant 04-community-homelabhub --input input.discover.json
  ```

## What's real offline, and what isn't

Because the examples use the **mock model**, it helps to know which parts of the
output reflect *your* configuration and which are placeholders:

| Fully yours, offline | Placeholder offline (real once you go live) |
|---|---|
| Brand voice, goal | The article body / comment text — the **mock model** writes generic filler around a real topic |
| The target keyword — your `seed_keyword`, since these examples connect no rank source | |
| Analytics summary + highlights (from your data + templates) | |
| Traffic summary (from your data + template) | |
| Every signal's summary, facts and items | |
| Which channel discovery picks, and why | |
| Prompt wording (your templates) | |
| Self-review notes | |

Only the **body text** is fake — and **09** calls no model at all, so its
newsletter is real end to end. That's a change worth knowing about if you saw an
earlier version: the target keyword used to come from a canned Search Console
fixture and override whatever you asked for. Now
`search_performance_provider` defaults to `"none"`, so your own `seed_keyword`
drives the run until you connect a real rank source.

So the **rendered prompt** (`preview-prompt`) is the best thing to look at
offline — it's built entirely from your config and data. The full run still
completes end to end so you can see the exact result schema.

## Then what?

| | |
|---|---|
| Wire in a tool you already pay for | [recipes.md](../../../docs/recipes.md) |
| Every config field | [configuration.md](../docs/configuration.md) |
| Write your own class | [extending.md](../docs/extending.md) |
