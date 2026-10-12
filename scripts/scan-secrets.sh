#!/usr/bin/env bash
# Publish gate. Non-zero exit means DO NOT PUBLISH.
#
#   scan-secrets.sh                    # this repo, tree + full history (pre-push)
#   scan-secrets.sh --generic          # same, minus the checks that need a private list (CI)
#   scan-secrets.sh --path FILE|DIR    # any file or folder about to go public
#   scan-secrets.sh --url URL          # a page that is ALREADY public
#
# Two private lists live OUTSIDE this repo (templates: scripts/scan-secrets-*.example):
#   ~/.config/scan-secrets/denylist         names that are themselves sensitive
#                                           (override the path with SCAN_DENYLIST)
#   ~/.config/scan-secrets/internal-terms   internal system vocabulary: agent, tool and
#                                           folder names (override with SCAN_INTERNAL_TERMS)
#
# History matters more than the tree: a secret committed once and deleted later
# leaves a clean tree and a leaky repo, and a tree-only gate keeps saying "clean"
# forever after. Default mode therefore scans the WORKING TREE and the FULL GIT HISTORY.
#
# The --path/--url modes exist because the gate is not repo-specific: a post, a
# conference abstract, a landing page and a README all leak the same way. Anything
# about to become public should pass this first.
#
# Hardened 2026-08-26 after a review found this script reporting "clean" on a tree
# that disclosed internal system vocabulary: agent profile names, tool filenames and
# private directory structure. None of those are credentials, but a gate that passes
# on a tree it should have flagged is worse than no gate: it converts "unchecked"
# into "verified". The list of things to look for must GROW over time — never shorten it.
#
# Hardened 2026-09-05 after a second, sharper version of the same defect: the
# personal-name check was implemented as a literal list of names INSIDE this script
# — and this script is published. The gate was leaking the exact names it existed to
# protect, in the file whose job was to stop that. Names now live in a local list.
#
# Hardened 2026-10-12 for the same reason, one class further: the internal-vocabulary
# check still carried its own literal list (agent, tool and folder names). That list
# moved to the second local list as well. This file carries only the mechanism.
#
# The general lesson, worth stating because it will recur: a denylist gate has to
# hold the secret it is matching on. Never let that gate be a public file.
#
# Why --generic exists: CI cannot hold the private lists, and a gate that hard-fails
# there is a gate that is always red, which everyone learns to ignore. So CI runs the
# generic classes and says out loud that the rest did not run. A run that skipped
# checks never prints a plain "clean".
set -uo pipefail

MODE="repo"; TARGET=""; GENERIC=0
while [ $# -gt 0 ]; do
  case "$1" in
    --path) MODE="path"; TARGET="${2:-}"; shift $(( $# >= 2 ? 2 : 1 )) ;;
    --url)  MODE="url";  TARGET="${2:-}"; shift $(( $# >= 2 ? 2 : 1 )) ;;
    --generic) GENERIC=1; shift ;;
    -h|--help) sed -n '2,/^# History matters/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SELF="scan-secrets.sh"
fail=0
TMPDIR_SCAN=""
cleanup () { [ -n "$TMPDIR_SCAN" ] && rm -rf "$TMPDIR_SCAN"; }
trap cleanup EXIT

# ── the private lists ──────────────────────────────────────────────────────
# One extended-regex fragment per line, '#' comments and blanks ignored; the
# fragments are OR-ed into a single pattern.
#   denylist       terms that are themselves sensitive: personal names, customer
#                  names, any internal codename.
#   internal-terms internal system vocabulary that must not appear in anything
#                  public: agent/profile names, tool and script names, the names
#                  of private folders.
#
# A MISSING list is a hard failure, not a warning. Its absence silently deletes a
# whole check class, which is the "unchecked becomes verified" failure this gate
# has now made twice. It fails loud, it is a one-line fix, and it clears itself,
# so it never becomes an always-red gate that everyone ignores. The one
# deliberate way around it is --generic, which says so in its output.
DENYLIST="${SCAN_DENYLIST:-$HOME/.config/scan-secrets/denylist}"
INTERNAL_LIST="${SCAN_INTERNAL_TERMS:-$HOME/.config/scan-secrets/internal-terms}"
load_list () { [ -f "$1" ] && grep -vE '^\s*(#|$)' "$1" | paste -sd'|' - ; }
PRIVATE_TERMS=""
INTERNAL_TERMS=""
if [ "$GENERIC" -eq 1 ]; then
  echo "generic mode: the denylist and internal-terms checks are SKIPPED (they need the private local lists)"
else
  PRIVATE_TERMS=$(load_list "$DENYLIST")
  INTERNAL_TERMS=$(load_list "$INTERNAL_LIST")
  missing=0
  if [ -z "$PRIVATE_TERMS" ]; then
    echo "✗ no denylist at $DENYLIST (or it is empty)."; missing=1
  fi
  if [ -z "$INTERNAL_TERMS" ]; then
    echo "✗ no internal-terms list at $INTERNAL_LIST (or it is empty)."; missing=1
  fi
  if [ "$missing" -eq 1 ]; then
    echo "  The personal-name and internal-vocabulary checks cannot run without their"
    echo "  lists, and a gate that silently drops a check is worse than no gate."
    echo "  Create the files (templates: scripts/scan-secrets-denylist.example and"
    echo "  scripts/scan-secrets-internal-terms.example; chmod 600), point SCAN_DENYLIST /"
    echo "  SCAN_INTERNAL_TERMS at them, or run with --generic to skip these two checks"
    echo "  knowingly."
    exit 1
  fi
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
check "possible channel id"   '(^|[^0-9v.])[0-9]{9,10}([^0-9.]|$)'
# Availability signals and compensation figures are personal and situational,
# not part of the work. Keep them off public pages.
check "availability signal"   'open.?to.?work|seeking (a )?(new|next) (role|opportunity|challenge)|looking for (my|a) next|actively (interviewing|looking|job)|available for hire|open to (new )?opportunities|relocating to the (united states|us|usa)\b' "-i"
check "compensation figure"   '(salary|compensation|base pay|total comp|package)[^.]{0,50}([€$£][[:space:]]?[0-9]|(eur|usd|chf)[[:space:]]?[0-9]|[0-9]{2,3}[[:space:]]?k\b)' "-i"

# ── the checks that need a private list ────────────────────────────────────
if [ "$GENERIC" -eq 0 ]; then
  # Case-insensitive, because a name is written "Alex" in prose and "alex" in a
  # denylist. The 2026-08-26 version matched case-sensitively and would have
  # missed every real occurrence — a check that only catches the lowercase
  # spelling of a proper noun catches nothing.
  check "private term (denylist)" "\\b($PRIVATE_TERMS)\\b" "-i"
  # Internal system vocabulary is the class the first version of this script
  # missed entirely. Its list is private for the same reason the name list is:
  # written out here, it would disclose exactly what it is meant to hide.
  # Case-sensitive on purpose: these are identifiers, not prose.
  check "internal vocabulary (local list)" "\\b($INTERNAL_TERMS)\\b"
fi

if [ "$fail" -eq 0 ]; then
  if [ "$GENERIC" -eq 1 ]; then
    echo "✓ generic checks clean — the private-list checks (denylist, internal terms) were NOT run, so this is not a full verification"
  else
    case "$MODE" in
      repo) echo "✓ clean (tree + full history) — safe to push" ;;
      *)    echo "✓ clean — safe to publish" ;;
    esac
  fi
else
  echo; echo "✗ DO NOT PUBLISH"
fi
exit "$fail"
