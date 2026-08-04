def contains_any(text: str, phrases: list[str]) -> bool:
    """Case-insensitive substring check — no regex syntax required in tenant config."""
    lowered = text.lower()
    return any(phrase.lower() in lowered for phrase in phrases)
