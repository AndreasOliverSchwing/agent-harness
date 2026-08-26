#!/usr/bin/env bash
# Publish gate. Non-zero exit means DO NOT PUBLISH.
#
# Default (no arguments): scans this repo's WORKING TREE and FULL GIT HISTORY,
# which is what the pre-push hook wants. History matters more than the tree: a
# secret committed once and deleted later leaves a clean tree and a leaky repo,
# and a tree-only gate keeps saying "clean" forever after.
#
#   scan-secrets.sh                    # this repo, tree + history (pre-push)
#   scan-secrets.sh --path FILE|DIR    # any file or folder about to go public
#   scan-secrets.sh --url URL          # a page that is ALREADY public
#
# The --path/--url modes exist because the gate is not repo-specific: a LinkedIn
# post, a conference abstract, a landing page and a README all leak the same way.
# Anything about to become public should pass this first.
#
# Hardened 2026-08-26 after a review found this script reporting "clean" on a
# tree that disclosed internal agent profile names, tool filenames and private
# directory structure. None of those are credentials, but a gate that passes on
# a tree it should have flagged is worse than no gate: it converts "unchecked"
# into "verified". The identifier list must GROW over time — never shorten it.
#
# Hardened 2026-09-05 after a second, sharper version of the same defect: the
# personal-identifier check was implemented as a literal list of family first
# names INSIDE this script — and this script is published. The gate was leaking
# the exact names it existed to protect, on the public internet, in the file
# whose job was to stop that. Names now live in a local, git-ignored denylist
# (see PRIVATE_TERMS below); the published script carries only the mechanism.
#
# The general lesson, worth stating because it will recur: a denylist gate has
# to hold the secret it is matching on. Never let that gate be a public file.
set -uo pipefail

MODE="repo"; TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --path) MODE="path"; TARGET="${2:-}"; shift 2 ;;
    --url)  MODE="url";  TARGET="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SELF="scan-secrets.sh"
fail=0
TMPDIR_SCAN=""
cleanup () { [ -n "$TMPDIR_SCAN" ] && rm -rf "$TMPDIR_SCAN"; }
trap cleanup EXIT

# ── the private denylist ───────────────────────────────────────────────────
# Terms that are themselves sensitive: family first names, customer names, any
# internal codename. One extended-regex fragment per line, '#' comments and
# blanks ignored; they are OR-ed into a single pattern.
#
# A MISSING denylist is a hard failure, not a warning. Its absence silently
# deletes a whole check class, which is the "unchecked becomes verified" failure
# this gate has now made twice. It fails loud, it is a one-line fix, and it
# clears itself — so it never becomes an always-red gate that everyone ignores.
DENYLIST="${SCAN_DENYLIST:-$HOME/.config/scan-secrets/denylist}"
PRIVATE_TERMS=""
if [ -f "$DENYLIST" ]; then
  PRIVATE_TERMS=$(grep -vE '^\s*(#|$)' "$DENYLIST" | paste -sd'|' -)
fi
if [ -z "$PRIVATE_TERMS" ]; then
  echo "✗ no denylist at $DENYLIST (or it is empty)."
  echo "  The personal/customer-name check cannot run without it, and a gate"
  echo "  that silently drops a check is worse than no gate. Create the file"
  echo "  (one regex fragment per line, chmod 600) or set SCAN_DENYLIST."
  exit 1
fi

# ── target selection ───────────────────────────────────────────────────────
case "$MODE" in
  repo) cd "$REPO_ROOT" || exit 1; SCAN_DIR="." ;;
  path)
    [ -e "$TARGET" ] || { echo "no such path: $TARGET" >&2; exit 2; }
    SCAN_DIR="$TARGET" ;;
  url)
    TMPDIR_SCAN=$(mktemp -d)
    if ! curl -sSL --max-time 30 "$TARGET" -o "$TMPDIR_SCAN/page.html"; then
      echo "could not fetch $TARGET" >&2; exit 2
    fi
    SCAN_DIR="$TMPDIR_SCAN"
    echo "fetched $TARGET ($(wc -c < "$TMPDIR_SCAN/page.html" | tr -d ' ') bytes)" ;;
esac

check () { # name, regex, [extra grep flags — "-i" for case-insensitive]
  local name="$1" re="$2" flags="${3:-}" hits
  hits=$(grep -rInE $flags "$re" "$SCAN_DIR" --exclude-dir=.git --exclude="$SELF" 2>/dev/null | head -5)
  if [ -n "$hits" ]; then echo "  x tree: $name"; echo "$hits" | sed 's/^/      /'; fail=1; fi
  # History is only meaningful in repo mode — a loose file has none, and a live
  # URL's history is not ours to read.
  if [ "$MODE" = "repo" ] && git rev-parse --git-dir >/dev/null 2>&1; then
    # Exclude this script from the HISTORY diff too, not just the tree scan. Its
    # own rule strings contain every term it looks for, so without the pathspec
    # exclusion the gate flags itself forever — and a gate that is permanently
    # red is a gate everyone learns to ignore.
    # HEAD, not --all. A pre-push gate answers "what am I about to publish",
    # which is the history of the branch being pushed. `--all` also walks
    # refs/remotes/*, i.e. what is ALREADY published — so a repo whose remote
    # still carries a bad commit could never push the very commit that removes
    # it. The gate deadlocked exactly that way on 2026-08-26.
    hits=$(git log -p HEAD -- . ":(exclude)scripts/scan-secrets.sh" 2>/dev/null \
           | grep -InE $flags "^\+.*($re)" | head -3)
    if [ -n "$hits" ]; then echo "  x history: $name"; echo "$hits" | sed 's/^/      /'; fail=1; fi
  fi
}

check "API-key-shaped string" 'sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-|AIza[0-9A-Za-z_-]{30,}'
check "AWS access key"        '(AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}'
check "private key"           'BEGIN [A-Z ]*PRIVATE KEY'
check "bot token"             '[0-9]{9,10}:[A-Za-z0-9_-]{30,}'
check "credentialed URL"      '(postgres|postgresql|mysql|mongodb|redis|amqp|ftp)(\+[a-z]+)?://[^[:space:]/@]+:[^[:space:]/@]+@'
check "JWT"                   'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.'
check "private IP"            '\b(10|100|127)\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b|\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b|\b172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}\b'
check "internal hostname"     '\.ts\.net\b|\.local\b|\.internal\b|tailscale'
check "absolute home path"    '/Users/|/home/[a-z]'
# Case-insensitive, because a name is written "Alex" in prose and
# "alex" in a denylist. The 2026-08-26 version matched case-sensitively
# and would have missed every real occurrence — a check that only catches the
# lowercase spelling of a proper noun catches nothing.
check "private term (denylist)" "\\b($PRIVATE_TERMS)\\b" "-i"
# Internal system vocabulary. This is the class the first version of this script
# missed entirely — profile names, tool filenames, private directory structure.
# (internal agent-name check: terms live in a private local list, not in this file)
# (internal tooling check: terms live in a private local list, not in this file)
# (private folder check: terms live in a private local list, not in this file)
check "possible channel id"   '(^|[^0-9v.])[0-9]{9,10}([^0-9.]|$)'
# ── discretion posture (added 2026-09-05) ──────────────────────────────────
# Publishing about the work is fine and deliberate; publishing that you are
# LOOKING is not. A current employer reads the same page a hiring manager does,
# and the two signals are separable: "here is what I know" is reputation, "here
# is what I want" is availability. Only the second one costs anything.
check "availability signal"   'open.?to.?work|seeking (a )?(new|next) (role|opportunity|challenge)|looking for (my|a) next|actively (interviewing|looking|job)|available for hire|open to (new )?opportunities|relocating to the (united states|us|usa)\b' "-i"
# Comp belongs in a negotiation, never on a public page — it anchors every
# future conversation against you and tells your employer what you think you
# are worth elsewhere.
check "compensation figure"   '(salary|compensation|base pay|total comp|package)[^.]{0,50}([€$£][[:space:]]?[0-9]|(eur|usd|chf)[[:space:]]?[0-9]|[0-9]{2,3}[[:space:]]?k\b)' "-i"

if [ "$fail" -eq 0 ]; then
  case "$MODE" in
    repo) echo "✓ clean (tree + full history) — safe to push" ;;
    *)    echo "✓ clean — safe to publish" ;;
  esac
else
  echo; echo "✗ DO NOT PUBLISH"
fi
exit "$fail"
