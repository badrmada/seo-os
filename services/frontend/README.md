# frontend — planned

Not built yet. This folder is a placeholder so the intended structure of the
system is visible; there is no code here.

It is **step 4** of
[the roadmap](../../docs/roadmap.md#4-the-frontend-watching-an-agent-work), and
last on purpose: it is a client of the [gateway](../gateway/) and of nothing
else, so a UI built before that one exists would be a client of a mock.

## What it will be

A UI over the runtime: your agents, their runs, and the drafts they produce.

- **Agents** — see what each one has wired in, edit its config, check it without
  spending an API call (the CLI's `list-specialists` and `check-data`, with a face).
- **Runs** — watch one happen. The runtime writes a state snapshot after every
  step, keyed by `run_id`, precisely so another process can read a run that hasn't
  finished. That's the seam this service is built on.
- **Review** — the part the runtime deliberately doesn't do. Drafts come back with
  self-review notes attached and nothing is published automatically; approving,
  editing and shipping a draft is a human step that wants an interface.

## What it will build against

Two contracts, both already stable:

| | |
|---|---|
| The JSON a run returns, success and failure | [output-schema.md](../seo-agents/docs/output-schema.md) — deliberately frozen |
| Where a run's state is while it's running | [`state_provider`](../seo-agents/docs/configuration.md#where-the-runs-state-is-kept-state_provider) — `file` or `redis` |

It will likely talk to a `gateway` service (HTTP, auth, queueing) rather than to
the runtime directly — see the [repo root](../../README.md#the-repository).

Until then, [`services/seo-agents`](../seo-agents/) is the whole product and its
CLI is the interface.
