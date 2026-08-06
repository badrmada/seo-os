"""`{"file": "name.j2"}` — a template value that lives in the tenant's `templates/`
folder instead of on one escaped JSON line.

Every template in this system is a JSON string, so a real one is a single line
with escaped newlines and escaped quotes: unreadable, undiffable, and
un-editable in anything that understands Jinja2 — and prompt wording is the thing
a tenant edits most. Anywhere a template string is accepted, this module also
accepts `{"file": "site_article.j2"}` and substitutes the file's contents before
the AgentConfig is built.

A plain string keeps working and means exactly what it always did. Those are the
only two forms.

**Why an object and not a sigil** (`"@templates/x.j2"`, `"file:x.j2"`): every
sigil is a prefix a template could legitimately start with, so it needs an escape
hatch, and the escape hatch is the bug — a tenant whose summary genuinely starts
with `@` would get a confusing file-not-found instead of their text.
`isinstance(value, dict)` has nothing to disambiguate.

**Why not a sibling `*_template_file` field per option**: templates appear in
nine-and-counting places (`prompt_templates.<channel>`, the analytics/traffic/
signal/search-performance `*_template` options, a discovery source's
`prompt_template`/`query_prompt_template`/`items_template`). Doubling each one
doubles the config surface, and every template option added after this would have
to remember to add its twin. One rule, resolved in one place, means a new
provider's template option gets this for free.

**Read at config-load time, not per render.** By the time an AgentConfig exists
every template is a string again, which is what keeps three things true:
`prompts.validate_template` and TemplateValidator work untouched (save-time
validation still catches a bad template, including one loaded from a file, with
no new code path); a run makes no filesystem call per prompt; and AgentConfig
stays plain data, so a cached or serialized config holds no lazy file reference.
The honest tradeoff: editing a template file does not affect an already-loaded
config — invisible for the CLI, and the same cache invalidation any config change
already needs for a long-lived server.
"""

from __future__ import annotations

from pathlib import Path

from .workspace import TEMPLATES_DIRNAME

FILE_KEY = "file"

# Where a `{"file": ...}` object is honored: the value of any key ending in
# `_template`, and every value inside the `prompt_templates` map (whose keys are
# channels, not option names).
#
# Keying off the naming convention rather than a hand-maintained list of the nine
# current options is what makes a *new* provider's template option work with no
# change here — every template option in this system is already named this way,
# and one that isn't gets told so by _reject_stragglers below rather than
# silently not working.
_TEMPLATE_SUFFIX = "_template"
_TEMPLATE_MAPS = frozenset({"prompt_templates"})

# Dicts whose keys are somebody else's vocabulary — HTTP header names, environment
# variable names, an MCP tool's own parameter names — rather than this config's
# structure. Not descended into at all: a header or an argument that happens to be
# named "file" is a perfectly ordinary thing, and must not be read as a file
# reference. A missing entry here is a loud error (an argument named "file" gets
# rejected by _reject_stragglers) rather than a silent file read, which is the
# reason to allow-list template slots and deny everything else rather than the
# other way round.
_OPAQUE_MAPS = frozenset({"api_headers", "headers", "env", "arguments"})


def resolve_template_files(
    data: dict, base_dir: str = "", source: str = "<config>",
) -> tuple[dict, list[dict]]:
    """Return a copy of a raw config with every `{"file": ...}` replaced by that
    file's text, plus a record of what was loaded and from where.

    The record is what `check-data` reports: that command exists to answer "will
    this config work", and "which file is this prompt actually coming from" is now
    part of that question.
    """
    templates_dir = Path(base_dir) / TEMPLATES_DIRNAME if base_dir else None
    loaded: list[dict] = []
    resolved = _walk(data, templates_dir, source, loaded, path="", in_template_map=False)
    return resolved, loaded


def _walk(value, templates_dir, source, loaded, path, in_template_map):
    if isinstance(value, dict):
        _reject_straggler(value, source, path)
        return {
            key: _walk_entry(key, item, templates_dir, source, loaded, path, in_template_map)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _walk(item, templates_dir, source, loaded, f"{path}[{index}]", in_template_map)
            for index, item in enumerate(value)
        ]
    return value


def _walk_entry(key, value, templates_dir, source, loaded, path, in_template_map):
    here = f"{path}.{key}" if path else str(key)

    if key in _OPAQUE_MAPS:
        return value
    if in_template_map or (isinstance(key, str) and key.endswith(_TEMPLATE_SUFFIX)):
        if isinstance(value, dict):
            return _read_template(value, templates_dir, source, loaded, here)
        return value
    if key in _TEMPLATE_MAPS:
        return _walk(value, templates_dir, source, loaded, here, in_template_map=True)
    return _walk(value, templates_dir, source, loaded, here, in_template_map=False)


def _reject_straggler(value: dict, source: str, path: str) -> None:
    """A `{"file": ...}` object somewhere that isn't a template value.

    Left alone it would reach a provider as a dict where a string was expected and
    fail later with something unrecognizable, so it is named here instead. The
    message says which convention is missing rather than just "not allowed": a
    template option this module doesn't recognize is a naming problem with a
    one-word fix.
    """
    if set(value) == {FILE_KEY} and path:
        raise ValueError(
            f'{path} in {source} is {{"{FILE_KEY}": ...}}, but that form is only accepted '
            f'for template values — a key ending in "{_TEMPLATE_SUFFIX}", or an entry in '
            f'{sorted(_TEMPLATE_MAPS)[0]}. Use a plain string here, or rename the option to '
            f'end in "{_TEMPLATE_SUFFIX}" if it really is a template. See docs/configuration.md.'
        )


def _read_template(value: dict, templates_dir, source: str, loaded: list, path: str) -> str:
    if set(value) != {FILE_KEY}:
        raise ValueError(
            f"{path} in {source} must be a template string or "
            f'{{"{FILE_KEY}": "name.j2"}}, got keys {sorted(value)}'
        )
    name = value[FILE_KEY]
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f'{path}.{FILE_KEY} in {source} must be a filename, got {name!r}')

    resolved = _contained_path(name, templates_dir, source, path)
    if not resolved.is_file():
        raise ValueError(
            f"{path} in {source}: no template file {name!r} in {templates_dir} "
            f"(available: {_available(templates_dir)})"
        )
    text = resolved.read_text(encoding="utf-8")
    loaded.append({"slot": path, "file": name, "path": str(resolved)})
    return text


def _contained_path(name: str, templates_dir, source: str, path: str) -> Path:
    """Resolve `name` inside the tenant's `templates/` folder, and nowhere else.

    The same containment `validate_name` gives a tenant name and `plugin_loader`
    gives a plugin, for the same reason: in a server this value arrives from a
    request or a database row, so "read any file the process can read" is a
    failure mode to design out rather than to discover. `..`, absolute paths and
    symlinks pointing out of the folder are all rejected — the last one only shows
    up after resolving, which is why the check isn't purely textual.
    """
    if templates_dir is None:
        raise ValueError(
            f'{path} in {source} uses {{"{FILE_KEY}": {name!r}}}, but this config has no '
            "tenant folder to read it from — a config built in code, or loaded without a "
            "base directory, has no templates/ folder. Use a template string instead."
        )

    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(
            f"{path} in {source}: template file {name!r} must be a path inside "
            f"{templates_dir}, not an absolute path and not containing '..'"
        )

    root = templates_dir.resolve()
    resolved = (templates_dir / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(
            f"{path} in {source}: template file {name!r} resolves to {resolved}, "
            f"outside {root} (a symlink leaving the folder counts)"
        )
    return resolved


def _available(templates_dir: Path) -> str:
    """What *is* in the folder. "No such file" alone sends someone looking in the
    wrong directory — the same treatment plugin_loader gives a missing plugin."""
    if not templates_dir.is_dir():
        return f"no {TEMPLATES_DIRNAME}/ folder"
    names = sorted(
        str(entry.relative_to(templates_dir))
        for entry in templates_dir.rglob("*")
        if entry.is_file() and not entry.name.startswith(".")
    )
    return ", ".join(names) or "empty"
