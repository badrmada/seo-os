from __future__ import annotations

# Standalone on purpose: takes any JSON value (a tenant's raw analytics file or API
# response), no config/tenant coupling — a UI calls this on whatever JSON it has in
# hand to power autocomplete/suggestions while someone writes
# AgentConfig.analytics_summary_template/analytics_highlights_template
# (see tools/clients/analytics_templated.py).


def infer_fields(value, prefix: str = "data") -> list[dict]:
    """Walks an arbitrary JSON value and returns a flat list of
    {"path": str, "type": str, "example": Any} for every leaf field.

    Lists are sampled via their first element only (repeated elements share the
    same shape in practice) — the sampled path gets a trailing "[]" segment, e.g.
    "data.top_by_upvotes[].content", matching the Jinja2 loop syntax
    (`{% for item in data.top_by_upvotes %}`) a tenant would actually write.
    """
    fields: list[dict] = []
    if isinstance(value, dict):
        for key, sub_value in value.items():
            fields.extend(infer_fields(sub_value, f"{prefix}.{key}"))
    elif isinstance(value, list):
        if value:
            fields.extend(infer_fields(value[0], f"{prefix}[]"))
    else:
        fields.append({"path": prefix, "type": type(value).__name__, "example": value})
    return fields
