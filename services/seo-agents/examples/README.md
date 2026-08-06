# Examples

Seven worked examples for fictional products, from the simplest possible setup to
advanced ones that plug in custom code. Each folder is a complete, **runnable**
configuration you can copy and adapt.

Every example runs **fully offline with no API keys** — it uses the built-in
"mock" provider for the AI model, connects no rank source, and reads any data from
small local files. Each README then shows the exact lines to change to **go
live** with real tools (Gemini, Google Search Console, Cloudflare, grounded
discovery).

| # | Product | Kind of business | Teaches |
|---|---|---|---|
| [01](01-starter-acme/) | **Acme** | (placeholder) | The absolute basics: the two files, running, reading the output. |
| [02](02-saas-blog-pingowl/) | **PingOwl** | Developer SaaS | Templated analytics **from a file**, a **custom article prompt**. |
| [03](03-ecommerce-roast-co/) | **Roast & Co.** | E-commerce | Templated analytics with **product-link highlights**, `external_article` for Medium, the self-review catching an issue. |
| [04](04-community-homelabhub/) | **HomelabHub** | Community forum | **Discovery**, the agent **choosing the channel itself**, comment disclosure checks. |
| [05](05-advanced-devboard/) | **DevBoard** | Job board | **Custom Python** analytics + a **custom discovery source**, templated traffic, **two discovery sources** scored together. |
| [06](06-mcp-discovery/) | **Scribe** | AI writing SaaS | Discovery from an **MCP server** — a custom source that's an MCP client (with a tiny stub server, runs offline). |
| [07](07-signal-inputs/) | **Sproutly** | Indoor gardening kits | **`signal_sources`** — adding data sources this project has never heard of (a trends export and a rank tracker), templated and custom. |

*(All brands and `example.com` domains are fictional.)*

## Running an example

First install the project once (from the repo root — see the main
[README](../README.md)):

```bash
cd services/agents-workers
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Then `cd` into an example and run it. Each example ships its own `tenant.json`
and `input.json`, and the app reads those two names from the folder you're in:

```bash
cd examples/02-saas-blog-pingowl
python src/main.py run --userdata examples --tenant <name>
```

Two handy variations:

- **Preview the prompt instead of drafting** — see exactly what gets sent to the
  AI model (this is the clearest way to watch your data flow through your
  templates):

  ```bash
  python src/main.py preview-prompt --userdata examples --tenant <name>
  ```

- **Use a different input file** — some examples include more than one:

  ```bash
  python src/main.py run --userdata examples --tenant <name> --input input.comment.json
  ```

## What's real offline, and what isn't

Because the examples use the **mock AI model**, it helps to know which parts of
the output reflect *your* configuration and which are placeholders:

| Fully yours, offline | Placeholder offline (real once you go live) |
|---|---|
| Brand voice, goal | The article body / comment text — the **mock AI** writes generic filler around a real topic |
| The target keyword — your `seed_keyword`, since these examples connect no rank source | |
| Analytics summary + highlights (from your data + templates) | |
| Traffic summary (from your data + template) | |
| Which channel discovery picks, and why | |
| Prompt wording (your templates) | |
| Self-review notes | |

Only the **body text** is fake. That's a change worth knowing about if you saw an
earlier version: the target keyword used to come from a canned Search Console
fixture and override whatever you asked for. Now
`search_performance_provider` defaults to `"none"`, so your own `seed_keyword`
drives the run until you connect a real rank source.

So the **rendered prompt** (`preview-prompt`) is the best thing to look at
offline — it's built entirely from your config and data. The full run still
completes end to end so you can see the exact output shape.
