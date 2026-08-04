import json
from pathlib import Path


class TrendingSearches:
    """A custom OpportunitySource.

    It reads a local "trending queries" export and turns each row into an
    Opportunity, carrying a channel hint the agent can act on. It stands in for what
    a real trends or keyword-volume API would return — the point is that the agent
    doesn't care where the opportunities come from.

    The contract is one method, discover(context), returning a list of opportunity
    dicts. Each item is validated and normalized for you, so you can't crash the run
    with a slightly-off value.
    """

    def __init__(self, config):
        self._config = config
        self._path = Path("data/trending.json")

    def discover(self, context: dict) -> list[dict]:
        rows = json.loads(self._path.read_text(encoding="utf-8"))
        return [
            {
                "source": "trending_searches",
                "topic": row["query"],
                "signal_strength": min(row["volume"] / 2000, 1.0),
                "intent": "informational",
                "suggested_channel_hint": row["channel"],
                "raw": row,
                "reason": f"{row['volume']} monthly searches for \"{row['query']}\", and rising.",
            }
            for row in rows
        ]
