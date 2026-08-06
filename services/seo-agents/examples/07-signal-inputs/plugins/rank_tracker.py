import json
from pathlib import Path


class RankTracker:
    """A custom SignalSource — a signal that needs real code rather than a template.

    The contract is one method, `collect(context)`, returning
    `{"summary": str, "facts": dict, "items": list[dict]}`. Only `summary` is
    required; `facts` and `items` are there for a prompt template that knows what
    this particular signal produces.

    What makes this a `custom` signal rather than a `templated` one is the
    *computation*: it classifies each tracked keyword by how far it has to move to
    reach page one, and counts the movers. A Jinja2 template can't express that.

    Note it uses `context` — a signal is often about whatever this run is going
    after, unlike an analytics report, which is just about the site.
    """

    def __init__(self, config, options=None):
        options = options or {}
        # Resolved against the tenant config's own folder, not the directory you
        # happen to run from — same rule every provider follows.
        self._path = Path(config.config_base_dir or ".") / options.get(
            "report_path", "data/rankings.json"
        )
        self._striking_distance = options.get("striking_distance", (11, 20))

    def collect(self, context: dict) -> dict:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        low, high = self._striking_distance

        striking = [
            row for row in data["tracked"] if low <= row["position"] <= high
        ]
        improved = [
            row for row in data["tracked"] if row["position"] < row["previous_position"]
        ]

        summary = (
            f"{len(striking)} tracked keywords sit at positions {low}-{high} — close "
            f"enough to page one that improving an existing page usually beats writing "
            f"a new one. {len(improved)} moved up since the last check."
        )
        seed = context.get("seed_keyword")
        if seed:
            match = next((r for r in data["tracked"] if r["keyword"] == seed), None)
            if match:
                summary += (
                    f' This run\'s keyword "{seed}" is at position {match["position"]} '
                    f'(was {match["previous_position"]}).'
                )

        return {
            "summary": summary,
            "facts": {
                "tracked": len(data["tracked"]),
                "striking_distance": len(striking),
                "improved": len(improved),
                "checked_on": data["checked_on"],
            },
            "items": [
                {
                    "label": row["keyword"],
                    "position": row["position"],
                    "url": row["url"],
                }
                for row in sorted(striking, key=lambda r: r["position"])
            ],
        }
