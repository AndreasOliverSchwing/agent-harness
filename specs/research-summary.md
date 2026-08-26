---
harness: research-summary
version: 2
owner_profile: <your-agent>
artifact_glob: $ARTIFACTS_DIR/market-brief-*.md
golden: $ARTIFACTS_DIR/market-brief-*.md
harness_compat: ">=0.2"
cadence: weekly
max_age_h:
  warn_after: 192
  error_after: 240
min_bytes: 500
required_sections:
  - Rubric Self-Check
self_eval_min: 10
banned_patterns:
  - pattern: "(?i)(studies show|experts (say|agree)|it is widely)(?![^.]*http)"
    reason: authority claims require a URL in the same sentence
---

# Harness: Research Summary (market briefs, relocation brief, job-scan digest)

## Purpose
External research a decision can rest on: every claim URL-sourced and re-fetched
at digest time, deltas over restatement, honest "nothing new" weeks.

## Input contract
1. The fixed rubric/watchlist for this brief (do not drift scope week to week).
2. Live fetches — every URL cited must have been fetched THIS run (no stale
   links; the known -Q-empty quirk means empty query results are possible:
   report "no qualifying results", never pad).
3. Last edition of the same brief (for the delta section).
4. **Checked-at date on every volatile claim** — re-fetch links at digest time.
   A claim that was true last week may not be true this week. Any recency-search
   tool MUST be invoked explicitly, never trusted to auto-route.

## Output template
```
# <Brief name> — <date>

## What changed since last edition
<deltas only; "nothing material" is valid>

## Findings (each: claim — URL — fetched <date>)

## Scored items (if rubric-based, e.g. job scan: rubric scores shown per item; below-threshold items excluded, count stated)

## Watch (not yet actionable)

## Rubric Self-Check
- [ ] 1 decision-useful  - [ ] 2 evidence-grounded  - [ ] 3 fresh
- [ ] 4 specific         - [ ] 5 risk-surfaced      - [ ] 6 privacy-clean
- [ ] 7 right altitude   - [ ] 8 simple             - [ ] 9 no overbuild
- [ ] 10 executable
FAIL-with-waiver: <criterion>: <reason>  (or "none")

## Self-eval extras
- Every claim has a URL fetched this run (spot-check: first + last).
- Sponsorship/pricing/availability claims sourced from the page or tagged [assumed].
- Delta section is genuine deltas, not a re-summary.
- Length cap respected (job digest ≤1 page ≤5 roles; market brief ≤2 pages).
- Every volatile claim has a checked-at date; links re-fetched at digest time.
```

## Escalation
Paywalled or contradictory sources on a decision-relevant claim → tag
`[unverified — paywall/conflict]` and list it under a "needs human eyes" line
rather than resolving by guess.

## Golden output
`$ARTIFACTS_DIR/market-brief-2026-W27.md` (structure; add self-eval).

## Verifier
harness_lint weekly; skeptic sample re-fetches 2 random URLs and compares claims.
