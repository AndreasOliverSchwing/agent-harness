# Release gate for harness_grounding

A check may propose a value only when it proposes no WRONG value on this set. The value can be an owner for a GTIN, a quantity for a link, a set correction, a merge or a restored link. A check that fails the set reports what it found and proposes nothing. `tests/test_release_gate.py` asserts the gate on every test run.

```bash
python3 -m unittest tests.test_release_gate -v   # the gate, as a test
python3 bench/gate/score.py .                    # the full table, including the opt-in inference
python3 bench/gate/generate.py                   # writes cases.json for inspection (not committed)
```

## What a case is

A case is an extraction, the mail it came from, and the truth a careful reader takes from the mail. The truth is set by construction: the generator writes each mail from a model of its positions, so it knows which line belongs to which item.

| Truth key | Meaning |
|---|---|
| `gtin_owner` | GTIN the extraction lacks → index of the item it belongs to, or `null` when no extracted item owns it |
| `link_quantity` | product link no item covers → `[quantity, unit]` the mail states, or `null` when it states no count |
| `set_fix` | item index → the set count the item should carry |
| `variant_of` | index of a split variant line → index of its item |
| `restored_link` | item index → the link a redaction cut, or `null` when the mail cannot tell which one |
| `ungrounded` | `[item index, field]` pairs whose id the mail does not print |

A proposal that matches the truth is correct. Any other proposal is WRONG: another item, another value, or a value where the truth states none. A truth entry with no proposal is an abstention. That costs recall, which `score.py` reports, but it never fails the gate.

**Mirror layouts are how ambiguity gets into the set.** An EAN line between two positions, or a quantity line between two links, is printed by two layouts with different owners: "EAN under each position" and "EAN above each position". The set holds both, so any rule that guesses fails one of the pair. The only rule that passes both is to report.

## What the set covers

| Family | Cases | Layouts |
|---|---|---|
| `gtin` | 2,356 | 1–5 positions; position markers (`Pos. 1:`, `1)`), blank-line blocks, plain lists, lists without part numbers, tables; the EAN on the product line, under it, above it, or after a `Menge` line; EANs on every position or only some; the extraction missing a GTIN, carrying one, or missing a whole position |
| `link` | 156 | 1–4 marketplace links; the quantity in front, behind, above or below the link, or absent; units Stück, Stk., St., x, Sets, Paar; a date, a datasheet link or a price nearby; thousands written `1.000`, `10 000`, `1'000` |
| `set` | 784 | 1–3 positions; the set line on the product line, under it, as a stack, or followed by more lines; a neighbour that is also a 2-pack with the same piece count; the owner holding pieces, sets, no quantity or text; shop links whose product id is in the query string |
| `variant` | 25 | attribute lines and indented variant lines (merge), separate positions with one part number (no merge), unmarked repeats (ambiguous), own GTINs per line; survivor and variant values of every shape |
| `redacted` | 9 | full, partial and middle cuts; one or two candidate links; the original missing |
| `id` | 26 | ids with NBSP, Unicode dashes, soft hyphens, fullwidth forms, `×`; invented ids and near misses |
| `probe` | 92 | every input from the QA rounds and the three reviews that states an answer |

Synthetic data only: invented part numbers and links, GTINs with the restricted-circulation prefix 2.

## Changing the set

The set is frozen on purpose. `cases.sha256` pins the digest of what `generate.build()` returns, and the gate test fails when they differ. The point of a fixed set is that it does not move to fit the code. A layout the set lacks is added in a commit of its own: regenerate, pin the new digest, and say in the message which layout was missing.
