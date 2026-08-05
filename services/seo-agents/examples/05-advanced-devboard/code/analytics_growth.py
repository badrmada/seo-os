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
        # file — resolved against the tenant config's own directory, not the
        # directory you happen to run the command from, so it works from anywhere
        # (and from a server running several tenants at once). config_base_dir is
        # set by AgentConfigLoader; it's empty for a config built in code, which
        # falls back to the old working-directory behavior.
        self._config = config
        self._path = Path(config.config_base_dir or ".") / "data/events.json"

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
