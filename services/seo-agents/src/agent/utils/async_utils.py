"""The one decision the whole async execution model follows from: every Protocol
in this system (tools/base.py, tools/llm/base.py) accepts a **sync or an async**
implementation, and the framework adapts.

`call()` is where that adapting happens. An async implementation is awaited; a
sync one is run in a worker thread (`asyncio.to_thread`) so it can never stall the
event loop that other tenants' runs are sharing. Two things this buys, both
essential:

  - Every existing `"custom"` class keeps working untouched — the example classes,
    everything in docs/extending.md, and any tenant's own code. `__init__(config)`
    + `def discover(context)` is still a complete, correct plugin. Breaking that
    would break the one contract users build against.
  - A blocking client can never stall the loop — which is not hypothetical:
    Google Search Console's SDK (googleapiclient, httplib2-based) *cannot* be made
    async, so it runs threaded here, correctly, rather than pretending otherwise.

Call sites are the observing proxies (agent/observability/observed.py) and the
few places a client is invoked outside them (stages, LLMOpportunitySource,
OutputManager). Nothing else needs to know which flavor it holds.
"""

from __future__ import annotations

import asyncio
import functools
import inspect

__all__ = ["call", "is_async_callable", "deadline"]


def is_async_callable(fn) -> bool:
    """True if calling `fn` returns a coroutine — checked *before* calling it,
    which is the whole point: a sync callable must go to a thread rather than be
    invoked inline, so "call it and see if the result is awaitable" is not an
    option (by then it has already blocked the loop).

    Unwraps `functools.partial` and `functools.wraps` decorators, and handles a
    callable *object* whose `__call__` is async — the shape a class-based plugin
    naturally takes.
    """
    while isinstance(fn, functools.partial):
        fn = fn.func
    fn = inspect.unwrap(fn)
    if inspect.iscoroutinefunction(fn):
        return True
    if inspect.isfunction(fn) or inspect.ismethod(fn) or inspect.isbuiltin(fn):
        return False
    dunder_call = getattr(type(fn), "__call__", None)
    return dunder_call is not None and inspect.iscoroutinefunction(dunder_call)


async def call(fn, *args, **kwargs):
    """Invoke `fn` — sync or async — from async code, and return its result.

    A sync callable that itself returns an awaitable (a plain `def` wrapping a
    coroutine, which is how some SDKs and most hand-rolled bridges look) is
    awaited too: the thread hop costs almost nothing and getting a coroutine
    object back where a value was expected is a genuinely confusing failure.
    """
    if is_async_callable(fn):
        return await fn(*args, **kwargs)
    result = await asyncio.to_thread(fn, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def deadline(seconds: float | None):
    """A whole-run time bound, on top of the per-call timeouts each client already
    sets. Those bound one HTTP request; this bounds the run — a pipeline of a
    dozen individually-timely calls, or a "custom" plugin with no timeout of its
    own at all, can still occupy a worker slot for far longer than anyone intends.

    Returns an async context manager: `asyncio.timeout(seconds)`, or a no-op when
    seconds is falsy (the default — an unbounded run is still the right default
    for a CLI someone is watching).
    """
    if not seconds:
        return _no_deadline()
    return asyncio.timeout(seconds)


class _no_deadline:
    """Null object for `deadline(None)` — the same surface `asyncio.timeout`
    offers (`async with`, plus `expired()` so a caller can tell an expired
    deadline from a TimeoutError something else raised), with no timer."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def expired(self) -> bool:
        return False
