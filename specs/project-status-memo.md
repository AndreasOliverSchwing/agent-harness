---
harness: project-status-memo
version: 1
owner_profile: <your-agent>
artifact_glob: $ARTIFACTS_DIR/project-ops-*.md
cadence: weekly
max_age_h: 192
min_bytes: 500
facts_notes:
  - $ARTIFACTS_DIR/$FACTS_DIR/facts.md
required_sections:
  - Self-Eval
required_markers:
  - "Facts:"
self_eval_min: 4
banned_patterns:
  - pattern: "(?i)on track(?![^.]*\\b(because|evidence|per)\\b)"
    reason: status adjectives require evidence in the same sentence
---

# Harness: Project Status Memo (project-ops weekly; template for any project)

## Purpose
Status a project in ≤1 page such that a reader can act without asking follow-ups.
Status words ("on track", "blocked") must carry their evidence in-sentence.

## Input contract
1. `the facts note.md` (cite date) — the project's gate state lives here or in
   the named state artifact; never in memory.
2. The project state artifact (`$STATE_DIR/project-state.json` for
   the product) — every gate claim traces to a field in it.
3. Repo state (git log since last memo), open review verdicts (round-5 GO etc.
   only with the review artifact path).

## Output template
```
# <Project> Ops — <ISO week>

## State vs gates
| Gate | State | Evidence (artifact/field/commit) |

## Changed since last week
<facts only; "nothing" is valid>

## Blocked on human
<named single next action per blocker, deep link — or "none">

## Risks that moved
<or "none">

Facts: the facts note <date> · state artifact <timestamp>
## Self-Eval
- [ ] Every gate/status claim has an artifact/field/commit in the same row or sentence.
- [ ] Human-blocked items name exactly one next action each, with a link.
- [ ] Nothing listed as open that the state artifact shows done (and vice versa).
- [ ] ≤1 page; "nothing changed" said plainly where true.
```

## Escalation
State artifact stale (>26h for daily-collected projects) → memo leads with
`INPUT STALE: <artifact>` instead of guessing.

## Golden output
`$ARTIFACTS_DIR/project-ops-2026-W27.md` (structure; add the new footer/self-eval).

## Verifier
harness_lint weekly; skeptic sample.
