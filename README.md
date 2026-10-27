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
Cordless impact driver 18 V 4-pack, part AC-4417, GTIN 2000000000008
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
`MISSING_GTIN attached gtin None -> 2000000000008`. The full runnable version is
`python3 examples/grounding_example.py`.

`ground()` raises `TypeError` when `extraction` is not a dict, when
`extraction["items"]` is present but not a list, or when `source_text` is not a
str. It does not coerce a wrong type: a silent `{"items": []}` would report a
clean result for a caller bug. A missing `items` key is allowed and returns an
empty list.

Inside an item, a wrong type never raises. An `other_ids` entry that is not a
string (a dict, a list, `None`) and a blank entry are skipped by every check that
reads `other_ids`. The entry stays in the item.

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
A `VARIANT_SPLIT` conflict is the same: the survivor keeps its own value, so
nothing is written and `new_value` is `None`. The variant's value is in
`old_value` and the survivor's value is named in `evidence`.
For the list key `other_ids`, `new_value` is the entry that was added.

`item_index` and `input_index` are the same number until an item is merged
away. `VARIANT_SPLIT` removes an item, so **after a merge, every later finding can
have `item_index` different from `input_index`**, whatever its code: `item_index`
counts the returned list, `input_index` counts the list you passed in. Read both
and never assume they are equal.
An item this module appends for an uncovered link has `input_index: None` and
carries the marketplace id in `other_ids` (`["ASIN:B0TEST1234"]`), never in
`part_number` or `description`.

### The six checks

| Code | What it catches | What it does |
|---|---|---|
| `UNGROUNDED_ID` | a `part_number` or `gtin` that does not occur in the source text on a token boundary | flags it, sets `uncertain: true`, keeps the item and the value |
| `MISSING_GTIN` | a valid GTIN in the source that no item carries. The keyword in front of it must name it (`GTIN`, `EAN`, `UPC`, `Barcode`, `Strichcode`, with an optional `-13`/`-8` suffix or `Nr.` label), only that run counts, and a digit run inside a link never counts | attaches it to the item the line names: the part number on the line, else one description match. A tie or no match is reported with no item (`reported`) |
| `REDACTED_LINK` | a product link cut in two by a redaction placeholder, e.g. `.../item/1[Telefon]234` | restores the full link when **exactly one** source link matches both the text before and the text after the cut (`restored`); otherwise leaves the link untouched and reports it (`flagged`); either way the item gets `uncertain: true` |
| `UNCOVERED_LINK` | a marketplace product link that no item covers | appends an item for it, marketplace id in `other_ids`, `uncertain: true` |
| `SET_MULTIPLIED` | `quantity: 3 x 4` read as 12 pieces instead of 3 sets of 4. A pack marker of the same size is **required in the source text** (`4 pack`, `4-pack`, `4er-Pack`, `4er Pack`, `pack of 4`, `set of 4`), and **the set line must belong to the item**: the item sits on the set line, on the line directly above it, or (when neither of those grounds an item) on the line directly below it, and the marker sits on the set line or on the item's line. A pack word in the extractor's description is not evidence | sets quantity 3, unit `set`, `uncertain: true`, at most once per item; the evidence names the unit change |
| `VARIANT_SPLIT` | one part number listed twice, the second line with no quantity of its own | folds the second into the first as a variant note and moves its link, gtin and other_ids onto the survivor; one `merged` finding per field written |

**Every change is reported. Nothing is deleted or rewritten silently.** The
module never drops an item without a finding, and never rewrites a value into
something the source does not prove — an unsupported part number is flagged, not
repaired, because the source does not say what the right value is.
`VARIANT_SPLIT` is the only check that removes an item; it reports `merged` and
keeps the part number and the variant label on the surviving item. A merge never
loses data, and it never writes without a finding. It reports one `merged`
finding per field it writes onto the survivor: `note`, `link`, `gtin` and
`other_ids` (one finding per entry added). Each of those findings has
`item_index` of the survivor and `input_index` of the merged line, so the record
says where the value came from. When both lines carry a different value for one
key, the survivor keeps its own, nothing is written, and the other value is named
in the finding (`old_value`) with `new_value: null` and `uncertain: true`.
A `unit` or a `price` is never written; a value that differs is reported the same
way. Variant notes read in the order the source lists them, and the merged line's
own note is appended as `variant: <label> (<note>)`.

### Synthetic benchmark

`bench/grounding/` holds 34 invented order requests: 30 injected errors across
six error types, four to six cases each, plus 4 error-free controls.
`python3 bench/grounding/run.py` scores them and rewrites `RESULTS.md`.

| error type | n | caught | corrected |
|---|---|---|---|
| `variant_split` | 4 | 4 | 4 |
| `invented_part_number` | 6 | 6 | 0 |
| `redacted_link` | 4 | 4 | 4 |
| `missed_link` | 6 | 6 | 0 |
| `set_multiplied` | 4 | 4 | 4 |
| `omit_gtin` | 6 | 6 | 6 |
| clean (control) | 4 | — | 4 |

`caught` means a finding with the expected code **on the item the case names**.
`corrected` means the checked output equals `gold` once `uncertain` and `note`
are removed. `tests/test_grounding_bench.py` runs the bench in a temp copy and
fails when the table or the sentences below drift from what it produces.

**These numbers are synthetic.** A person wrote the cases to exercise the checks,
and injected the errors by design. Read them as "the checks fire on the error they
name", not as a recall rate on real extractor output.

- 30 of 30 injected errors produced a finding with the expected code on the expected item.
- 0 of 4 error-free controls produced any finding.
- 18 of 30 injected errors reached the gold extraction exactly; 22 of 34 cases did, counting the controls. The other 12 injected errors cannot, by design: `UNGROUNDED_ID` flags instead of repairing, and `UNCOVERED_LINK` appends a placeholder instead of restoring the link on the existing item.

### Limits

- **A product link needs a real marketplace host.** `product_link_id()` accepts
  only the registrable domains in `MARKETPLACE_DOMAINS`: `amazon.de`,
  `amazon.com`, `amazon.co.uk`, `ebay.de`, `ebay.com`, `ebay.co.uk`,
  `aliexpress.com` and their siblings. Any subdomain of one of those counts.
  `amazon.example.com` and `www.ebay.attacker.example` do not: a path that looks
  like a product path on another domain is never read as a product.
- **Untrusted text stays cheap.** Link scanning is linear in the length of the
  text: a 20,000-character line with a long punctuation run finishes in about a
  millisecond. The identifier path is linear too: the quantity label (`qty`,
  `Menge`, `quantity:`), the digit-run scan behind `find_gtins`, and the
  pack-marker scan that runs once per source instead of once per set line.
  The link checks and the GTIN check index their work once per mail: the links
  the items already cover are a set of id forms, so a mail with one uncovered
  link per line is linear, and the GTIN owner tokenises a line once, so a line
  of thousands of numbers costs its own length. 100 KB of either shape finishes
  in under a second. Timing tests in the suite hold those bounds.
- **The checks prove absence, not truth.** A value that occurs in the source can
  still be attached to the wrong item.
- **An id is grounded on a token boundary, not as a substring.** A source that
  holds `AC-4417` does not ground the part number `AC-441`, and the date
  `2026-10-26` does not ground `2610`. A whole token of a source id is grounded:
  `4417` in `AC-4417` counts. The joiners are `-`, `.`, `/`, `_`, whitespace and
  the hyphen lookalikes a PDF leaves behind (U+2010, U+2011, U+2012, U+2013,
  U+2014, U+2015, soft hyphen U+00AD, zero-width space U+200B), in the value and
  in the source, so `AC–4417` (en dash) is the same id as `AC-4417`.
- **Only a joiner may sit inside a grounded id.** Any other punctuation ends the
  token, so `Pos. 3: AC-4417, 8 Stück` does not ground `AC-44178`, `AC-4417 | 8`
  does not ground `AC-4417-8`, and `Art.-Nr. 4417 (10 Stück)` does not ground
  `4417-10`. A joiner between two digits belongs to the number it splits, so
  `2026-10-26` does not ground `1026` either: the two are one number, not two
  ids.
- **German text and PDF ligatures do not shift the id index.** `ß` upper-cases
  to `SS`, and `ﬁ` and `ﬂ` upper-case to `FI` and `FL`. The boundary list keeps
  one slot per output character, so `Industriestraße 12` above an order grounds
  the ids in that order exactly as `Industriestrasse 12` does.
- `UNGROUNDED_ID` never repairs a value. A human must resolve every flag.
- `UNCOVERED_LINK` appends a second item when the position exists but lost its
  link. Treat the pair as one position; it is a prompt to look, not a merge.
- A 12-digit marketplace item number with a valid GS1 check digit is read as a
  GTIN-12. Nothing in the text distinguishes the two.
- **A GTIN needs its keyword.** `MISSING_GTIN` attaches a digit run only when
  the keyword in front of it names the number (`GTIN`, `EAN`, `UPC`, `Barcode`,
  `Strichcode`, with an optional `-13`/`-8` suffix or `Nr.` label) and the run
  is not inside a link. Only the run directly after the keyword counts, so
  another number on the same line is never taken for the GTIN. Eight digits
  with a valid check digit are also how a phone number or an order number
  looks, so a bare run proves nothing. A supplier that prints a GTIN with no
  keyword gets no attachment.
- **A GTIN goes to the item its line names.** The part number on the GTIN's
  line decides, on a token boundary. A bare GTIN line is read with the line
  above it. When no part number stands on the line, the description decides,
  and only when exactly one item matches. A tie, or no match at all, is
  reported with no item, and so is every GTIN for an item this module appended.
  One item in the extraction is not a reason to attach.
- **A GTIN is one number space.** GTIN-8, -12, -13 and -14 are compared as the
  GTIN-14 (zero-padded) form, so `200000000004` and `0200000000004` are the same
  GTIN: it is grounded, and it is not attached twice. A number an item already
  carries in `gtin`, `part_number` or `other_ids` (with or without an `EAN:`
  label) is never attached again. `gtin14()` exposes the padded form.
- Quantity words are English and German only (`x`, `pcs`, `Stück`, `Menge`, …).
- **A quantity counts whole pieces.** The number of digits behind a separator
  decides its meaning, so no locale is needed: three digits group thousands
  (`1.000 Stück` and `1,000 x` are both 1000, `1.000.000` is one million), and
  one, two, four or more digits are a fraction (`1,5` is 1.5, `12,50` is 12.5).
  When both `.` and `,` occur, the last one is the decimal separator
  (`1.234,56` and `1,234.56` are both 1234.56). A price is never a quantity: a
  line with a currency sign (`12,50 €`, `EUR 12,50`, `Preis 12,50`) gives no
  quantity, and neither does a bare fraction alone above a link (`12,50`).
  Two links on one line get one quantity each. A link with no quantity in front
  of it reads the line directly above, and only that line: that line must hold
  nothing but a quantity, and no link above it may own it. A link with no
  quantity of its own claims the line below it. A blank line is layout, so
  nothing beyond it is borrowed. A link alone on its line also reads the line
  directly below, because a supplier writes the quantity under the position as
  often as above it. A link with no quantity of its own gets none. The number
  pattern starts on a left boundary, so `1.000.000 Stück` reads as 1000000 and
  not as its last group `000`. `"2 pcs"` and `Decimal("2")` are quantities, so
  `VARIANT_SPLIT` keeps a line that carries one. The unit words are `x`, `×`,
  `pcs`, `pieces`, `St.`, `Stck.`, `ST`, `Stk.`, `Stück`, `Stueck`, `VE`,
  `Paar`, `Pack`, `kg`, `units` and `sets`. A quantity field that is not empty
  keeps its line even when the parser cannot read the number: a line that
  states `"2 St."` or `10**400` is a second position, never a variant. Parsing
  is capped: a number above `10**9` or a digit run longer than 12 is ignored, so
  `10**400` and a 5,000-digit run produce no quantity instead of an exception.
- `SET_MULTIPLIED` needs a pack marker of the same size in the source text, on
  the set line or on the item's own line. A set line belongs to one item: the
  item on its own line, else the one on the line directly above, else the one on
  the line directly below. A set line whose item sits further away corrects
  nothing. A pack word only in the extractor's description is never enough.
  A line grounds an item by its part number, GTIN or link, or by the item's
  description words. The description is a fallback only: when the item's part
  number occurs anywhere in the source, only a line that carries that id
  grounds it. The pack words (`pack`, `packs`, `packung`, `packungen`, `set`,
  `sets`, `satz`) are generic and never count as a description match.
- The checks are mechanical pattern matches. They are deterministic on purpose: a
  grounding check that needs a model fails exactly when the extraction is least
  trustworthy.

## Layout

```
harness_lint.py     the checker — no LLM, no network, seconds to run
harness_grounding.py  the grounding checks — is the data true to its source?
specs/              example specs, genericised from a private system
examples/           conforming artifacts, plus a runnable grounding example
bench/grounding/    34 synthetic cases and the scoring runner behind RESULTS.md
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
