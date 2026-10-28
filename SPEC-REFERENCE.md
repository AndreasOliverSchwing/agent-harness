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
checked, findings = g.ground(extraction, source_text)            # suggest only
checked, findings = g.ground(extraction, source_text, apply=True)  # write
```

The arguments are checked, not guessed. `ground()` raises `TypeError` when
`extraction` is not a dict, when `extraction["items"]` is present but not a list,
when `source_text` is not a str, or when `apply` is not a bool. The message names
the argument and the type it received. Coercing a wrong type would be worse than
the error: a bare list or a tuple of items came back as `{"items": []}` with no
finding, and bytes as a source flagged every id. A missing `items` key stays
allowed and returns an empty list, because the data model tolerates missing keys.
`apply` is keyword-only, so a third positional argument cannot be read as a flag
by accident.

### Suggest by default

`apply=False` is the default. Three checks would write an extraction value —
SET_MULTIPLIED, MISSING_GTIN, and the quantity of the item appended for an
UNCOVERED_LINK — and each writes it only with `apply=True`. With the default
each records a finding with action `suggested`:

- `new_value` is the value `apply=True` would write;
- the item keeps the value the caller passed in, and is marked `uncertain: true`
  because the source does not support that value;
- the item appended for an uncovered link is still appended, with `link`,
  `other_ids` and `uncertain`, and without a quantity and without a unit. When a
  quantity was read for the link, a second finding follows its `added` finding
  directly: code `UNCOVERED_LINK`, action `suggested`, field `quantity`,
  `old_value` `None`, `new_value` the quantity, `item_index` the appended item,
  `input_index` `None`. Its evidence names the unit word `apply=True` writes.

`apply=True` restores the writing behaviour: `corrected`, `attached`, and `added`
with the source's quantity and unit. A `reported` MISSING_GTIN finding is the same in
both modes, because neither writes anything. VARIANT_SPLIT, REDACTED_LINK and
UNGROUNDED_ID behave the same in both modes.

A producer that wants to gate its own output blocks on an item with `uncertain: true`,
on a finding with action `flagged` or `reported`, or on a `suggested` finding it
has not yet decided about. `flagged` and `reported` are the ones the module
cannot resolve on its own; `suggested` is the one the module will not resolve
without permission. The gating policy belongs to the producer, not to the spec.

### REDACTED_LINK restores only a unique match

A redaction cut leaves a link such as `https://de.aliexpress.com/item/1[Telefon]234567`.
The text before the placeholder is the prefix; the text after it is the suffix. The
module restores a source link only when **exactly one** source link starts with the
prefix **and** ends with the suffix.

Both sides are compared without the query string and the fragment. A source link is
stored cleaned, and an extractor keeps the `?spm=…` that almost every AliExpress link
carries, so a cut link that still holds its query string is compared as
`https://de.aliexpress.com/item/1[Telefon]234567`. A `[` inside a query string is not
a cut in a product path and raises no finding.

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

The id must also have the length its marketplace uses:

| Path | Digits | Id |
|---|---|---|
| `/dp/B0TEST1234`, `/gp/product/B0TEST1234` | 10, letters and digits | `ASIN:B0TEST1234` |
| `/itm/<number>` | 9 to 13 | `EBAY:<number>` |
| `/item/<number>` | 9 to 17 | `ALIEXPRESS:<number>` |

A shorter or longer digit run is not a product link: `product_link_id()` returns
`None` and the link is an ordinary link. The bound is what keeps the covered-link
index linear. Every distinct id length adds one form length to that index, and
the index holds every substring of that length, so an unbounded id length let one
98 KB mail of hostile links cost gigabytes.

An item covers a link when it holds the same cleaned link, or when the id of the
link occurs in its `part_number`, its `other_ids` or its own `link`. The covered
links are indexed once per mail, in a set of cleaned urls and a set of id forms,
and an item that `UNCOVERED_LINK` appends extends that index. Asking whether a
link is covered is therefore a lookup, not a scan of every item, so a mail with
one uncovered link per line stays linear in its own length.

### Which digit runs are GTINs

A digit run of 8, 12, 13 or 14 characters with a valid GS1 check digit is a
candidate, not a GTIN. `MISSING_GTIN` needs two more conditions:

- **The line names it.** The keyword `GTIN`, `EAN`, `UPC`, `Barcode`,
  `Bar-Code`, `Strichcode` or `Strichcodenummer` must stand in front of the
  number (German and English, any case, singular or plural: `EANs`,
  `Barcodes`). A length suffix or a `Nr.`/`No.` label may sit between them, with
  a hyphen, a space or no separator: `EAN`, `EAN:`, `EAN-13:`, `EAN13:`,
  `GTIN-13`, `EAN-Nr.:`, `EAN Nr.`, `EAN Nr.:`, `GTIN No.` and `Barcode Nr.` all
  name the run that follows.
  Only that run is the GTIN. Another number on the same line is not: in
  `Bestellung 20261023, Sechskantschraube M8 AC-44170, EAN 2000000000015` the
  only GTIN is `2000000000015`. `Tel. +49 89 12345670` and `Order 20261023`
  pass the check digit and are not GTINs.
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

### A GTIN is one number space

GS1 has one number space, not four. A GTIN-8, -12 and -13 are the GTIN-14 with
leading zeros, so `200000000011` and its padded form `00200000000011` are the
same GTIN. `gtin14(code)` returns that form, or `None` when the check digit is
wrong.

Both GTIN checks compare the padded form:

- `UNGROUNDED_ID` grounds a `gtin` value when the source holds the same number
  under another spelling. The extractor may write `00200000000011` where the
  source has `200000000011`.
- `MISSING_GTIN` does not attach a number that an item already carries. The
  item's `gtin`, `part_number` and `other_ids` all count, and a label in front of
  the number is ignored: `EAN:00200000000011` carries the GTIN.

### Which item a GTIN belongs to

`MISSING_GTIN` writes only to the item the GTIN's line names. The part number
decides first, on a token boundary: a supplier line prints the part number of
the product it prices, and two siblings can share every description word. A
part number the line only mentions in passing does not decide: when the line
holds one item's part number and exactly one OTHER item's description words,
the line is ambiguous and the GTIN is reported (`Dichtungssatz passend für
AC-44170` names the gasket, not AC-44170).

- A bare GTIN line (keyword and number, nothing else) names no product, so it
  reads the neighbour that names an item: the line directly above it, or the
  line directly below it. A bare line with an item-naming line on exactly ONE
  side is anchored, and it reads that neighbour. The anchored lines also state
  the mail's layout, because a supplier prints every EAN on the same side of its
  position: when every anchor reads the line above, a bare line with item lines
  on BOTH sides reads the line above too; when every anchor reads the line
  below, such a line reads the line below. A mail with no anchor, or with
  anchors that disagree, reports the line between two items, because the line
  above belongs to the previous position.
- When no part number stands on the line, the description decides, and only
  when exactly one item reaches the match ratio.
- A tie, or no match at all, is reported with `item_index` null. Nothing is
  written. One item in the extraction is not a reason to attach.
- An item this module appended never takes a GTIN. It carries a link and no
  product.
- An item takes at most one attached GTIN. A line that names a thousand numbers
  names one product, so the first number is attached and the rest are reported
  with `item_index` null. An item that collects every number on a hostile line
  is not a GTIN.

Each line is tokenised once, and the owner of a line is resolved once, no matter
how many numbers stand on it. The GTIN check is linear in the length of the mail.
The keyword pattern keeps a run of spaces with one quantifier: `\s*(?::\s*)?`,
not `\s*:?\s*`, because the second form splits the run between two quantifiers
and tries every split (20,000 spaces after `EAN` took 2.8 s; it now takes 5 ms).

### How an id is grounded

`UNGROUNDED_ID` grounds an id on a token boundary. A source that holds
`AC-4417` does not ground the part number `AC-441`: the character after the
match is a digit, so the occurrence sits inside a token. The same rule stops
`2610` from being grounded by the date `2026-10-26`.

A whole token of a source id is grounded. `AC-4417` is two tokens, so the part
number `4417` counts.

The joiners are `-`, `.`, `/`, `_`, whitespace and the hyphen lookalikes a PDF or
a mail client leaves behind: U+2010, U+2011, U+2012, U+2013, U+2014, U+2015, soft
hyphen U+00AD, zero-width space U+200B, minus sign U+2212, fullwidth hyphen
U+FF0D, word joiner U+2060 and byte-order mark U+FEFF. A joiner is a boundary in
the source and is removed from the value, so `AC-4417`, `AC 4417`, `AC.4417` and
`AC–4417` (en dash) are one id, and none of them is flagged against the other.
Punctuation that is not a joiner is dropped from the value too, so `(AC-4417)`
means `AC-4417`.

An id that occurs verbatim is grounded too, whatever punctuation it holds. The
source text is searched for the id's own characters, case-insensitive, and both
ends of the match must fall on a token boundary. This is what a German cable or
fastener designation needs: `NYM-J 3x1,5`, `H07V-K 1x2,5`, `SPAX 4,5x40`,
`M8×40` and `AC-4417(B)` hold a comma, a multiplication sign or a bracket, which
no joiner rule can absorb, and each is grounded when the source prints it. The
boundary rule still holds: `NYM-J 3x1,5` is not grounded by `NYM-J 3x1,55`,
because the character after the match is a digit.

Only a joiner may sit INSIDE an id that is compared in its flat form. Any other
punctuation ends the token, so a match that runs through `,` `|` `(` `)` `;` or
`:` grounds nothing:
`Pos. 3: AC-4417, 8 Stück` does not ground `AC-44178`, `AC-4417 | 8 | Stück` does
not ground `AC-4417-8`, and `Art.-Nr. 4417 (10 Stück)` does not ground `4417-10`.

A joiner between two digits belongs to the number it splits. The `-` in
`2026-10-26` ties `10` to `26`, so no id may start or end between them: `1026` is
the tail of that number and is flagged, while the whole `2026-10-26` is grounded.
A joiner after a letter ties nothing (`AC-4417`), and whitespace ties nothing, so
`4417` in `AC-4417` and `Pos. 1 4417` both still ground their id.

A German `ß` and the PDF ligatures `ﬁ` and `ﬂ` upper-case to two characters
(`SS`, `FI`, `FL`). The check compares upper-case text, so it keeps one boundary
slot per OUTPUT character. `Industriestraße 12` above an order therefore grounds
the ids in that order exactly as `Industriestrasse 12` does, and no id after the
`ß` shifts.

### How a quantity is read

A quantity counts whole pieces. That is the only fact the module needs, and it
makes a separator unambiguous without knowing the locale:

| Text | Digits behind the separator | Value |
|---|---|---|
| `1.000 Stück` | 3 | 1000 |
| `1,000 x` | 3 | 1000 |
| `1.000.000` | repeated, groups of 3 | 1000000 |
| `1,5` | 1 | 1.5 |
| `12,50` | 2 | 12.5 |
| `1.234,56` | both, `,` last | 1234.56 |
| `1,234.56` | both, `.` last | 1234.56 |
| `0,250` | 3, but the first group is `0` | 0.25 |
| `26.10.2026` | repeated, later group of 2 | none |
| `1.5.2026` | repeated, later group of 1 | none |

Three digits behind a separator group thousands. One, two, four or more digits
are a fraction. When both `.` and `,` occur, the last one is the decimal
separator. `"2 pcs"` and `Decimal("2")` are quantities, so a variant line that
carries one is a second position and `VARIANT_SPLIT` keeps it.

A repeated separator groups thousands only when the first group has 1 to 3
digits and every later group has exactly 3. Anything else is not a number, so a
delivery date on a line of its own (`26.10.2026`, `1.5.2026`, `13.11.2026`) is
never read as a quantity. A first group of `0` never groups thousands, so
`0,250` is 0.25 and not 250.

The unit words are `x`, `×`, `pcs`, `pieces`, `St.`, `Stck.`, `ST`, `Stk.`,
`Stück`, `Stueck`, `units` and `sets`. One list
serves every quantity pattern, so a word that parses in a value also parses on a
line of its own. `VE`, `Paar`, `Pack` and `kg` are not in the list: none of them
names a count of pieces, so a quantity they follow is not read at all.

An item appended for an uncovered link states the unit the source names. A piece
word, or no unit word, is `piece`; `set` and `sets` are `set`. Any other word is
no unit this module can name, so that link gets no quantity: nothing is suggested
and nothing is written.

`VARIANT_SPLIT` asks whether the quantity field is empty, not whether the number
parses. A field that holds `"2 St."` or `10**400` is a quantity the extractor
wrote down, so its line is a second position and is never merged. A merge must
not lose an order line.

A price is never a quantity. A line that carries a currency sign (`12,50 €`,
`EUR 12,50`, `Preis 12,50`, `total 12,50`) gives a link no quantity, and so does
a bare fraction on its own line above a link (`12,50`): a count of 12.5 pieces
does not exist, so the number is money.

Two links on one line get one quantity each, because each link reads only the
text between the previous link and itself (`4x <a> 2x <b>` is 4 and 2). A link
with no quantity in front of it gets none: the second link of `4x <a> <b>` does
not inherit the 4.

A link with nothing in front of it on its line reads the line directly above,
and only that line. That line must hold nothing but a quantity, and no link on
the line above that may own it: a link claims the line below it only when it is
a product link and took no quantity from its own line or from the line above, so
`"<a>\n2 Stück\n<b>"` gives `<a>` the 2 and `<b>` nothing. A blank line is
layout, so nothing beyond it is borrowed. A link alone on its line also reads the
line directly below, because a supplier writes the quantity under the position
as often as above it: `"<a>\n2 Stück\n<b>\n3 Stück"` is 2 and 3, and
`"2 Stück\n<a>\n3 Stück\n<b>"` is 2 and 3 too. An address that is not a product
link (a datasheet PDF, an order page) claims nothing, so the link below it reads
the quantity line between them. A link with no quantity of its own gets none.

The number pattern starts on a left boundary, so `1.000.000 Stück` reads as
1000000 and not as its last group `000`.

Parsing is capped so untrusted text cannot raise: a digit run longer than 12 is
ignored, and a value above `10**9` is not a quantity. `10**400` raises
`OverflowError` on `float()` and a 5,000-digit run raises `ValueError` on
`int()` in Python 3.11+, so both are rejected before conversion.

### A set line corrects only its own item

`SET_MULTIPLIED` rewrites a quantity only when the set line belongs to that item.
A set line carries a quantity label and an `N x M` pair. The labels are `Menge`,
`Bestellmenge`, `Liefermenge`, `Anzahl`, `quantity` and `qty`, in any case, with
an optional colon, an optional full stop (`Qty.`) and an optional packaging-unit
note (`Menge (VE)`). A bare `28 x 5` is not a set line: it is also how a
dimension is written.
Two conditions must hold, and both must hold on one line:

1. **The item belongs to the set line.** A set line belongs to the items
   grounded on its own line. When its own line grounds no item, it belongs to
   the line directly above it. A stack of set lines whose own lines ground
   nothing shares one owner line: the line above the stack, else the line below
   it, because a supplier writes one position's set lines together. The line
   below a stack is read once for the whole stack, and only when no line above
   the stack grounds an item. A set line of a stack never reaches the line
   below on its own: that split one stack between two positions and rewrote a
   correct quantity on the position below. Only an item
   the extractor produced and that states a quantity can own a set line, so a
   placeholder this module appended never takes one. A quantity the parser
   cannot read is a stated quantity here: an item on the set line keeps the
   line, and no other item takes it, so `"…, Menge: 4 x 2"` never rewrites the
   position above.
   A line grounds an item when the part number occurs on it on a token
   boundary, when its GTIN is on it, when the item's link is on it, or when it
   shares at least `MATCH_RATIO` of the item's description words. Both links
   are compared without their query string and fragment, the way
   `find_links()` reads a source link, so an item link that keeps its
   `?spm=…` is still the link on the line. A line that
   grounds an item by its id, GTIN or link grounds no other item by description
   words.
   The description rule is a fallback only. When the item's own id occurs
   anywhere in the source, only a line that carries that id grounds it. So
   `"NW-22010 Kabelbinder 2er-Pack"` never grounds `AC-44170 Schraube M8
   2er-Pack` through the shared word "Pack". The pack words (`pack`, `packs`,
   `packung`, `packungen`, `set`, `sets`, `satz`) are generic and never count
   as a description match.
   An id longer than 64 characters is not used for line matching. A part
   number of 65 characters or more is not a part number: it does not ground a
   line, so the set line it sits on owns no item. `UNGROUNDED_ID` still reads
   it, because that check does not build forms.
   The line-form scan builds a form only at a length an indexed id has, and it
   yields the form instead of collecting the forms in a set. A form of another
   length cannot match, so building it is wasted work: on a 100 KB line of
   one-letter tokens beside a 64-character part number the old scan held
   millions of forms, 1.86 s and 568 MB. The scan now takes 0.09 s and 19 MB.
2. **A pack marker that names the same pack size sits on the set line or on the
   owner line.** The marker must sit on one of those two lines, not anywhere in
   the mail.

A pack marker on another product's line is another product's pack. It proves
nothing about this item, so the item keeps its quantity and gets no finding. A
missed correction is safer than a wrong one.

    AC-44170 Schraube 2er-Pack, 8 Stück
    NW-22010 Kabelbinder 2er-Pack, Menge: 4 x 2

Item `AC-44170` stays at 8 pieces. The set line sits on the `NW-22010` line, so
it belongs to `NW-22010`, which is already 4 sets.

    NW-22010 Kabelbinder 2er-Pack
    Menge: 3 x 2
    AC-44170 Schraube M8, 6 Packungen à 2er-Pack

Item `AC-44170` stays at 6 pieces. The set line belongs to the line directly
above it, so the screws two lines below it are never corrected.

    AC-44170 2-pack
    Menge: 4 x 2

Item `AC-44170` with quantity 8 becomes 4 sets: the part number and the pack
marker sit on one line, and the set line is next to it. The finding's evidence
names the unit change: `unit "piece" -> "set"`.

An item is corrected at most once, and the pack size always comes from the
source text. The forms `5 pack`, `5-pack`, `5er-Pack` and `5er Pack` all name
the pack size 5.

The set check costs the length of the mail, not the square of it. The pack
markers are read once per line, not once per set line, and each pack-marker
pattern starts on a digit boundary and reads at most four digits, so a long
digit run is not retried at every position. The per-line facts (id form, token
boundaries, pack sizes, words, links, GTIN-14s) are built once for the whole
mail, so a mail with 1,000 positions costs 1,000 lines of work. Two timing tests
hold these bounds: one mail has a set line beside a 20,000-digit run, the other
has one set block per line for 1,000 positions.

### A merge reports every field it writes

`VARIANT_SPLIT` folds a repeated part number into the item it repeats. The
survivor is the first item of that part number; the merged line has no quantity
field of its own, so it is not a second position.

A merge writes four fields at most, and it reports each one it writes with its
own finding: `note`, `link`, `gtin` and `other_ids`. Each finding has
`item_index` of the survivor and `input_index` of the merged line, so a reader
can tell where the value came from. `other_ids` gets one finding per entry
added, and `new_value` is that entry.

Nothing a merge touches is dropped:

- the merged line's own `note` is appended to the survivor's note,
  `variant: <label> (<note>)`;
- a `unit` or a `price` is never written, because a merge cannot prove which
  value the order line means. A value that differs from the survivor's is
  reported as a conflict: `old_value` is the merged line's value, `new_value` is
  `None`, and `evidence` names the value the survivor kept. The two compare
  ignoring case and surrounding space, so `piece` and `Piece` are one value.

### The finding contract

Every finding from every check has exactly eight keys:

| Key | Meaning |
|---|---|
| `code` | stable machine code: `UNGROUNDED_ID`, `MISSING_GTIN`, `REDACTED_LINK`, `UNCOVERED_LINK`, `SET_MULTIPLIED`, `VARIANT_SPLIT` |
| `action` | what `ground()` did: `flagged`, `attached`, `reported`, `restored`, `added`, `corrected`, `merged`, `suggested` |
| `field` | item key the finding concerns, or `None` when no single key is concerned |
| `old_value` | what that key held before this module touched it, or `None` |
| `new_value` | what this module wrote into that key, or, for a `suggested` finding, the value `apply=True` would write; `None` when it wrote nothing and suggests nothing |
| `item_index` | index in the returned items list, or `None` |
| `input_index` | index in the caller's items list, or `None` for an appended item |
| `evidence` | one short quotable sentence |

Rules:

- There is no `value` key. It held the old value for some codes and the new one
  for others. Read `old_value` and `new_value`.
- `new_value` is `None` for `flagged` and `reported`: neither writes anything. A
  `reported` finding names the value it is about in `evidence`.
- `new_value` is NOT `None` for a `suggested` finding, although it writes nothing:
  it names the value `apply=True` would write. That is the whole point of the
  action — a caller can read the value, show it, and decide.
- `new_value` is also `None` for a `VARIANT_SPLIT` conflict. The survivor keeps
  its own value, so nothing is written; `old_value` is the merged line's value
  and `evidence` names the value the survivor kept.
- For the list key `other_ids`, `new_value` is the entry that was added.
- `other_ids` entries must be strings. A check that reads `other_ids` skips every
  entry that is not a string, and every blank string, so a caller-supplied dict,
  list or `None` cannot raise out of `ground()`. The entry stays in the item; the
  module only declines to read it as an id.
- `item_index` and `input_index` are the same number until an item is merged
  away. `VARIANT_SPLIT` removes an item, so after a merge, every later finding can
  have `item_index` different from `input_index`, whatever its code: `item_index`
  counts the returned list, `input_index` counts the caller's list.
- `evidence` is cut at 200 characters with `…` when the quoted line is longer.
  A hostile mail can put 100 KB on one line, and one finding may not cost the
  length of that line. Every quoted value is cut this way: the set line of a
  `SET_MULTIPLIED` finding, and both links of a `REDACTED_LINK` finding.
- Every `MISSING_GTIN` finding's `evidence` starts with `GTIN <number>: `, so
  the cut can never drop the number the finding is about. The quote and the note
  follow it.
- An appended item carries the marketplace id in `other_ids`
  (`["ASIN:B0TEST1234"]`). It never puts the id in `part_number` or
  `description`. Its `input_index` is `None`. With the default `apply=False` it
  carries no `quantity` and no `unit`; with `apply=True` it carries both, and the
  unit is the one the source names: `piece` for a piece word or no unit word,
  `set` for `set`/`sets`.
- `field` is `None` when the finding is about no single item key.

## Trust boundary

Specs are executable input: globs are expanded and read, patterns are compiled as
regexes. **Run this only against specs you control.** A hostile spec can read any
file the process can read; a crafted pattern can burn CPU, which is why regex
evaluation is bounded and reported rather than retried.
