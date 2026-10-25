#!/usr/bin/env python3
"""harness_grounding — tie an LLM extraction back to the text it came from.

No LLM. No network. Standard library only, Python >= 3.8. The checker is
deterministic on purpose: a grounding check that needs a model to run fails
exactly when the model is having a bad day, which is when the extraction is
least trustworthy.

The job is narrow. An extractor reads a supplier mail or a PDF and returns
items. This module compares those items against the source text and marks the
ones the source does not support. It never deletes an item and never rewrites a
value into something the source does not say. It only adds a flag, attaches a
value the source proves, or reports a fact.

Data model — plain dicts, no classes, missing keys allowed everywhere.

extraction = {
    "items": [
        {
            "quantity": 4,              # number, optional
            "unit": "piece",            # str, optional
            "description": "Hex bolt M8",   # str, optional
            "part_number": "AC-4417",   # str, optional
            "gtin": "4006381333931",    # str, optional
            "other_ids": ["EBAY:1"],    # list of str, optional
            "link": "https://...",      # str, optional
            "uncertain": False,         # bool, optional; set True by this module
            "note": "..."               # str, optional
        }
    ]
}

finding = {
    "code": "UNGROUNDED_ID",   # stable machine code, see below
    "item_index": 0,           # int index into extraction["items"], or None
    "value": "AC-9999",        # the value the finding is about
    "evidence": "...",         # short quotable reason, safe to show a human
    "action": "flagged",       # what ground() did about it
}

Codes and actions in this module:

    UNGROUNDED_ID / "flagged"    an item id does not occur in the source text.
                                 The item stays; uncertain is set True.
    MISSING_GTIN  / "attached"   a valid GTIN in the source was missing from the
                                 extraction. It is attached to the best-matching
                                 item, which is marked uncertain.
    MISSING_GTIN  / "reported"   same, but no item could take it. The finding
                                 has item_index None and nothing is changed.
    REDACTED_LINK / "restored"   an item link was cut by a redaction
                                 placeholder such as "[Telefon]". The link from
                                 the source text is put back.
    UNCOVERED_LINK / "added"     a product link in the source that no item
                                 covers. A new item is appended and marked
                                 uncertain.
    SET_MULTIPLIED / "corrected" the source says "N x M" and the item is an
                                 M-pack with quantity N*M. The quantity becomes
                                 N, the unit "set", and the item is marked
                                 uncertain.
    VARIANT_SPLIT / "merged"     a later item repeats the part number of an
                                 earlier one and has no quantity of its own. It
                                 is folded into the first item as a variant.

A link is a product link only when product_link_id() returns an id for it. A
link to any other host is not a product and is never added as an item.

ground() returns (checked, findings). checked is a deep copy of the input, so
the caller keeps the original and the module never mutates what it was given.
Findings come in a stable order: VARIANT_SPLIT in item order, then
UNGROUNDED_ID in item order, then REDACTED_LINK and UNCOVERED_LINK in source
order, then SET_MULTIPLIED in source order, then MISSING_GTIN in source order.

Every finding carries an item_index that is valid in the returned item list.
VARIANT_SPLIT runs first because it is the only check that removes an item;
UNGROUNDED_ID runs before any item is appended, so an item this module adds is
never flagged against itself.

    import harness_grounding as g
    checked, findings = g.ground(extraction, source_text)
"""
from __future__ import annotations

import copy
import math
import re
from urllib.parse import urlsplit

# GS1 accepts these lengths. The check digit is the last digit in every case.
GTIN_LENGTHS = (8, 12, 13, 14)

# An id shorter than this is too common to prove anything. "M8" occurs in half
# the supplier mails ever written, so flagging it would be noise.
MIN_ID_LEN = 4

# A description word must be at least this long to count towards a match. Short
# words ("for", "the", "x") match everywhere and carry no product meaning.
MIN_WORD_LEN = 4

# Share of the shorter word set that two texts must have in common. Measured
# over the shorter set so a long source line cannot drown a short description.
MATCH_RATIO = 0.5

# A placeholder earlier than this is not a redacted product path. Every product
# path this module recognises is longer than 12 characters, so a "[" in the
# first 12 characters is the start of the link itself, not a cut in its middle.
MIN_REDACTED_AT = 12

# Two part numbers are the same variant only when their normalized forms are
# equal and at least this long. Below it, two items can share an id by accident.
MIN_VARIANT_ID_LEN = 5

_SEPARATORS = re.compile(r"[\s\-./_]+")

# A standalone digit run: no digit directly before or after it. The negative
# lookarounds are what stop "940063813339311" (15 digits) from matching as a
# 14-digit GTIN plus a stray digit.
_DIGIT_RUN = re.compile(r"(?<!\d)\d{8,14}(?!\d)")

# Letter runs only. Digits and underscores split words, which is what we want
# for descriptions such as "Hex bolt M8".
_WORD = re.compile(r"[^\W\d_]+")

# A link ends at whitespace or at a character that cannot be part of a URL in
# prose. The trailing punctuation of a sentence is removed in _clean_link.
_URL = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)

# Sentence punctuation that follows a link but does not belong to it.
_TRAILING_PUNCTUATION = re.compile(r"[)>\].,;]+$")

# Marketplace product paths. The host is checked separately, so a path that
# looks like a product path on another host stays an ordinary link.
_AMAZON_ASIN = re.compile(r"/(?:dp|gp/product)/([A-Za-z0-9]{10})(?=/|$)")
_EBAY_ITEM = re.compile(r"/itm/(\d+)")
_ALIEXPRESS_ITEM = re.compile(r"/item/(\d+)")

# A quantity at the start of a line, with or without a unit word:
# "4x", "4 x", "4 pcs", "4 Stück".
_QTY_LEAD = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(?:x|×|pcs?\.?|pieces?|stk\.?|stück)(?=\s|$)",
    re.IGNORECASE)

# A line that holds nothing but a quantity: "4", "4 x", "4 pcs", "4 Stück".
_QTY_ONLY = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(?:x|×|pcs?\.?|pieces?|stk\.?|stück)?\s*$",
    re.IGNORECASE)

# "quantity: 28 x 5", "qty 28x5", "Menge: 28 x 5". The label is required: a
# bare "28 x 5" is also how a dimension or a pair of sizes is written.
_SET_LINE = re.compile(
    r"\b(?:quantity|qty|menge)\s*:?\s*(\d+)\s*[x×]\s*(\d+)", re.IGNORECASE)

# Ways a description marks a pack of M pieces.
_PACK_MARKERS = (
    re.compile(r"(\d+)\s*-\s*pack\b", re.IGNORECASE),
    re.compile(r"\bpack\s+of\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"\bset\s+of\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"(\d+)\s*er\b", re.IGNORECASE),
)


def _is_ascii_digits(text):
    """True only for plain ASCII digits.

    str.isdigit() also accepts superscripts and other numeric glyphs, and
    int() then raises on some of them. A GTIN is ASCII by definition.
    """
    return bool(text) and all("0" <= ch <= "9" for ch in text)


def gtin_valid(code):
    """Return True when code is a GTIN-8, -12, -13 or -14 with a good check digit.

    The GS1 rule: number the digits of the code without its check digit from
    the right, weight them 3, 1, 3, 1, ..., sum, and the check digit is
    whatever makes the sum a multiple of 10.

        gtin_valid("4006381333931")  # True
        gtin_valid("4006381333932")  # False, last digit changed
        gtin_valid("96385074")       # True, GTIN-8

    Non-strings and wrong lengths return False. Surrounding whitespace is
    ignored, because a value copied out of a table often carries a space.
    """
    if not isinstance(code, str):
        return False
    code = code.strip()
    if len(code) not in GTIN_LENGTHS or not _is_ascii_digits(code):
        return False
    total = 0
    for position, char in enumerate(reversed(code[:-1])):
        total += int(char) * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10 == int(code[-1])


def find_gtins(text):
    """Return the valid GTINs in text, in order of appearance, without repeats.

    Only standalone runs of 8, 12, 13 or 14 digits count. A run of 9, 10, 11 or
    more than 14 digits is not a GTIN and is skipped whole: a longer number is
    never trimmed down to a shorter one, because that would invent a valid
    check digit out of a different number.
    """
    if not isinstance(text, str) or not text:
        return []
    found = []
    for match in _DIGIT_RUN.finditer(text):
        run = match.group(0)
        if len(run) in GTIN_LENGTHS and gtin_valid(run) and run not in found:
            found.append(run)
    return found


def normalize_id(value):
    """Return value upper-cased with whitespace, "-", ".", "/" and "_" removed.

    Two ids are the same id when their normalized forms are equal. That makes
    "ac-4417", "AC 4417" and "AC4417" one value, which is how a source text and
    an extractor usually differ.

        normalize_id("ac-44 17/a.b_c")  # "AC4417ABC"
        normalize_id(None)              # ""
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _SEPARATORS.sub("", value.upper())


def find_links(text):
    """Return the http(s) links in text, in order of appearance, without repeats.

    A link ends at the first whitespace. Punctuation that closes the sentence
    around the link -- ")", ">", "]", ",", ".", ";" -- is removed, because it
    belongs to the prose and not to the address. Query and fragment are
    removed: two links that differ only in "?tag=..." or "#top" are one link.

        find_links("see https://a.example.com/x?y=1).")  # ["https://a.example.com/x"]

    Non-strings and text without links return an empty list.
    """
    if not isinstance(text, str) or not text:
        return []
    found = []
    for match in _URL.finditer(text):
        url = _clean_link(match.group(0))
        if url and url not in found:
            found.append(url)
    return found


def product_link_id(url):
    """Return a stable id for a marketplace product link, or None.

    The host decides. The label before the public suffix must be the
    marketplace: "amazon", "ebay" or "aliexpress". A path that looks like a
    product path on any other host is not a product link, because the id would
    then point at nothing that can be checked.

        https://www.amazon.de/dp/B0TEST1234          -> "ASIN:B0TEST1234"
        https://www.amazon.de/gp/product/B0TEST1234  -> "ASIN:B0TEST1234"
        https://www.ebay.de/itm/123456789012         -> "EBAY:123456789012"
        https://de.aliexpress.com/item/1005001234567 -> "ALIEXPRESS:1005001234567"
        https://www.example.com/p/AC-4417            -> None

    The ASIN is upper-cased, so a link written in lower case gives the same id.
    A path segment before "/dp/" is allowed: Amazon puts the product title
    there.
    """
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    labels = host.split(".")
    if len(labels) < 2:
        return None
    names = set(labels[:-1])
    path = parts.path or ""
    if "amazon" in names:
        match = _AMAZON_ASIN.search(path)
        return "ASIN:" + match.group(1).upper() if match else None
    if "ebay" in names:
        match = _EBAY_ITEM.search(path)
        return "EBAY:" + match.group(1) if match else None
    if "aliexpress" in names:
        match = _ALIEXPRESS_ITEM.search(path)
        return "ALIEXPRESS:" + match.group(1) if match else None
    return None


def ground(extraction, source_text):
    """Check extraction against source_text. Return (checked, findings).

    checked is a deep copy of extraction with flags and attachments applied.
    The input dict is never modified. findings is a list of finding dicts in a
    stable order; it is empty when the extraction is fully grounded.
    """
    checked = copy.deepcopy(extraction)
    if not isinstance(checked, dict):
        checked = {"items": []}
    items = checked.get("items")
    if not isinstance(items, list):
        items = []
        checked["items"] = items

    if not isinstance(source_text, str):
        source_text = ""

    flat_source = normalize_id(source_text)
    findings = []
    # VARIANT_SPLIT runs first because it is the only check that removes an
    # item. Every finding recorded after it therefore carries an index that is
    # still valid in the final list. UNGROUNDED_ID runs before any item is
    # appended, so an item this module adds is never flagged against itself.
    findings.extend(_check_variant_split(items))
    findings.extend(_check_ids(items, flat_source))
    findings.extend(_check_redacted_links(items, source_text))
    findings.extend(_check_uncovered_links(items, source_text))
    findings.extend(_check_set_multiplied(items, source_text))
    findings.extend(_check_missing_gtins(items, source_text))
    return checked, findings


# ── checks ─────────────────────────────────────────────────────────────────

def _check_ids(items, flat_source):
    """UNGROUNDED_ID: flag item ids that do not occur in the normalized source.

    Only part_number and gtin are checked in this card. A value shorter than
    MIN_ID_LEN is skipped: it is too common to prove or disprove.
    """
    findings = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        for key in ("part_number", "gtin"):
            raw = item.get(key)
            if raw is None or raw == "":
                continue
            flat = normalize_id(raw)
            if len(flat) < MIN_ID_LEN or flat in flat_source:
                continue
            item["uncertain"] = True
            findings.append({
                "code": "UNGROUNDED_ID",
                "item_index": index,
                "value": raw if isinstance(raw, str) else str(raw),
                "evidence": "%s=%s normalizes to %s, which does not occur in "
                            "the source text" % (key, raw, flat),
                "action": "flagged",
            })
    return findings


def _check_redacted_links(items, source_text):
    """REDACTED_LINK: put back a product link that a redaction cut in two.

    A redaction pass that protects phone numbers can hit a long digit run in a
    product link and leave ".../item/1[Telefon]234567". The text before the
    placeholder still identifies the product, so the original link from the
    source is restored when one starts with that prefix. A "[" inside the first
    MIN_REDACTED_AT characters is not a cut in the middle of a path and is left
    alone.
    """
    findings = []
    links = find_links(source_text)
    if not links:
        return findings
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        link = item.get("link")
        if not isinstance(link, str):
            continue
        cut = link.find("[")
        if cut < MIN_REDACTED_AT:
            continue
        prefix = link[:cut]
        for candidate in links:
            if not candidate.startswith(prefix):
                continue
            item["link"] = candidate
            findings.append({
                "code": "REDACTED_LINK",
                "item_index": index,
                "value": candidate,
                "evidence": "item link %s was cut at character %d by a "
                            "redaction placeholder; the source holds %s"
                            % (link, cut, candidate),
                "action": "restored",
            })
            break
    return findings


def _check_uncovered_links(items, source_text):
    """UNCOVERED_LINK: add an item for a product link that no item covers.

    A product link in the source is a position the extractor missed. The item
    is appended, never inserted, so the extractor's own order stays readable.
    The quantity comes from the same line ("4x <link>") or from the line above
    when that line holds nothing but a quantity.
    """
    findings = []
    lines = source_text.splitlines()
    seen = set()
    for line_index, line in enumerate(lines):
        for match in _URL.finditer(line):
            url = _clean_link(match.group(0))
            if not url or url in seen:
                continue
            seen.add(url)
            link_id = product_link_id(url)
            if link_id is None:
                continue
            if any(_covers_link(item, url, link_id) for item in items):
                continue
            items.append({
                "description": link_id,
                "link": url,
                "part_number": link_id,
                "quantity": _link_quantity(lines, line_index, line,
                                           match.group(0)),
                "unit": "piece",
                "uncertain": True,
            })
            findings.append({
                "code": "UNCOVERED_LINK",
                "item_index": len(items) - 1,
                "value": link_id,
                "evidence": line.strip() or url,
                "action": "added",
            })
    return findings


def _check_set_multiplied(items, source_text):
    """SET_MULTIPLIED: an M-pack ordered N times is N sets, not N*M pieces.

    The source writes "quantity: 28 x 5" for 28 sets of 5. An extractor that
    reports 140 pieces has lost the set, and the quote then prices 140 single
    items. The quantity is set back to the number of sets.
    """
    findings = []
    for match in _SET_LINE.finditer(source_text):
        outer = int(match.group(1))
        pack = int(match.group(2))
        # A "1 x 1" is not a set and "0" is not an order.
        if outer <= 0 or pack <= 1:
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if _quantity_of(item) != outer * pack:
                continue
            if not _marks_pack(item.get("description"), pack):
                continue
            item["quantity"] = outer
            item["unit"] = "set"
            item["uncertain"] = True
            findings.append({
                "code": "SET_MULTIPLIED",
                "item_index": index,
                "value": str(outer * pack),
                "evidence": "source says \"%s\": %d sets of %d, so the "
                            "quantity is %d sets and not %d pieces"
                            % (match.group(0), outer, pack, outer, outer * pack),
                "action": "corrected",
            })
    return findings


def _check_variant_split(items):
    """VARIANT_SPLIT: fold a variant line back into the item it belongs to.

    A supplier lists one part number twice, once per variant, under one
    quantity. The later line has no quantity of its own because it is not a
    second position. It is merged into the first item as a note and removed, so
    the quote does not count the part twice.
    """
    findings = []
    for index in range(len(items) - 1, 0, -1):
        item = items[index]
        if not isinstance(item, dict):
            continue
        number = normalize_id(item.get("part_number"))
        if len(number) < MIN_VARIANT_ID_LEN:
            continue
        if _quantity_of(item) is not None:
            continue
        first_index = None
        for earlier in range(index):
            other = items[earlier]
            if (isinstance(other, dict)
                    and normalize_id(other.get("part_number")) == number):
                first_index = earlier
                break
        if first_index is None:
            continue
        first = items[first_index]
        label = item.get("description") or number
        piece = "variant: %s" % label
        note = first.get("note")
        first["note"] = ("%s · %s" % (note, piece)) if note else piece
        # The link is the only evidence that the variant is a real product, so
        # it moves to the surviving item instead of disappearing with this one.
        if isinstance(item.get("link"), str) and not first.get("link"):
            first["link"] = item["link"]
        first["uncertain"] = True
        items.pop(index)
        findings.append({
            "code": "VARIANT_SPLIT",
            "item_index": first_index,
            "value": str(first.get("part_number") or number),
            "evidence": "items share part number %s and \"%s\" has no quantity "
                        "of its own, so it is a variant of the first item"
                        % (number, label),
            "action": "merged",
        })
    findings.sort(key=lambda finding: finding["item_index"])
    return findings


def _check_missing_gtins(items, source_text):
    """MISSING_GTIN: attach a valid GTIN from the source that no item carries.

    The target item is the one whose description shares at least MATCH_RATIO of
    its words with the source line holding the GTIN. When no description is
    close enough and exactly one item exists, that item takes the GTIN: with a
    single item there is no wrong item to pick. Otherwise the finding is
    reported with item_index None and nothing is changed.
    """
    findings = []
    gtins = find_gtins(source_text)
    if not gtins:
        return findings

    lines = source_text.splitlines()
    carried = _carried_ids(items)
    for gtin in gtins:
        if gtin in carried:
            continue
        line = _line_with(lines, gtin)
        index = _best_item(items, line)
        if index is None:
            findings.append({
                "code": "MISSING_GTIN",
                "item_index": None,
                "value": gtin,
                "evidence": line or "GTIN %s occurs in the source text" % gtin,
                "action": "reported",
            })
            continue

        item = items[index]
        if normalize_id(item.get("gtin")):
            others = _as_list(item.get("other_ids"))
            others.append(gtin)
            item["other_ids"] = others
        else:
            item["gtin"] = gtin
        item["uncertain"] = True
        carried.add(gtin)
        findings.append({
            "code": "MISSING_GTIN",
            "item_index": index,
            "value": gtin,
            "evidence": line or "GTIN %s occurs in the source text" % gtin,
            "action": "attached",
        })
    return findings


# ── helpers ────────────────────────────────────────────────────────────────

def _as_list(value):
    """Coerce an optional id list field to a list, without changing the input."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _carried_ids(items):
    """Normalized ids that some item already claims, from gtin and other_ids."""
    carried = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for value in [item.get("gtin")] + _as_list(item.get("other_ids")):
            flat = normalize_id(value)
            if flat:
                carried.add(flat)
    return carried


def _line_with(lines, needle):
    """Return the first source line containing needle, stripped. "" if none."""
    for line in lines:
        if needle in line:
            return line.strip()
    return ""


def _words(text):
    """Lower-case words of MIN_WORD_LEN or more letters. A set, for overlap."""
    if not isinstance(text, str):
        return set()
    return set(w.lower() for w in _WORD.findall(text) if len(w) >= MIN_WORD_LEN)


def _overlap(left, right):
    """Share of the smaller word set that both sets hold. 0.0 when either is empty."""
    if not left or not right:
        return 0.0
    return len(left & right) / float(min(len(left), len(right)))


def _best_item(items, line):
    """Index of the item that best matches line, or None.

    A tie goes to the earlier item: the first mention in the extraction is the
    one a human would check first.
    """
    line_words = _words(line)
    best_index = None
    best_ratio = 0.0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        ratio = _overlap(_words(item.get("description")), line_words)
        if ratio > best_ratio:
            best_index = index
            best_ratio = ratio
    if best_index is not None and best_ratio >= MATCH_RATIO:
        return best_index
    if len(items) == 1 and isinstance(items[0], dict):
        return 0
    return None


# ── link helpers ───────────────────────────────────────────────────────────

def _clean_link(url):
    """Return url without sentence punctuation, query or fragment.

    "https://a.example.com/x?y=1)." becomes "https://a.example.com/x". The
    order matters: the closing ")" must go before the query is cut, otherwise
    "?y=1)." would look like part of the query.
    """
    if not isinstance(url, str):
        return ""
    url = _TRAILING_PUNCTUATION.sub("", url.strip())
    cut = len(url)
    for character in "?#":
        position = url.find(character)
        if position != -1 and position < cut:
            cut = position
    return url[:cut]


def _to_number(value):
    """Return value as an int or float, or None when it holds no number.

    A quantity arrives as 4, "4", 4.0 or "1,5" depending on the extractor. All
    of those must compare equal to the number in the source.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number == int(number) else number


def _quantity_of(item):
    """Return the numeric quantity of an item, or None when it has none."""
    if not isinstance(item, dict):
        return None
    return _to_number(item.get("quantity"))


def _link_id_forms(link_id):
    """Return the normalized forms of a product link id.

    Both the full id ("ASIN:B0TEST1234") and its bare part ("B0TEST1234") count
    as a match, because an extractor writes one or the other into part_number.
    """
    forms = [normalize_id(link_id)]
    _head, separator, tail = link_id.partition(":")
    if separator and tail:
        forms.append(normalize_id(tail))
    return [form for form in forms if form]


def _covers_link(item, url, link_id):
    """True when item already stands for the product link url.

    An item covers a link when it holds the same cleaned link, or when the id
    of the link occurs in its part_number, its other_ids or its own link.
    """
    if not isinstance(item, dict):
        return False
    item_link = item.get("link")
    if isinstance(item_link, str) and _clean_link(item_link).lower() == url.lower():
        return True
    forms = _link_id_forms(link_id)
    values = [item.get("part_number"), item.get("link")]
    values.extend(_as_list(item.get("other_ids")))
    for value in values:
        flat = normalize_id(value)
        if not flat:
            continue
        for form in forms:
            if form in flat:
                return True
    return False


def _link_quantity(lines, line_index, line, raw_url):
    """Return the quantity that belongs to a link, or None.

    The quantity is on the same line before the link ("4x <link>"), or on the
    line above when that line holds nothing but a quantity. A blank line above
    is skipped: an empty line is layout, not information.
    """
    rest = line.replace(raw_url, " ", 1)
    match = _QTY_LEAD.match(rest)
    if match:
        return _to_number(match.group(1))
    for back in range(line_index - 1, -1, -1):
        previous = lines[back].strip()
        if not previous:
            continue
        match = _QTY_ONLY.match(previous)
        return _to_number(match.group(1)) if match else None
    return None


def _marks_pack(description, pack):
    """True when description marks a pack of exactly pack pieces.

    "5-pack", "pack of 5", "set of 5" and "5er" all say the same thing. The
    number must match the pack size in the source; a "10-pack" is not a "5 x".
    """
    if not isinstance(description, str) or not description:
        return False
    for pattern in _PACK_MARKERS:
        for match in pattern.finditer(description):
            if int(match.group(1)) == pack:
                return True
    return False

