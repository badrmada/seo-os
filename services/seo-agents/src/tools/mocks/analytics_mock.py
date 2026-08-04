class MockAppAnalyticsClient:
    """Deterministic, no-network stand-in for a real AppAnalyticsClient — used for
    offline test runs. Canned, product-neutral content (not modeled on any real
    tenant), so a zero-config run still exercises the summary/highlights shape a
    real provider (e.g. tools/clients/analytics_templated.py's TemplatedAnalyticsClient)
    returns."""

    _HIGHLIGHTS = [
        {
            "label": "A recent post about staying focused while working from home.",
            "url": "https://example.com/content/1",
        },
        {
            "label": "A short guide on building a daily reading habit instead of doomscrolling.",
            "url": "https://example.com/content/2",
        },
        {
            "label": "A discussion on whether remaster culture is holding back new ideas.",
            "url": "https://example.com/content/3",
        },
    ]

    def report(self, limit: int = 5) -> dict:
        return {
            "summary": (
                "72 pieces of content shared recently, with steady engagement "
                "across the community."
            ),
            "highlights": self._HIGHLIGHTS[:limit],
        }
