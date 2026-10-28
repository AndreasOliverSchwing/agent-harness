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

### Fix round 3

The third review round of 2026-10-27 (branch `feat/grounding-checks`) is
flag-first: each item narrows a rule so that it writes or claims less. Each entry
below is one card of that round.

- **`ground()` only suggests by default (3.1).** The three checks that would write
  an extraction value — `SET_MULTIPLIED`, `MISSING_GTIN` and the quantity of an
  item appended for an `UNCOVERED_LINK` — now write it only with
  `ground(..., apply=True)`. The default `apply=False` records a finding with the
  new action `suggested`: its `new_value` is the value `apply=True` would write,
  and every item keeps the value the caller passed in. The item is still marked
  `uncertain: true`, the placeholder item for an uncovered link is still appended
  (without quantity and unit), and a `MISSING_GTIN` no item can take is still
  `reported`, in both modes. A non-bool `apply` raises `TypeError`; the flag is
  keyword-only. Documented in `README.md`, `SPEC-REFERENCE.md` and the module
  docstring.
- **A marketplace id has a real length, so a mail of hostile links stays linear
  (B1′).** `_EBAY_ITEM` and `_ALIEXPRESS_ITEM` accepted a digit run of any
  length. Every distinct id length became a form length in the covered-link
  index, and the index held every substring of every one of those lengths, so
  one untrusted mail cost gigabytes: 24 KB took 0.95 s and 229 MB, 98 KB took
  11.25 s and 2,528 MB. An eBay item number has 9 to 13 digits and an
  AliExpress item id 9 to 17; a shorter or longer run is not a product link, so
  `product_link_id()` returns `None` for it. The same two mails now take 0.03 s
  and 2 MB, and 0.11 s and 7 MB. Documented in `SPEC-REFERENCE.md`, `README.md`
  and the `product_link_id()` docstring.
- **The line-form scan yields a form instead of holding a set (N5′).**
  `_line_id_forms` put every form up to the longest indexed id into a set, so a
  100 KB line of one-letter tokens beside a 64-character part number built
  millions of forms: 1.86 s and 568 MB. It now yields each form and builds it
  only at a length an indexed id actually has, because a form of any other
  length cannot match. The same input takes 0.09 s and 19 MB. The existing
  set-check tests stay green; documented in `SPEC-REFERENCE.md` and `README.md`.
- **A date is never a quantity (B2′).** `_split_quantity_text` joined any
  repeated separator, so `26.10.2026` read as 26102026 and a delivery date alone
  on a line above or below a link became an order of 26 million pieces;
  `_to_number("1.5.2026")` read as 152026. A repeated separator now groups
  thousands only when the first group has 1 to 3 digits and every later group
  has exactly 3; anything else is not a number and `_to_number` returns `None`.
  A first group of `0` never groups thousands, so `0,250` is 0.25 and not 250.
  `1.000`, `1.000.000`, `1,5`, `12,50`, `1.234,56` and `1,234.56` keep their
  values. Documented in `SPEC-REFERENCE.md`, `README.md` and the module
  docstring.
- **An uncovered link takes its own quantity, never the line below its
  neighbour's (S5′).** In the "quantity above the link" layout a link claimed
  the line below it even after it had already taken the quantity above itself,
  and any URL claimed it. `2 Stück / <a> / 3 Stück / <b> / 4 Stück / <c>` gave
  `[2, 4, None]` and now gives `[2, 3, 4]`; a datasheet PDF above a quantity
  line made the link below it read `None` instead of 3. Only a product link
  claims the line below, and only when it took no quantity from its own line or
  from the line above. The quantity-below layout is unchanged: `<a> / 2 Stück /
  <b> / 3 Stück` is still 2 and 3. Both modes are tested. Documented in
  `SPEC-REFERENCE.md`, `README.md` and the `_claims_the_line_below()` and
  `_link_quantity()` docstrings.
- **An uncovered link states the source's unit, not always "piece" (S6′).** An
  appended item wrote `unit: "piece"` whatever the source said, and the quantity
  patterns read `VE`, `Paar`, `Pack` and `kg` as piece words: `2 Paar <link>`,
  `3 VE <link>`, `1,5 kg` above a link and `0,250 kg <link>` all became counts of
  pieces. Only two units are known now. A piece word (`x`, `×`, `pc`, `pcs`,
  `piece`, `pieces`, `Stück`, `Stueck`, `Stk.`, `St.`, `Stck.`, `unit`, `units`)
  or no unit word gives `piece`; `set` and `sets` give `set`; any other word
  gives no quantity, so nothing is suggested and nothing is written. `A` / `2
  Sets` is quantity 2 with unit `set`. Both modes are tested. Documented in
  `SPEC-REFERENCE.md`, `README.md` and the module docstring.
- **A reported GTIN names itself in the evidence (S12′).** The evidence quoted
  the position line and cut it at 200 characters, so a 250-character line that
  ends in `, EAN 2000000000015` produced a `reported` finding with `old_value`,
  `new_value` and `item_index` `None` and an evidence that stopped before the
  number: nothing said which GTIN it was about. Every `MISSING_GTIN` finding's
  evidence now starts with `GTIN <number>: `, then the quote and the note, so
  the cut can never drop the number. Documented in `SPEC-REFERENCE.md`,
  `README.md` and the module docstring.
- **Every quoted value in an evidence is capped (N2′).** `SET_MULTIPLIED` quoted
  the whole set-line match, so `Menge:` followed by 50,000 spaces gave 50,112
  characters of evidence. `REDACTED_LINK` quoted the cut link and the candidate,
  so a 50 KB path gave 100,209 characters. Both now pass every quoted value
  through `_quote`, so each stops at 200 characters with `…`. `old_value` and
  `new_value` still carry the full value. Documented in `SPEC-REFERENCE.md`.
- **An id that occurs verbatim is grounded, whatever punctuation it holds
  (S7′).** The token index made every character that is not alphanumeric, not a
  joiner and not whitespace a hard stop, so an id the source printed character
  for character was flagged: `NYM-J 3x1,5`, `H07V-K 1x2,5`, `SPAX 4,5x40`,
  `M8×40` and `AC-4417(B)` all raised `UNGROUNDED_ID`. An id is now also
  grounded when its own text occurs in the source, case-insensitive, with both
  its start and its end on a token boundary, so `NYM-J 3x1,5` is grounded and
  the same value is still flagged against `NYM-J 3x1,55`. The minus sign U+2212
  and the fullwidth hyphen U+FF0D are joiners now, and the word joiner U+2060
  and a byte-order mark U+FEFF are ignored like U+200B, so a PDF or a mail
  client that swaps the hyphen does not break an id. The round-2 rule is
  unchanged: `Pos. 3: AC-4417, 8 Stück` does not ground `AC-44178`. Both modes
  are tested. Documented in `SPEC-REFERENCE.md`, `README.md`, the module
  docstring and `_id_occurs_verbatim()`.
- **"EAN Nr." and "GTIN No." are read (S8′).** The label group in
  `_GTIN_VALUE` put its word boundary after the optional full stop,
  `\s+(?:nr\.?|no\.?)\b`. A full stop is not a word character, so the boundary
  after `Nr.` fell between two non-word characters and never matched: `EAN Nr.
  2000000000015`, `EAN Nr.:`, `GTIN No.` and `Barcode Nr.` produced no finding
  at all. The boundary now stands before the stop, `(?:nr|no)\b\.?`, so all four
  spellings name the run that follows them. `EAN-Code:` and `EAN-Nummer:` stay
  out of scope: the docs never claimed them. Both modes are tested. Documented
  in `SPEC-REFERENCE.md`, `README.md` and `_gtin_candidates()`.
- **A GTIN never goes to its neighbour (S9′).** Two regressions from the S6
  fix, both about a GTIN that lands on the wrong item. (a) A bare GTIN line was
  always read together with the line above it, so in a list that prints the
  GTIN above its own position line the next position's number went to the item
  above: `EAN: G1 / AC-44170 … / EAN: G2 / NW-22010 …` put G2 on AC-44170. A
  bare GTIN line now takes the line above only when the line below names no
  item, so both numbers are reported and nothing is written. (b) Any part
  number on the line won, even one mentioned in passing:
  `Pos. 2: Dichtungssatz passend für AC-44170, …, EAN G2` gave G2 to AC-44170.
  When the line holds one item's part number and exactly one OTHER item's
  description words, the line is ambiguous and the GTIN is reported. Both parts
  are tested in both modes. Documented in `SPEC-REFERENCE.md`, `README.md` and
  the `_check_missing_gtins()`, `_gtin_owner()` and `_names_an_item()`
  docstrings.
- **A stack of set lines corrects only the position it belongs to (S1′).** The
  per-line owner pass ran before the block pass, so the second set line of a
  stack took the position below it and one stack was split between two
  positions. `Pos. 1: AC-44170 … 2er-Pack / Bestellmenge: 4 x 2 /
  Liefermenge: 4 x 2 / Pos. 2: NW-22010 … 2er-Pack, 8 Stück` rewrote both items
  to 4 sets; only AC-44170 is corrected now. A set line reads its own line and
  the line above; the line below is read once for a whole stack, and only when
  no line above the stack grounds an item. On the realistic German mail this
  keeps Pos. 50 at "6 Stück" instead of 3 sets. Both modes are tested.
  `test_a_block_of_set_lines_corrects_only_its_own_item` asserted the split the
  docs never claimed; it now asserts the documented rule. Documented in
  `SPEC-REFERENCE.md`, `README.md` and the `_check_set_multiplied()` and
  `_set_owner_lines()` docstrings.
- **A set line on an item whose quantity does not parse corrects nothing
  (S2′).** The `correctable` filter only kept items with a readable quantity, so
  a set line that sat on its own item's line fell through to the item above it:
  `NW-22010 …, 8 Stück / AC-44170 …, Menge: 4 x 2` rewrote NW-22010's correct 8
  to 4 sets. A line that grounds an item with a non-empty quantity the parser
  cannot read is now that item's line, and no other item takes it. Tested in
  both modes. Documented in `SPEC-REFERENCE.md`, `README.md` and the
  `_check_set_multiplied()` and `_set_owner_lines()` docstrings.
- **A line that names an item by its id grounds no other item by description
  words (S3′).** A description of two words or fewer matched on one shared
  word, so the line `Pos. 1: NW-22010 Kabelbinder 200 mm, 2er-Pack, Menge:
  4 x 2` also grounded the item "Kabelbinder schwarz", which has no id in the
  source, and its correct 8 became 4 sets. A line that grounds an item by its
  part number, GTIN or link now grounds no other item by description words: the
  line names one product, so it is that product's line. Tested in both modes.
  Documented in `SPEC-REFERENCE.md`, `README.md` and the `_grounded_by_line()`
  docstring.
- **A placeholder on the owner line takes no set correction (N3′).** The
  correction loop walked every item the owner line grounds, and the item this
  module appends for an uncovered link sits on that line. With `apply=True` the
  link check has already written the multiplied quantity there, so the
  placeholder was rewritten to 4 sets as well, although `SPEC-REFERENCE.md`
  says a placeholder never takes a set line. An item with `input_index` `None`
  is skipped. Tested in both modes. Documented in `README.md` and the
  `_check_set_multiplied()` docstring.
- **A bare GTIN line follows the mail's layout (S9′a follow-up).** Card 3.4
  reported every bare GTIN line that had an item line below it, so the common
  layout that prints the EAN under each position lost every number but the last:
  `AC-44170 …\nEAN: G1\nNW-22010 …\nEAN: G2` reported G1 and attached only G2. A
  bare line with an item-naming line on exactly ONE side is now anchored, and it
  reads that neighbour, above or below. The anchored lines also state the mail's
  layout: when every anchor reads the line above, a bare line with item lines on
  both sides reads the line above, and when every anchor reads the line below,
  such a line reads the line below. A mail with no anchor, or with anchors that
  disagree, reports the line between two items, as card 3.4 did. Both layouts
  and the S9′ input now attach every number to its own position; tested in both
  modes. Documented in `SPEC-REFERENCE.md`, `README.md`, and the
  `_check_missing_gtins()`, `_bare_gtin_layout()` and `_gtin_owner()` docstrings.

- **A link with a query string grounds its own line (S4′).** The set check
  indexed an item's link exactly as the caller wrote it and looked it up among
  the cleaned source links, so an item whose only grounding was its link was not
  on the line when that link kept its query string:
  `Pos. 1: Kabelbinder 5er-Pack <link>?spm=a2g0o.detail.0.0` over
  `Menge: 28 x 5` left the quantity at 140 with no finding. Both links are now
  compared without their query string and fragment, the way `find_links()` reads
  a source link and the way fix round 2 already did for `REDACTED_LINK` (S13).
  28 is suggested in the default mode and written with `apply=True`. Documented
  in `SPEC-REFERENCE.md`, `README.md` and the `_grounded_by_line()` docstring.

- **A GTIN never goes to an item whose part number is printed elsewhere
  (S10′).** The description fallback of the GTIN owner lacked the guard the set
  check has. `Pos. 1: AC-44170 Kabelbinder 200 mm schwarz, 8 Stück` over
  `Pos. 2: Kabelbinder 200 mm weiß, 4 Stück, EAN G1` handed Pos. 2's GTIN to
  AC-44170, because both lines share the word "Kabelbinder". An item whose own
  part number occurs on a source line is now not a candidate for the description
  fallback: the line that prints the id is that item's line. G1 is reported in
  both modes. The id-in-source set is built once per check and only when a
  description fallback is reached. Documented in `SPEC-REFERENCE.md`,
  `README.md` and the `_check_missing_gtins()`, `_gtin_owner()` and
  `_ids_printed_on_a_line()` docstrings.

### Fixed in the second review round

The recheck of 2026-10-27 (branch `feat/grounding-checks`) found defects on
ordinary German supplier mail. Each entry below is one item of that recheck.

- `MISSING_GTIN` takes only the run that follows the keyword. The keyword was
  checked per line, so another number on that line became the GTIN:
  `Bestellung 20261023, Sechskantschraube M8 AC-44170, EAN 2000000000015`
  attached `20261023`, and `EAN auf Anfrage unter Tel. 089 12345670` attached
  `12345670`. The value pattern now needs the digit run directly after the
  keyword, with an optional `-8`/`-12`/`-13`/`-14` suffix or a `Nr.`/`No.`
  label in between.
- `MISSING_GTIN` writes a GTIN only to the item its line names. Two siblings
  that shared every description word both took the first GTIN, a tie went to
  the first item, and an item this module appended from a bare link received
  one. The part number on the GTIN's line now decides, a bare GTIN line is read
  with the line above it, the description counts only when exactly one item
  matches, and a tie, no match or an appended item is reported with
  `item_index` `None`.
- `MISSING_GTIN` reads the keyword spellings real mail writes: `EAN13`,
  `GTIN13`, `EAN-13`, `GTIN-13`, `EANs`, `Barcodes`, `Bar-Code` and
  `Strichcodenummer` all name the run that follows. The length suffix may end
  with a hyphen, a space or no separator. The separator after the keyword is
  `\s*(?::\s*)?` instead of `\s*:?\s*`: the old form split a run of spaces
  between two quantifiers, so 20,000 spaces after `EAN` took 2.8 s and now
  takes 5 ms.
- An item takes at most one attached GTIN. Every other number the source names
  is reported with `item_index` `None`, because an item that collects every
  number on a hostile line is not a GTIN. A finding's evidence quote stops at
  200 characters, as the finding contract says.
- `UNCOVERED_LINK` gives an appended item only its own quantity line. The
  second link of `A` / `2 Stück` / `B` / `3 Stück` took the first link's
  quantity and gave `[None, 2]`. The number pattern also starts on a left
  boundary now, so `1.000.000 Stück` no longer reads as 0.
- `SET_MULTIPLIED` reads the labels real mail writes: `Bestellmenge`,
  `Liefermenge`, `Anzahl`, `Qty.` and `Menge (VE)` count beside `Menge` and
  `Qty`. Before, each of those lines read as a plain quantity, so 140 pieces
  stayed 140 pieces instead of 28 sets of 5.
- `UNGROUNDED_ID` grounds an id only across the documented joiners. Punctuation
  that is not a joiner (comma, pipe, bracket, semicolon, colon) ends a token,
  so `Pos. 3: AC-4417, 8 Stück` no longer grounds `AC-44178`, and a joiner
  between two digits ties them into one number, so the date `2026-10-26` no
  longer grounds `1026`.
- `VARIANT_SPLIT` reports one finding per field it writes. A merge that moved
  the link, `gtin` or `other_ids` onto the survivor wrote them with no finding,
  and dropped the merged line's own note, unit and price. Each written field
  now has a finding with the `input_index` of the merged line, the merged
  note is appended, and a unit or price that differs is reported.
- A `VARIANT_SPLIT` conflict writes nothing, so `new_value` is `None`.
  `old_value` names the merged line's value and the evidence names the value
  the survivor kept.
- `item_index` and `input_index` can differ after any merge, not only for
  `VARIANT_SPLIT`. A merge removes an item, so every finding recorded after it
  can carry two different numbers, whatever its code. `README.md` and
  `SPEC-REFERENCE.md` said otherwise and now say this.
- The link check and the GTIN check index their work once per mail. The link
  check rescanned every item, including the items it had just appended: 100 KB
  with one eBay link per line took 11.98 s and now takes 0.10 s. The GTIN check
  re-tokenised the line for every number on it: 100 KB of GTINs on one line
  took 185.78 s and now takes 0.30 s.
- The set check ignores an id longer than 64 characters when it matches a
  line. One 1,000-character part number beside a line of 3,300 tokens built a
  1,000-character form for every token: 352 MB and 1.33 s. The same input now
  takes 0.011 s and 14 MB. `UNGROUNDED_ID` still reads an id of any length,
  because that check builds no forms.
- `REDACTED_LINK` compares a cut link without its query string. The source
  link is stored cleaned, so it holds no `?spm=…`; an extractor keeps it, so a
  cut AliExpress link never matched, was flagged, and `UNCOVERED_LINK` then
  appended the same product a second time. The cut is now looked for in the
  link without its query string and fragment, and one match restores it. Two
  matches still stay flagged.
- Three timing tests could not fail, because their sources never reached the
  code they claimed to guard. The pack-marker test now holds a set line beside
  the 20,000-digit run (a bare `(\d+)` takes it from 0.010 s to 11.53 s), the
  set-line test uses 1,000 blocks on 1,000 lines (rebuilding the per-line facts
  takes it from 0.053 s to 30.10 s), and the digit-run test guards the
  de-duplication of 40,000 distinct numbers (a list scan took 6.36 s; a set
  takes 0.050 s).

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
