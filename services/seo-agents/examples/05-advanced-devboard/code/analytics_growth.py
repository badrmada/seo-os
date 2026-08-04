import json
from pathlib import Path


class GrowthAnalytics:
    """A custom AppAnalyticsClient.

    It computes a 7-day-vs-previous-7-day growth rate from a daily series — a real
    calculation a Jinja2 template can't express. That's exactly when you reach for
    provider="custom" instead of "templated".

    The contract is one method, report(limit), returning
    {"summary": str, "highlights": [{"label": str, "url": str}, ...]}.
    """

    def __init__(self, config):
        # config is the tenant's full AgentConfig. This client also reads a local
        # file, resolved relative to the directory you run the command from.
        self._config = config
        self._path = Path("data/events.json")

    def report(self, limit: int = 5) -> dict:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        counts = [count for _day, count in data["jobs_by_day"]]
        last7, prior7 = sum(counts[-7:]), sum(counts[-14:-7])
        growth = (last7 - prior7) / prior7 if prior7 else 0.0

        summary = (
            f"{last7} job posts in the last 7 days, {growth:+.0%} vs the previous 7 days, "
            f"across {data['companies_hiring']} companies hiring."
        )
        highlights = [
            {
                "label": f"{c['name']} — {c['open_roles']} open roles",
                "url": f"https://devboard.example.com/companies/{c['slug']}",
            }
            for c in data["top_companies"][:limit]
        ]
        return {"summary": summary, "highlights": highlights}
