# SEO-OS documentation

Two levels. **This folder** is the product: what SEO-OS is, how it thinks, and how
to wire your own tools into it — true no matter which service you end up running.
**[`services/seo-agents/docs/`](../services/seo-agents/docs/)** is the runtime
reference: every config field, every command, the architecture.

## Start here

| | Read | Time |
|---|---|---|
| **1** | [Root README](../README.md) — what it does, a real result, why it exists | 5 min |
| **2** | [concepts.md](concepts.md) — the model: capabilities, providers, specialists, skills | 15 min |
| **3** | [seo-agents/README.md](../services/seo-agents/README.md) — the quickstart, and a real config explained line by line | 20 min |
| **4** | [recipes.md](recipes.md) — wiring in the tools you already pay for | as needed |

If you'd rather read code than prose, skip to
[examples/](../services/seo-agents/examples/) — eight runnable agents, offline, no
keys.

## By question

| You're asking | Go to |
|---|---|
| What is this and is it for me? | [Root README](../README.md) |
| How does it actually work? | [concepts.md](concepts.md) |
| How do I run it on my product? | [seo-agents/README.md](../services/seo-agents/README.md) |
| What does this config field do? | [configuration.md](../services/seo-agents/docs/configuration.md) |
| How do I connect Ahrefs / my CMS / my rank tracker? | [recipes.md](recipes.md) |
| How do I write my own provider or stage? | [extending.md](../services/seo-agents/docs/extending.md) |
| What JSON does a run return? | [output-schema.md](../services/seo-agents/docs/output-schema.md) |
| What commands are there? | [cli.md](../services/seo-agents/docs/cli.md) |
| Why is it built this way? | [architecture.md](../services/seo-agents/docs/architecture.md) |
| What's built, what's next? | [roadmap.md](../services/seo-agents/docs/roadmap.md) |
| Where does my contribution go? | [CONTRIBUTING.md](../CONTRIBUTING.md) |

## About these docs

Two conventions worth knowing as a reader:

- **Nothing claims a capability that doesn't exist.** Where a page shows an
  integration that isn't shipped — a backlink API, a CMS — it says so and shows
  you the config you'd write. Real field names throughout.
- **Every command has been executed, not proofread.**
  [`scripts/check_docs.py`](../scripts/check_docs.py) runs every documented
  command and resolves every link and anchor, in CI on every pull request. It's
  there because stale commands and renamed headings are invisible to reading —
  they've been caught six times this way and zero times by review.

The plan these docs were written against, including what was deliberately left
out, is [DOCS_PLAN.md](../DOCS_PLAN.md).
