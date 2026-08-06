"""One Redis key per run — the store for anything that runs the agent in more than
one process.

Async on purpose: `redis.asyncio` is a native coroutine client, so a snapshot
costs the event loop a round-trip and not a worker thread. That is the difference
that matters once a server is running many tenants' runs concurrently — see
agent/utils/async_utils.py for why both flavors are accepted anyway.

`redis` is imported *inside* the constructor rather than at module scope, the same
way tools/clients/opportunity_mcp.py defers `mcp`: this module is reachable from
the provider registry, which every CLI command loads, and a tenant on "memory" or
"file" should not pay for a client they never build.
"""

import json
from urllib.parse import urlsplit, urlunsplit

DEFAULT_URL = "redis://localhost:6379/0"
DEFAULT_KEY_PREFIX = "seo-agent:run:"


class RedisStateStore:
    """Snapshots at `<key_prefix><run_id>`, replaced in place as a run progresses.

    Unlike the file store, nothing about a `run_id` needs checking here — a Redis
    key is an opaque byte string, so a run_id that would be a hostile filename is
    just a key. The prefix is what keeps this agent's keys out of everyone else's
    namespace in a shared instance.

    **Connecting is lazy.** `from_url` builds a pool and connects on first use, so
    an unreachable Redis is not a construction failure — it is a save failure,
    which degrades the run rather than failing it (state/base.py, rule 2). That is
    the right split: a typo in the URL should not throw away a finished draft.
    """

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        ttl_seconds: float = 0,
        timeout_seconds: float = 5.0,
    ) -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ValueError(
                'state_provider="redis" needs the redis package — '
                "pip install -r requirements.txt"
            ) from exc

        self.url = url or DEFAULT_URL
        self.key_prefix = key_prefix
        self.ttl_seconds = float(ttl_seconds or 0)
        # Both halves of the timeout, not just the read: a Redis that accepts the
        # connection and never answers and a Redis that never accepts it are the
        # same problem for a run on a deadline.
        self._client = Redis.from_url(
            self.url,
            decode_responses=True,
            socket_timeout=float(timeout_seconds),
            socket_connect_timeout=float(timeout_seconds),
        )

    async def save(self, run_id: str, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False)
        # ex=None is "no expiry", which is the default and the documented one:
        # deciding on a tenant's behalf when their run history disappears is not
        # this store's call. A long-lived deployment sets ttl_seconds.
        await self._client.set(
            self._key(run_id), payload, ex=int(self.ttl_seconds) or None,
        )

    async def load(self, run_id: str) -> dict | None:
        payload = await self._client.get(self._key(run_id))
        return json.loads(payload) if payload else None

    async def delete(self, run_id: str) -> None:
        await self._client.delete(self._key(run_id))

    async def close(self) -> None:
        """Called by whoever built the store (agent/service.py) once the run is
        done. A pool per request is the same trade the rest of this system makes —
        nothing is cached between runs — and an unclosed one leaks a socket per
        run, which a server notices long before anything else goes wrong."""
        await self._client.aclose()

    def describe(self) -> str:
        expiry = f"expires after {self.ttl_seconds:g}s" if self.ttl_seconds else "no expiry"
        return f'{_without_credentials(self.url)} (prefix "{self.key_prefix}", {expiry})'

    def _key(self, run_id: str) -> str:
        return f"{self.key_prefix}{run_id}"


def _without_credentials(url: str) -> str:
    """`describe()` is printed by `check-data` and `list-tools`, and a Redis URL is
    one of the few config values that carries its own password inline."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.password:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    user = f"{parts.username}:***@" if parts.username else "***@"
    return urlunsplit((parts.scheme, f"{user}{host}", parts.path, parts.query, parts.fragment))
