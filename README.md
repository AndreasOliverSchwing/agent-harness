# agent-harness

**A contract layer for recurring agent output — and a linter that checks it mechanically.**

Most agent tooling answers *how do I run this?* This answers the harder question:
**how do I know it worked?**

Every recurring output an agent produces — a daily brief, a weekly digest, a research
memo, a review verdict — is governed by a spec: a markdown file with YAML front matter
declaring what the output must contain, how fresh it must be, what it must never say,
and a self-evaluation rubric the producer fills in honestly. `harness_lint.py` then
checks the artifacts against those specs with no LLM and no network, in seconds.

```bash
python3 harness_lint.py --all --invariants
```

## Why this exists

Agents fail quietly. They produce something *shaped* like the right answer, on time,
well-formed — and wrong, or stale, or never actually delivered. Every check that
measures the artifact will call that green.

This layer exists because of a specific incident, which is worth stating plainly
because it generalises:

> A weekly digest was written to disk every Saturday for three weeks and rejected by
> the messaging API every Saturday. The file was present, fresh and well-formed, so
> every check passed. The recipient saw nothing for three weeks, and the entire
> downstream workflow sat at zero without a single alarm.

**Artifact freshness is not delivery.** For any output that must reach a human,
completion is delivery, not a file path — and the spec has to say so, and something
has to assert it.

## The three ideas

**1. A spec per recurring output.** Front matter is machine-checkable; the prose below
is for whoever writes the producer prompt.

```yaml
---
harness: research-summary
artifact_glob: $NOTES_DIR/briefs/market-brief-*.md
cadence: weekly
max_age_h: 192
min_bytes: 500
required_sections: [Rubric Self-Check]
self_eval_min: 10
banned_patterns:
  - pattern: "(?i)(studies show|experts (say|agree))(?![^.]*http)"
    reason: authority claims require a URL in the same sentence
---
```

`min_bytes` is deliberately low on most specs, because **"nothing happened this week"
is a valid and required answer.** A harness that punishes an empty week teaches the
producer to pad, and padding is worse than silence.

**2. A self-evaluation rubric the producer fills in.** Every run appends a checklist
and ticks it honestly.

> **An unchecked box is the system working.** It is the producer reporting a real miss.
> A false `[x]` is the one unforgivable failure — it is the only thing that makes the
> whole layer worthless.

The corollary matters as much: **never hand-edit a past artifact to turn the board
green.** Patch the spec or the producer, and let the next scheduled run prove it.

**3. Invariants — cross-cutting assertions no single spec owns.** One ships:
`--invariants` asserts that scheduled output actually reached a human. It is
deliberately the only one — an invariant is worth having when you have seen it go
red against a real incident, and shipping a list of untested ones would be exactly
the decoration this method is supposed to replace.

It is a **dead-man's switch**, not an error-grep, and that distinction was learned
the hard way. The first version matched known failure phrases in the job's run
record. It caught a delivery that logged its own error — and silently passed a
delivery that just dropped, and a job that had stopped running altogether. Both
returned green. *An invariant that only catches the failures polite enough to
announce themselves is not a liveness check.* It now requires positive evidence
and fails on absence:

| | |
|---|---|
| FAIL | the record names a delivery failure |
| FAIL | the record shows the run was not delivered |
| FAIL | an enabled job has not run within `HARNESS_JOB_MAX_AGE_H` |
| FAIL | `HARNESS_JOBS_GLOB` matched no files, or a record is unreadable |
| WARN | an enabled job has never run |
| WARN | records exist but contain no enabled jobs |
| WARN | the runner records no delivery evidence either way |

The two absence cases fail rather than pass on purpose. For a check whose whole
job is "fail when the thing did not happen", finding nothing to look at and
reporting success would be the same silent-green failure one level up.

The warnings are deliberate. Failing on every unknown would leave the invariant
red on any runner that does not record delivery — and a gate that is always red is
ignored exactly like one that is always green.

A good invariant **reproduces a known failure before you trust it.** If it cannot go
red against the incident that motivated it, it is not evidence — it is decoration.

## Layout

```
harness_lint.py     the checker — no LLM, no network, seconds to run
specs/              example specs, genericised from a private system
examples/           conforming artifacts, so a fresh clone runs green
tests/              unittest suite — run with: python3 -m unittest discover -s tests
scripts/            secret scan (tree + full history) + hook installer
SPEC-REFERENCE.md   every front-matter key and what it does
spec.schema.json    JSON Schema for the front matter (editor + CI validation)
CHANGELOG.md        what changed, and why
```

Every check is tested against a fixture that should fail it, through **both**
front-matter parser paths — with PyYAML and without. That parity is not
ceremonial: the no-PyYAML path silently disabled the banned-patterns check in the
first published version, and no test existed to notice.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `HARNESS_SPECS_DIR` | directory of spec files | `./specs` |
| `ARTIFACTS_DIR` | root the specs' `artifact_glob` resolves against | `./examples` |
| `STATE_DIR` | root for `$STATE_DIR` references in specs | same as `ARTIFACTS_DIR` |
| `HARNESS_JOBS_GLOB` | your job runner's run records, for the delivery invariant. Several globs may be joined with the OS path separator | unset (invariant skipped) |
| `HARNESS_JOB_MAX_AGE_H` | dead-man's switch: fail when an enabled job has not run in this many hours | `0` (disabled) |

Two optional dependencies, both with tested absence paths: **PyYAML** (a built-in
parser handles the front matter without it) and **jsonschema** (the near-miss key
check still runs without it). Neither is required — the linter must work on a
fresh clone with nothing installed, and "works without it" is a code path with
tests, not an assumption.

Defaults are chosen so a fresh clone runs green with no configuration; point
`ARTIFACTS_DIR` at your own output directory for real use.

Git does not ship hooks with a clone, so after cloning run
`./scripts/install-hooks.sh` to wire the secret scan to `pre-push`. CI runs it too.

PyYAML is used when present. Without it a small built-in parser handles the front
matter — including the nested `banned_patterns` form. If any pattern cannot be
parsed the run says so rather than skipping the check, because a safety check that
quietly does not run is worse than no check.

### Trust boundary

Specs are executable input: `artifact_glob` is expanded and read, and
`banned_patterns` are compiled as regexes. **Run this only against specs you
control.** A hostile spec can read any file the process can read, and a crafted
pattern can burn CPU — regex evaluation is capped at 5 seconds and reported, not
retried.

## What this is not

Not an agent framework, not an orchestrator, not a scheduler. It assumes you already
have those. It is the thin layer that tells you whether what they produced is worth
anything — and it is deliberately mechanical, because a checker that needs a model to
run is a checker that fails exactly when the model is having a bad day.

## License

MIT.
