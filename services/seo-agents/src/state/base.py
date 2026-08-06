"""What a state store is, and the rules a run holds one to.

A store keeps **observable run snapshots**, keyed by `run_id`: the state after
each of the graph's super-steps while a run is in flight, and the finished result
once it ends. That is what makes a run's progress visible from *outside* the
process running it — a job row, a progress endpoint, a worker asking how far the
last attempt got.

**This is not LangGraph's checkpointer.** `compile(checkpointer=...)` is a
separate mechanism, for *resuming* an interrupted graph, with its own format and
its own guarantees. Nothing here resumes anything, and "we already persist state"
would be a wrong answer to the day resume is genuinely wanted. The two are
deliberately not conflated.

**Every method below may be `def` or `async def`**, exactly like the tool
Protocols in tools/base.py — the framework awaits an async implementation and runs
a sync one in a worker thread (agent/utils/async_utils.py's `call()`), so a store
built on a blocking library and one built on a native async client are both
first-class. `FileStateStore` is sync because file I/O is; `RedisStateStore` is
async because redis.asyncio is.

Three rules an implementation can rely on, and must not break:

  1. **The state it is handed is JSON-serializable.** It holds today (`Channel`
     subclasses `str`, everything else is plain data) and is a constraint on
     `AgentState`, not a hope: no live objects, clients or file handles ever land
     in it. A store may therefore serialize with plain `json.dumps` and let a
     violation surface as the error it is.
  2. **Nothing a store raises fails the run.** Every call goes through
     agent/managers/state_manager.py's `StateSnapshots`, which degrades, records
     and continues. A run that produced a good draft is not failed by a Redis
     outage — and by the time the terminal snapshot is written, the result is
     already computed and about to be returned.
  3. **`save()` runs after every super-step.** Against a remote store that is N
     round-trips on the critical path, so a store is free to batch internally —
     `flush()` (optional) is called with the terminal snapshot, and that snapshot
     always happens.

Retention and multi-writer coordination are deliberately out of scope: snapshots
are keyed by `run_id`, last write wins, one writer per run.
"""

from typing import Protocol


class StateStore(Protocol):
    """Where a run's snapshots go. Selected by `config.state_provider`; built by
    agent/managers/state_manager.py's `build_state_store()`.

    Implementations:
      - state/memory_store.py's `InMemoryStateStore` — the default. A dict, so the
        snapshots live exactly as long as the process does.
      - state/file_store.py's `FileStateStore` — one JSON file per run under the
        tenant's own folder. No infrastructure, survives the process.
      - state/redis_store.py's `RedisStateStore` — one key per run, optionally
        expiring. What a multi-process worker pool wants.
      - `state_provider: "custom"` — a tenant's own class (Postgres, DynamoDB,
        their job table), loaded like every other custom provider.

    Two optional methods, both absent from most stores:
      - `flush()` — called with the terminal snapshot, for a store that batches.
      - `close()` — called by whoever built the store (agent/service.py) once the
        run is over, for a store holding a connection pool.
    """

    def save(self, run_id: str, state: dict) -> None:
        """Write this run's latest snapshot, replacing any earlier one.

        Called after every super-step and once more with the finished result, so
        the last snapshot of a completed run is the same JSON the caller gets back
        (docs/output-schema.md) while the ones before it are the raw graph state,
        `working` and all. Both carry `run_id` and `phase`, which is what a
        progress reader actually needs.
        """
        ...

    def load(self, run_id: str) -> dict | None:
        """The latest snapshot for a run, or None if this store has never seen it.
        Nothing in the agent calls this — it exists for whatever is *watching* the
        run."""
        ...

    def delete(self, run_id: str) -> None:
        """Forget a run. Also never called by the agent: retention belongs to
        whoever owns the store, which is why there is no TTL in the interface (the
        Redis store has one in its own options, where a vendor's mechanism
        belongs)."""
        ...
