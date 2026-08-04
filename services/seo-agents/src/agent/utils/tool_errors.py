from datetime import datetime, timezone


def record_tool_error(tool_errors: list, tool: str, node: str, exc: Exception) -> None:
    """Appends one ToolError (agent/schemas/opportunity.py) in place. Shared by every
    stage that wraps an external call at its call site instead of letting it raise
    past the node — see agent/graph/stages/discover.py, analyze.py, draft.py."""
    tool_errors.append({
        "tool": tool,
        "node": node,
        "error_type": type(exc).__name__,
        "message": str(exc)[:500],
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    })
