# Spec reference

A spec is a markdown file with YAML front matter. Everything above the `---` is
machine-checked; everything below it is prose for whoever writes the producer's
prompt, and is never parsed.

```yaml
---
harness: research-summary          # name shown in output; conventionally the filename
artifact_glob: $ARTIFACTS_DIR/market-brief-*.md
cadence: weekly                    # documentation only, not enforced
max_age_h: 192
min_bytes: 500
optional: false
required_sections: [Rubric Self-Check]
required_markers: ["Generated"]
self_eval_min: 10
banned_patterns:
  - pattern: "(?i)(studies show|experts say)(?![^.]*http)"
    reason: authority claims require a URL in the same sentence
---
```

## Keys

| Key | Type | Effect |
|---|---|---|
| `harness` | string | Display name. Defaults to the filename stem. |
| `artifact_glob` | string | Glob for the output to check. `~` and `$VARS` expand. The **newest** match by mtime is checked. Omit it for an on-demand harness — absence is then reported as a skip, never a failure. |
| `max_age_h` | int **or** `{warn_after, error_after}` | Freshness limit in hours. A plain number is a hard limit. The two-tier form warns first and fails later, so a brief one day late reads differently from one five days late. |
| `golden` | string | Glob of known-good artifacts that must **always** keep passing this spec. Checked in addition to the newest one, so a change to the spec or the linter that would break previously-good output fails in CI rather than on the next real run. This is the mechanical form of "never hand-edit a past artifact to turn the board green". |
| `harness_compat` | string | Minimum linter version, e.g. `">=0.2"`. A spec written for a newer linter warns instead of being silently misread by an older one. |
| `min_bytes` | int | Fail if the artifact is smaller. **Keep this low.** "Nothing happened this week" is a valid answer, and a floor that punishes an honest empty week teaches the producer to pad. |
| `optional` | bool | When true, a missing artifact is a warning rather than a failure. |
| `required_sections` | list of strings | Each must appear as a markdown heading (`#`–`####`). Case-insensitive. |
| `required_markers` | list of strings | Each must appear anywhere in the text. Use this for footers and non-heading text. |
| `self_eval_min` | int | Fail if fewer than N checkbox lines (`- [ ]` or `- [x]`) are present. Counts **checked and unchecked alike** — see below. |
| `banned_patterns` | list of `{pattern, reason}` | Fail if any regex matches. `reason` is shown with the failure. Evaluation is capped at 5 seconds per pattern. |

Unknown keys are ignored, so `version`, `owner`, `cadence` and similar are free to
use as documentation. But a key that is *almost* a known one is reported: writing
`max_age` instead of `max_age_h` does not error, it silently removes the freshness
check, and that is the same silent-disable class as a dead safety check. Install
`jsonschema` for full validation against `spec.schema.json`; the near-miss check
runs with or without it.

Editors can validate specs directly:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/AndreasOliverSchwing/agent-harness/main/spec.schema.json
```

## On `self_eval_min`

It counts boxes, not ticks — deliberately.

An **unchecked box is the system working**: the producer honestly reporting a miss.
What this check catches is a rubric that was never filled in at all. It cannot
catch a *false* tick, and nothing mechanical can — which is exactly why a false
`[x]` is the one unforgivable failure in this method, and why you never hand-edit
a past artifact to turn the board green. Patch the spec or the producer, and let
the next scheduled run prove it.

## Environment

| Variable | Meaning | Default |
|---|---|---|
| `HARNESS_SPECS_DIR` | directory of specs | `./specs` |
| `ARTIFACTS_DIR` | root for `$ARTIFACTS_DIR` in globs | `./examples` |
| `STATE_DIR` | root for `$STATE_DIR` in globs | same as `ARTIFACTS_DIR` |
| `HARNESS_JOBS_GLOB` | job-runner run records, for the delivery invariant. Several globs may be joined with the OS path separator (`:` on Unix) | unset → invariant skipped |
| `HARNESS_JOB_MAX_AGE_H` | dead-man's switch: fail when an enabled job has not run in this many hours. Set it to your longest cadence plus a grace window | `0` → disabled |

The delivery invariant reads these keys from each job record, in order:
`delivered_at`, `delivered`, `last_delivery_at`, `last_delivered_at`,
`delivery_ok`, `last_delivery_ok`. A truthy value is positive evidence; an
explicit `false`/`null` is a failure; absence of all of them is reported as
unverified rather than assumed good.

## CLI

```
harness_lint.py --all [--invariants] [--json]
harness_lint.py --spec NAME [--artifact FILE] [--json]
```

`--all` and `--spec` are mutually exclusive. `--artifact` overrides the glob for a
single spec, which is how you check a specific file before it ships.

Exit code is the number of failing harnesses plus failing invariants, capped at
125. Zero means everything checked passed.

## Trust boundary

Specs are executable input: globs are expanded and read, patterns are compiled as
regexes. **Run this only against specs you control.** A hostile spec can read any
file the process can read; a crafted pattern can burn CPU, which is why regex
evaluation is bounded and reported rather than retried.
