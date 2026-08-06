# gateway — planned

Not built yet. This folder is a placeholder so the intended structure of the
system is visible; there is no code here.

It is **step 2** of
[the roadmap](../../docs/roadmap.md#2-the-gateway-the-api) — after
[an MCP server](../../docs/roadmap.md#1-mcp-the-whole-thing-callable-by-an-llm),
which puts an LLM in front of the runtime for a fraction of the cost, and before
the frontend that calls this. It will be a **FastAPI** application: the runtime is async end to end
and Pydantic-shaped, so a Python gateway *imports* `AgentService` instead of
re-serializing its whole contract across a language boundary. It is also the
first thing in this system that is a long-running *service* rather than a CLI or
a store, which is why
[`deploy/compose/`](../../deploy/compose/) already carries its shape, commented
out: a service, a port, a health check.

## What it will be

The layer between the outside world and the runtime — everything the runtime
deliberately refuses to grow into:

- **An HTTP API** over `AgentService.aexecute()`, which already exists as a
  channel-agnostic entry point for exactly this. The CLI is one adapter over it;
  this would be a second.
- **Dispatch** — deciding what actually executes an accepted run, and how progress
  and results get back from it. In a cluster that is a Job per run; on one host it
  is this process. See
  [step 3](../../docs/roadmap.md#3-dispatch-and-the-data-flow-between-gateway-and-workers).
- **Auth and multi-user isolation.** The runtime already isolates agents from each
  other on disk and in memory; deciding *who may run which* is this layer's job.
- **A queue and workers.** One run is one call today. Scheduling them, retrying
  them, and running many at once belongs here.
- **An approval workflow.** The runtime drafts and never publishes. Turning "a
  human approves every word" into a real review-and-ship loop needs somewhere to
  hold state between the draft and the decision.

## Why it isn't in the runtime

Because a queue that can't be swapped out is worse than no queue. The runtime's
job is to make a run *callable*, observable and durable; owning the transport,
the schedule and the approval policy is a separate concern with different
operational needs.

What it already provides this layer is the seam a queue actually needs: a run
whose state is readable from another process while it's still going
([`state_provider`](../seo-agents/docs/configuration.md#where-the-runs-state-is-kept-state_provider)),
and a [frozen result schema](../seo-agents/docs/output-schema.md) to hand onward.

Notably, **a failed run is a successful request** — it returns a result with
`phase: "failed"`, and only an unrunnable request raises. That distinction is the
one this service would map onto HTTP status codes: the first is a 200 carrying a
failed run, the second a 4xx.

Until this exists, [`services/seo-agents`](../seo-agents/) is the whole product
and its CLI is the interface.
