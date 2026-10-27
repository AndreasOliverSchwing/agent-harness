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

Many job runners never write a positive "delivered" field — they only write an
error and clear it on the next success. Left alone, this check then warns on every
healthy job forever, which is alert fatigue wearing the costume of rigour. Setting
`HARNESS_ERRORS_CLEARED_ON_SUCCESS=1` declares that behaviour so a clean recent run
counts as delivered. It is a declaration and not a default because assuming it
silently is precisely the false-green this check exists to prevent — and it never
masks a logged failure.

The two absence cases fail rather than pass on purpose. For a check whose whole
job is "fail when the thing did not happen", finding nothing to look at and
reporting success would be the same silent-green failure one level up.

The warnings are deliberate. Failing on every unknown would leave the invariant
red on any runner that does not record delivery — and a gate that is always red is
ignored exactly like one that is always green.

A good invariant **reproduces a known failure before you trust it.** If it cannot go
red against the incident that motivated it, it is not evidence — it is decoration.

## Grounding checks — is the data inside the artifact true to its source?

The linter answers one question: **does this artifact meet its contract?**
`harness_grounding.py` answers the next one: **is the data inside it true to the
text it came from?**

These are different questions, and the gap between them is where the quiet
failures live. An extractor reads a supplier mail and returns items. The result
can be well-formed, complete, on time, and pass every lint check — and still hold
a part number that no source line contains. **Lint checks shape. Grounding checks
truth.**

The module is a library, not a linter mode. Call `ground()` in the producer after
extraction. It returns a checked copy and a list of findings. It never mutates the
input, never calls a model, never opens a socket.

```python
import harness_grounding as g

source = """Example Tool Supply - order request
Cordless impact driver 18 V 4-pack, part AC-4417, GTIN 4006381333931
quantity: 3 x 4
"""

extraction = {"items": [{
    "quantity": 12,
    "unit": "piece",
    "description": "Cordless impact driver 18 V 4-pack",
    "part_number": "AC-9999",
}]}

checked, findings = g.ground(extraction, source)
for finding in findings:
    print(finding["code"], finding["action"], finding["field"],
          finding["old_value"], "->", finding["new_value"])
```

That extraction is well-formed and wrong in three ways. It prints
`UNGROUNDED_ID flagged part_number AC-9999 -> None`,
`SET_MULTIPLIED corrected quantity 12 -> 3` and
`MISSING_GTIN attached gtin None -> 4006381333931`. The full runnable version is
`python3 examples/grounding_example.py`.

### The finding contract

Every finding, from every check, has exactly these eight keys:

| Key | Meaning |
|---|---|
| `code` | stable machine code, one of the six below |
| `action` | what `ground()` did: `flagged`, `attached`, `reported`, `restored`, `added`, `corrected`, `merged` |
| `field` | the item key the finding concerns (`part_number`, `gtin`, `link`, `quantity`, `note`, `other_ids`), or `None` when no single key is concerned |
| `old_value` | what that key held before this module touched it; `None` when the key was absent or empty |
| `new_value` | what this module wrote into that key; `None` when it wrote nothing |
| `item_index` | index of the item in the **returned** items list, or `None` |
| `input_index` | index of that item in the list **you passed in**, or `None` for an item this module appended |
| `evidence` | one short quotable sentence, safe to show a human |

There is no `value` key. It was ambiguous: it held the old value for some codes
and the new one for others. Read `old_value` and `new_value`.

`new_value` is `None` for a `flagged` or `reported` finding, because neither
writes anything. A `reported` finding names the value it is about in `evidence`.
For the list key `other_ids`, `new_value` is the entry that was added.

`VARIANT_SPLIT` is the one code where the two indexes differ: `item_index` is
the surviving item, `input_index` is the variant line that was folded into it.
An item this module appends for an uncovered link has `input_index: None` and
carries the marketplace id in `other_ids` (`["ASIN:B0TEST1234"]`), never in
`part_number` or `description`.

### The six checks

| Code | What it catches | What it does |
|---|---|---|
| `UNGROUNDED_ID` | a `part_number` or `gtin` that does not occur in the source text | flags it, sets `uncertain: true`, keeps the item and the value |
| `MISSING_GTIN` | a valid GTIN in the source that no item carries | attaches it to the best-matching item (`attached`), or reports it with no item when nothing matches (`reported`) |
| `REDACTED_LINK` | a product link cut in two by a redaction placeholder, e.g. `.../item/1[Telefon]234` | restores the full link from the source text |
| `UNCOVERED_LINK` | a marketplace product link that no item covers | appends an item for it, marketplace id in `other_ids`, `uncertain: true` |
| `SET_MULTIPLIED` | `quantity: 3 x 4` read as 12 pieces instead of 3 sets of 4 | sets quantity 3, unit `set`, `uncertain: true` |
| `VARIANT_SPLIT` | one part number listed twice, the second line with no quantity of its own | folds the second into the first as a variant note and moves its link, gtin and other_ids onto the survivor; reports `merged` |

**Every change is reported. Nothing is deleted or rewritten silently.** The
module never drops an item without a finding, and never rewrites a value into
something the source does not prove — an unsupported part number is flagged, not
repaired, because the source does not say what the right value is.
`VARIANT_SPLIT` is the only check that removes an item; it reports `merged` and
keeps the part number and the variant label on the surviving item. A merge never
loses data: the variant's link, gtin and other_ids move onto the survivor when it
lacks them. When both lines carry a different value for one key, the survivor
keeps its own and the other value is named in the finding (`old_value`) with
`uncertain: true`. Variant notes read in the order the source lists them.

### Synthetic benchmark

`bench/grounding/` holds 30 invented order requests: six error types, four to six
cases each, plus four error-free controls. `python3 bench/grounding/run.py` scores
them and rewrites `RESULTS.md`.

| error type | n | caught | corrected |
|---|---|---|---|
| `variant_split` | 4 | 4 | 4 |
| `invented_part_number` | 6 | 6 | 0 |
| `missed_link` | 6 | 6 | 0 |
| `set_multiplied` | 4 | 4 | 4 |
| `omit_gtin` | 6 | 6 | 6 |
| clean (control) | 4 | — | 4 |

**These numbers are synthetic.** A person wrote the cases to exercise the checks,
and injected the errors by design. Read them as "the checks fire on the error they
name", not as a recall rate on real extractor output.

- 30 of 30 injected errors produced a finding with the expected code.
- 0 of 4 error-free cases produced any finding.
- 14 of 30 reached the gold extraction exactly. The other 16 cannot, by design:
  `UNGROUNDED_ID` flags instead of repairing, and `UNCOVERED_LINK` appends a
  placeholder instead of restoring the link on the existing item.

### Limits

- **The checks prove absence, not truth.** A value that occurs in the source can
  still be attached to the wrong item.
- `UNGROUNDED_ID` never repairs a value. A human must resolve every flag.
- `UNCOVERED_LINK` appends a second item when the position exists but lost its
  link. Treat the pair as one position; it is a prompt to look, not a merge.
- A 12-digit marketplace item number with a valid GS1 check digit is read as a
  GTIN-12. Nothing in the text distinguishes the two.
- The GTIN-to-item match uses word overlap on the description. A short or generic
  description ("Set", "Artikel") can miss, and the finding then carries no item.
- Quantity words are English and German only (`x`, `pcs`, `Stück`, `Menge`, …).
- The checks are mechanical pattern matches. They are deterministic on purpose: a
  grounding check that needs a model fails exactly when the extraction is least
  trustworthy.

## Layout

```
harness_lint.py     the checker — no LLM, no network, seconds to run
harness_grounding.py  the grounding checks — is the data true to its source?
specs/              example specs, genericised from a private system
examples/           conforming artifacts, plus a runnable grounding example
bench/grounding/    30 synthetic cases and the scoring runner behind RESULTS.md
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
| `HARNESS_ERRORS_CLEARED_ON_SUCCESS` | declare that your runner clears its error field on a successful run, so a clean recent run counts as delivered | unset (runs are reported unverified) |

Two optional dependencies, both with tested absence paths: **PyYAML** (a built-in
parser handles the front matter without it) and **jsonschema** (the near-miss key
check still runs without it). Neither is required — the linter must work on a
fresh clone with nothing installed, and "works without it" is a code path with
tests, not an assumption.

Defaults are chosen so a fresh clone runs green with no configuration; point
`ARTIFACTS_DIR` at your own output directory for real use.

Git does not ship hooks with a clone, so after cloning run
`./scripts/install-hooks.sh` to wire the secret scan to `pre-push`. The scan needs
two private lists that live outside the repo (templates:
`scripts/scan-secrets-*.example`); without them it fails loudly rather than
skipping those checks. CI runs the generic classes only (`--generic`) and says so
in its output.

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
