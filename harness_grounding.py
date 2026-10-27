#!/usr/bin/env python3
"""harness_grounding — tie an LLM extraction back to the text it came from.

No LLM. No network. Standard library only, Python >= 3.8. The checker is
deterministic on purpose: a grounding check that needs a model to run fails
exactly when the model is having a bad day, which is when the extraction is
least trustworthy.

The job is narrow. An extractor reads a supplier mail or a PDF and returns
items. This module compares those items against the source text and marks the
ones the source does not support. It never rewrites a value into something the
source does not say, and it reports every change it makes. VARIANT_SPLIT is the
only check that removes an item; it reports the merge.

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
    "action": "flagged",       # what ground() did about it
    "field": "part_number",    # item key the finding concerns, or None
    "old_value": "AC-9999",    # value the field held before, or None
    "new_value": None,         # value written into the field, None if none
    "item_index": 0,           # index into the RETURNED items list, or None
    "input_index": 0,          # index into the CALLER's items list, or None
    "evidence": "...",         # short quotable reason, safe to show a human
}

THE FINDING CONTRACT. Every finding from every check carries exactly those
eight keys and no others. The rules, once, for all six checks:

    code, action   the machine code and what ground() did about it.
    field          the item key the finding concerns: "part_number", "gtin",
                   "link", "quantity", "note", "other_ids", or None when no
                   single key is concerned.
    old_value      what that key held before this module touched it. None when
                   the key was absent or empty.
    new_value      what this module wrote into that key. None when it wrote
                   nothing, which is what a "flagged" or "reported" finding
                   does. For the list key other_ids it is the entry added.
    item_index     index of the item in the RETURNED items list. None when no
                   item is concerned.
    input_index    index of that item in the list the caller passed in. None
                   for an item this module appended, because it has no place in
                   the caller's input.

A finding never carries a bare "value" key. It is ambiguous: it held the old
value for some codes and the new one for others. Read old_value and new_value.

An appended item never invents a part number or a description. A marketplace id
belongs in other_ids, so an item added for an uncovered link carries
other_ids ["ASIN:..."], the link, the quantity and the unit, nothing else.

Codes and actions in this module:

    UNGROUNDED_ID / "flagged"    an item id does not occur in the source text
                                 on a token boundary. field is the key,
                                 old_value the id. The item stays; uncertain is
                                 set True.
    MISSING_GTIN  / "attached"   a valid GTIN in the source was missing from the
                                 extraction. A digit run counts as a GTIN only
                                 when its line names one (GTIN, EAN, UPC,
                                 Barcode, Strichcode) and the run is not inside
                                 a link. It is attached to the best-matching
                                 item, which is marked uncertain. field is
                                 "gtin" or "other_ids", new_value the GTIN.
    MISSING_GTIN  / "reported"   same, but no item could take it. The finding
                                 has item_index None, both value slots None and
                                 nothing is changed; evidence names the GTIN.
    REDACTED_LINK / "restored"   an item link was cut by a redaction
                                 placeholder such as "[Telefon]" and exactly
                                 one source link matches the text before and
                                 after the cut. That link is put back and the
                                 item is marked uncertain. field is "link".
    REDACTED_LINK / "flagged"    same cut, but no source link or more than one
                                 matches it. Nothing is rewritten, the item is
                                 marked uncertain and the finding names the cut
                                 link in old_value.
    UNCOVERED_LINK / "added"     a product link in the source that no item
                                 covers. A new item is appended and marked
                                 uncertain. field is "link", new_value the link.
    SET_MULTIPLIED / "corrected" the source says "N x M" and the item is an
                                 M-pack with quantity N*M. The quantity becomes
                                 N, the unit "set", and the item is marked
                                 uncertain. field is "quantity".
    VARIANT_SPLIT / "merged"     a later item repeats the part number of an
                                 earlier one and has no quantity of its own. It
                                 is folded into the first item as a variant.
                                 field is "note", new_value the note added. Its
                                 link, gtin and other_ids move onto the
                                 survivor when it lacks them; a value both
                                 lines carry differently stays on the survivor
                                 and is reported as one more VARIANT_SPLIT
                                 finding for that field, with old_value the
                                 merged line's value.

A link is a product link only when product_link_id() returns an id for it. A
link to any other host is not a product and is never added as an item.

ground() returns (checked, findings). checked is a deep copy of the input, so
the caller keeps the original and the module never mutates what it was given.
Findings come in a stable order: VARIANT_SPLIT in item order, then
UNGROUNDED_ID in item order, then REDACTED_LINK in item order, then
UNCOVERED_LINK in source order, then SET_MULTIPLIED in source order, then
MISSING_GTIN in source order.

VARIANT_SPLIT runs first because it is the only check that removes an item, so
every later item_index is already final. UNGROUNDED_ID runs before any item is
appended, so an item this module adds is never flagged against itself.

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

# Words that name no product. A GTIN line carries them ("GTIN ...", "part ...")
# and a description may carry them too, so an overlap on one of them alone
# proves nothing and must not move a GTIN to another product.
GENERIC_WORDS = frozenset((
    "part", "parts", "item", "items", "number", "nummer", "artikel",
    "gtin", "ean", "upc", "barcode", "strichcode",
    "quantity", "menge", "qty", "piece", "pieces", "stueck", "stück",
    "order", "bestellung", "supply", "confirmation", "total", "price",
    "preis", "product", "produkt", "please", "siehe",
))

# A placeholder earlier than this is not a redacted product path. Every product
# path this module recognises is longer than 12 characters, so a "[" in the
# first 12 characters is the start of the link itself, not a cut in its middle.
MIN_REDACTED_AT = 12

# Two part numbers are the same variant only when their normalized forms are
# equal and at least this long. Below it, two items can share an id by accident.
MIN_VARIANT_ID_LEN = 5

# The characters that join the parts of one id. A supplier writes AC-4417,
# AC 4417, AC.4417 and AC/4417 for the same part. A PDF or a mail client also
# swaps the hyphen for a lookalike: hyphen U+2010, non-breaking hyphen U+2011,
# figure dash U+2012, en dash U+2013, em dash U+2014, horizontal bar U+2015,
# soft hyphen U+00AD or zero-width space U+200B. All of them are separators
# here, so the same id written with one of them stays the same id.
_ID_JOINERS = "-./_\u2010\u2011\u2012\u2013\u2014\u2015\u00ad\u200b"
_ID_JOINER_SET = frozenset(_ID_JOINERS)

# Whitespace plus every joiner above. re.escape keeps "-" literal inside the
# class; "\s" is kept so unicode spaces a table exports are separators too.
_SEPARATORS = re.compile("[" + re.escape(_ID_JOINERS) + r"\s]+")

# A standalone digit run: no digit directly before or after it. The negative
# lookarounds are what stop "940063813339311" (15 digits) from matching as a
# 14-digit GTIN plus a stray digit.
_DIGIT_RUN = re.compile(r"(?<!\d)\d{8,14}(?!\d)")

# Letter runs only. Digits and underscores split words, which is what we want
# for descriptions such as "Hex bolt M8".
_WORD = re.compile(r"[^\W\d_]+")

# A link ends at whitespace or at a character that cannot be part of a URL in
# prose: angle brackets, the double quote, the backslash, the closing square
# bracket of Markdown "[url](url)" and the curly quotes a German mail uses. The
# trailing punctuation of a sentence is removed in _clean_link.
_URL = re.compile(r'https?://[^\s<>"\\\]“”]+', re.IGNORECASE)

# Sentence punctuation that follows a link but does not belong to it: closing
# brackets, a full stop, comma, semicolon, colon, "!", an apostrophe, Markdown
# bold and the German quotes. str.rstrip of these characters is linear; the
# regex that used to do this job backtracked quadratically on a long run of ")"
# inside a link (20,000 characters took about a second).
_TRAILING_PUNCTUATION = ")>].,;:!'\"*“”‘’"

# Marketplace product paths. The host is checked separately, so a path that
# looks like a product path on another host stays an ordinary link. The path is
# matched in any case: suppliers copy "/DP/" as often as "/dp/".
_AMAZON_ASIN = re.compile(r"/(?:dp|gp/product)/([A-Za-z0-9]{10})(?=/|$)",
                          re.IGNORECASE)
_EBAY_ITEM = re.compile(r"/itm/(\d+)")
_ALIEXPRESS_ITEM = re.compile(r"/item/(\d+)")

# The registrable domain of every marketplace this module recognises, mapped to
# the marketplace name. A host is a marketplace host only when its registrable
# domain is one of these exact strings: "amazon.example.com" and
# "www.ebay.attacker.example" are other people's domains, whatever labels they
# carry. Any subdomain of a listed domain is that marketplace.
MARKETPLACE_DOMAINS = {
    "amazon.de": "amazon",
    "amazon.at": "amazon",
    "amazon.ch": "amazon",
    "amazon.com": "amazon",
    "amazon.co.uk": "amazon",
    "amazon.fr": "amazon",
    "amazon.it": "amazon",
    "amazon.es": "amazon",
    "amazon.nl": "amazon",
    "amazon.pl": "amazon",
    "amazon.se": "amazon",
    "amazon.com.tr": "amazon",
    "amazon.ca": "amazon",
    "amazon.com.au": "amazon",
    "amazon.co.jp": "amazon",
    "amazon.in": "amazon",
    "ebay.de": "ebay",
    "ebay.at": "ebay",
    "ebay.ch": "ebay",
    "ebay.com": "ebay",
    "ebay.co.uk": "ebay",
    "ebay.fr": "ebay",
    "ebay.it": "ebay",
    "ebay.es": "ebay",
    "ebay.nl": "ebay",
    "ebay.pl": "ebay",
    "aliexpress.com": "aliexpress",
    "aliexpress.us": "aliexpress",
}

# A line that names a GTIN. Without one of these words the module cannot tell a
# GTIN from a phone number, an order number or a marketplace id: "Tel. +49 89
# 12345670" and "Order 20261023" both carry a valid check digit. German and
# English, because a supplier writes one or the other.
_GTIN_KEYWORD = re.compile(
    r"\b(?:gtin|ean|upc|bar\s*code|strichcode)\b", re.IGNORECASE)

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

# Ways a supplier writes that the item is a pack of M pieces: "5 pack",
# "5-pack", "5er-Pack", "5er Pack", "pack of 5", "set of 5", "5er".
_PACK_MARKERS = (
    re.compile(r"(\d+)\s*-\s*pack\b", re.IGNORECASE),
    re.compile(r"(\d+)\s*pack\b", re.IGNORECASE),
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


def gtin14(code):
    """Return code as a GTIN-14, or None when it is not a valid GTIN.

    GS1 has one number space, not four: a GTIN-8, -12 and -13 are the GTIN-14
    with leading zeros. Comparing the padded form is what makes "036000291452"
    and "0036000291452" the same GTIN.

        gtin14("036000291452")   # "00036000291452"
        gtin14("0036000291452")  # "00036000291452"
        gtin14("4006381333932")  # None, bad check digit
    """
    if not gtin_valid(code):
        return None
    return code.strip().zfill(14)


# A label an extractor puts in front of a GTIN: "EAN:...", "GTIN 13:...",
# "UPC-...". It names the number and is not part of it.
_GTIN_LABEL = re.compile(
    r"^(?:ean|gtin|upc)(?:[-\s]?\d{1,2})?\s*[:\-]?\s*", re.IGNORECASE)


def _gtin14_of_value(value):
    """Return the GTIN-14 form of an id value, with or without a label.

    An extractor writes the same GTIN as "036000291452", "EAN:036000291452" or
    "0036000291452". All three are the same number; None means the value is not
    a valid GTIN at all.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for candidate in (text, _GTIN_LABEL.sub("", text)):
        form = gtin14(candidate)
        if form is not None:
            return form
    return None


def normalize_id(value):
    """Return value upper-cased with whitespace and every id joiner removed.

    Two ids are the same id when their normalized forms are equal. That makes
    "ac-4417", "AC 4417" and "AC4417" one value, which is how a source text and
    an extractor usually differ. The joiners are "-", ".", "/", "_", whitespace
    and the hyphen lookalikes a PDF leaves behind: U+2010, U+2011, U+2012,
    U+2013, U+2014, U+2015, soft hyphen U+00AD and zero-width space U+200B.

        normalize_id("ac-44 17/a.b_c")  # "AC4417ABC"
        normalize_id("AC\\u20134417")     # "AC4417", en dash
        normalize_id(None)              # ""

    This is the comparable form of an id, not the whole grounding rule: see
    _id_form and _id_occurs, which also require a token boundary.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _SEPARATORS.sub("", value.upper())


def _id_form(value):
    """Return the comparable form of an id: upper-case letters and digits only.

    Everything else is dropped, joiners included, so "AC-4417", "ac 4417",
    "AC\u20134417" and "(AC-4417)" are one value. The form is compared against
    the source on token boundaries, which is what stops a prefix from counting
    as the id.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return "".join(char for char in value.upper() if char.isalnum())


def _source_id_index(text):
    """Return (flat, boundary) for a source text.

    flat is the text upper-cased with every character that is not a letter or a
    digit removed. boundary[i] is True when flat position i is a token boundary
    in the original text: the start, the end, or a position next to a character
    that is not part of an id.

    The boundaries are the point. A source that holds "AC-4417" also holds the
    substring "AC-441", but not the token: the character after it is a digit, so
    that occurrence is inside a token and grounds nothing.
    """
    flat = []
    boundary = [True]
    for char in text:
        if not char.isalnum():
            # A joiner or any other separator ends the token before it and
            # starts the token after it: one boundary at this flat position.
            boundary[-1] = True
            continue
        flat.append(char.upper())
        boundary.append(False)
    boundary[-1] = True
    return "".join(flat), boundary


def _id_occurs(flat_value, flat_source, boundary):
    """True when flat_value occurs in flat_source on token boundaries."""
    if not flat_value:
        return False
    start = flat_source.find(flat_value)
    while start != -1:
        if boundary[start] and boundary[start + len(flat_value)]:
            return True
        start = flat_source.find(flat_value, start + 1)
    return False


def find_links(text):
    """Return the http(s) links in text, in order of appearance, without repeats.

    A link ends at the first whitespace or at a character that cannot be part of
    a URL in prose. Punctuation that closes the sentence around the link -- ")",
    ">", "]", ",", ".", ";", ":", "!", "'", Markdown "**" and the German quotes
    -- is removed, because it belongs to the prose and not to the address. A
    Markdown "[url](url)" therefore yields the address, not the glue between the
    two halves. Query and fragment are removed: two links that differ only in
    "?tag=..." or "#top" are one link.

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

    The host decides, and it must be a real marketplace domain. The host is
    reduced to its registrable domain ("www.amazon.de" -> "amazon.de") and that
    domain must be in MARKETPLACE_DOMAINS. A label that merely contains the
    name is not enough: "amazon.example.com" and "www.ebay.attacker.example" are
    other people's domains, so an id from them would point at a product that
    cannot be checked.

        https://www.amazon.de/dp/B0TEST1234          -> "ASIN:B0TEST1234"
        https://www.amazon.de/gp/product/B0TEST1234  -> "ASIN:B0TEST1234"
        https://www.ebay.de/itm/123456789012         -> "EBAY:123456789012"
        https://de.aliexpress.com/item/1005001234567 -> "ALIEXPRESS:1005001234567"
        https://www.example.com/p/AC-4417            -> None
        https://amazon.example.com/dp/B0TEST1234     -> None

    The ASIN is upper-cased, so a link written in lower case gives the same id.
    A path segment before "/dp/" is allowed: Amazon puts the product title
    there. Any subdomain of a marketplace domain is that marketplace.
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
    marketplace = _marketplace_of(host)
    if marketplace is None:
        return None
    path = parts.path or ""
    if marketplace == "amazon":
        match = _AMAZON_ASIN.search(path)
        return "ASIN:" + match.group(1).upper() if match else None
    if marketplace == "ebay":
        match = _EBAY_ITEM.search(path)
        return "EBAY:" + match.group(1) if match else None
    match = _ALIEXPRESS_ITEM.search(path)
    return "ALIEXPRESS:" + match.group(1) if match else None


def _marketplace_of(host):
    """Return the marketplace that owns host, or None.

    The host is matched on its exact registrable domain: the last two or three
    labels must be in MARKETPLACE_DOMAINS. Two labels cover "amazon.de", three
    cover "amazon.co.uk" and "amazon.com.tr". A longer suffix is never accepted,
    so "amazon.example.com" stays a foreign domain.
    """
    if not host:
        return None
    labels = host.split(".")
    for size in (3, 2):
        if len(labels) >= size:
            domain = ".".join(labels[-size:])
            if domain in MARKETPLACE_DOMAINS:
                return MARKETPLACE_DOMAINS[domain]
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

    # origins[i] is the index of items[i] in the caller's list, or None for an
    # item this module appended. It moves with every pop and append, so a
    # finding can name both the returned position and the input position.
    origins = list(range(len(items)))
    findings = []
    # VARIANT_SPLIT runs first because it is the only check that removes an
    # item. Every finding recorded after it therefore carries an index that is
    # still valid in the final list. UNGROUNDED_ID runs before any item is
    # appended, so an item this module adds is never flagged against itself.
    findings.extend(_check_variant_split(items, origins))
    findings.extend(_check_ids(items, origins, source_text))
    findings.extend(_check_redacted_links(items, origins, source_text))
    findings.extend(_check_uncovered_links(items, origins, source_text))
    findings.extend(_check_set_multiplied(items, origins, source_text))
    findings.extend(_check_missing_gtins(items, origins, source_text))
    return checked, findings


def _finding(code, action, evidence, field=None, old_value=None,
             new_value=None, item_index=None, input_index=None):
    """Build one finding with the documented eight keys, in a fixed order."""
    return {
        "code": code,
        "action": action,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "item_index": item_index,
        "input_index": input_index,
        "evidence": evidence,
    }


# ── checks ─────────────────────────────────────────────────────────────────

def _check_ids(items, origins, source_text):
    """UNGROUNDED_ID: flag item ids that do not occur in the source text.

    Only part_number and gtin are checked. A value shorter than MIN_ID_LEN is
    skipped: it is too common to prove or disprove.

    An id is grounded only on a token boundary. "AC-441" is not grounded by a
    source that holds "AC-4417", and "2610" is not grounded by the date
    2026-10-26. A gtin is compared as a GTIN-14, so the same number with or
    without its leading zeros is grounded either way.
    """
    findings = []
    flat_source, boundary = _source_id_index(source_text)
    source_gtins14 = set()
    for gtin in find_gtins(source_text):
        form = gtin14(gtin)
        if form is not None:
            source_gtins14.add(form)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        for key in ("part_number", "gtin"):
            raw = item.get(key)
            if raw is None or raw == "":
                continue
            if key == "gtin":
                form = _gtin14_of_value(raw)
                if form is not None and form in source_gtins14:
                    continue
            flat = _id_form(raw)
            if len(flat) < MIN_ID_LEN or _id_occurs(flat, flat_source, boundary):
                continue
            item["uncertain"] = True
            findings.append(_finding(
                "UNGROUNDED_ID", "flagged",
                evidence="%s=%s normalizes to %s, which does not occur in "
                         "the source text on a token boundary"
                         % (key, raw, flat),
                field=key,
                old_value=raw if isinstance(raw, str) else str(raw),
                item_index=index,
                input_index=origins[index]))
    return findings


def _check_redacted_links(items, origins, source_text):
    """REDACTED_LINK: put back a product link that a redaction cut in two.

    A redaction pass that protects phone numbers can hit a long digit run in a
    product link and leave ".../item/1[Telefon]234567". The text before the
    placeholder is the prefix and the text after it is the suffix. The link is
    restored only when EXACTLY ONE source link starts with that prefix and ends
    with that suffix: two links that share a prefix (every AliExpress id starts
    with 1005...) are not evidence for either product.

    No match or several matches means the cut link cannot be proven. It is left
    exactly as the extractor wrote it and the item is flagged uncertain, so a
    human checks it. A restore is also a data change, so it sets uncertain too.

    A "[" inside the first MIN_REDACTED_AT characters is not a cut in the
    middle of a path and is left alone without a finding.
    """
    findings = []
    links = find_links(source_text)
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
        close = link.find("]", cut)
        suffix = link[close + 1:] if close != -1 else ""
        matches = [candidate for candidate in links
                   if candidate.startswith(prefix) and candidate.endswith(suffix)]

        item["uncertain"] = True
        if len(matches) != 1:
            findings.append(_finding(
                "REDACTED_LINK", "flagged",
                evidence="item link %s was cut at character %d by a "
                         "redaction placeholder and %s"
                         % (link, cut, _match_count_note(len(matches))),
                field="link",
                old_value=link,
                item_index=index,
                input_index=origins[index]))
            continue

        candidate = matches[0]
        item["link"] = candidate
        findings.append(_finding(
            "REDACTED_LINK", "restored",
            evidence="item link %s was cut at character %d by a "
                     "redaction placeholder; exactly one source link "
                     "matches the text before and after it: %s"
                     % (link, cut, candidate),
            field="link",
            old_value=link,
            new_value=candidate,
            item_index=index,
            input_index=origins[index]))
    return findings


def _match_count_note(count):
    """Return the reason a cut link was not restored, for the evidence text."""
    if count == 0:
        return "no source link matches both the text before and the text after it"
    return ("%d source links match both the text before and the text after it, "
            "so the product is ambiguous" % count)


def _check_uncovered_links(items, origins, source_text):
    """UNCOVERED_LINK: add an item for a product link that no item covers.

    A product link in the source is a position the extractor missed. The item
    is appended, never inserted, so the extractor's own order stays readable.
    The quantity comes from the same line ("4x <link>") or from the line above
    when that line holds nothing but a quantity. The marketplace id goes into
    other_ids: it is not a part number and not a description.
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
                "other_ids": [link_id],
                "link": url,
                "quantity": _link_quantity(lines, line_index, line,
                                           match.group(0)),
                "unit": "piece",
                "uncertain": True,
            })
            origins.append(None)
            findings.append(_finding(
                "UNCOVERED_LINK", "added",
                evidence=line.strip() or url,
                field="link",
                new_value=url,
                item_index=len(items) - 1,
                input_index=None))
    return findings


def _check_set_multiplied(items, origins, source_text):
    """SET_MULTIPLIED: an M-pack ordered N times is N sets, not N*M pieces.

    The source writes "quantity: 28 x 5" for 28 sets of 5. An extractor that
    reports 140 pieces has lost the set, and the quote then prices 140 single
    items. The quantity is set back to the number of sets.

    Two rules keep the correction honest. The pack size comes from the source
    text only: a pack word in the extractor's description is the extractor's
    own claim and proves nothing about the order. And an item is corrected at
    most once, so a second set line cannot compound one correction into
    another (8 -> 4 -> 2).
    """
    findings = []
    corrected = set()
    for match in _SET_LINE.finditer(source_text):
        outer = int(match.group(1))
        pack = int(match.group(2))
        # A "1 x 1" is not a set and "0" is not an order.
        if outer <= 0 or pack <= 1:
            continue
        if not _marks_pack(source_text, pack):
            continue
        for index, item in enumerate(items):
            if index in corrected or not isinstance(item, dict):
                continue
            if _quantity_of(item) != outer * pack:
                continue
            old = item.get("quantity")
            item["quantity"] = outer
            item["unit"] = "set"
            item["uncertain"] = True
            corrected.add(index)
            findings.append(_finding(
                "SET_MULTIPLIED", "corrected",
                evidence="source says \"%s\": %d sets of %d, so the "
                         "quantity is %d sets and not %d pieces"
                         % (match.group(0), outer, pack, outer, outer * pack),
                field="quantity",
                old_value=old,
                new_value=outer,
                item_index=index,
                input_index=origins[index]))
    return findings


def _check_variant_split(items, origins):
    """VARIANT_SPLIT: fold a variant line back into the item it belongs to.

    A supplier lists one part number twice, once per variant, under one
    quantity. The later line has no quantity of its own because it is not a
    second position. It is merged into the first item as a note and removed, so
    the quote does not count the part twice.

    A merge must not lose data. The variant's link, gtin and other_ids move to
    the survivor when the survivor lacks them. When both lines carry a
    different value for one key, the survivor keeps its own and the variant's
    value is named in the finding, with uncertain set. other_ids is a list, so
    both values are kept and there is no conflict.

    The merges are found first and applied afterwards, in input order. Notes
    therefore read the way the source lists the variants, and an item_index is
    read from the rebuilt list instead of being recorded before a later pop
    shifts it.
    """
    survivor_for = {}
    pairs = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        number = normalize_id(item.get("part_number"))
        if len(number) < MIN_VARIANT_ID_LEN:
            continue
        if number not in survivor_for:
            # The first line of a part number is the survivor. It is never
            # merged away itself, so it is always a valid merge target.
            survivor_for[number] = index
            continue
        if _quantity_of(item) is not None:
            continue
        pairs.append((index, survivor_for[number]))
    if not pairs:
        return []

    input_of = list(origins)
    merges = []
    for index, first_index in pairs:
        first = items[first_index]
        variant = items[index]
        label = variant.get("description") or normalize_id(
            first.get("part_number"))
        piece = "variant: %s" % label
        note = first.get("note")
        first["note"] = ("%s · %s" % (note, piece)) if note else piece
        first["uncertain"] = True
        merges.append((first, _merge_variant_values(first, variant), piece,
                       input_of[index]))

    removed = set(index for index, _first_index in pairs)
    kept = [index for index in range(len(items)) if index not in removed]
    position_of = {}
    for position, index in enumerate(kept):
        position_of.setdefault(id(items[index]), position)
    items[:] = [items[index] for index in kept]
    origins[:] = [input_of[index] for index in kept]

    findings = []
    for first, conflicts, piece, variant_input in merges:
        position = position_of[id(first)]
        number = normalize_id(first.get("part_number"))
        findings.append(_finding(
            "VARIANT_SPLIT", "merged",
            evidence="items share part number %s and the variant line has no "
                     "quantity of its own, so it is folded into item %d"
                     % (number, position),
            field="note",
            new_value=piece,
            item_index=position,
            input_index=variant_input))
        for key, theirs, ours in conflicts:
            findings.append(_finding(
                "VARIANT_SPLIT", "merged",
                evidence="the merged line carries %s %s and the surviving item "
                         "carries %s; the survivor keeps its own value and the "
                         "merged one is reported here"
                         % (key, theirs, ours),
                field=key,
                old_value=theirs,
                new_value=ours,
                item_index=position,
                input_index=variant_input))
    findings.sort(key=lambda finding: (finding["item_index"],
                                       finding["input_index"]))
    return findings


def _merge_variant_values(survivor, variant):
    """Move what the variant holds and the survivor lacks. Return conflicts.

    A conflict is (field, variant_value, survivor_value): both lines carry a
    different value, the survivor keeps its own and the variant's is reported.
    """
    conflicts = []
    for key in ("link", "gtin"):
        theirs = variant.get(key)
        if not isinstance(theirs, str) or not theirs.strip():
            continue
        ours = survivor.get(key)
        if not isinstance(ours, str) or not ours.strip():
            survivor[key] = theirs
            continue
        if key == "link":
            same = (_clean_link(theirs).lower() == _clean_link(ours).lower())
        else:
            same = normalize_id(theirs) == normalize_id(ours)
        if not same:
            conflicts.append((key, theirs, ours))
    theirs_ids = _as_list(variant.get("other_ids"))
    if theirs_ids:
        ours_ids = _as_list(survivor.get("other_ids"))
        known = set(normalize_id(value) for value in ours_ids)
        added = [value for value in theirs_ids
                 if normalize_id(value) not in known]
        if added:
            survivor["other_ids"] = ours_ids + added
    return conflicts


def _gtin_candidates(source_text):
    """Return [(gtin, line)] for the GTINs the source marks as GTINs, in order.

    Two rules decide what counts, and both exist because a digit run alone
    proves nothing:

    - The line must carry a GTIN keyword: GTIN, EAN, UPC, Barcode, Strichcode.
      "Tel. +49 89 12345670" and "Order 20261023" are valid check digits and
      are not GTINs.
    - The run must not sit inside a link. A marketplace item number in a URL
      is a product position, not a GTIN.

    The same number written twice is returned once, compared as a GTIN-14.
    """
    found = []
    seen = set()
    for line in source_text.splitlines():
        if not _GTIN_KEYWORD.search(line):
            continue
        spans = [match.span() for match in _URL.finditer(line)]
        for match in _DIGIT_RUN.finditer(line):
            run = match.group(0)
            if len(run) not in GTIN_LENGTHS or not gtin_valid(run):
                continue
            if any(start <= match.start() < end for start, end in spans):
                continue
            form = gtin14(run)
            if form in seen:
                continue
            seen.add(form)
            found.append((run, line.strip()))
    return found


def _check_missing_gtins(items, origins, source_text):
    """MISSING_GTIN: attach a valid GTIN from the source that no item carries.

    The target item is the one whose description shares at least MATCH_RATIO of
    its content words with the source line holding the GTIN. Generic words do
    not count: "part" occurs in half the descriptions and in half the source
    lines, so an overlap on it alone must not move a GTIN to another product.

    When no description is close enough and exactly one item exists, that item
    takes the GTIN: with a single item there is no wrong item to pick.
    Otherwise the finding is reported with item_index None and nothing is
    changed.
    """
    findings = []
    candidates = _gtin_candidates(source_text)
    if not candidates:
        return findings

    carried = _carried_gtins(items)
    for gtin, line in candidates:
        form = gtin14(gtin)
        if form in carried:
            continue
        index = _best_item(items, line)
        if index is None:
            findings.append(_finding(
                "MISSING_GTIN", "reported",
                evidence=line or "GTIN %s occurs in the source text" % gtin,
                field="gtin",
                item_index=None,
                input_index=None))
            continue

        item = items[index]
        if normalize_id(item.get("gtin")):
            others = _as_list(item.get("other_ids"))
            others.append(gtin)
            item["other_ids"] = others
            field = "other_ids"
        else:
            item["gtin"] = gtin
            field = "gtin"
        item["uncertain"] = True
        carried.add(form)
        findings.append(_finding(
            "MISSING_GTIN", "attached",
            evidence=line or "GTIN %s occurs in the source text" % gtin,
            field=field,
            new_value=gtin,
            item_index=index,
            input_index=origins[index]))
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


def _carried_gtins(items):
    """Return the GTIN-14 forms that some item already claims.

    A GTIN counts as carried wherever an extractor put it: in gtin, in
    part_number, or in other_ids with a label ("EAN:0036000291452"). The
    comparison is on the GTIN-14 form, so the same number with or without its
    leading zeros is not attached a second time under the other spelling.
    """
    carried = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        values = [item.get("gtin"), item.get("part_number")]
        values.extend(_as_list(item.get("other_ids")))
        for value in values:
            form = _gtin14_of_value(value)
            if form is not None:
                carried.add(form)
    return carried


def _words(text):
    """Lower-case content words of MIN_WORD_LEN or more letters, as a set.

    Generic words are dropped. "part", "item", "quantity" and "GTIN" name no
    product, and they occur in descriptions and in source lines alike, so an
    overlap on one of them alone must not decide where a GTIN goes.
    """
    if not isinstance(text, str):
        return set()
    return set(word.lower() for word in _WORD.findall(text)
               if len(word) >= MIN_WORD_LEN
               and word.lower() not in GENERIC_WORDS)


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

    The strip is a str.rstrip of a fixed set of characters, so the cost is
    linear in the length of the link. A regex with a "$" anchor is not: on a
    long run of ")" inside a link it retried from every position.
    """
    if not isinstance(url, str):
        return ""
    url = url.strip().rstrip(_TRAILING_PUNCTUATION)
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


def _marks_pack(text, pack):
    """True when text marks a pack of exactly pack pieces.

    "5 pack", "5-pack", "5er-Pack", "5er Pack", "pack of 5", "set of 5" and
    "5er" all say the same thing. The number must match the pack size; a
    "10-pack" is not a "5 x".
    """
    if not isinstance(text, str) or not text:
        return False
    for pattern in _PACK_MARKERS:
        for match in pattern.finditer(text):
            if int(match.group(1)) == pack:
                return True
    return False

