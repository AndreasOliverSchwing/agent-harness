# Changelog

Versions follow [SemVer](https://semver.org/). The spec front matter is a public
contract: a breaking change to it means a major bump, and removed keys keep
working as aliases for one major version rather than disappearing.

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
