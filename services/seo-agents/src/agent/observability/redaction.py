"""Nothing the reporter prints goes out unredacted. Two independent risks, one
module: config values that are secrets (API keys, auth headers, connection
strings) must never appear at all, and payloads (prompts, LLM responses, tool
results) must be truncated rather than dumped whole — a grounded discovery prompt
plus its response is thousands of characters, which is noise, not observability.

Kept separate from reporter.py because the rule ("don't print secrets") outlives
any particular output format, and because PLAN.md Step 1's move of secrets into
provider-owned `options` doesn't remove the requirement — it just changes which
dict they're sitting in.
"""

import re

REDACTED = "***redacted***"

# Matched against a key name's individual *segments*, not as a raw substring.
# Substring matching is too eager to be useful here — it redacts "seed_keyword"
# (contains "key"), "tokens" (contains "token"), and "author" (contains "auth"),
# which hides the very fields verbose mode exists to show. Splitting on word
# boundaries first keeps the check strict without weakening it: "gemini_api_key",
# "X-Api-Key", and "authorization" all still trip it.
#
# Covers today's fields (gemini_api_key, cloudflare_api_token, gsc_key_file, the
# auth entries inside traffic_api_headers/analytics_api_headers) and the shapes
# Step 8's state-store connection details will arrive in (dsn, credentialed URLs).
_SECRET_SEGMENTS = frozenset({
    "key", "keys", "apikey", "token", "tokens", "secret", "secrets",
    "password", "passwd", "pwd", "credential", "credentials",
    "authorization", "auth", "cookie", "dsn", "bearer", "signature",
})

# Names that would trip the check above but are known, non-sensitive fields this
# reporter emits itself. Kept deliberately tiny — an entry here is a standing
# promise that the field never carries a credential.
_SAFE_NAMES = frozenset({
    "tokens",  # LLM usage count from LLMResponse.tokens, not an auth token
})

_MAX_PREVIEW_CHARS = 400
_MAX_ITEMS = 5
_MAX_DEPTH = 3


def _segments(name: str) -> list[str]:
    """Split a key name into lowercase words: "gemini_api_key" and "X-Api-Key" both
    become [..., "api", "key"], "seedKeyword" becomes ["seed", "keyword"]."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name))
    return [part for part in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if part]


def looks_secret(name: str) -> bool:
    """True if a key name suggests its value is a credential."""
    if str(name).lower() in _SAFE_NAMES:
        return False
    return any(segment in _SECRET_SEGMENTS for segment in _segments(name))


def preview(text, limit: int = _MAX_PREVIEW_CHARS) -> str:
    """One-line, length-capped rendering of a payload. Newlines collapse to spaces
    so a multi-line prompt stays a single event line — the stream is meant to be
    scannable, and a 60-line prompt dumped mid-run destroys that."""
    if text is None:
        return ""
    flattened = " ".join(str(text).split())
    if len(flattened) <= limit:
        return flattened
    return f"{flattened[:limit]}… (+{len(flattened) - limit} chars)"


def redact(value, _depth: int = 0):
    """Recursively strip secret-looking values out of a structure before it's
    reported. Applied to every field of every event, so no call site has to
    remember to do it — the reporter is the only thing that formats output, and it
    redacts unconditionally.

    Also bounds size (lists truncated to _MAX_ITEMS, nesting to _MAX_DEPTH), since
    an unbounded structure is as useless in a live stream as a leaked one is
    dangerous.
    """
    if _depth >= _MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        return {
            key: (REDACTED if looks_secret(key) else redact(item, _depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        items = [redact(item, _depth + 1) for item in list(value)[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append(f"… (+{len(value) - _MAX_ITEMS} more)")
        return items
    if isinstance(value, str):
        return preview(value)
    return value
