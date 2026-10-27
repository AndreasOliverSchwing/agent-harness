# Changelog

Versions follow [SemVer](https://semver.org/). The spec front matter is a public
contract: a breaking change to it means a major bump, and removed keys keep
working as aliases for one major version rather than disappearing.

## Unreleased

Fixes from the pre-release QA review of 0.3.0. No public API is removed, but the
finding shape changes: read `old_value` / `new_value` instead of `value`.

### Changed
- **The finding contract is settled.** Every finding from all six checks has
  exactly eight keys: `code`, `action`, `field`, `old_value`, `new_value`,
  `item_index`, `input_index`, `evidence`. The ambiguous `value` key is gone: it
  held the old value for some codes and the new one for others. `item_index`
  names the item in the returned list; `input_index` names it in the list the
  caller passed in, and is `None` for an appended item. Documented once in
  `SPEC-REFERENCE.md` and in the module docstring.
- An item appended for an uncovered link carries the marketplace id in
  `other_ids` (`["ASIN:B0TEST1234"]`), not in `part_number` or `description`.
- **`ground()` raises `TypeError` on a wrong input type.** A non-dict
  `extraction`, an `items` value that is not a list, and a non-str `source_text`
  were coerced to `{"items": []}` / `""` and returned a clean result. They now
  raise, with a message that names the argument and the type it received. A
  missing `items` key is still allowed and returns an empty list. Documented in
  `README.md` and `SPEC-REFERENCE.md`.

### Fixed
- **The benchmark figures are measured, not typed.** `bench/grounding/` had no
  `REDACTED_LINK` case, and a catch was scored on the finding code alone, so a
  flag on the wrong item counted. The set now holds 44 cases — 38 injected
  errors across six error types plus 6 error-free controls — and a catch needs
  the expected code on the item the case names (`expect_item`). Ten of the cases
  are German supplier mail: `ß` in the address, `Stück` and `St.` quantities,
  `Menge:` set lines and a signature block. The first 34 were English-only and
  stayed green on mail the checks could not read. A test runs the bench in a
  temp copy and fails when `README.md` or this file quotes a figure the bench
  does not produce. 38 of 38 injected errors produced a finding with the
  expected code on the expected item. 0 of 6 error-free controls produced any
  finding. 23 of 38 injected errors reached the gold extraction exactly; 29 of
  44 cases did, counting the controls.
- The quantity-label regex is linear. `\s*:?\s*` split a run of spaces between
  two quantifiers, so every split was tried: 20,000 spaces after `qty` took
  2.9 s. The colon now sits inside one optional group. The pack-marker patterns
  also start on a digit boundary and read at most four digits, so a long digit
  run no longer restarts the scan at every position (20,000 digits took 13 s),
  and the pack-marker scan runs once per source instead of once per set line
  (1,000 set lines took 1.2 s). Timing tests guard every bound.
- GTINs are compared as one number space. GTIN-8, -12, -13 and -14 are padded to
  the GTIN-14 form, so the source's `200000000011` grounds an extraction's
  `00200000000011` (no `UNGROUNDED_ID`) and the number is not attached a second
  time (no duplicate `MISSING_GTIN`). A GTIN an item already carries in
  `part_number` or in `other_ids` with an `EAN:` label is never attached again.
  New public helper: `gtin14(code)`.
- `MISSING_GTIN` attaches a digit run only when the source names it. The line
  must carry `GTIN`, `EAN`, `UPC`, `Barcode`, `Strichcode` or `bar code`, and the
  run must not sit inside a link. A phone number (`Tel. +49 89 12345670`), an
  order number (`Order 20261023`) and a 13-digit marketplace id in a URL are no
  longer written into `gtin`. The GTIN-to-item match also drops generic words, so
  an overlap on "part" alone no longer moves a GTIN to the wrong item.
- `UNGROUNDED_ID` grounds an id on a token boundary, not as a substring. A
  source that holds `AC-4417` no longer grounds `AC-441`, `NW-22019` no longer
  grounds `NW-2201`, and the date `2026-10-26` no longer grounds `2610`. The
  joiners `-`, `.`, `/`, `_`, whitespace and the hyphen lookalikes a PDF leaves
  behind (U+2010, U+2011, U+2012, U+2013, U+2014, U+2015, soft hyphen U+00AD,
  zero-width space U+200B) are normalized in the value and in the source, so an
  id written with an en dash or a soft hyphen is not flagged. Documented in
  `SPEC-REFERENCE.md`, "How an id is grounded".
- A German `ß` or a PDF ligature no longer shifts the id index. `ß` upper-cases
  to `SS`, and `ﬁ` and `ﬂ` to `FI` and `FL`, but the boundary list kept one slot
  per source character, so every later id sat off the list: a correct id was
  flagged as `UNGROUNDED_ID`, and `ground()` raised `IndexError` on a source such
  as `Grüße aus der Lindenstraße`. The boundary list now keeps one slot per
  output character. Documented in `README.md` and `SPEC-REFERENCE.md`.
- Link scanning is linear in the length of the text. `_clean_link` strips the
  prose punctuation with `str.rstrip` instead of a `$`-anchored regex, which
  retried from every position: a 20,000-character link holding a long run of
  `)` took about a second and now takes about a millisecond. A timing test
  guards the bound.
- Link detection keeps the address and drops the prose around it. A trailing
  German closing quote, `!`, `'`, `**`, `:` or bracket no longer becomes part of
  the link, and a Markdown `[url](url)` yields the address instead of the
  `](`-glued text that used to land in an appended item. `/DP/` is matched in
  any case, so `/DP/`, `/Dp/` and `/GP/Product/` all give the same ASIN.
- `product_link_id` accepts only real marketplace hosts. The host must reduce to
  a registrable domain in `MARKETPLACE_DOMAINS` (`amazon.de`, `amazon.com`,
  `ebay.co.uk`, `aliexpress.com`, ...). `amazon.example.com`,
  `www.ebay.attacker.example` and `notamazon.de` are rejected, so a lookalike
  domain can no longer inject a product id or an appended item.
- `REDACTED_LINK` no longer restores the wrong product. A cut link is restored
  only when **exactly one** source link matches both the text before and the text
  after the placeholder. Otherwise nothing is rewritten and the finding says
  `flagged`, with the cut link in `old_value`. Every restore now sets
  `uncertain: true`, because a restore is a data change. Two eBay links that
  share `/itm/1` and two AliExpress ids that share `1005` no longer collide.
- `VARIANT_SPLIT`: every `item_index` now points at the surviving item. Merges
  are found first and applied in input order, so a later pop cannot invalidate
  an index that was already recorded. A merge no longer loses data: the
  variant's link, gtin and other_ids move onto the survivor when it lacks them,
  and a value both lines carry differently is reported in the finding
  (`old_value`) with `uncertain: true`. Variant notes keep input order.
- `VARIANT_SPLIT` no longer deletes an order line it cannot read. The check
  asked whether the quantity *parsed*, so a line that stated `2 St.`, `2 Stck.`,
  `2 ST`, `2 VE`, `2 Paar`, `2 Pack`, `2,5 kg` or `2 Stueck` was folded into the
  first item and lost. It now asks whether the quantity field is empty, so a line
  that states a quantity is always a second position. The unit words are one
  shared list, used by the value parser and by the link-quantity patterns: `x`,
  `×`, `pcs`, `pieces`, `St.`, `Stck.`, `ST`, `Stk.`, `Stück`, `Stueck`, `VE`,
  `Paar`, `Pack`, `kg`, `units`, `sets`. Documented in `SPEC-REFERENCE.md`.
- `SET_MULTIPLIED`: corrected at most once per item, so two set lines cannot
  compound (8 → 4 → 2). The pack size comes from the source text only; a pack
  word in the extractor's description is no longer accepted as evidence. `5
  pack` is now recognised alongside `5-pack`, `5er-Pack` and `5er Pack`.
- `SET_MULTIPLIED` corrects only the item the set line belongs to. The item
  must be grounded on a line of the set block (the set line, or the line
  directly before or after the block) by its part number, GTIN, link,
  marketplace id or description words, and the pack marker must sit on that
  same line. Another product's "2er-Pack" no longer rewrites a correct
  quantity of 8, and a set line whose item line sits further away corrects
  nothing.
- A non-string `other_ids` entry no longer crashes the set check. A dict entry
  raised `TypeError: unhashable type: 'dict'` out of `ground()`, but only when
  the source carried a set line, because only that check builds the id index.
  Every check that reads `other_ids` now skips an entry that is not a string, and
  a blank string. The entry stays in the item. Documented in
  `SPEC-REFERENCE.md`.
- The docs no longer claim more than the code does. The module docstring said
  the module "never deletes an item" and "only adds a flag, attaches a value
  the source proves, or reports a fact": three checks remove, correct or
  restore one. The `_check_ids` docstring carried internal wording ("in this
  card"). `examples/grounding_example.py` and `tests/test_grounding.py` called
  their GTINs invented while quoting the public Wikipedia EAN-13, EAN-8 and
  UPC-A examples, one of which sits on a real GS1 prefix. Those numbers are now
  GTINs with the GS1 prefix 2, which is reserved for internal use, so no real
  product carries them, and both files say so. `harness_grounding.py` lost its
  shebang: it is a library and never had a `main()`.
- Quantities are read as counts of whole pieces, so a German thousands group is
  no longer read as a fraction: `1.000 Stück` and `1,000 x` are 1000, not 1. A
  separator with three digits behind it groups; one, two, four or more digits
  are a fraction (`1,5` is 1.5); with both `.` and `,` the last one is the
  decimal separator (`1.234,56` is 1234.56). A price is never a quantity: a line
  with a currency sign, and a bare fraction alone above a link, give the link no
  quantity. Two links on one line get one quantity each, and a link with no
  quantity in front of it gets none. `"2 pcs"` and `Decimal("2")` are quantities,
  so `VARIANT_SPLIT` keeps a line that carries one. Numeric parsing is capped:
  a digit run longer than 12 and a value above `10**9` are ignored, so `10**400`
  (`OverflowError` on `float()`) and a 5,000-digit run (`ValueError` on `int()`
  in Python 3.11+) give no quantity instead of an exception. Documented in
  `SPEC-REFERENCE.md`, "How a quantity is read".

## 0.3.0 — 2026-10-25

**A well-formed artifact can still hold data the source never said.** The linter
checks that an artifact meets its contract. It cannot check that the values inside
it are true. This release adds that second half as a library: `harness_grounding.py`
compares an extraction against the source text it came from and reports every value
the source does not support.

### Added
- `harness_grounding.py` — six checks, standard library only, no model and no
  network: `UNGROUNDED_ID`, `MISSING_GTIN`, `REDACTED_LINK`, `UNCOVERED_LINK`,
  `SET_MULTIPLIED`, `VARIANT_SPLIT`. `ground()` returns a deep copy plus findings
  and never mutates its input. It never deletes an item without a finding and never
  rewrites a value into something the source does not prove: an unsupported part
  number is flagged and marked `uncertain`, not repaired, because the source does
  not say what the right value is.
- `bench/grounding/` — synthetic order requests across the six error types plus
  error-free controls, a scoring runner and a generated `RESULTS.md`. The
  figures live in `RESULTS.md` and `README.md`, and a test asserts that both
  match what the runner produces; they move whenever a case is added. The
  benchmark reports; it does not gate CI.
- `examples/grounding_example.py` — one invented order request, three findings,
  runnable with no configuration.
- README section on the grounding checks, with the six checks, the synthetic
  numbers and the limits; `SPEC-REFERENCE.md` states that grounding is a library
  called from a producer and deliberately not a spec key.

### Changed
- `scripts/scan-secrets.sh`: the internal-vocabulary list (agent, tool and folder
  names) no longer lives in the published script. A gate that lists the thing it
  hides leaks it, which is the defect this gate was hardened against twice
  already. Both private lists (personal names, internal vocabulary) now load from
  files outside the repo; the repo carries only the mechanism and placeholder
  templates (`scripts/scan-secrets-*.example`).
- New `--generic` mode for CI, which cannot hold the private lists: it runs the
  checks that need none and prints that the rest were not run, instead of a plain
  "clean". The default mode still fails loudly when a list is missing.
- `harness_grounding` is packaged (`py-modules`), so an installed release carries
  the module the README documents.

## 0.2.1 — 2026-08-31

### Fixed
- The delivery invariant looked for positive-delivery fields (`delivered_at`,
  `delivered`, …) that a real runner turned out never to write, so every healthy
  job resolved to "unverified" and only a logged error could fail the check — a
  failure-signal model wearing a dead-man's-switch label. Added
  `HARNESS_ERRORS_CLEARED_ON_SUCCESS`, an explicit declaration that the runner
  clears its error on success, which makes a clean recent run count as delivered.
  A declaration rather than a default: assuming it silently is the false-green
  this check exists to prevent. Verified against a live runner where a job that
  had carried a delivery error showed clean fields after its next successful run
  while a still-failing job on the same runner kept its error. Two tests pin both
  readings, including that the declaration never masks a logged failure.

## 0.2.0 — 2026-08-26

**The delivery invariant had the blind spot it was written to close.** It matched
known failure phrases in a job's run record — a failure-*signal* model. It caught
a delivery that logged its own error, and silently passed two worse cases: a
delivery that just dropped, and a job that had stopped running altogether. Both
returned PASS. An invariant that only catches the failures polite enough to
announce themselves is not a liveness check. It is now a dead-man's switch:
positive delivery evidence is required and absence fails.

### Added
- `spec.schema.json` — JSON Schema for the front matter. Validated when
  `jsonschema` is installed; the near-miss key check runs either way and is the
  half that catches real mistakes (`max_age` instead of `max_age_h` does not
  error, it silently removes the freshness check).
- `max_age_h` accepts `{warn_after, error_after}` so an artifact that is slightly
  late warns before it fails. The plain number still means a hard limit.
- `golden:` — a glob of known-good artifacts that must always keep passing. The
  mechanical form of "never hand-edit a past artifact to turn the board green".
- `harness_compat:` — a spec can declare the linter version it needs and get a
  warning instead of being silently misread by an older one.
- `HARNESS_JOB_MAX_AGE_H` — dead-man's switch on the jobs themselves.
- `--version`; `CHANGELOG.md`; `pyproject.toml`; issue templates.

### Fixed
- The no-PyYAML fallback parser did not handle list-of-maps, so every
  `banned_patterns` entry was compiled as the literal string `pattern: "…`. It
  matched nothing and warned about nothing: the check was **silently dead** in
  exactly the zero-dependency environment the README advertises.
- A front-matter value of the wrong type crashed the linter instead of failing.
- `--all` silently overrode `--spec`; now mutually exclusive.
- `banned_patterns` are untrusted regexes — catastrophic backtracking measured at
  ~239s. Evaluation is capped at 5s and reported.
- `scripts/scan-secrets.sh` reported "clean" on a tree containing internal
  disclosures — converting *unchecked* into *verified*, which is worse than no
  gate. It now scans history as well as the tree and knows more secret classes.
- The same gate scanned `--all` refs, so a repo whose remote still carried a bad
  commit could never push the commit that removed it. Scoped to `HEAD`.
- Unclosed file handles.

- The fallback parser did not handle a plain nested mapping, so
  `max_age_h: {warn_after, error_after}` collapsed to an empty list and the
  freshness check **silently stopped running** — a 200-hour-stale artifact
  passed. The same silent-disable class as the `banned_patterns` bug,
  reintroduced by the ladder feature itself. A general parser-parity test now
  compares both parsers over every bundled spec.
- The fallback did not decode double-quoted YAML escapes, so `"\\b"` stayed
  three characters instead of becoming a regex word boundary. The same banned
  pattern therefore *meant something different* depending on whether PyYAML was
  installed.
- The delivery invariant reported PASS when `HARNESS_JOBS_GLOB` matched nothing,
  when a record was unreadable, and when no job in it was enabled. For a check
  whose purpose is "fail on absence", that is the silent-green failure one level
  up. Those now FAIL, FAIL and WARN.
- `--artifact` was silently ignored unless `--spec` was given; it is now required.
- `required_sections`/`required_markers` given as a string were iterated
  character by character, producing garbage failures instead of a type error.

### Changed
- Specs are genericised; the README no longer claims more than it delivers.

## 0.1.0 — 2026-08-25

Initial release.
