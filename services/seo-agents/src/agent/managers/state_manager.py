"""Turns `AgentConfig.state_provider` into a concrete state store, and drives it
safely during a run — the counterpart to ToolsManager, for the run-context plane
rather than the tools plane. Output sinks live next door in output_manager.py for
the same reason: neither is something a stage calls.

Two halves, and they exist for different reasons:

  - `build_state_store()` is the provider selection, exactly like every other
    kind — a name in agent/managers/providers.py's `CATALOG`, a factory here, and
    `src/tests/test_providers.py` failing if the two disagree.
  - `StateSnapshots` is the guard around it. A store is the one provider whose
    failures are *routine* (a network hop, after every super-step) and whose
    output nobody is waiting on, so "degrade, record, continue" is not a nicety
    here — before this existed, a `save()` raising propagated out of the runner
    and turned a successful run into `phase="failed"`, throwing away a finished
    draft because a snapshot didn't land.
"""

from state.file_store import FileStateStore
from state.memory_store import InMemoryStateStore
from state.redis_store import DEFAULT_KEY_PREFIX, DEFAULT_URL, RedisStateStore

from ..config.paths import resolve_path
from ..observability import NullReporter
from ..utils.async_utils import call as acall
from .plugin_loader import load_custom


def _file_store(config, options: dict) -> FileStateStore:
    # A folder under the tenant's own directory by default, resolved the way every
    # other tenant path is (agent/config/paths.py) — so two tenants that both
    # say "state" get their own, and the same config means the same folder
    # whatever directory the process was started from.
    return FileStateStore(resolve_path(config, options.get("path", "state")))


def _redis_store(config, options: dict) -> RedisStateStore:
    return RedisStateStore(
        options.get("url", DEFAULT_URL),
        key_prefix=options.get("key_prefix", DEFAULT_KEY_PREFIX),
        ttl_seconds=options.get("ttl_seconds", 0),
        timeout_seconds=float(options.get("timeout_seconds", 5.0)),
    )


# provider name -> (config, options) -> a store satisfying state/base.py's
# StateStore. The keys here are the contract with providers.py's CATALOG; see
# tools_manager.py's module docstring for why that contract is tested rather than
# trusted. "custom" is handled by build_state_store ahead of this table, like a
# sink's is.
_STORE_FACTORIES = {
    "memory": lambda config, options: InMemoryStateStore(),
    "file": _file_store,
    "redis": _redis_store,
}


def build_state_store(config):
    """The single place a state provider is chosen.

    Raises rather than falling back to memory: a tenant who asked for Redis and
    silently got in-process snapshots would find out from an empty dashboard days
    later. A store that can't be *built* is a request error (agent/service.py
    raises `RunRequestError`, as it does for a misconfigured sink); a store that
    can't be *written to* is a degrade — see `StateSnapshots`.
    """
    provider = getattr(config, "state_provider", "memory") or "memory"
    options = getattr(config, "state_options", None) or {}
    if provider == "custom":
        return load_custom(
            getattr(config, "state_custom_class", ""), "state_custom_class", config, options,
        )
    try:
        factory = _STORE_FACTORIES[provider]
    except KeyError:
        raise ValueError(
            f"Unknown state store provider {provider!r}; must be "
            f'{", ".join(sorted(repr(k) for k in _STORE_FACTORIES))}, or "custom"'
        ) from None
    return factory(config, options)


class StateSnapshots:
    """Everything the run does *to* a store, in one place: never fatal, sync or
    async, and bounded when the store is down.

    Three behaviors, each earning its keep:

    - **A failed write degrades the run, never fails it.** By the time the
      terminal snapshot is written the result is already computed and about to be
      returned; failing then would discard work over a bookkeeping entry.
    - **It is recorded.** A degrade nothing records is a bug, not a degrade — so
      the failure lands on the reporter's event stream *and* on `failures`, which
      agent/service.py hands back on `RunResult.state_errors`. Otherwise a store
      that has quietly been dead for a week looks exactly like one that is working.
    - **A dead store costs at most two attempts.** `save()` runs after every
      super-step, so a store that times out would otherwise add its timeout to the
      run's wall clock once per stage. After the first failure the intermediate
      saves are skipped — but the terminal one is always attempted, because it is
      the snapshot anything watching actually needs, and the store may well have
      come back.

    Errors are reported as events but successes are not: at verbose level 1 a
    `tool_start`/`tool_end` pair per super-step would bury the run's actual work
    under bookkeeping, which is the opposite of what verbose mode is for.
    """

    def __init__(self, store=None, reporter=None) -> None:
        self.store = store
        self.reporter = reporter or NullReporter()
        self.failures: list[str] = []
        self._broken = False

    @classmethod
    def wrap(cls, store, reporter=None) -> "StateSnapshots":
        """Accept either a bare store or an already-wrapped one. AgentRunner takes
        `state_store=` from whoever calls it — the service passes the wrapper it
        owns (so it can read `failures` and close the store afterwards), while a
        test or an embedding application passes a plain store and shouldn't have to
        know this class exists."""
        if isinstance(store, cls):
            return store
        return cls(store, reporter)

    @property
    def active(self) -> bool:
        """False when there is no store at all — the runner uses this to pick
        `ainvoke` over `astream`, since streaming exists here only to produce
        snapshots."""
        return self.store is not None

    async def save(self, run_id: str, state: dict, *, final: bool = False) -> None:
        if not self.active or (self._broken and not final):
            return
        if await self._attempt("save", self.store.save, run_id, state) and final:
            flush = getattr(self.store, "flush", None)
            if flush is not None:
                # The one call a batching store needs: save() may buffer, but the
                # terminal snapshot has to be durable by the time the run returns.
                await self._attempt("flush", flush)

    async def close(self) -> None:
        """Release whatever the store holds — a connection pool, a file handle.
        Called by whoever *built* the store, never by AgentRunner, which is handed
        one it doesn't own."""
        close = getattr(self.store, "close", None) if self.active else None
        if close is not None:
            await self._attempt("close", close)

    async def _attempt(self, method: str, fn, *args) -> bool:
        try:
            await acall(fn, *args)
        except Exception as exc:  # noqa: BLE001 - see the class docstring
            self._broken = True
            message = f"{method}: {type(exc).__name__}: {exc}"
            # Deduplicated: a store that is down fails identically after every
            # super-step, and ten copies of one message is a worse record than one.
            if message not in self.failures:
                self.failures.append(message)
            self.reporter.event(
                "tool_error", tool="state_store", method=method, error=message,
            )
            return False
        return True
