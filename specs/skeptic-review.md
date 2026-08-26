---
harness: skeptic-review
version: 4
owner_profile: <your-agent>
artifact_glob: $ARTIFACTS_DIR/*weekly-eval.md
cadence: weekly
max_age_h: 192
min_bytes: 400
required_sections:
  - Verdict
  - Checks performed
  - Sampled deliverables
  - Findings
self_eval_min: 0
banned_patterns:
  - pattern: "(?i)looks good to me(?![^.]*\\b(because|verified|ran|checked)\\b)"
    reason: approval requires named checks performed
---

# Harness: Skeptic / Code Review (frontier verifier pass)

## Purpose
Adversarial verification of implementations and weekly deliverable sampling.
This is the pass that catches what producers' self-evals miss — it must stay
concrete, reproducible, and artifact-anchored.

## Input contract
1. The producer's handover + its harness spec (grade against the spec's
   self-eval items, not taste).
2. The actual artifacts: run `harness_lint.py` first; re-run 1–2 of the
   producer's claimed commands/queries yourself and compare.
3. For code: the diff itself, not the description of the diff.

## Output template
```
# Review — <target> — <date>

## Checks performed (commands run, files read — verbatim)
## Findings (most severe first; each: file/line or artifact, failure scenario)
## Self-eval audit: which producer [x] claims did NOT hold, if any
## Verdict: APPROVED | MUST-FIX (with the fix named) 
```

## Grading dimensions (Universal Rubric §3)
Grade each sampled artifact against all 10 rubric criteria (PASS/FAIL). Ship bar:
all 10 PASS, or each FAIL carries a named waiver in the artifact. Two FAILs on
the same criterion two weeks running → harness revision proposal.

1. **Decision usefulness** — names the decision it serves; reader knows what to do.
2. **Evidence grounding** — every number has a named source; claims labeled Fact/Assumption/Unknown.
3. **Freshness** — cites facts notes with current `updated:` dates.
4. **Specificity** — paths, owners, thresholds, not "improve the process."
5. **Risk surfacing** — names single biggest risk + what did NOT happen.
6. **Privacy** — PII/portfolio stays local; states which fence applies.
7. **Action altitude** — actions the owner can take this week.
8. **Simplicity** — ≤ the harness page cap; one memo not five.
9. **No overbuild** — uses existing scripts; scope fence present.
10. **Implementation readiness** — files, commands, acceptance test, rollback named.

## Rules
- A finding without a concrete failure scenario is an opinion — cut it.
- A false `[x]` in a producer's self-eval is automatically MUST-FIX severity,
  regardless of the underlying issue's size (it corrupts the whole eval chain).
- Verdict artifact lands in `$ARTIFACTS_DIR/reviews/<ISO-week>-weekly-eval.md`
  — claims of skeptic approval are invalid without a file here (shared-policy
  rule). The cloud agent still writes only inside its `$ARTIFACTS_DIR/reviews/`
  fence; the no_agent eval script writes the durable record, so the agent's
  file_access deny on `<agent-runtime> stays intact.
- **An absent or >8-day-old record is a FAIL, not a SKIP.** The lane was dark
  from ~2026-07-07 to 2026-07-26: verdicts existed only as wrapper stdout inside
  `<eval-state>.json`, no `.md` was ever written, so the old glob matched
  nothing and lint SKIPped — while the reviewer was in fact returning MUST-FIX
  findings nobody saw. `optional: true` is deliberately gone.
- **Verdict-distribution tracking**: monthly rollup reports GO/NO-GO/GO-WITH-CHANGES
  distribution. A reviewer that never says NO-GO has failed its own eval — flag
  it. 100% GO across a full month = auto-flag for harness revision.

## Escalation
Systemic pattern across ≥2 weekly samples (same failure class) → file a
harness-change proposal, not just per-artifact findings.

## Verifier
the deep-reasoning agent liveness canary (weekly) proves the reviewer lane is alive; the operator
spot-reads MUST-FIX items in the Sunday memo.

*Changelog: v4 2026-07-26 — glob moved to the durable `$ARTIFACTS_DIR/reviews/*weekly-eval.md` record now written by `<eval-script>` (`<record-writer>`); `optional: true` removed so a missing/stale record FAILs; `self_eval_min` 10 → 0 (the record is script-generated from run data — producer self-eval honesty is audited inside the grader packets, and ticking boxes a script cannot substantiate would be the false-`[x]` failure this harness exists to catch); required_sections aligned to the record's real sections. Root cause of the dark lane: `<grader>()` only ever captured wrapper stdout, and `<packet-cleanup>()` swept `eval-*.md` at the review root, deleting anything written there.*
*Changelog: v3 2026-07-07 — `optional: true` so a run with no genuine verdict `.md` SKIPs instead of grading an eval INPUT packet (eval packets now write to `$ARTIFACTS_DIR/reviews/packets/`; QA fix on top of the consistency patch's Task 3).*
