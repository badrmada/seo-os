"""Covers state persistence (PLAN.md Step I): the store is a selectable provider
like every other kind, and a run is never at its mercy.

Two properties everything here is arranged around, because they pull in opposite
directions and both matter:

  - **A store that can't be *built* is a request error.** An unknown provider, a
    custom class that won't import, a folder that can't be created — nothing has
    run yet, so the caller is told before a pipeline spends anything.
  - **A store that can't be *written to* only degrades the run.** By the time the
    terminal snapshot is written the result is already computed; failing then
    would throw away a finished draft over a bookkeeping entry. The degrade is
    recorded (`RunResult.state_errors`), because a degrade nothing records is a
    bug — and a dead store is attempted at most twice, or its timeout would be
    added to the run's wall clock once per super-step.

The Redis tests run against a real server (`SEO_AGENT_TEST_REDIS_URL`, default
localhost) and skip when there isn't one — a store whose client is only ever
mocked is a store nobody has actually run.
"""

import asyncio
import json
import os
import socket
import time
import uuid
from urllib.parse import urlsplit

import pytest

from agent.config.agent_config import AgentConfig
from agent.managers.run_manager import AgentRunner
from agent.managers.state_manager import StateSnapshots, build_state_store
from agent.service import AgentService, RunRequest, RunRequestError
from state.file_store import FileStateStore
from state.memory_store import InMemoryStateStore
from state.redis_store import DEFAULT_URL, RedisStateStore, _without_credentials

INPUT = {"seed_keyword": "static site seo"}
SNAPSHOT = {"run_id": "abc123", "phase": "running", "working": {"chosen_keyword": "seo"}}

REDIS_URL = os.environ.get("SEO_AGENT_TEST_REDIS_URL", DEFAULT_URL)


def _config(**overrides) -> AgentConfig:
    """All-mock, and no sinks — a state test must not print to the terminal
    running the suite."""
    return AgentConfig(**{"output_sinks": [], **overrides})


# --- the stores themselves --------------------------------------------------


def test_the_memory_store_round_trips():
    store = InMemoryStateStore()
    store.save("run-1", SNAPSHOT)

    assert store.load("run-1") == SNAPSHOT
    assert store.load("never-seen") is None

    store.delete("run-1")
    assert store.load("run-1") is None


def test_the_memory_store_copies_rather_than_aliasing():
    """It holds a *snapshot*. Handing back the live dict would let a caller edit
    the run's state, and let the run edit what a caller already read."""
    store = InMemoryStateStore()
    state = {"phase": "running", "working": {}}
    store.save("run-1", state)

    state["working"]["mutated"] = True
    loaded = store.load("run-1")
    loaded["phase"] = "tampered"

    assert store.load("run-1") == {"phase": "running", "working": {}}


def test_the_file_store_writes_one_file_per_run(tmp_path):
    store = FileStateStore(str(tmp_path / "state"))
    store.save("run-1", SNAPSHOT)

    written = tmp_path / "state" / "run-1.json"
    assert json.loads(written.read_text()) == SNAPSHOT
    assert store.load("run-1") == SNAPSHOT
    assert store.load("never-seen") is None

    store.delete("run-1")
    assert not written.exists()
    store.delete("run-1")  # deleting an absent run is not an error


def test_the_file_store_creates_its_folder_when_it_is_built(tmp_path):
    """Built before the run, like a sink: a folder that can't be created should
    fail while the request is being set up, not after a pipeline has run."""
    FileStateStore(str(tmp_path / "deep" / "nested" / "state"))
    assert (tmp_path / "deep" / "nested" / "state").is_dir()


def test_the_file_store_replaces_a_snapshot_atomically(tmp_path):
    """A snapshot is overwritten several times per run and read by something
    watching it happen — a half-written file is exactly what a plain write hands
    that reader, and only under load."""
    store = FileStateStore(str(tmp_path))
    store.save("run-1", {"phase": "queued"})
    store.save("run-1", {"phase": "done"})

    assert store.load("run-1") == {"phase": "done"}
    assert [p.name for p in tmp_path.iterdir()] == ["run-1.json"]  # no temp file left


@pytest.mark.parametrize("run_id", ["../escape", "nested/run", "", ".hidden", "a" * 200])
def test_the_file_store_refuses_a_run_id_that_is_not_a_filename(tmp_path, run_id):
    """`run_id` comes from the caller (AgentInput.run_id), so it is checked rather
    than trusted before it becomes a path."""
    store = FileStateStore(str(tmp_path))
    with pytest.raises(ValueError, match="run_id"):
        store.save(run_id, SNAPSHOT)


def test_the_file_store_will_not_follow_a_symlink_out_of_its_folder(tmp_path):
    """Containment is checked after resolving, not on the string: `..` and
    separators are already rejected above, but a symlink only shows up once the
    path is real."""
    outside = tmp_path / "outside"
    outside.mkdir()
    store = FileStateStore(str(tmp_path / "state"))
    (tmp_path / "state" / "escape.json").symlink_to(outside / "escape.json")

    with pytest.raises(ValueError, match="outside"):
        store.save("escape", SNAPSHOT)


# --- provider selection -----------------------------------------------------


def test_memory_is_the_default_store():
    assert isinstance(build_state_store(AgentConfig()), InMemoryStateStore)


def test_a_file_stores_folder_resolves_against_the_tenants_own_directory(tmp_path):
    """Same rule as every other tenant path: two tenants that both say "state"
    get their own, whatever directory the process was started from."""
    tenant = tmp_path / "acme"
    tenant.mkdir()
    config = AgentConfig(
        config_base_dir=str(tenant), state_provider="file", state_options={"path": "state"},
    )

    assert build_state_store(config).directory == (tenant / "state").resolve()


def test_a_custom_store_is_loaded_like_every_other_custom_provider():
    config = AgentConfig(
        state_provider="custom",
        state_custom_class=f"{__name__}:RecordingStore",
        state_options={"label": "mine"},
    )

    store = build_state_store(config)

    assert store.label == "mine"


# --- the guard around it ----------------------------------------------------


def test_a_store_failure_never_fails_the_run():
    """The whole point of the step's second constraint. Before the guard existed,
    a save() raising propagated out of the runner and turned a run that had
    produced a good draft into phase="failed"."""
    result = AgentService().execute(RunRequest(
        config=_config(state_provider="custom", state_custom_class=f"{__name__}:ExplodingStore"),
        input=INPUT,
    ))

    assert result.run["phase"] == "done"
    assert result.run["output"]["content"]


def test_a_store_failure_is_recorded_on_the_result():
    """A degrade nothing records is a bug: without this, a store that has been
    dead for a week looks exactly like one that is working."""
    result = AgentService().execute(RunRequest(
        config=_config(state_provider="custom", state_custom_class=f"{__name__}:ExplodingStore"),
        input=INPUT,
    ))

    assert result.state_errors == ["save: RuntimeError: state store is down"]


def test_a_dead_store_is_attempted_twice_and_no_more():
    """save() runs after every super-step, so a store that times out would add its
    timeout to the run once per stage. After the first failure the intermediate
    saves are skipped — but the terminal one is always attempted, since it is the
    snapshot anything watching actually needs and the store may have come back."""
    store = ExplodingStore(AgentConfig())

    run = AgentRunner(_config()).run(INPUT, state_store=store)

    assert run["phase"] == "done"
    assert store.attempts == 2


def test_a_healthy_store_sees_every_super_step_and_then_the_result():
    store = RecordingStore(AgentConfig())

    run = AgentRunner(_config()).run(INPUT, state_store=store)

    phases = [state["phase"] for _, state in store.saved]
    assert phases[0] == "queued"
    assert len(store.saved) > 2                     # mid-flight, not just at the end
    assert store.saved[-1][1] == run                # ...and the last one is the result
    assert "working" not in store.saved[-1][1]      # the result, not the raw graph state
    assert "working" in store.saved[-2][1]          # which the ones before it are


def test_a_failed_run_still_writes_its_terminal_snapshot():
    """A failure is the thing anything watching this run most needs to see, so it
    is written even when the store has been failing all along."""
    store = RecordingStore(AgentConfig())

    run = AgentRunner(_config()).run({"channel": "not-a-channel"}, state_store=store)

    assert run["phase"] == "failed"
    assert store.saved[-1][1]["phase"] == "failed"


def test_an_async_store_is_awaited_and_a_sync_one_is_not_run_on_the_loop():
    """Same contract as every tool Protocol (tools/base.py): a store may be `def`
    or `async def`, and a blocking one goes to a worker thread rather than
    stalling the runs sharing the process."""
    delay = 0.2

    async def scenario():
        started = time.perf_counter()
        snapshots = StateSnapshots(SleepingStore(delay))
        await asyncio.gather(*(snapshots.save(f"run-{i}", SNAPSHOT) for i in range(3)))
        return time.perf_counter() - started

    assert asyncio.run(scenario()) < 2 * delay


def test_the_store_is_closed_when_the_run_is_over():
    """A store built per request holds its connections for one request. Without
    this a server leaks a pool per run, which it notices long after the cause."""
    config = _config(state_provider="custom", state_custom_class=f"{__name__}:ClosingStore")

    AgentService().execute(RunRequest(config=config, input=INPUT))

    assert ClosingStore.closed


def test_a_store_that_cannot_be_built_is_a_request_error():
    """The other half of the split: nothing has run yet, so this is a 4xx and not
    a failed run — the same treatment a misconfigured sink gets."""
    with pytest.raises(RunRequestError, match="state store configuration"):
        AgentService().execute(RunRequest(config=_config(state_provider="nope"), input=INPUT))


# --- end to end, on disk ----------------------------------------------------


def test_a_run_leaves_the_result_on_disk_under_its_run_id(tmp_path):
    config = _config(state_provider="file", state_options={"path": str(tmp_path / "state")})

    result = AgentService().execute(RunRequest(config=config, input=INPUT))

    written = tmp_path / "state" / f"{result.run_id}.json"
    assert json.loads(written.read_text()) == result.run


def test_every_snapshot_of_a_real_run_is_json_serializable(tmp_path):
    """The step's first constraint, checked the only way that means anything: a
    real run against a store that actually serializes. A live object anywhere in
    AgentState shows up here as a recorded save failure and nowhere else."""
    config = _config(
        state_provider="file", state_options={"path": str(tmp_path / "state")},
        discovery_sources=[{"name": "trends", "provider": "mock"}],
        signal_sources=[{"name": "rank_tracker", "provider": "mock"}],
    )

    result = AgentService().execute(RunRequest(config=config, input=INPUT))

    assert result.state_errors == []
    assert result.run["phase"] == "done"


# --- Redis, against a real server -------------------------------------------


def _skip_unless_redis() -> str:
    """Probed with a plain socket so the check costs no event loop and no client:
    every Redis test below builds its own store inside its own `asyncio.run`,
    since an async connection pool belongs to the loop that first used it."""
    pytest.importorskip("redis")
    parts = urlsplit(REDIS_URL)
    try:
        socket.create_connection((parts.hostname or "localhost", parts.port or 6379), 1).close()
    except OSError as exc:
        pytest.skip(f"no Redis at {REDIS_URL} ({exc})")
    return f"seo-agent-test:{uuid.uuid4().hex[:8]}:"


def test_the_redis_store_round_trips():
    prefix = _skip_unless_redis()

    async def scenario():
        store = RedisStateStore(REDIS_URL, key_prefix=prefix)
        try:
            await store.save("run-1", SNAPSHOT)
            loaded = await store.load("run-1")
            missing = await store.load("never-seen")
            await store.delete("run-1")
            return loaded, missing, await store.load("run-1")
        finally:
            await store.close()

    loaded, missing, after_delete = asyncio.run(scenario())
    assert loaded == SNAPSHOT
    assert missing is None
    assert after_delete is None


def test_the_redis_stores_ttl_actually_expires_the_key():
    """The one option that would silently do nothing if it were wired up wrong —
    and the difference between a bounded keyspace and one that grows forever."""
    prefix = _skip_unless_redis()

    async def scenario():
        store = RedisStateStore(REDIS_URL, key_prefix=prefix, ttl_seconds=60)
        try:
            await store.save("run-1", SNAPSHOT)
            # Reaching for the client: a TTL isn't observable through the
            # StateStore interface, and asserting it via describe() would only
            # test the sentence, not the key.
            ttl = await store._client.ttl(f"{prefix}run-1")
            await store.delete("run-1")
            return ttl
        finally:
            await store.close()

    assert 0 < asyncio.run(scenario()) <= 60


def test_a_run_lands_in_redis_under_its_run_id():
    prefix = _skip_unless_redis()
    config = _config(
        state_provider="redis",
        state_options={"url": REDIS_URL, "key_prefix": prefix, "ttl_seconds": 300},
    )

    result = AgentService().execute(RunRequest(config=config, input=INPUT))

    async def read_back():
        store = RedisStateStore(REDIS_URL, key_prefix=prefix)
        try:
            snapshot = await store.load(result.run_id)
            await store.delete(result.run_id)
            return snapshot
        finally:
            await store.close()

    assert result.state_errors == []
    assert asyncio.run(read_back()) == result.run


def test_an_unreachable_redis_degrades_the_run_instead_of_failing_it():
    """The failure mode a network-backed store makes routine. Port 1 is reserved
    and refuses instantly, so this costs no timeout."""
    pytest.importorskip("redis")
    config = _config(
        state_provider="redis",
        state_options={"url": "redis://localhost:1/0", "timeout_seconds": 1},
    )

    result = AgentService().execute(RunRequest(config=config, input=INPUT))

    assert result.run["phase"] == "done"
    assert len(result.state_errors) == 1


def test_describe_never_leaks_a_redis_password():
    """`describe()` is printed by check-data and list-tools, and a Redis URL is one
    of the few config values carrying its own password inline."""
    assert _without_credentials("redis://user:hunter2@example.com:6379/0") == (
        "redis://user:***@example.com:6379/0"
    )
    assert "hunter2" not in RedisStateStore(
        "redis://:hunter2@localhost:6379/0",
    ).describe()


# --- fixtures ---------------------------------------------------------------


class RecordingStore:
    def __init__(self, config, options=None):
        self.label = (options or {}).get("label", "")
        self.saved: list[tuple[str, dict]] = []

    def save(self, run_id: str, state: dict) -> None:
        self.saved.append((run_id, dict(state)))

    def load(self, run_id: str):
        return next((state for saved_id, state in self.saved if saved_id == run_id), None)

    def delete(self, run_id: str) -> None:
        self.saved = [entry for entry in self.saved if entry[0] != run_id]


class ExplodingStore(RecordingStore):
    def __init__(self, config, options=None):
        super().__init__(config, options)
        self.attempts = 0

    def save(self, run_id: str, state: dict) -> None:
        self.attempts += 1
        raise RuntimeError("state store is down")


class ClosingStore(RecordingStore):
    """Async on purpose — the shape a real connection-pooled store takes."""

    closed = False

    async def save(self, run_id: str, state: dict) -> None:
        self.saved.append((run_id, dict(state)))

    async def close(self) -> None:
        ClosingStore.closed = True


class SleepingStore:
    """A blocking store, the shape a tenant's existing sync class has."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def save(self, run_id: str, state: dict) -> None:
        time.sleep(self.delay)
