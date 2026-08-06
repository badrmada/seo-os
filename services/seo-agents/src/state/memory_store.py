import copy


class InMemoryStateStore:
    """Dict-backed store for a run's state, keyed by run_id. Holds a snapshot after
    every graph node transition so a run's progress is inspectable mid-flight.

    The default (`state_provider: "memory"`), and the right one for a CLI: the
    snapshots live exactly as long as the process that made them, which is exactly
    as long as anything can read them. Anything that outlives the process — a
    progress endpoint, a worker pool, a job row — wants "file", "redis", or its own
    class instead. See state/base.py for the interface all of them share.
    """

    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}

    def save(self, run_id: str, state: dict) -> None:
        self._runs[run_id] = copy.deepcopy(state)

    def load(self, run_id: str) -> dict | None:
        state = self._runs.get(run_id)
        return copy.deepcopy(state) if state is not None else None

    def delete(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    def describe(self) -> str:
        return f"in this process only; {len(self._runs)} run(s) held"
