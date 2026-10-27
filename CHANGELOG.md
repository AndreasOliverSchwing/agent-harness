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

### Fixed
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
- `SET_MULTIPLIED`: corrected at most once per item, so two set lines cannot
  compound (8 → 4 → 2). The pack size comes from the source text only; a pack
  word in the extractor's description is no longer accepted as evidence. `5
  pack` is now recognised alongside `5-pack`, `5er-Pack` and `5er Pack`.

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
- `bench/grounding/` — 30 synthetic order requests across the six error types plus
  four error-free controls, a scoring runner and a generated `RESULTS.md`. Measured:
  30 of 30 injected errors caught, 0 of 4 false alarms, 14 of 30 corrected to gold.
  The 16 uncorrected cases cannot reach gold by design (`UNGROUNDED_ID` flags
  instead of repairing; `UNCOVERED_LINK` appends a placeholder). The benchmark
  reports; it does not gate CI.
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
