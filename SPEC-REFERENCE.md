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

## Grounding is not a spec key

`harness_grounding.py` is a library that a producer calls. It is not a front-matter
key, and none is planned. Add no key for it.

The linter reads an artifact that already exists on disk. A grounding check needs two
things the linter does not have: the extraction and the source text it came from. A
spec key would have to name both files, and it still could not say which item came
from which source line. Keep the call in the producer, where both are in hand.

```python
import harness_grounding as g
checked, findings = g.ground(extraction, source_text)
```

A producer that wants to gate its own output blocks on an item with `uncertain: true`,
or on a finding with action `flagged` or `reported`. Those two are the ones the module
cannot resolve on its own. The gating policy belongs to the producer, not to the spec.

### REDACTED_LINK restores only a unique match

A redaction cut leaves a link such as `https://de.aliexpress.com/item/1[Telefon]234567`.
The text before the placeholder is the prefix; the text after it is the suffix. The
module restores a source link only when **exactly one** source link starts with the
prefix **and** ends with the suffix.

- Exactly one match: the link is written back and the item gets `uncertain: true`.
  The finding is `REDACTED_LINK` / `restored`.
- No match, or more than one: nothing is rewritten. The item keeps the cut link and
  gets `uncertain: true`. The finding is `REDACTED_LINK` / `flagged`, with the cut
  link in `old_value` and `new_value` `None`.

The uniqueness rule exists because a shared prefix proves nothing. Every AliExpress
item id starts with `1005`, and two eBay links can share `/itm/1`, so the first link
with a matching prefix is often another product. A wrong restore is worse than a flag:
the item then quotes another product's price.

### Which links are product links

`product_link_id()` returns an id only for a link on a real marketplace domain.
The host is reduced to its registrable domain, and that domain must be a key of
`MARKETPLACE_DOMAINS` (`amazon.de`, `amazon.com`, `amazon.co.uk`, `ebay.de`,
`ebay.com`, `ebay.co.uk`, `aliexpress.com` and siblings). Any subdomain of a
listed domain is that marketplace, so `de.aliexpress.com` works.

The rule is exact on purpose. `amazon.example.com`, `www.ebay.attacker.example`
and `notamazon.de` are other people's domains; a product id read from them would
point at a product that cannot be checked, and `UNCOVERED_LINK` would then append
an item for a link that is not a shop's product page at all.

### Which digit runs are GTINs

A digit run of 8, 12, 13 or 14 characters with a valid GS1 check digit is a
candidate, not a GTIN. `MISSING_GTIN` needs two more conditions:

- **The line names it.** The line the run sits on must carry `GTIN`, `EAN`,
  `UPC`, `Barcode`, `Strichcode` or `bar code` (German and English, any case).
  `Tel. +49 89 12345670` and `Order 20261023` both pass the check digit and are
  not GTINs. Without the keyword there is no way to tell them apart.
- **It is not inside a link.** A 13-digit marketplace item number in a URL is a
  product position, not a GTIN. The rule is per occurrence: the same number
  outside the link on the same line still counts.

A supplier that prints a GTIN with no keyword gets no attachment. That is a
deliberate loss: a wrong GTIN is worse than a missing one, because the quote then
prices another product.

The GTIN is then attached to the item whose description shares at least half of
its content words with the line. Generic words do not count (`part`, `item`,
`quantity`, `GTIN`, `Barcode`, …). An overlap on "part" alone decides nothing,
so a line that names no product produces `reported` with `item_index: None`
rather than a GTIN on the wrong item.

### How an id is grounded

`UNGROUNDED_ID` grounds an id on a token boundary. A source that holds
`AC-4417` does not ground the part number `AC-441`: the character after the
match is a digit, so the occurrence sits inside a token. The same rule stops
`2610` from being grounded by the date `2026-10-26`.

A whole token of a source id is grounded. `AC-4417` is two tokens, so the part
number `4417` counts.

The joiners are `-`, `.`, `/`, `_`, whitespace and the hyphen lookalikes a PDF or
a mail client leaves behind: U+2010, U+2011, U+2012, U+2013, U+2014, U+2015, soft
hyphen U+00AD and zero-width space U+200B. A joiner is a boundary in the source
and is removed from the value, so `AC-4417`, `AC 4417`, `AC.4417` and `AC–4417`
(en dash) are one id, and none of them is flagged against the other. Punctuation
that is not a joiner is dropped from the value too, so `(AC-4417)` means
`AC-4417`.

### The finding contract

Every finding from every check has exactly eight keys:

| Key | Meaning |
|---|---|
| `code` | stable machine code: `UNGROUNDED_ID`, `MISSING_GTIN`, `REDACTED_LINK`, `UNCOVERED_LINK`, `SET_MULTIPLIED`, `VARIANT_SPLIT` |
| `action` | what `ground()` did: `flagged`, `attached`, `reported`, `restored`, `added`, `corrected`, `merged` |
| `field` | item key the finding concerns, or `None` when no single key is concerned |
| `old_value` | what that key held before this module touched it, or `None` |
| `new_value` | what this module wrote into that key, or `None` when it wrote nothing |
| `item_index` | index in the returned items list, or `None` |
| `input_index` | index in the caller's items list, or `None` for an appended item |
| `evidence` | one short quotable sentence |

Rules:

- There is no `value` key. It held the old value for some codes and the new one
  for others. Read `old_value` and `new_value`.
- `new_value` is `None` for `flagged` and `reported`: neither writes anything. A
  `reported` finding names the value it is about in `evidence`.
- For the list key `other_ids`, `new_value` is the entry that was added.
- `VARIANT_SPLIT` is the one code where the two indexes differ. `item_index` is
  the surviving item; `input_index` is the variant line that was folded into it.
- An appended item carries the marketplace id in `other_ids`
  (`["ASIN:B0TEST1234"]`). It never puts the id in `part_number` or
  `description`. Its `input_index` is `None`.
- `field` is `None` when the finding is about no single item key.

## Trust boundary

Specs are executable input: globs are expanded and read, patterns are compiled as
regexes. **Run this only against specs you control.** A hostile spec can read any
file the process can read; a crafted pattern can burn CPU, which is why regex
evaluation is bounded and reported rather than retried.
