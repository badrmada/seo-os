class MockOpportunitySource:
    """Deterministic, no-network stand-in for a real OpportunitySource (tools/base.py)
    — used for offline test runs, and (via the `fail` flag) to exercise
    DiscoverStage's degrade-don't-abort handling of a source that raises, without
    needing a real provider to actually fail on demand."""

    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name  # the discovery_sources registry key this instance was built for
        self.fail = fail  # simulate this source raising, to test the caller's degrade path

    def discover(self, context: dict) -> list[dict]:
        if self.fail:
            raise RuntimeError(f"mock opportunity source {self.name!r} configured to fail")
        topic = context.get("seed_keyword") or "your topic"
        return [
            {
                "source": self.name,
                "topic": f"{topic} (mock opportunity from {self.name})",
                "signal_strength": 0.5,
                "intent": "informational",
                "suggested_channel_hint": None,
                "raw": {},
                "reason": f"Canned fixture from MockOpportunitySource({self.name!r}).",
            }
        ]
