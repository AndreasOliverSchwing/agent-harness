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

A quantity is read as a count of whole pieces: a separator with exactly three
digits behind it groups thousands ("1.000" and "1,000" are 1000) and one, two,
four or more digits are a fraction ("1,5" is 1.5). A value above 10**9 or a
digit run longer than 12 is not a quantity, so untrusted text cannot raise.
ground() raises TypeError when extraction is not a dict, when extraction["items"]
is not a list, or when source_text is not a str.

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
                                 M-pack with quantity N*M, and the set line
                                 belongs to that item (the item is grounded on
                                 the set line or the line next to it, and the
                                 pack marker sits on that same line). The
                                 quantity becomes N, the unit "set", and the
                                 item is marked uncertain. field is "quantity".
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
from bisect import bisect_right
from decimal import Decimal
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

# A GTIN named by a keyword: the keyword, then at most a length suffix
# ("EAN-13", "EAN-8") or a "Nr."/"No." label, then the digit run. Only that run
# is the GTIN. A line may hold other numbers before or after it, and an order
# number or a telephone number on the same line is not a GTIN.
#
# The suffix must end at a separator, otherwise a GTIN that starts with "13"
# would lose its first two digits to the suffix. The run itself must not sit
# inside a longer digit run.
_GTIN_VALUE = re.compile(
    r"\b(?:gtin|ean|upc|bar\s*code|strichcode)\b"
    r"(?:\s*[-\u2013]\s*(?:8|12|13|14|nr\.?|no\.?)(?=[\s:.])"
    r"|\s+(?:nr\.?|no\.?)\b)?"
    r"\s*:?\s*"
    r"(\d{8,14})(?!\d)",
    re.IGNORECASE)

# The largest quantity this module reads, and the longest digit run it will
# look at. A quantity is a count of pieces; 10**9 is a billion pieces and no
# order carries that. The cap keeps numeric parsing safe on untrusted text:
# float(10 ** 400) raises OverflowError, and int() of a 5,000-digit run raises
# ValueError on Python 3.11+ (sys.set_int_max_str_digits). Both must be a
# "no quantity" answer, never a crash.
MAX_QUANTITY = 10 ** 9
MAX_QUANTITY_DIGITS = 12

# A quantity word at the end of a value: "2 pcs", "2 Stück", "4x", "2 St.".
# One list serves every quantity pattern, so a word that parses in a value also
# parses on a line of its own. The longer spellings come first: an alternative
# that matched "st" inside "stck" would leave "ck" behind and lose the number.
_QUANTITY_UNIT_WORDS = (
    r"x|×|pcs?\.?|pieces?|stck\.?|stueck|stück|stk\.?|st\.?|units?|sets?"
    r"|ve\.?|paar|pack|kg")
_QUANTITY_UNIT = re.compile(
    r"\s*(?:%s)\s*$" % _QUANTITY_UNIT_WORDS, re.IGNORECASE)

# A value that is a number with optional separators, and nothing else. A price
# with a currency sign ("12,50 €") is not a quantity and does not match.
_NUMBER_TEXT = re.compile(r"[+-]?[\d.,]+")

# A quantity that stands in front of a link on the same line: "4x <link>",
# "1.000 Stück <link>". The digit runs are bounded so a long run cannot make
# the pattern backtrack, and so no unbounded int() is reachable.
_QTY_BEFORE = re.compile(
    r"(\d{1,%d}(?:[.,]\d{1,%d})?)\s*"
    r"(?:%s)\s*$"
    % (MAX_QUANTITY_DIGITS, MAX_QUANTITY_DIGITS, _QUANTITY_UNIT_WORDS),
    re.IGNORECASE)

# A line that holds nothing but a quantity: "4", "4 x", "4 pcs", "1.000 Stück".
# Group 2 is the unit word; it is None for a bare number.
_QTY_LINE = re.compile(
    r"^\s*(\d{1,%d}(?:[.,]\d{1,%d})?)"
    r"(?:\s*(%s))?\s*$"
    % (MAX_QUANTITY_DIGITS, MAX_QUANTITY_DIGITS, _QUANTITY_UNIT_WORDS),
    re.IGNORECASE)

# A line that carries money carries no quantity. A price sits on the same line
# above a link that a quantity would sit on, so it needs its own rule.
_CURRENCY = re.compile(
    r"[€$£¥]|\b(?:eur|usd|chf|gbp|preis|price|sum|total|gesamt|netto|brutto"
    r"|mwst|vat)\b",
    re.IGNORECASE)

# "quantity: 28 x 5", "qty 28x5", "Menge: 28 x 5". The label is required: a
# bare "28 x 5" is also how a dimension or a pair of sizes is written.
#
# The colon sits inside one optional group on purpose. "\s*:?\s*" splits a run
# of spaces between two quantifiers, so every split is tried: 20,000 spaces
# after "qty" took 2.9 s. "\s*(?::\s*)?" has one owner for the run.
# The digit runs are bounded and start and end on a digit boundary: a bare
# "(\d+)" retries from every position inside a long run, and int() of a
# 5,000-digit match raises ValueError on Python 3.11+. A set size is a small
# number, so the cap is the quantity cap.
_SET_LINE = re.compile(
    r"\b(?:quantity|qty|menge)\s*(?::\s*)?(?<!\d)(\d{1,%d})\s*[x×]\s*"
    r"(\d{1,%d})(?!\d)" % (MAX_QUANTITY_DIGITS, MAX_QUANTITY_DIGITS),
    re.IGNORECASE)

# Ways a supplier writes that the item is a pack of M pieces: "5 pack",
# "5-pack", "5er-Pack", "5er Pack", "pack of 5", "set of 5", "5er".
#
# The digit run is bounded and starts on a boundary. A bare "(\d+)" retries from
# every position inside a long digit run: 20,000 digits took 6 s. A pack size is
# a small number, so four digits is the limit and "(?<!\d)" keeps the pattern out
# of the middle of a longer number.
_PACK_MARKERS = (
    re.compile(r"(?<!\d)(\d{1,4})\s*-\s*pack\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d{1,4})\s*pack\b", re.IGNORECASE),
    re.compile(r"\bpack\s+of\s+(\d{1,4})\b", re.IGNORECASE),
    re.compile(r"\bset\s+of\s+(\d{1,4})\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d{1,4})\s*er\b", re.IGNORECASE),
)

# The line breaks str.splitlines() splits on. A set line is placed on the line
# it starts on, so the offsets of the lines are needed, not only their text.
_LINE_BREAK = re.compile("\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


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
        # upper() does not return one character for every character: "ß"
        # becomes "SS", and the PDF ligatures "ﬁ" and "ﬂ" become "FI" and "FL".
        # One boundary slot per OUTPUT character keeps flat and boundary
        # aligned; one slot per source character shifted every later id.
        upper = char.upper()
        if not upper:
            continue
        flat.append(upper)
        boundary.extend([False] * len(upper))
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

    A wrong input type raises TypeError. Coercing it would turn a caller's bug
    into an empty, "clean" result: a bare list or a tuple of items would come
    back as {"items": []} with no finding, and bytes as a source would flag
    every id. The three arguments are checked, not guessed:

        ground(extraction: dict, source_text: str) -> (dict, list)

    extraction["items"] must be a list when the key is present. A missing
    "items" key is allowed and returns an empty list, because the data model
    tolerates missing keys.
    """
    if not isinstance(extraction, dict):
        raise TypeError("extraction must be a dict, not %s"
                        % type(extraction).__name__)
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a str, not %s"
                        % type(source_text).__name__)
    if "items" in extraction and not isinstance(extraction["items"], list):
        raise TypeError('extraction["items"] must be a list, not %s'
                        % type(extraction["items"]).__name__)

    checked = copy.deepcopy(extraction)
    items = checked.setdefault("items", [])
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
    The quantity is the one written in front of THAT link on the same line
    ("4x <link>"), or, for the first link on its line, the quantity on the line
    above when that line holds nothing but a quantity. Two links on one line
    therefore get one quantity each, and a link with no quantity of its own
    gets none: a single "4x" in front of two links belongs to the first one
    only. The marketplace id goes into other_ids: it is not a part number and
    not a description.
    """
    findings = []
    lines = source_text.splitlines()
    seen = set()
    for line_index, line in enumerate(lines):
        # The text between the previous link on this line and this one. The
        # quantity of a link is written in that segment, never in another
        # link's segment.
        previous_end = 0
        for match in _URL.finditer(line):
            segment = line[previous_end:match.start()]
            first_on_line = previous_end == 0
            previous_end = match.end()
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
                "quantity": _link_quantity(lines, line_index, segment,
                                           first_on_line),
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

    Three rules keep the correction honest. The pack size comes from the source
    text only: a pack word in the extractor's description is the extractor's
    own claim and proves nothing about the order. An item is corrected at most
    once, so a second set line cannot compound one correction into another
    (8 -> 4 -> 2). And a set line corrects only the item it belongs to.

    The owning item is decided per set line, not per block of set lines. A set
    line belongs to the items grounded on its own line; when its own line
    grounds none, to the items grounded on the line directly above; when that
    line grounds none either, to the items grounded on the line directly below.
    A stack of set lines whose own lines ground nothing shares one owner line
    (the line above the stack, else the line below it), because a supplier
    writes one position's set lines together. A pack marker that names the same
    pack size must sit on the set line or on the owner line. A pack marker on
    another product's line is another product's pack, so a missed correction is
    the safe answer.

    The lines are walked once, not once per set line. Each set line offers the
    items its owner line grounds, and the earliest set line that fits an item's
    quantity corrects it. A mail with a thousand positions stays linear, which
    matters on untrusted text.
    """
    findings = []
    corrected = set()
    bodies, starts = _source_lines(source_text)
    if not bodies:
        return findings
    matches = list(_SET_LINE.finditer(source_text))
    if not matches:
        return findings
    # The line of each set line. A set line may run over a line break, because
    # the spaces around the "x" may be a newline; it then belongs to the line it
    # starts on.
    line_of = [bisect_right(starts, match.start()) - 1 for match in matches]
    has_set = [False] * len(bodies)
    by_line = {}
    for position, line_index in enumerate(line_of):
        if 0 <= line_index < len(bodies):
            has_set[line_index] = True
            by_line.setdefault(line_index, []).append(matches[position])
    if not by_line:
        return findings
    facts = _line_facts(bodies)
    grounded = _grounded_by_line(items, facts)
    # A set line can only correct an item the extractor produced that states a
    # quantity, so a line that grounds no such item cannot own one. This keeps
    # an item this module appended (a placeholder built from a bare link) from
    # taking a set line away from the product line below it.
    correctable = [
        [index for index in line
         if origins[index] is not None and _quantity_of(items[index]) is not None]
        for line in grounded]
    owner_line = _set_owner_lines(by_line, correctable, has_set, len(bodies))
    for line_index in sorted(by_line):
        owner = owner_line.get(line_index)
        if owner is None:
            continue
        # The marker may sit on the set line or on the owner line. It may not
        # sit on a third line: that is another product's pack.
        packs = set(facts[owner]["packs"])
        packs.update(facts[line_index]["packs"])
        if not packs:
            continue
        for match in by_line[line_index]:
            outer = int(match.group(1))
            pack = int(match.group(2))
            # A "1 x 1" is not a set and "0" is not an order.
            if outer <= 0 or pack <= 1 or pack not in packs:
                continue
            product = outer * pack
            for item_index in grounded[owner]:
                if item_index in corrected:
                    continue
                item = items[item_index]
                if _quantity_of(item) != product:
                    continue
                old = item.get("quantity")
                old_unit = item.get("unit")
                item["quantity"] = outer
                item["unit"] = "set"
                item["uncertain"] = True
                corrected.add(item_index)
                findings.append(_finding(
                    "SET_MULTIPLIED", "corrected",
                    evidence="source says \"%s\": %d sets of %d, so the "
                             "quantity is %d sets and not %d pieces; "
                             "unit \"%s\" -> \"set\""
                             % (match.group(0), outer, pack, outer, product,
                                old_unit if isinstance(old_unit, str)
                                and old_unit.strip() else "piece"),
                    field="quantity",
                    old_value=old,
                    new_value=outer,
                    item_index=item_index,
                    input_index=origins[item_index]))
    return findings


def _check_variant_split(items, origins):
    """VARIANT_SPLIT: fold a variant line back into the item it belongs to.

    A supplier lists one part number twice, once per variant, under one
    quantity. The later line has no quantity of its own because it is not a
    second position. It is merged into the first item as a note and removed, so
    the quote does not count the part twice.

    "No quantity of its own" is a question about the field, not about the
    number. A line whose quantity field is non-empty is a second position even
    when _to_number() cannot read the value ("2 St.", 10 ** 400), so it is never
    merged: a merge must not delete an order line.

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
        if not _quantity_field_is_empty(item):
            # A line with a quantity of its own is a second position, not a
            # variant. That holds whether or not the parser can read the
            # number: never delete a line the parser cannot read.
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
    """Return [(gtin, line, previous_line)] for the marked GTINs, in order.

    Two rules decide what counts, and both exist because a digit run alone
    proves nothing:

    - The line must name the number with a GTIN keyword: GTIN, EAN, UPC,
      Barcode, Strichcode, with an optional length suffix or "Nr." label. The
      GTIN is the digit run that follows the keyword. "Tel. +49 89 12345670"
      and "Order 20261023" are valid check digits and are not GTINs, and
      neither is a number that sits elsewhere on a line that names a GTIN:
      "Bestellung 20261023, ... EAN 2000000000015" names one GTIN.
    - The run must not sit inside a link. A marketplace item number in a URL
      is a product position, not a GTIN.

    previous_line is the line above, and it is passed only when this line holds
    nothing but the keyword and the number: a supplier often prints the GTIN on
    a line of its own under the position it belongs to. A line that carries any
    other word ("GTIN ..., part of set") names no product either, but it is not
    a bare GTIN line, so nothing above it is borrowed.

    The same number written twice is returned once, compared as a GTIN-14.
    """
    found = []
    seen = set()
    lines = source_text.splitlines()
    for index, line in enumerate(lines):
        if not _GTIN_KEYWORD.search(line):
            continue
        spans = [match.span() for match in _URL.finditer(line)]
        for match in _GTIN_VALUE.finditer(line):
            run = match.group(1)
            if len(run) not in GTIN_LENGTHS or not gtin_valid(run):
                continue
            if any(start <= match.start(1) < end for start, end in spans):
                continue
            form = gtin14(run)
            if form in seen:
                continue
            seen.add(form)
            rest = line[:match.start()] + line[match.end():]
            bare = not any(character.isalnum() for character in rest)
            previous = lines[index - 1].strip() if index and bare else ""
            found.append((run, line.strip(), previous))
    return found


def _check_missing_gtins(items, origins, source_text):
    """MISSING_GTIN: attach a valid GTIN from the source that no item carries.

    The target item is the one the GTIN's line names. The part number on the
    line decides first, on a token boundary: a supplier line carries the part
    number of the product it prices, and two siblings can share every
    description word.

    When the line names no part number, the description decides, and only when
    exactly one item matches. Generic words do not count: "part" occurs in half
    the descriptions and in half the source lines, so an overlap on it alone
    must not move a GTIN to another product. A tie, or no match at all, is
    reported with item_index None and nothing is written.

    An item this module appended is never a target: its fields come from a bare
    link, so it states no product that a GTIN could belong to.
    """
    findings = []
    candidates = _gtin_candidates(source_text)
    if not candidates:
        return findings

    carried = _carried_gtins(items)
    for gtin, line, previous in candidates:
        form = gtin14(gtin)
        if form in carried:
            continue
        index, note = _gtin_owner(items, origins, line, previous)
        evidence = "%s (%s)" % (line, note) if line else \
            "GTIN %s occurs in the source text (%s)" % (gtin, note)
        if index is None:
            findings.append(_finding(
                "MISSING_GTIN", "reported",
                evidence=evidence,
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
            evidence=evidence,
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


def _gtin_owner(items, origins, line, previous=""):
    """Return (index, note) for the item a GTIN line names, or (None, note).

    The part number on the line decides, on a token boundary. It is the one
    field that separates two siblings whose descriptions read alike, and it is
    what the supplier prints next to the GTIN.

    A bare GTIN line (keyword and number, nothing else) names no product of its
    own: the supplier prints it under the position it belongs to. Such a line is
    read together with the line directly above it.

    When neither line names a part number, the description decides, but only
    when exactly one item reaches MATCH_RATIO. A tie, or no match at all, is
    (None, note): a reported GTIN is cheap, a GTIN on the wrong product is not.

    An item this module appended is never a candidate. It carries a link and no
    product, so it cannot own a GTIN.
    """
    match_line = previous or line
    flat, boundary = _source_id_index(match_line)
    by_part = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or origins[index] is None:
            continue
        form = _id_form(item.get("part_number"))
        if len(form) < MIN_ID_LEN:
            continue
        if _id_occurs(form, flat, boundary):
            by_part.append(index)
    if len(by_part) == 1:
        return by_part[0], "the line names that item's part number"
    if len(by_part) > 1:
        return None, ("the line names the part number of %d items, so the "
                      "product is ambiguous" % len(by_part))
    line_words = _words(match_line)
    by_words = [index for index, item in enumerate(items)
                if isinstance(item, dict) and origins[index] is not None
                and _overlap(_words(item.get("description")), line_words)
                >= MATCH_RATIO]
    if len(by_words) == 1:
        return by_words[0], "exactly one description matches the line"
    if len(by_words) > 1:
        return None, ("%d descriptions match the line, so the product is "
                      "ambiguous" % len(by_words))
    return None, "the line names no item of the extraction"


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

    A quantity arrives as 4, "4", 4.0, Decimal("4"), "2 pcs" or "1.000" (German
    for 1000) depending on the extractor. All of those must compare equal to
    the number in the source.

    A quantity counts whole pieces, so the number of digits behind a separator
    decides what the separator means. See _split_quantity_text.

    The answer is None, never an exception, for a value this module cannot
    trust: a number above MAX_QUANTITY, a digit run longer than
    MAX_QUANTITY_DIGITS, a non-finite float, or text that is not a number.
    float(10 ** 400) raises OverflowError and int() of a 5,000-digit run raises
    ValueError on Python 3.11+, so both are rejected before conversion.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if abs(value) <= MAX_QUANTITY else None
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > MAX_QUANTITY:
            return None
        return int(value) if value == int(value) else value
    if isinstance(value, Decimal):
        if not value.is_finite() or abs(value) > MAX_QUANTITY:
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, str):
        return _number_from_text(value)
    return None


def _number_from_text(text):
    """Return the number a quantity value holds, or None.

    "2 pcs" and "2 Stück" carry a quantity word after the number; the word is
    dropped. A currency sign is not a quantity word, so "12,50 €" is None.
    """
    text = text.strip()
    if not text:
        return None
    text = _QUANTITY_UNIT.sub("", text).strip()
    if not text or not _NUMBER_TEXT.fullmatch(text):
        return None
    if sum(1 for character in text if "0" <= character <= "9") \
            > MAX_QUANTITY_DIGITS:
        return None
    negative = text.startswith("-")
    text = text.lstrip("+-")
    whole, fraction = _split_quantity_text(text)
    if not _is_ascii_digits(whole or "0"):
        return None
    if fraction is not None and not _is_ascii_digits(fraction):
        return None
    number = int(whole or "0")
    if fraction:
        number = number + int(fraction) / (10 ** len(fraction))
    if number > MAX_QUANTITY:
        return None
    if negative:
        number = -number
    return int(number) if number == int(number) else number


def _split_quantity_text(text):
    """Split a number text into its whole part and its fraction, or None.

    A quantity counts whole pieces, so the digit count decides the meaning of
    a separator and no locale is needed:

    - three digits behind a separator group thousands: "1.000" and "1,000" are
      both 1000, and "1.000.000" is one million;
    - one, two, four or more digits behind a separator are a fraction: "1,5"
      is 1.5 and "12,50" is 12.5;
    - when both characters occur, the last one is the decimal separator, so
      "1.234,56" and "1,234.56" are both 1234.56.

    A repeated separator always groups: "1.000.000".
    """
    has_dot = "." in text
    has_comma = "," in text
    if has_dot and has_comma:
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        group = "," if decimal == "." else "."
        head, _separator, tail = text.rpartition(decimal)
        return head.replace(group, ""), tail
    if not has_dot and not has_comma:
        return text, None
    separator = "." if has_dot else ","
    parts = text.split(separator)
    if len(parts) > 2:
        return "".join(parts), None
    head, tail = parts
    if len(tail) == 3:
        return head + tail, None
    return head, tail


def _quantity_of(item):
    """Return the numeric quantity of an item, or None when it has none."""
    if not isinstance(item, dict):
        return None
    return _to_number(item.get("quantity"))


def _quantity_field_is_empty(item):
    """True when an item states no quantity at all.

    This is not the same question as _quantity_of(). A value the parser cannot
    read ("2 St.", 10 ** 400) is still a quantity the extractor wrote down, and
    a line that carries one is a second position. VARIANT_SPLIT must never
    delete such a line, so it asks this question instead of "is it a number".
    """
    if not isinstance(item, dict):
        return True
    value = item.get("quantity")
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


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


def _link_quantity(lines, line_index, segment, first_on_line):
    """Return the quantity that belongs to one link, or None.

    segment is the text on the link's own line between the previous link and
    this one, so two links on one line cannot share a quantity. A quantity
    written in front of the link ("4x <link>") belongs to it. A link with
    nothing in front of it on its line takes the quantity from the line above,
    but only the first link on a line: the second link of "4x <a> <b>" has no
    quantity of its own and must not inherit one. A blank line above is
    skipped: an empty line is layout, not information.
    """
    match = _QTY_BEFORE.search(_without_markdown_text(segment))
    if match:
        return _to_number(match.group(1))
    if not first_on_line:
        return None
    for back in range(line_index - 1, -1, -1):
        previous = lines[back].strip()
        if not previous:
            continue
        return _previous_line_quantity(previous)
    return None


def _without_markdown_text(segment):
    """Drop Markdown link text that sits between a quantity and its link.

    A Markdown link puts its label between the quantity and the address, so
    "4x [Hex bolt M8](<link>)" must still read as 4.
    """
    if not segment.rstrip().endswith("]("):
        return segment
    cut = segment.rfind("[")
    return segment[:cut] if cut != -1 else segment


def _previous_line_quantity(previous):
    """Return the quantity a line above a link holds, or None.

    The line must hold nothing but a quantity: a whole number ("2"), or a
    number with a quantity word ("4 x", "2 pcs", "1.000 Stück"). A line that
    carries money is never a quantity, and neither is a bare fraction: "12,50"
    on its own line above a link is a price, not a count of 12.5 pieces.
    """
    if _CURRENCY.search(previous):
        return None
    match = _QTY_LINE.match(previous)
    if not match:
        return None
    number = _to_number(match.group(1))
    if number is None:
        return None
    if match.group(2) is None and isinstance(number, float):
        return None
    return number


def _marked_pack_sizes(text):
    """Return the pack sizes that a pack marker in text names.

    "5 pack", "5-pack", "5er-Pack", "5er Pack", "pack of 5", "set of 5" and
    "5er" all say the same thing, so the sizes are collected once per text. A
    per-match scan over the whole text is quadratic: 1,000 set lines in one
    16 KB mail took 1.2 s. The set check calls this once per line, which keeps
    the work linear in the length of the mail.
    """
    sizes = set()
    if not isinstance(text, str) or not text:
        return sizes
    for pattern in _PACK_MARKERS:
        for match in pattern.finditer(text):
            sizes.add(int(match.group(1)))
    return sizes


# ── set-line helpers ───────────────────────────────────────────────────────

def _source_lines(text):
    """Return (bodies, starts) for the lines of text.

    bodies[i] is line i without its line break. starts[i] is the offset of that
    line in text, so a match found in the whole text can be placed on its line
    without a second scan. The breaks are the ones str.splitlines() uses.
    """
    bodies = []
    starts = []
    position = 0
    for match in _LINE_BREAK.finditer(text):
        bodies.append(text[position:match.start()])
        starts.append(position)
        position = match.end()
    if position < len(text):
        bodies.append(text[position:])
        starts.append(position)
    return bodies, starts


def _set_owner_lines(by_line, grounded, has_set, line_count):
    """Return {set-line index: owner-line index} for the set check.

    A set line belongs to the items grounded on its own line. When its own line
    grounds no item, it belongs to the line directly above it; when that line
    grounds none either, to the line directly below. A stack of set lines whose
    own lines ground nothing shares one owner line, taken from the line above
    the stack or, failing that, the line below it: a supplier writes one
    position's set lines together, so the stack is one position.

    A line that grounds no item at all is never an owner, so a set line next to
    nothing is dropped instead of reaching across the mail.
    """
    owner = {}
    for line_index in by_line:
        for candidate in (line_index, line_index - 1, line_index + 1):
            if 0 <= candidate < line_count and grounded[candidate]:
                owner[line_index] = candidate
                break
    for first, last in _set_blocks(has_set):
        block = [index for index in range(first, last + 1) if index in by_line]
        if not block:
            continue
        # A set line of the block that found an owner of its own already
        # belongs to that item. The others share the block's neighbour.
        shared = None
        for index in block:
            if index in owner:
                shared = owner[index]
                break
        if shared is None:
            for candidate in (first - 1, last + 1):
                if 0 <= candidate < line_count and grounded[candidate]:
                    shared = candidate
                    break
        if shared is None:
            continue
        for index in block:
            owner.setdefault(index, shared)
    return owner


def _set_blocks(has_set):
    """Return the (first, last) line indexes of each block of set lines.

    The blocks are returned in source order, so a block is resolved before the
    block below it.
    """
    blocks = []
    index = 0
    while index < len(has_set):
        if not has_set[index]:
            index += 1
            continue
        first = index
        while index + 1 < len(has_set) and has_set[index + 1]:
            index += 1
        blocks.append((first, index))
        index += 1
    return blocks


def _line_facts(bodies):
    """Return the per-line facts the set check needs, once per line.

    Each line carries its id form and token boundaries, the pack sizes its pack
    markers name, its content words, its links and its GTIN-14s. The check asks
    these questions for every line and every item, so they are computed once per
    line instead of once per question.
    """
    facts = []
    for body in bodies:
        flat, boundary = _source_id_index(body)
        gtins14 = set()
        for gtin in find_gtins(body):
            form = gtin14(gtin)
            if form is not None:
                gtins14.add(form)
        facts.append({
            "flat": flat,
            "boundary": boundary,
            "packs": _marked_pack_sizes(body),
            "words": _words(body),
            "links": find_links(body),
            "gtins14": gtins14,
        })
    return facts


def _line_id_forms(fact, max_length):
    """Return every id form the line holds on a token boundary.

    A match must start and end on a token boundary, so a matchable form is a
    run of the line's letters and digits, or several runs in a row. Only forms
    no longer than the longest id in the mail are built, which bounds the work
    on a line of many words.
    """
    flat = fact["flat"]
    stops = [position for position, flag in enumerate(fact["boundary"]) if flag]
    runs = [flat[stops[index]:stops[index + 1]]
            for index in range(len(stops) - 1)]
    forms = set()
    for first in range(len(runs)):
        text = runs[first]
        if len(text) > max_length:
            continue
        forms.add(text)
        for second in range(first + 1, len(runs)):
            text += runs[second]
            if len(text) > max_length:
                break
            forms.add(text)
    return forms


def _grounded_by_line(items, facts):
    """Return, per line, the sorted item indexes that line grounds.

    A line grounds an item when it holds the item's part number or GTIN on a
    token boundary, when it holds the item's link (or a link with the item's
    marketplace id), or when it shares MATCH_RATIO of the item's description
    words. The description rule is what carries an item whose part number the
    extractor did not read.

    The lookup is inverted on purpose. A supplier line names one product, so a
    line grounds a few items at most, while a mail holds many. The set check
    then walks the few items a line grounds instead of every item in the mail
    for every set line, which keeps a mail with a thousand positions linear
    enough to run on untrusted text.
    """
    ids = {}
    gtins = {}
    links = {}
    words_any = {}
    words_check = {}
    item_words = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        for value in (item.get("part_number"), item.get("gtin")):
            form = _gtin14_of_value(value)
            if form is not None:
                gtins.setdefault(form, set()).add(index)
            flat = _id_form(value)
            if len(flat) >= MIN_ID_LEN:
                ids.setdefault(flat, set()).add(index)
        link = item.get("link")
        if isinstance(link, str) and link:
            links.setdefault(link, set()).add(index)
        for link_id in _as_list(item.get("other_ids")):
            if not isinstance(link_id, str) or not link_id.strip():
                # other_ids is caller-supplied, so an entry may be any type. A
                # dict or a list is not an id and is unhashable: it would raise
                # TypeError out of ground(). Skip it, and skip a blank string.
                continue
            links.setdefault(("id", link_id), set()).add(index)
        item_words[index] = _words(item.get("description"))
        # A description of one or two content words matches on one shared word
        # alone: overlap is shared/min(...) and one shared word of two is
        # already MATCH_RATIO. Those items need no overlap sum, so the bucket
        # itself is the answer. A longer description is summed per candidate.
        bucket = words_any if len(item_words[index]) <= 2 else words_check
        for word in item_words[index]:
            bucket.setdefault(word, set()).add(index)
    max_length = max([len(form) for form in ids] or [0])
    grounded = []
    for fact in facts:
        found = set()
        for form in _line_id_forms(fact, max_length):
            found.update(ids.get(form, ()))
        for form in fact["gtins14"]:
            found.update(gtins.get(form, ()))
        for url in fact["links"]:
            found.update(links.get(url, ()))
            link_id = product_link_id(url)
            if link_id is not None:
                found.update(links.get(("id", link_id), ()))
        line_words = fact["words"]
        for word in line_words:
            found.update(words_any.get(word, ()))
        for word in line_words:
            for index in words_check.get(word, ()):
                if _overlap(item_words[index], line_words) >= MATCH_RATIO:
                    found.add(index)
        grounded.append(sorted(found))
    return grounded
