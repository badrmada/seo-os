"""Run observability — verbose mode (PLAN.md Step 7).

A run used to be a black box: src/main.py printed one JSON blob at the end and
nothing before it. With grounded LLM discovery, parallel fan-out, and
degrade-don't-abort folding failures into tool_errors, there was no way to tell a
working run from one stalled on a slow API call or one quietly degrading.

Everything here lives in the *run-context plane* (PLAN.md's three-plane split): it
is how a run is observed, not something the agent calls to do its work. So none of
it enters Tools, none of it enters AgentState, and the result JSON documented in
docs/output-schema.md gains no fields. The two hooks are `observe_tools()` (wraps
the clients) and `observed_node()` (wraps pipeline stages) — no stage contains
reporting code.
"""

from .observed import observe_tools, observed_node
from .reporter import (
    CollectingReporter,
    JsonReporter,
    NullReporter,
    RunReporter,
    StreamReporter,
    TextReporter,
    build_reporter,
)

__all__ = [
    "CollectingReporter",
    "JsonReporter",
    "NullReporter",
    "RunReporter",
    "StreamReporter",
    "TextReporter",
    "build_reporter",
    "observe_tools",
    "observed_node",
]
