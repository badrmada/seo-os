import copy


class InMemoryStateStore:
    """Dict-backed store for a run's state, keyed by run_id. Holds a snapshot after
    every graph node transition so a run's progress is inspectable mid-flight."""

    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}

    def save(self, run_id: str, state: dict) -> None:
        self._runs[run_id] = copy.deepcopy(state)

    def load(self, run_id: str) -> dict :
        state = self._runs.get(run_id)
        return copy.deepcopy(state) if state is not None else None

    def delete(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
