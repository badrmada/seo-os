class MockSignalSource:
    """Deterministic, no-network stand-in for a real SignalSource (tools/base.py) —
    for offline runs and tests. Canned, product-neutral content, so a run with
    `signal_sources` configured but nothing real behind it still exercises the
    summary/facts/items shape a real signal returns.

    Named after its config entry, since a run can have several signals and a
    generic mock summary is otherwise indistinguishable between them.
    """

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def collect(self, context: dict) -> dict:
        # "fail": true is how a tenant or a test reproduces the degrade path
        # deliberately — same option MockOpportunitySource takes.
        if self.fail:
            raise RuntimeError(f"mock signal {self.name!r} failed on purpose")
        topic = context.get("seed_keyword") or "the site's main topics"
        return {
            "summary": (
                f"Mock signal {self.name!r}: interest in {topic} is up 12% over the "
                "last 30 days, with the sharpest rise in how-to style queries."
            ),
            "facts": {"change_pct": 12.0, "window_days": 30},
            "items": [
                {"label": f"{topic} for beginners", "value": 4800},
                {"label": f"how to choose {topic}", "value": 2600},
            ],
        }
