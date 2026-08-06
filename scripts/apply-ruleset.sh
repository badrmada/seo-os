#!/usr/bin/env bash
#
# Apply .github/rulesets/main.json to the repository's branch rules.
#
#   GITHUB_TOKEN=ghp_... scripts/apply-ruleset.sh          # apply
#   GITHUB_TOKEN=ghp_... scripts/apply-ruleset.sh --dry-run # show the payload
#
# The token needs admin rights on the repo: a fine-grained token with
# "Administration: read and write", or a classic token with `repo`. That is a
# strictly larger permission than anything CI holds, which is why this is a
# script a person runs and not a workflow — nothing in .github/workflows/ can or
# should be able to rewrite the rules that govern it.
#
# Idempotent: it looks for a ruleset with the same name and updates it in place,
# so re-running after editing the JSON is the intended way to change protection.
#
# Run it AFTER the workflows have run at least once — see the note at the top of
# the JSON about required checks that have never reported.

set -euo pipefail

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ruleset_file="$repo_root/.github/rulesets/main.json"

[ -f "$ruleset_file" ] || { echo "missing: $ruleset_file" >&2; exit 1; }

# The repo comes from the git remote rather than a constant, so a fork applies
# its own rules to itself instead of failing against someone else's repository.
origin="$(git -C "$repo_root" remote get-url origin)"
slug="$(printf '%s' "$origin" | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')"

case "$slug" in
  */*) ;;
  *) echo "could not read owner/repo from origin: $origin" >&2; exit 1 ;;
esac

# Strip the annotations, at every depth, and fail loudly on malformed JSON
# rather than sending something the API will reject for a reason it words badly.
payload="$(python3 - "$ruleset_file" <<'PY'
import json, sys

def strip(node):
    if isinstance(node, dict):
        return {k: strip(v) for k, v in node.items() if k != "_comment"}
    if isinstance(node, list):
        return [strip(v) for v in node]
    return node

with open(sys.argv[1]) as fh:
    try:
        doc = json.load(fh)
    except json.JSONDecodeError as exc:
        sys.exit(f"{sys.argv[1]}: {exc}")

print(json.dumps(strip(doc), indent=2))
PY
)"

if $DRY_RUN; then
  echo "repository: $slug"
  echo "payload:"
  printf '%s\n' "$payload"
  exit 0
fi

: "${GITHUB_TOKEN:?set GITHUB_TOKEN to a token with admin rights on $slug}"

api() {
  local method="$1" path="$2"; shift 2
  curl -sS -X "$method" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$slug$path" "$@"
}

name="$(printf '%s' "$payload" | python3 -c 'import sys,json;print(json.load(sys.stdin)["name"])')"

existing="$(api GET /rulesets | python3 -c "
import sys, json
try:
    sets = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)
if isinstance(sets, dict):            # an error object, not a list
    sys.exit(sets.get('message', 'unexpected response from /rulesets'))
print(next((str(s['id']) for s in sets if s['name'] == '$name'), ''))
")"

if [ -n "$existing" ]; then
  echo "updating ruleset '$name' (id $existing) on $slug"
  response="$(api PUT "/rulesets/$existing" -d "$payload")"
else
  echo "creating ruleset '$name' on $slug"
  response="$(api POST /rulesets -d "$payload")"
fi

# The API answers a rejected payload with 200-shaped JSON carrying a message, so
# read the body rather than trusting the exit status.
printf '%s' "$response" | python3 -c '
import sys, json

body = json.load(sys.stdin)

if "id" not in body:
    print("failed:", body.get("message", body), file=sys.stderr)
    for err in body.get("errors", []):
        print("  -", err, file=sys.stderr)
    sys.exit(1)

rules = {r["type"] for r in body.get("rules", [])}
print(f"ok: ruleset {body[\"id\"]} \"{body[\"name\"]}\" is {body[\"enforcement\"]}")
print("  rules:  " + ", ".join(sorted(rules)))
for actor in body.get("bypass_actors", []):
    print(f"  bypass: {actor[\"actor_type\"]} {actor.get(\"actor_id\")} ({actor[\"bypass_mode\"]})")
'

cat <<'EOF'

Verify in the UI: Settings -> Rules -> Rulesets -> main. Two things worth an eye:
  - the three required checks resolved to real checks, not typos
  - the bypass list holds exactly one person, and it is you
EOF
