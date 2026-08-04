class MockTrafficClient:
    """Canned, product-neutral traffic text so the agent can reason about growth
    (the goal) before a real traffic tool is wired in — see tools/base.py's
    SiteTrafficClient."""

    def traffic_summary(self, days: int = 28) -> dict:
        return {
            "summary": (
                f"182,000 requests over the last {days} days, 34% from organic "
                "search, trending +6.2% vs. the prior period."
            ),
        }
