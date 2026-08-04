class NullTrafficClient:
    """SiteTrafficClient for a tenant with no traffic tool at all — traffic_provider=
    "none". Rather than branching around the call site in agent/graph/stages/analyze.py,
    the pipeline always calls tools.traffic.traffic_summary(); this just always returns an empty
    summary, which the prompt template's {% if traffic_summary %} guard skips over."""

    def traffic_summary(self, days: int = 28) -> dict:
        return {"summary": ""}
