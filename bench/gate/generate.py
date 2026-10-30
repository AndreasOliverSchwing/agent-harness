#!/usr/bin/env python3
"""Build the release gate for harness_grounding: a fixed case set with the truth stated by construction.

    python3 generate.py            # writes cases.json next to this file and prints its sha256

A check may propose a value (an owner, a quantity, a merge, a restored link)
only when it proposes no WRONG value on this set. A check that fails it reports
what it found and proposes nothing. The set is frozen: cases.sha256 pins the
digest of what build() returns, and score.py refuses to run when they differ, so
the set changes only when someone regenerates it on purpose and pins the new
digest in the same commit.

A case is an extraction, the mail it came from, and the truth a careful reader
takes from the mail:

    gtin_owner     GTIN the extraction lacks -> input index of the item it
                   belongs to, or None when no extracted item owns it
    link_quantity  product link no item covers -> [quantity, unit] the mail
                   states for it ("piece" or "set"), or None when the mail
                   states no count of pieces or sets for it
    set_fix        input index -> the set count the item should carry
    variant_of     input index of a split variant line -> input index of its item
    restored_link  input index -> the link the redaction cut, or None when the
                   mail cannot tell which link it was
    ungrounded     [input index, field] pairs whose id the mail does not print

A value the truth does not hold must not be proposed. None means a reader
cannot tell, so only an abstention passes. The matrix also holds mirror
layouts: where two layouts print the same structure with different owners (an
EAN line between two positions, a quantity line between two links), any rule
that guesses fails one of the pair.

Synthetic data only: invented part numbers and links, GTINs with the
restricted-circulation prefix 2.
"""
import copy
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def gtin13(body12):
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body12)))
    return body12 + str((10 - total % 10) % 10)


PNS = ["AC-44170", "NW-22010", "BX-30951", "KT-77120", "RM-50440"]
DESCS = ["Sechskantschraube M8x40 verzinkt", "Kabelbinder 200 mm schwarz",
         "Unterlegscheibe M10 Edelstahl", "Nylondübel 8 mm grau", "Gewindestange M10 1 m"]
QTYS = [8, 4, 25, 6, 50]
GTINS = [gtin13("2%011d" % (k + 1)) for k in range(5)]
LABELS = ["EAN:", "EAN", "EAN-13:", "GTIN:", "EAN Nr.", "GTIN No."]
GREETING = ["Guten Tag,", "bitte schicken Sie uns ein Angebot für folgende Artikel:", ""]
SIGNOFF = ["", "Mit freundlichen Grüßen", "Max Muster", "Einkauf", "Tel. 09391 123456"]

LINKS = ["https://www.amazon.de/dp/B0TESTAAA1", "https://www.ebay.de/itm/123456789012",
         "https://de.aliexpress.com/item/1005001234567.html", "https://www.amazon.de/dp/B0TESTDDD4"]
LINK_UNITS = [("Stück", "piece"), ("Stk.", "piece"), ("St.", "piece"), ("Sets", "set"), ("Paar", None)]
LINK_QTYS = [2, 3, 4, 5]

CASES = []
_counter = [0]


def add(case_id, family, items, source, **truth):
    """Append one case. truth keys with int keys are stored as strings (JSON)."""
    clean = {}
    for key, value in truth.items():
        if isinstance(value, dict):
            value = {str(k): v for k, v in value.items()}
        clean[key] = value
    CASES.append({"id": case_id, "family": family, "items": items,
                  "source_text": source, "truth": clean})
    _counter[0] += 1


def mail(lines, greeting=True, signoff=True):
    out = (GREETING if greeting else []) + list(lines) + (SIGNOFF if signoff else [])
    return "\n".join(out) + "\n"


# ── GTIN owner ──────────────────────────────────────────────────────────────
# Where each position's EAN line sits, per mail style. Styles with position
# markers or blank-line blocks keep the EAN inside the block; only the
# unmarked styles also print it above the product line.
GTIN_STYLES = {
    "pos": ("same", "below", "menge", "belowmenge"),
    "posbl": ("same", "below", "menge", "belowmenge"),
    "num": ("same", "below", "menge", "belowmenge"),
    "para": ("same", "below", "menge", "belowmenge", "above", "abovemenge"),
    "dense": ("same", "below", "menge", "belowmenge", "above", "abovemenge"),
    "descpara": ("same", "below", "menge", "belowmenge", "above", "abovemenge"),
    "descdense": ("same", "below", "menge", "belowmenge", "above", "abovemenge"),
    "table": ("same",),
    "tablehdr": ("same",),
}


def coverages(n):
    pats = [("all", tuple(range(n)))]
    if n >= 2:
        pats += [("first", (0,)), ("last", (n - 1,)), ("notfirst", tuple(range(1, n))),
                 ("notlast", tuple(range(n - 1)))]
    if n >= 3:
        pats += [("mid", (n // 2,)), ("alt", tuple(range(0, n, 2)))]
    seen, out = set(), []
    for name, cov in pats:
        if cov not in seen:
            seen.add(cov)
            out.append((name, cov))
    return out


def gtin_header(style, k, qty_in_header):
    pn, desc, q = PNS[k], DESCS[k], QTYS[k]
    if style in ("pos", "posbl"):
        head = "Pos. %d: %s %s" % (k + 1, pn, desc)
    elif style == "num":
        head = "%d) %s %s" % (k + 1, pn, desc)
    elif style in ("para", "dense"):
        head = "%s %s" % (pn, desc)
    else:
        head = desc
    return head + (", %d Stück" % q if qty_in_header else "")


def gtin_block(style, placement, k, has_ean, label):
    ean = "%s %s" % (label, GTINS[k])
    indent = "   " if style == "num" else ""
    menge = indent + "Menge: %d Stück" % QTYS[k]
    with_menge = placement in ("menge", "belowmenge", "abovemenge")
    head = gtin_header(style, k, qty_in_header=not with_menge)
    if not has_ean:
        return [head, menge] if with_menge else [head]
    if placement == "same":
        return [head + ", " + ean]
    if placement == "below":
        return [head, indent + ean]
    if placement == "menge":
        return [head, menge, indent + ean]
    if placement == "belowmenge":
        return [head, indent + ean, menge]
    if placement == "above":
        return [ean, head]
    return [ean, head, menge]  # abovemenge


def gtin_table(style, n, cov, label):
    lines = ["Pos | Art.-Nr. | Bezeichnung | Menge | EAN"]
    for k in range(n):
        if k in cov:
            ean = ("%s %s" % (label, GTINS[k])) if style == "table" else GTINS[k]
        else:
            ean = "-"
        lines.append("%d | %s | %s | %d Stück | %s" % (k + 1, PNS[k], DESCS[k], QTYS[k], ean))
    return lines


def gen_gtin():
    for style, placements in GTIN_STYLES.items():
        with_pn = not style.startswith("desc")
        for placement in placements:
            for n in range(1, 6):
                for cov_name, cov in coverages(n):
                    extractions = [("none", (), None)]
                    if len(cov) >= 2:
                        extractions.append(("one", (cov[0],), None))
                    if n >= 2:
                        extractions.append(("drop", (), cov[-1]))
                    for ext_name, present, dropped in extractions:
                        label = LABELS[_counter[0] % len(LABELS)]
                        if style.startswith("table"):
                            body = gtin_table(style, n, cov, label)
                        else:
                            body = []
                            for k in range(n):
                                if k and style in ("posbl", "para", "descpara"):
                                    body.append("")
                                body += gtin_block(style, placement, k, k in cov, label)
                        items, index_of = [], {}
                        for k in range(n):
                            if k == dropped:
                                continue
                            item = {"description": DESCS[k], "quantity": QTYS[k], "unit": "piece"}
                            if with_pn:
                                item["part_number"] = PNS[k]
                            if k in present:
                                item["gtin"] = GTINS[k]
                            index_of[k] = len(items)
                            items.append(item)
                        owner = {GTINS[k]: index_of.get(k) for k in cov if k not in present}
                        add("gtin/%s/%s/n%d/%s/%s" % (style, placement, n, cov_name, ext_name),
                            "gtin", items, mail(body), gtin_owner=owner, ungrounded=[])


# ── Uncovered-link quantity ──────────────────────────────────────────────────
LINK_PLACEMENTS = ("before", "x", "after", "above", "below", "none")


def link_lines(placement, link, qty, unit_word):
    if qty is None or placement == "none":
        return [link]
    if placement == "before":
        return ["%d %s %s" % (qty, unit_word, link)]
    if placement == "x":
        return ["%dx %s" % (qty, link)]
    if placement == "after":
        return ["%s %d %s" % (link, qty, unit_word)]
    if placement == "above":
        return ["%d %s" % (qty, unit_word), link]
    return [link, "%d %s" % (qty, unit_word)]  # below


DISTRACTORS = [None, "Liefertermin: 26.10.2026",
               "Datenblatt: https://www.example.com/datenblatt-ac44170.pdf",
               "Preis: 12,50 € pro Stück"]


def gen_links():
    for placement in LINK_PLACEMENTS:
        for n in range(1, 5):
            if placement == "none":
                patterns = [("none", ())]
            else:
                patterns = [("all", tuple(range(n)))]
                if n >= 2:
                    patterns += [("notfirst", tuple(range(1, n))), ("notlast", tuple(range(n - 1))),
                                 ("firstonly", (0,))]
            for cov_name, cov in patterns:
                for covered in ("none", "first"):
                    if covered == "first" and n < 2:
                        continue
                    unit_word, unit = LINK_UNITS[_counter[0] % len(LINK_UNITS)]
                    if placement == "x":
                        unit_word, unit = "x", "piece"
                    distractor = DISTRACTORS[_counter[0] % len(DISTRACTORS)]
                    body = ["Hallo,", "bitte zusätzlich bestellen:"]
                    if distractor:
                        body.append(distractor)
                    truth, items = {}, []
                    for k in range(n):
                        qty = LINK_QTYS[k] if k in cov else None
                        body += link_lines(placement, LINKS[k], qty, unit_word)
                        if covered == "first" and k == 0:
                            items.append({"link": LINKS[0], "quantity": qty or 1, "unit": unit or "piece"})
                            continue
                        truth[LINKS[k]] = [qty, unit] if (qty is not None and unit) else None
                    body += ["", "Danke und Gruß", "Max"]
                    add("link/%s/n%d/%s/%s/%s" % (placement, n, cov_name, covered,
                                                   unit_word.rstrip(".")),
                        "link", items, "\n".join(body) + "\n", link_quantity=truth)
    # Thousands separators: each written the way German and Swiss mail writes it.
    for text, value in (("1.000", 1000), ("2.500", 2500), ("10 000", 10000), ("10 000", 10000),
                        ("10 000", 10000), ("1'000", 1000), ("1’000", 1000), ("2 500", 2500)):
        for placement in ("before", "above", "below"):
            if placement == "before":
                body = ["%s Stück %s" % (text, LINKS[0])]
            elif placement == "above":
                body = ["%s Stück" % text, LINKS[0]]
            else:
                body = [LINKS[0], "%s Stück" % text]
            add("link/thousands/%s/%s" % (placement, ascii(text)), "link", [], "\n".join(body) + "\n",
                link_quantity={LINKS[0]: [value, "piece"]})


# ── Set-line owner ───────────────────────────────────────────────────────────
SET_LAYOUTS = ("own", "below", "stack", "belowmore")
SET_STYLES = ("pos", "dense", "desc", "shop")
SET_OWNER = ("pieces", "sets", "empty", "text")
SHOP = "https://shop.example.de/artikel.php?id=%d"


def set_line(style, k, desc, qty_part):
    pn = PNS[k]
    if style == "pos":
        return "Pos. %d: %s %s%s" % (k + 1, pn, desc, qty_part)
    if style == "dense":
        return "%s %s%s" % (pn, desc, qty_part)
    if style == "desc":
        return "%s%s" % (desc, qty_part)
    return "%s%s: %s" % (desc, qty_part, SHOP % (4417 + k))


def gen_sets():
    for n in range(1, 4):
        for owner in range(n):
            for layout in SET_LAYOUTS:
                for style in SET_STYLES:
                    for owner_kind in SET_OWNER:
                        for tempt in ((True, False) if n >= 2 else (False,)):
                            for mismatch in ((False, True) if n <= 2 and owner_kind == "pieces" else (False,)):
                                lines, items = [], []
                                for k in range(n):
                                    pack = 5 if (k == owner and mismatch) else (2 if (k == owner or tempt) else 5)
                                    desc = "%s %der-Pack" % (DESCS[k], pack)
                                    item = {"description": desc, "unit": "piece"}
                                    if style in ("pos", "dense"):
                                        item["part_number"] = PNS[k]
                                    if style == "shop":
                                        item["link"] = SHOP % (4417 + k)
                                    if k == owner:
                                        if owner_kind == "pieces":
                                            item["quantity"] = 8
                                        elif owner_kind == "sets":
                                            item["quantity"], item["unit"] = 4, "set"
                                        elif owner_kind == "text":
                                            item["quantity"] = "4 x 2"
                                        else:
                                            del item["unit"]
                                        if layout == "own":
                                            lines.append(set_line(style, k, desc, ", Menge: 4 x 2"))
                                        else:
                                            lines.append(set_line(style, k, desc, ""))
                                            if layout == "stack":
                                                lines += ["Bestellmenge: 4 x 2", "Liefermenge: 4 x 2"]
                                            else:
                                                lines.append("Menge: 4 x 2")
                                                if layout == "belowmore":
                                                    lines.append("Farbe: schwarz")
                                    else:
                                        q = 8 if tempt else 10
                                        item["quantity"] = q
                                        lines.append(set_line(style, k, desc, ", %d Stück" % q))
                                    items.append(item)
                                fix = {} if (owner_kind == "sets" or mismatch) else {owner: 4}
                                add("set/%s/%s/n%d/o%d/%s/%s%s" % (style, layout, n, owner, owner_kind,
                                                                    "tempt" if tempt else "plain",
                                                                    "/mismatch" if mismatch else ""),
                                    "set", items, mail(lines), set_fix=fix, ungrounded=[])


# ── Variant split ────────────────────────────────────────────────────────────
VARIANT_SHAPES = {
    "plain": ({}, {}),
    "variant_link": ({}, {"link": "https://www.amazon.de/dp/B0TESTBBB2"}),
    "both_links": ({"link": "https://www.amazon.de/dp/B0TESTAAA1"}, {"link": "https://www.amazon.de/dp/B0TESTBBB2"}),
    "query_link": ({"link": "https://www.amazon.de/dp/B0TESTAAA1?th=1"},
                   {"link": "https://www.amazon.de/dp/B0TESTAAA1?th=2"}),
    "survivor_int_gtin": ({"gtin": int(GTINS[0])}, {"gtin": GTINS[1]}),
    "variant_int_gtin": ({}, {"gtin": int(GTINS[1])}),
    "extractor_keys": ({"position": 1, "confidence": 0.93}, {"position": 2, "confidence": 0.81}),
    "variant_note": ({}, {"note": "Farbton laut Muster"}),
    "variant_other_ids": ({"other_ids": ["EBAY:123456789012"]}, {"other_ids": ["ASIN:B0TESTBBB2"]}),
}


def gen_variants():
    neighbour = "Pos. 2: NW-22010 Kabelbinder 200 mm schwarz, 4 Stück"
    nb_item = {"part_number": "NW-22010", "description": "Kabelbinder 200 mm schwarz", "quantity": 4, "unit": "piece"}
    for layout in ("attr", "indent"):
        for shape, (extra_first, extra_variant) in VARIANT_SHAPES.items():
            first = dict({"part_number": "AC-44170", "description": "Sechskantschraube M8x40", "quantity": 20,
                          "unit": "piece"}, **extra_first)
            if layout == "attr":
                lines = ["Pos. 1: AC-44170 Sechskantschraube M8x40, 20 Stück", "Ausführung: verzinkt"]
                variant = dict({"part_number": "AC-44170", "description": "Ausführung verzinkt"}, **extra_variant)
                indent = ""
            else:
                lines = ["Pos. 1  AC-44170 Sechskantschraube M8x40, 20 Stück", "        Ausführung: verzinkt",
                         "        AC-44170 Sechskantschraube M8x40, Ausführung: blank"]
                variant = dict({"part_number": "AC-44170", "description": "Sechskantschraube M8x40 blank"},
                               **extra_variant)
                indent = "        "
            # The links sit inside the position's block, before the next position.
            for item in (first, variant):
                if "link" in item:
                    lines.append(indent + "Link: %s" % item["link"])
            lines.append(neighbour)
            add("variant/%s/%s" % (layout, shape), "variant", [first, variant, dict(nb_item)], mail(lines),
                variant_of={1: 0})
    # The same part number twice as two positions: merging loses a position.
    first = {"part_number": "AC-44170", "description": "Sechskantschraube M8x40 verzinkt", "quantity": 20,
             "unit": "piece"}
    second = {"part_number": "AC-44170", "description": "Sechskantschraube M8x40 blank"}
    add("variant/separate/menge-folgt", "variant", [dict(first), dict(second), dict(nb_item)],
        mail(["Pos. 1: AC-44170 Sechskantschraube M8x40 verzinkt, 20 Stück",
              "Pos. 2: AC-44170 Sechskantschraube M8x40 blank, Menge folgt",
              "Pos. 3: NW-22010 Kabelbinder 200 mm schwarz, 4 Stück"]), variant_of={})
    add("variant/separate/nachlieferung", "variant", [dict(first), dict(second)],
        mail(["1) AC-44170 Sechskantschraube M8x40 verzinkt, 20 Stück",
              "2) AC-44170 Sechskantschraube M8x40 blank (Menge nach Rücksprache)"]), variant_of={})
    # Unmarked lines: a reader cannot tell a variant line from a second position.
    add("variant/dense/ambiguous", "variant", [dict(first), dict(second)],
        mail(["AC-44170 Sechskantschraube M8x40 verzinkt, 20 Stück", "AC-44170 Sechskantschraube M8x40 blank"]),
        variant_of={})
    # Each line has its own EAN: two trade items, not one split line (third review p7, #10).
    add("variant/own-gtin/str", "variant",
        [{"part_number": "AC-44170", "description": "Arbeitshandschuh Nitril blau", "quantity": 20, "gtin": GTINS[0]},
         {"part_number": "AC-44170", "description": "Arbeitshandschuh Nitril grün", "gtin": GTINS[1]}],
        mail(["AC-44170 Arbeitshandschuh Nitril blau, 20 Paar, EAN %s" % GTINS[0],
              "AC-44170 Arbeitshandschuh Nitril grün, EAN %s" % GTINS[1]]), variant_of={})
    add("variant/own-gtin/int-survivor", "variant",
        [{"part_number": "AC-44170", "description": "Arbeitshandschuh Nitril blau", "quantity": 20,
          "gtin": int(GTINS[0])},
         {"part_number": "AC-44170", "description": "Arbeitshandschuh Nitril grün", "gtin": GTINS[1]}],
        mail(["AC-44170 Arbeitshandschuh Nitril blau, 20 Paar, EAN %s" % GTINS[0],
              "AC-44170 Arbeitshandschuh Nitril grün, EAN %s" % GTINS[1]]), variant_of={})
    # Two products, each split once.
    add("variant/attr/two-pairs", "variant",
        [{"part_number": "AC-44170", "description": "Sechskantschraube M8x40", "quantity": 20},
         {"part_number": "AC-44170", "description": "Ausführung verzinkt"},
         {"part_number": "NW-22010", "description": "Kabelbinder 200 mm", "quantity": 4},
         {"part_number": "NW-22010", "description": "Farbe schwarz"}],
        mail(["Pos. 1: AC-44170 Sechskantschraube M8x40, 20 Stück", "Ausführung: verzinkt",
              "Pos. 2: NW-22010 Kabelbinder 200 mm, 4 Stück", "Farbe: schwarz"]), variant_of={1: 0, 3: 2})
    # A key that is not a string (N2″). JSON keys are strings, so score.py turns
    # the listed key into an int before the call.
    add("variant/attr/int-key", "variant",
        [{"part_number": "AC-44170", "description": "Sechskantschraube M8x40", "quantity": 20},
         {"part_number": "AC-44170", "description": "Ausführung verzinkt", "1": "x"}],
        mail(["Pos. 1: AC-44170 Sechskantschraube M8x40, 20 Stück", "Ausführung: verzinkt"]),
        variant_of={1: 0}, py_int_keys=[[1, "1"]])


# ── Redacted link ────────────────────────────────────────────────────────────
EBAY_A = "https://www.ebay.de/itm/123456789012"
EBAY_B = "https://www.ebay.de/itm/987654321098"
ALI = "https://de.aliexpress.com/item/1005001234567.html"


def gen_redacted():
    def case(name, items, lines, restored):
        add("redacted/" + name, "redacted", items, "\n".join(lines) + "\n", restored_link=restored)
    item = lambda link: {"description": "Kabelbinder 200 mm", "quantity": 2, "link": link}
    case("ebay/full-cut", [item("https://www.ebay.de/itm/[Telefon]")],
         ["Pos. 1: Kabelbinder 200 mm, 2 Stück %s" % EBAY_A], {0: EBAY_A})
    case("ebay/partial-cut", [item("https://www.ebay.de/itm/12[Telefon]12")],
         ["Pos. 1: Kabelbinder 200 mm, 2 Stück %s" % EBAY_A], {0: EBAY_A})
    case("ali/middle-cut", [item("https://de.aliexpress.com/item/1005[Telefon]1234567.html")],
         ["Pos. 1: Kabelbinder 2 Stück %s" % ALI], {0: ALI})
    case("ebay/two-links/full-cut", [item("https://www.ebay.de/itm/[Telefon]")],
         ["Pos. 1: Kabelbinder 200 mm, 2 Stück %s" % EBAY_A, "Alternativ: %s" % EBAY_B], {0: None})
    case("ebay/two-links/discriminating-cut", [item("https://www.ebay.de/itm/12[Telefon]12")],
         ["Pos. 1: Kabelbinder 200 mm, 2 Stück %s" % EBAY_A, "Alternativ: %s" % EBAY_B], {0: EBAY_A})
    case("ebay/two-items/both-full-cut",
         [item("https://www.ebay.de/itm/[Telefon]"), dict(item("https://www.ebay.de/itm/[Telefon]"), quantity=3)],
         ["Pos. 1: Kabelbinder 200 mm, 2 Stück %s" % EBAY_A, "Pos. 2: Kabelbinder 300 mm, 3 Stück %s" % EBAY_B],
         {0: None, 1: None})
    case("ebay/original-absent", [item("https://www.ebay.de/itm/[Telefon]")],
         ["Pos. 1: Kabelbinder 200 mm, 2 Stück (Link folgt)"], {0: None})
    case("ebay/early-bracket", [item("[Telefon]https://www.ebay.de/itm/123456789012")],
         ["Pos. 1: Kabelbinder 200 mm, 2 Stück %s" % EBAY_A], {0: EBAY_A})
    case("ebay/plus-other-host", [item("https://www.ebay.de/itm/[Telefon]")],
         ["Pos. 1: Kabelbinder 200 mm, 2 Stück %s" % EBAY_A, "Datenblatt: https://www.example.com/kb200.pdf"],
         {0: EBAY_A})


# ── Id grounding ─────────────────────────────────────────────────────────────
def gen_ids():
    grounded = [
        ("AC-44170", "Artikel AC-44170, 4 Stück"), ("AC-44170", "Artikel AC 44170, 4 Stück"),
        ("AC-44170", "Artikel AC–44170, 4 Stück"), ("AC-44170", "Artikel AC−44170, 4 Stück"),
        ("AC-44170", "Artikel AC 44170, 4 Stück"), ("AC-44170", "Artikel AC.44170, 4 Stück"),
        ("AC-44170", "Artikel AC/44170, 4 Stück"), ("AC-44170", "Artikel AC-­44170, 4 Stück"),
        ("AC-44170", "Artikel ＡＣ－４４１７０, 4 Stück"),
        ("NYM-J 3x1,5", "Pos. 1: 50 m Mantelleitung NYM-J 3x1,5 grau"),
        ("NYM-J 3x1,5", "Pos. 1: NYM-J 3x1,5, 100 m"), ("NYM-J 3x1,5", "Pos. 1: NYM–J 3x1,5, 100 m"),
        ("NYM-J 3x1,5", "Pos. 1: NYM-J  3x1,5   100 m"), ("NYM-J 3x1,5", "Pos. 1: NYM-J 3x1,5, 100 m"),
        ("H07V-K 1x2,5", "Pos. 2: H07V-K 1x2,5 schwarz"), ("H07V-K 1x2,5", "Pos. 2: H07V−K 1x2,5 schwarz"),
        ("SPAX 4,5x40", "Pos. 3: SPAX 4,5x40 Senkkopf"), ("M8×40", "Pos. 4: Schraube M8×40 verzinkt"),
        ("M8x40", "Pos. 4: Sechskantschraube M8×40 DIN 933"), ("AC-4417(B)", "Pos. 5: AC-4417(B) Ersatzteil"),
    ]
    ungrounded = [
        ("AC-99999", "Artikel AC-44170, 4 Stück"), ("AC-44178", "AC-4417, 8 Stück"),
        ("NYM-J 3x1,50", "NYM-J 3x1,5 grau"), ("M8×40", "M8×400 Gewindestange"),
        ("AC-4417", "Artikel AC-44170, 4 Stück"), ("NW-22011", "NW-22010 Kabelbinder"),
    ]
    for index, (pn, source) in enumerate(grounded):
        add("id/grounded/%02d" % index, "id", [{"part_number": pn, "quantity": 1}], source + "\n", ungrounded=[])
    for index, (pn, source) in enumerate(ungrounded):
        add("id/ungrounded/%02d" % index, "id", [{"part_number": pn, "quantity": 1}], source + "\n",
            ungrounded=[[0, "part_number"]])


# ── Every past probe whose input states an answer ────────────────────────────
G1, G2, G3 = GTINS[0], GTINS[1], GTINS[2]
A, B, C, D = ("https://www.amazon.de/dp/B0TESTAAA1", "https://www.amazon.de/dp/B0TESTBBB2",
              "https://www.amazon.de/dp/B0TESTCCC3", "https://www.amazon.de/dp/B0TESTDDD4")
A_TEST = "https://www.amazon.de/dp/B0TEST1234"


def gen_probes():
    P = lambda name, items, lines, **truth: add("probe/" + name, "probe", items,
                                              "\n".join(lines) + "\n", **truth)
    # QA probes: which item a GTIN line belongs to
    AC, NW, XY = ("AC-44170 Sechskantschraube M8, 8 Stück", "NW-22010 Kabelbinder 200 mm, 4 Stück",
                  "XY-90001 Handschuhe Gr. 9, 2 Paar")
    I3 = lambda: [{"part_number": "AC-44170", "quantity": 8}, {"part_number": "NW-22010", "quantity": 4},
                  {"part_number": "XY-90001", "quantity": 2}]
    E = lambda g: "EAN: " + g
    P("qa/gtin/under-2", I3(), [AC, E(G1), NW, E(G2)], gtin_owner={G1: 0, G2: 1})
    P("qa/gtin/under-3", I3(), [AC, E(G1), NW, E(G2), XY, E(G3)], gtin_owner={G1: 0, G2: 1, G3: 2})
    P("qa/gtin/above-2", I3(), [E(G1), AC, E(G2), NW], gtin_owner={G1: 0, G2: 1})
    P("qa/gtin/above-3", I3(), [E(G1), AC, E(G2), NW, E(G3), XY], gtin_owner={G1: 0, G2: 1, G3: 2})
    P("qa/gtin/conflicting-anchors", I3(), [E(G1), AC, E(G2), NW, E(G3)],
      gtin_owner={G1: 0, G2: None, G3: 1})
    P("qa/gtin/no-anchor", I3(), ["Bestellung:", AC, E(G1), NW, "Danke"], gtin_owner={G1: None})
    P("qa/gtin/under-blank", I3(), [AC, E(G1), "", NW, E(G2)], gtin_owner={G1: 0, G2: 1})
    P("qa/gtin/under-header", I3(), ["Ihre Bestellung", AC, E(G1), NW, E(G2)], gtin_owner={G1: 0, G2: 1})
    P("qa/gtin/menge-2 (B1″)", I3(), [AC, "Menge: 8 Stück", E(G1), NW, "Menge: 4 Stück", E(G2)],
      gtin_owner={G1: 0, G2: 1})
    P("qa/gtin/menge-then-position (B1″)",
      [{"part_number": "AC-44170", "description": "Sechskantschraube M8"},
       {"part_number": "AC-44171", "description": "Sechskantmutter M8"}],
      ["AC-44170 Sechskantschraube M8", "Menge: 100 Stück", E(G1), "AC-44171 Sechskantmutter M8"],
      gtin_owner={G1: 0})
    P("qa/gtin/block (S1″)", [{"part_number": "AC-44170", "description": "Sechskantschraube M8x40 verzinkt"}],
      ["Art.-Nr. AC-44170", "Sechskantschraube M8x40 verzinkt, EAN %s" % G1], gtin_owner={G1: 0})
    P("qa/gtin/sibling-veto (S2″)",
      [{"part_number": "AC-44170", "quantity": 100},
       {"part_number": "NW-22010", "description": "Kabelbinder schwarz", "quantity": 50}],
      ["AC-44170 Kabelbinder 200 mm schwarz, EAN %s" % G1, "NW-22010 Kabelbinder 300 mm schwarz"],
      gtin_owner={G1: 0})
    P("qa/gtin/part-number-in-passing (S9′ b)",
      [{"part_number": "AC-44170", "gtin": G1, "quantity": 1}, {"description": "Dichtungssatz", "quantity": 2}],
      ["Pos. 1: AC-44170 Druckminderer 1/2 Zoll, 1 Stück, EAN %s" % G1,
       "Pos. 2: Dichtungssatz passend für AC-44170, 2 Stück, EAN %s" % G2], gtin_owner={G2: 1})
    P("qa/gtin/fallback-id-elsewhere (S10′)",
      [{"part_number": "AC-44170", "description": "Kabelbinder 200 mm schwarz", "quantity": 8}],
      ["Pos. 1: AC-44170 Kabelbinder 200 mm schwarz, 8 Stück", "Pos. 2: Kabelbinder 200 mm weiß, 4 Stück, EAN %s" % G1],
      gtin_owner={G1: None})
    P("qa/gtin/fallback-no-id", [{"description": "Kabelbinder 200 mm weiß", "quantity": 4}],
      ["Pos. 2: Kabelbinder 200 mm weiß, 4 Stück, EAN %s" % G1], gtin_owner={G1: 0})
    for label in ("EAN Nr. ", "EAN Nr.: ", "GTIN No. ", "Barcode Nr. "):
        P("qa/gtin/label %r (S8′)" % label, [{"part_number": "AC-44170", "quantity": 8}],
          ["AC-44170 Schraube M8, 8 Stück", label + G1], gtin_owner={G1: 0})
    # QA probes: which quantity an uncovered link takes
    for name, want, lines in [
        ("above-2", [2, 3], ["2 Stück", A, "3 Stück", B]),
        ("above-3 (S5′)", [2, 3, 4], ["2 Stück", A, "3 Stück", B, "4 Stück", C]),
        ("above-4", [2, 3, 4, 5], ["2 Stück", A, "3 Stück", B, "4 Stück", C, "5 Stück", D]),
        ("below-2", [2, 3], [A, "2 Stück", B, "3 Stück"]),
        ("below-3 (B2″)", [2, 3, 4], [A, "2 Stück", B, "3 Stück", C, "4 Stück"]),
        ("below-4", [2, 3, 4, 5], [A, "2 Stück", B, "3 Stück", C, "4 Stück", D, "5 Stück"]),
        ("same-line-3", [2, 3, 4], ["2x " + A, "3x " + B, "4x " + C]),
        ("below-3-mail (p4)", [2, 3, 4], ["Hallo,", "bitte bestellen:", A, "2 Stück", B, "3 Stück", C, "4 Stück", "",
                                          "Gruß", "Max"]),
    ]:
        links = [A, B, C, D][:len(want)]
        P("qa/link/" + name, [], lines, link_quantity={l: [q, "piece"] for l, q in zip(links, want)})
    P("qa/link/datasheet-above (S5′)", [], ["Datenblatt: https://www.example.com/datenblatt-ac44170.pdf",
                                                "3 Stück", A], link_quantity={A: [3, "piece"]})
    P("qa/link/paar (S6′)", [], ["2 Paar " + A], link_quantity={A: None})
    P("qa/link/kg (S6′)", [], ["0,250 kg " + A], link_quantity={A: None})
    P("qa/link/sets (S6′)", [], [A, "2 Sets"], link_quantity={A: [2, "set"]})
    P("qa/link/date-above (B2′)", [], ["Liefertermin:", "26.10.2026", A], link_quantity={A: None})
    P("qa/link/date-below (B2′)", [], ["Bitte zusätzlich bestellen:", A, "26.10.2026"], link_quantity={A: None})
    for text, value in (("10 000", 10000), ("10 000", 10000), ("1'000", 1000), ("1’000", 1000),
                        ("2 500 x", 2500)):
        unit = "" if text.endswith("x") else " Stück"
        P("review/link/thousands %s (S4″)" % ascii(text), [], [text + unit + " " + A_TEST],
          link_quantity={A_TEST: [value, "piece"]})
    # QA probes: which item a set line corrects
    AC_S = {"part_number": "AC-44170", "description": "Sechskantschraube M8 2er-Pack", "quantity": 8, "unit": "piece"}
    NW_S = {"part_number": "NW-22010", "description": "Kabelbinder 200 mm 2er-Pack", "quantity": 8, "unit": "piece"}
    P1 = "Pos. 1: AC-44170 Sechskantschraube M8, 2er-Pack"
    P2 = "Pos. 2: NW-22010 Kabelbinder 200 mm, 2er-Pack, 8 Stück"
    P("qa/set/stack-under (S1′)", [dict(AC_S), dict(NW_S)], [P1, "Bestellmenge: 4 x 2", "Liefermenge: 4 x 2", P2],
      set_fix={0: 4})
    P("qa/set/stack-above-first", [dict(AC_S), dict(NW_S)], ["Bestellmenge: 4 x 2", "Liefermenge: 4 x 2", P1, P2],
      set_fix={0: 4})
    P("qa/set/under", [dict(AC_S), dict(NW_S)], [P1, "Menge: 4 x 2", P2], set_fix={0: 4})
    P("qa/set/own-line", [dict(AC_S), dict(NW_S)], [P1 + ", Menge: 4 x 2", P2], set_fix={0: 4})
    P("qa/set/each-own", [dict(AC_S), dict(NW_S)],
      [P1, "Menge: 4 x 2", "Pos. 2: NW-22010 Kabelbinder 200 mm, 2er-Pack", "Menge: 4 x 2"], set_fix={0: 4, 1: 4})
    P("qa/set/own-unreadable (S2′)", [dict(NW_S), dict(AC_S, quantity="4 x 2")],
      ["Pos. 1: NW-22010 Kabelbinder 200 mm, 2er-Pack, 8 Stück",
       "Pos. 2: AC-44170 Sechskantschraube M8, 2er-Pack, Menge: 4 x 2"], set_fix={1: 4})
    P("qa/set/own-empty (B4″)", [{"part_number": "AC-44170", "quantity": 8}, {"part_number": "NW-22010"}],
      ["AC-44170 Schraube 2er-Pack, 8 Stück", "NW-22010 Kabelbinder 2er-Pack, Menge: 4 x 2"], set_fix={1: 4})
    P("qa/set/shared-word (S3′)",
      [dict(NW_S, quantity=4, unit="set"), {"description": "Kabelbinder schwarz", "quantity": 8}],
      ["Pos. 1: NW-22010 Kabelbinder 200 mm, 2er-Pack, Menge: 4 x 2", "Pos. 2: Kabelbinder schwarz, 2er-Pack, 8 Stück"],
      set_fix={})
    P("qa/set/no-id-own-line", [{"description": "Kabelbinder schwarz 2er-Pack", "quantity": 8}],
      ["Pos. 2: Kabelbinder schwarz, 2er-Pack, Menge: 4 x 2"], set_fix={0: 4})
    P("qa/set/missed-link-placeholder (S3″)", [{"description": "Kabelbinder 2er-Pack", "quantity": 8}],
      ["Kabelbinder 2er-Pack https://www.amazon.de/dp/B0TEST1234", "Menge: 4 x 2"], set_fix={0: 4}, link_quantity={})
    P("qa/set/query-string-link (S4′)",
      [{"link": "https://de.aliexpress.com/item/1005001234567.html?spm=a2g0o.detail.0.0", "quantity": 140}],
      ["Pos. 1: Kabelbinder 5er-Pack https://de.aliexpress.com/item/1005001234567.html?spm=a2g0o.detail.0.0",
       "Menge: 28 x 5"], set_fix={0: 28})
    P("qa/set/shop-id-query (B3″)",
      [{"part_number": "AC-44170", "link": "https://shop.example.de/artikel.php?id=4417", "quantity": 8, "unit": "piece"},
       {"part_number": "NW-22010", "link": "https://shop.example.de/artikel.php?id=4418", "quantity": 4, "unit": "set"}],
      ["AC-44170 Kabelbinder 2er-Pack, 8 Stück: https://shop.example.de/artikel.php?id=4417",
       "NW-22010 Schraube M8 2er-Pack: https://shop.example.de/artikel.php?id=4418", "Menge: 4 x 2"], set_fix={})
    P("qa/set/guard-1-2-words (N4′)",
      [{"part_number": "NW-22010", "description": "Kabelbinder schwarz 2er-Pack", "quantity": 6},
       {"part_number": "AC-44170", "description": "Kabelbinder weiß 2er-Pack", "quantity": 6}],
      ["NW-22010 Kabelbinder schwarz 2er-Pack", "Menge: 3 x 2", "AC-44170 Kabelbinder weiß, 6 Packungen à 2er-Pack"],
      set_fix={0: 3})
    P("qa/set/guard-3-words (3.9b)",
      [{"part_number": "NW-22010", "description": "Kabelbinder schwarz UV-beständig 2er-Pack", "quantity": 6},
       {"part_number": "AC-44170", "description": "Kabelbinder weiß UV-beständig 2er-Pack", "quantity": 6}],
      ["Kabelbinder schwarz UV-beständig 2er-Pack", "Menge: 3 x 2",
       "AC-44170 Kabelbinder weiß UV-beständig, 6 Packungen à 2er-Pack"], set_fix={0: 3})
    # QA probes: variant merges (their id inputs are in gen_ids)
    BASE = "https://de.aliexpress.com/item/1005001234567.html"
    for name, variant, merge in [
        ("query-only-link", {"part_number": "AC-44170", "link": BASE + "?sku_id=12000000002"}, False),
        ("int-gtin", {"part_number": "AC-44170", "gtin": int(GTINS[0])}, False),
        ("unknown-key", {"part_number": "AC-44170", "colour": "silber"}, False),
        ("plain-variant-line", {"part_number": "AC-44170", "description": "Ausführung silber"}, True),
    ]:
        P("qa/merge/" + name,
          [{"part_number": "AC-44170", "quantity": 4, "link": BASE + "?sku_id=12000000001"}, variant],
          ["AC-44170 4 Stück " + BASE, "Ausführung: silber"], variant_of={1: 0} if merge else {})
    P("qa/merge/extractor-key-position (S6″)",
      [{"part_number": "AC-44170", "quantity": 20, "position": 1}, {"part_number": "AC-44170", "position": 2}],
      ["Pos. 1: AC-44170 20 Stück", "   Ausführung: blank"], variant_of={1: 0})
    # Third review: GTIN layouts from realistic mail
    item1 = [{"part_number": "AC-44170", "description": "Sechskantschraube DIN 933 M8x40 verzinkt", "quantity": 100}]
    P("review/p1A", [dict(i) for i in item1],
      ["Pos. 1  Art.-Nr. AC-44170", "Sechskantschraube DIN 933 M8x40 verzinkt, EAN %s" % G1, "Menge: 100 Stück"],
      gtin_owner={G1: 0})
    P("review/p1B", [dict(i) for i in item1],
      ["Pos. 1  Art.-Nr. AC-44170", "Sechskantschraube DIN 933 M8x40 verzinkt", "EAN: %s" % G1, "Menge: 100 Stück"],
      gtin_owner={G1: 0})
    P("review/p1C",
      [{"part_number": "AC-44170", "description": "Sechskantschraube M8x40", "quantity": 100},
       {"part_number": "AC-44171", "description": "Sechskantschraube M10x50", "quantity": 50}],
      ["1) AC-44170 Sechskantschraube M8x40", "   Menge: 100 Stück", "   EAN: %s" % G1,
       "2) AC-44171 Sechskantschraube M10x50", "   Menge: 50 Stück", "   EAN: %s" % G2],
      gtin_owner={G1: 0, G2: 1})
    P("review/p2-confirmation",
      [{"part_number": "AC-44170", "description": "Sechskantschraube DIN 933 M8x40 verzinkt", "quantity": 100, "unit": "piece"},
       {"part_number": "AC-44171", "description": "Sechskantschraube DIN 933 M10x50 verzinkt", "quantity": 50, "unit": "piece"},
       {"part_number": "NW-22010", "description": "Kabelbinder 200 mm schwarz", "quantity": 20, "unit": "piece"}],
      ["Sehr geehrte Damen und Herren,", "anbei unsere Auftragsbestätigung zu Ihrer Bestellung:",
       "1) AC-44170 Sechskantschraube DIN 933 M8x40 verzinkt", "   Menge: 100 Stück", "   EAN: %s" % G1,
       "2) AC-44171 Sechskantschraube DIN 933 M10x50 verzinkt", "   Menge: 50 Stück", "   EAN: %s" % G2,
       "3) NW-22010 Kabelbinder 200 mm schwarz", "   Menge: 20 Stück", "   EAN: %s" % G3,
       "Mit freundlichen Grüßen", "Beispiel Werkzeughandel GmbH", "Industriestraße 12, 80331 Musterstadt"],
      gtin_owner={G1: 0, G2: 1, G3: 2})
    for name, items in (("p9-cable-tie", [{"part_number": "AC-44170", "description": "Cable tie 200 mm", "quantity": 100},
                                          {"part_number": "NW-22010", "description": "Kabelbinder 300 mm schwarz", "quantity": 50}]),
                        ("p9-no-description", [{"part_number": "AC-44170", "quantity": 100},
                                               {"part_number": "NW-22010", "description": "Kabelbinder schwarz", "quantity": 50}])):
        P("review/" + name, items,
          ["Pos. 1: AC-44170 Kabelbinder 200 mm schwarz, 100 Stück, EAN %s" % G1,
           "Pos. 2: NW-22010 Kabelbinder 300 mm schwarz, 50 Stück"], gtin_owner={G1: 0})
    P("review/p15-pdf-table",
      [{"part_number": "AC-44170", "description": "Sechskantschraube DIN 933 M8x40", "quantity": 100, "unit": "piece"},
       {"part_number": "AC-44171", "description": "Sechskantmutter DIN 934 M8", "quantity": 100, "unit": "piece"},
       {"part_number": "NW-22010", "description": "Kabelbinder 200 mm schwarz 5er-Pack", "quantity": 20, "unit": "piece"}],
      ["Von: Einkauf <einkauf@kunde.example>", "Betreff: AW: Bestellung 4711 – Auftragsbestätigung",
       "Sehr geehrter Herr Beispiel,", "vielen Dank für Ihre Bestellung. Wir bestätigen wie folgt:",
       "Pos.  Art.-Nr.    Bezeichnung                         Menge     EAN",
       "1     AC-44170    Sechskantschraube DIN 933 M8x40     100 St.   %s" % G1,
       "2     AC-44171    Sechskantmutter DIN 934 M8          100 St.   %s" % G2,
       "3     NW-22010    Kabelbinder 200 mm schwarz 5er-Pack Menge: 4 x 5   %s" % G3,
       "Liefertermin: 26.10.2026", "Mit freundlichen Grüßen", "i. A. Erika Muster",
       "Beispiel Werkzeughandel GmbH · Industriestraße 12 · 80331 Musterstadt",
       "Tel. +49 89 1234567-0 · Fax +49 89 1234567-99", "> -----Ursprüngliche Nachricht-----",
       "> Pos. 1: AC-44170 Sechskantschraube M8x40, 100 Stück", "> Pos. 2: AC-44171 Sechskantmutter M8, 100 Stück",
       "> Pos. 3: NW-22010 Kabelbinder 5er-Pack, 4 Packungen"],
      gtin_owner={G1: 0, G2: 1, G3: 2}, set_fix={2: 4})
    # Third review: set lines, a redacted link and merges
    P("review/p5-missed-link-no-id", [{"description": "Kabelbinder 200 mm schwarz 2er-Pack", "quantity": 8, "unit": "piece"}],
      ["Kabelbinder 200 mm schwarz 2er-Pack %s" % A_TEST, "Menge: 4 x 2"], set_fix={0: 4}, link_quantity={})
    P("review/p5-missed-link-with-id",
      [{"part_number": "AC-44170", "description": "Kabelbinder 200 mm schwarz 2er-Pack", "quantity": 8}],
      ["AC-44170 Kabelbinder 200 mm schwarz 2er-Pack %s" % A_TEST, "Menge: 4 x 2"], set_fix={0: 4}, link_quantity={})
    P("review/p10-redacted-default",
      [{"description": "Kabelbinder", "quantity": 2, "link": "https://de.aliexpress.com/item/1005[Telefon]1234567.html"}],
      ["Pos. 1: Kabelbinder 2 Stück https://de.aliexpress.com/item/1005001234567.html"],
      restored_link={0: "https://de.aliexpress.com/item/1005001234567.html"})
    spec = ["AC-44170 Schraube M8 2er-Pack, 8 Stück", "NW-22010 Kabelbinder 2er-Pack, Menge: 4 x 2"]
    P("review/p12-spec-no-quantity",
      [{"part_number": "AC-44170", "description": "Schraube M8 2er-Pack", "quantity": 8, "unit": "piece"},
       {"part_number": "NW-22010", "description": "Kabelbinder 2er-Pack"}], spec, set_fix={1: 4})
    P("review/p12-spec-text-quantity",
      [{"part_number": "AC-44170", "description": "Schraube M8 2er-Pack", "quantity": 8, "unit": "piece"},
       {"part_number": "NW-22010", "description": "Kabelbinder 2er-Pack", "quantity": "4 x 2"}], spec, set_fix={1: 4})
    P("review/p13-set-below-no-quantity",
      [{"part_number": "NW-22010", "description": "Kabelbinder 2er-Pack"},
       {"part_number": "AC-44170", "description": "Schraube M8 2er-Pack", "quantity": 8, "unit": "piece"}],
      ["NW-22010 Kabelbinder 2er-Pack", "Menge: 4 x 2", "AC-44170 Schraube M8 2er-Pack, 8 Stück"], set_fix={0: 4})
    P("review/p16-shop-id-query",
      [{"description": "Kabelbinder 2er-Pack", "link": "https://shop.example.de/artikel.php?id=4417", "quantity": 8, "unit": "piece"},
       {"description": "Schraube M8 2er-Pack", "link": "https://shop.example.de/artikel.php?id=4418", "quantity": 4, "unit": "set"}],
      ["Kabelbinder 2er-Pack, 8 Stück: https://shop.example.de/artikel.php?id=4417",
       "Schraube M8 2er-Pack: https://shop.example.de/artikel.php?id=4418", "Menge: 4 x 2"], set_fix={})
    P("review/p17-int-gtin-overwritten (S7″)",
      [{"part_number": "AC-44170", "quantity": 20, "gtin": int(GTINS[0])}, {"part_number": "AC-44170", "gtin": GTINS[1]}],
      ["AC-44170 Handschuh blau, 20 Paar", "AC-44170 Handschuh grün"], variant_of={})
    P("review/p6-indented-variant",
      [{"part_number": "AC-44170", "description": "Arbeitshandschuh Nitril blau", "quantity": 20, "unit": "Paar"},
       {"part_number": "AC-44170", "description": "Arbeitshandschuh Nitril grün"}],
      ["Pos. 1  AC-44170 Arbeitshandschuh Nitril, 20 Paar", "        Farbe: blau",
       "        AC-44170 Arbeitshandschuh Nitril, Farbe: grün"], variant_of={1: 0})
    for key, first_value, variant_value in (("position", 1, 2), ("confidence", 0.93, 0.81), ("line", 1, 3)):
        P("review/p6-indented-variant-%s (S6″)" % key,
          [{"part_number": "AC-44170", "description": "Arbeitshandschuh Nitril blau", "quantity": 20, "unit": "Paar",
            key: first_value},
           {"part_number": "AC-44170", "description": "Arbeitshandschuh Nitril grün", key: variant_value}],
          ["Pos. 1  AC-44170 Arbeitshandschuh Nitril, 20 Paar", "        Farbe: blau",
           "        AC-44170 Arbeitshandschuh Nitril, Farbe: grün"], variant_of={1: 0})
    # Third review: punctuation the verbatim rule should accept (S5″)
    for index, (source, pn) in enumerate((
            ("Pos. 1: NYM-J 3x1,­5, 100 m", "NYM-J 3x1,5"), ("SPAX 4,5 x 40 verzinkt", "SPAX 4,5 x 40"),
            ("NYM-J 3x1,​5 Ring 100 m", "NYM-J 3x1,5"))):
        P("review/p8-verbatim-%d (S5″)" % index, [{"part_number": pn, "quantity": 1}], [source], ungrounded=[])
    # Third review: label spellings real mail carries (S10″); the owner is the only item.
    for label in ("EAN:", "EAN-Code:", "EAN-Nummer:", "EAN-Nr.:", "GTIN (EAN):", "EAN/GTIN:", "EAN‑13:",
                  "UPC#", "EAN13", "Barcode (EAN):", "Strichcode:", "EAN =", "GTIN-13 / EAN:"):
        P("review/p11-label %r (S10″)" % label, [{"part_number": "AC-44170", "quantity": 1}],
          ["AC-44170 Sechskantschraube M8, %s %s" % (label, G1)], gtin_owner={G1: 0})


def build():
    """Return the case list. Deterministic: no clock, no randomness, no environment."""
    del CASES[:]
    _counter[0] = 0
    gen_gtin()
    gen_links()
    gen_sets()
    gen_variants()
    gen_redacted()
    gen_ids()
    gen_probes()
    ids = [case["id"] for case in CASES]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids: %s" % sorted(set(i for i in ids if ids.count(i) > 1))[:5])
    return copy.deepcopy(CASES)


def serialize(cases):
    return json.dumps(cases, ensure_ascii=False, indent=0, sort_keys=True) + "\n"


def digest(cases):
    return hashlib.sha256(serialize(cases).encode("utf-8")).hexdigest()


def main():
    cases = build()
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "cases.json")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(serialize(cases))
    families = {}
    for case in cases:
        families[case["family"]] = families.get(case["family"], 0) + 1
    print("%d cases %s" % (len(cases), families))
    print("sha256 %s (cases.sha256 pins it)" % digest(cases))


if __name__ == "__main__":
    main()
