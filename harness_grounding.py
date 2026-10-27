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
            "gtin": "2000000000039",    # str, optional
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

Every value a finding quotes in its evidence is cut at 200 characters with an
ellipsis, so the cost of a finding never grows with the length of the line it
quotes. A MISSING_GTIN finding puts its GTIN before the quote, so the cut can
never drop the number it is about.

A quantity is read as a count of whole pieces: a separator with exactly three
digits behind it groups thousands ("1.000" and "1,000" are 1000) and one, two,
four or more digits are a fraction ("1,5" is 1.5). A repeated separator groups
only when the first group has 1 to 3 digits and every later group has exactly 3
("1.000.000"); anything else is not a number, so a date such as "26.10.2026" is
never a quantity. A value above 10**9 or a
digit run longer than 12 is not a quantity, so untrusted text cannot raise.
ground() raises TypeError when extraction is not a dict, when extraction["items"]
is not a list, when source_text is not a str, or when apply is not a bool.

ground() only SUGGESTS by default. Three checks would write a value into the
extraction — SET_MULTIPLIED, MISSING_GTIN and the quantity of an item appended
for an UNCOVERED_LINK — and each writes it only when the caller passes
apply=True. With the default apply=False the check records a "suggested"
finding instead: its new_value is the value apply=True would write, and every
item keeps exactly the value the caller passed in. Everything those checks do
that writes no extraction value still happens in both modes: the item is marked
uncertain, the placeholder item for an uncovered link is appended, and a GTIN
that no item can take is still reported. A producer that shows a suggestion to a
human, or that quotes the extraction before a human agrees, calls ground() with
no flag and reads the finding.

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
                   does, and what a VARIANT_SPLIT conflict does: the survivor
                   keeps its own value. For the list key other_ids it is the
                   entry added.
    item_index     index of the item in the RETURNED items list. None when no
                   item is concerned.
    input_index    index of that item in the list the caller passed in. None
                   for an item this module appended, because it has no place in
                   the caller's input.

A finding never carries a bare "value" key. It is ambiguous: it held the old
value for some codes and the new one for others. Read old_value and new_value.

item_index and input_index are the same number until a merge removes an item.
VARIANT_SPLIT removes one, so after a merge every later finding can carry two
different numbers, whatever its code. Read both; never assume they are equal.

An appended item never invents a part number or a description. A marketplace id
belongs in other_ids, so an item added for an uncovered link carries
other_ids ["ASIN:..."], the link and, with apply=True, the quantity and the unit.
Nothing else.

Codes and actions in this module:

    UNGROUNDED_ID / "flagged"    an item id does not occur in the source text
                                 on a token boundary. An id that occurs
                                 verbatim, whatever punctuation it holds, is
                                 grounded. field is the key, old_value the id.
                                 The item stays; uncertain is set True.
    MISSING_GTIN  / "attached"   a valid GTIN in the source was missing from the
                                 extraction. A digit run counts as a GTIN only
                                 when its line names one (GTIN, EAN, UPC,
                                 Barcode, Strichcode) and the run is not inside
                                 a link. It is attached to the best-matching
                                 item, which is marked uncertain. field is
                                 "gtin" or "other_ids", new_value the GTIN.
    MISSING_GTIN  / "suggested"  the same attachment with apply=False. field and
                                 new_value name the key and the value apply=True
                                 would write, item_index and input_index the item
                                 it would go to. The item is marked uncertain
                                 and keeps its own gtin and other_ids.
    MISSING_GTIN  / "reported"   same, but no item could take it. The finding
                                 has item_index None, both value slots None and
                                 nothing is changed; evidence names the GTIN.
                                 Every MISSING_GTIN evidence starts with
                                 "GTIN <number>: ", so the 200-character quote
                                 cut can never drop the number.
                                 This is the same in both modes.
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
                                 With apply=False the item carries no quantity
                                 and no unit; the quantity read for the link
                                 follows as its own "suggested" finding. With
                                 apply=True it carries the quantity and the
                                 unit the source names: "piece" for a piece
                                 word or no unit word, "set" for set/sets.
    UNCOVERED_LINK / "suggested" the quantity read for that link, with
                                 apply=False. field is "quantity", old_value
                                 None, new_value the quantity, item_index the
                                 appended item, input_index None. It follows the
                                 "added" finding of its own link directly, and
                                 its evidence names the unit word apply=True
                                 writes. Nothing is written.
    SET_MULTIPLIED / "corrected" the source says "N x M" and the item is an
                                 M-pack with quantity N*M, and the set line
                                 belongs to that item (the item is grounded on
                                 the set line or the line next to it, and the
                                 pack marker sits on that same line). The
                                 quantity becomes N, the unit "set", and the
                                 item is marked uncertain. field is "quantity".
    SET_MULTIPLIED / "suggested" the same correction with apply=False. field is
                                 "quantity", old_value the item's quantity,
                                 new_value the N apply=True would write; the
                                 quantity and the unit stay exactly as given.
    VARIANT_SPLIT / "merged"     a later item repeats the part number of an
                                 earlier one and has no quantity of its own. It
                                 is folded into the first item as a variant.
                                 One finding per field written onto the
                                 survivor: "note" (new_value the note added,
                                 with the merged line's own note appended),
                                 "link", "gtin" and "other_ids" (one finding
                                 per entry added). Every one of them has
                                 input_index of the merged line. A value both
                                 lines carry differently stays on the survivor
                                 and is reported as one more VARIANT_SPLIT
                                 finding for that field, with old_value the
                                 merged line's value and new_value None,
                                 because nothing was written. A unit or a price
                                 is never written and a difference is reported
                                 the same way.

A link is a product link only when product_link_id() returns an id for it. A
link to any other host is not a product and is never added as an item.

ground() returns (checked, findings). checked is a deep copy of the input, so
the caller keeps the original and the module never mutates what it was given.
Findings come in a stable order: VARIANT_SPLIT in item order, then
UNGROUNDED_ID in item order, then REDACTED_LINK in item order, then
UNCOVERED_LINK in source order, then SET_MULTIPLIED in source order, then
MISSING_GTIN in source order. The quantity suggestion of an appended item
follows its own "added" finding directly, so the pair for one link is adjacent.

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

# Longest id the set check uses for line matching. A part number longer than 64
# characters is not a part number, so it is not used for line matching: without
# the cap one 1,000-character part number made the scan build 1,000-character
# forms for every token on the line (352 MB and 1.3 s on a 6 KB mail). It is
# still grounded and still flagged by UNGROUNDED_ID, which does not build forms.
# The scan itself builds only a form whose length an indexed id has, and yields
# it instead of holding a set, so a long line of short tokens is cheap (N5').
# See SPEC-REFERENCE.md, "A set line corrects only its own item".
MAX_LINE_ID_LEN = 64

# A description word must be at least this long to count towards a match. Short
# words ("for", "the", "x") match everywhere and carry no product meaning.
MIN_WORD_LEN = 4

# Share of the shorter word set that two texts must have in common. Measured
# over the shorter set so a long source line cannot drown a short description.
MATCH_RATIO = 0.5

# The unit an item appended for an uncovered link states. Only two units are
# known: a piece word, or no unit word at all, is a count of pieces; "set" and
# "sets" are a count of sets. Any other word ("Paar", "VE", "Pack", "kg") is not
# a unit this module can name, so the link gets no quantity: a pair is not a
# piece, and a kilogram is not a count of pieces at all.
APPENDED_UNIT = "piece"
SET_UNIT = "set"
PIECE_UNIT_WORDS = frozenset((
    "x", "\u00d7", "pc", "pcs", "piece", "pieces", "st\u00fcck", "stueck",
    "stk", "st", "stck", "unit", "units"))
SET_UNIT_WORDS = frozenset(("set", "sets"))

# Longest quote a finding's evidence carries. A finding's evidence is one short
# quotable sentence (SPEC-REFERENCE.md), and a hostile mail can put 100 KB on one
# line: the cost of a finding may not grow with the length of that line.
QUOTE_LIMIT = 200

# Words that name no product. A GTIN line carries them ("GTIN ...", "part ...")
# and a description may carry them too, so an overlap on one of them alone
# proves nothing and must not move a GTIN to another product.
#
# The pack words are here for the same reason on a set line: "2er-Pack" names a
# packaging size, not a product, so "Pack" alone must not let one product's line
# own another product's item.
GENERIC_WORDS = frozenset((
    "part", "parts", "item", "items", "number", "nummer", "artikel",
    "gtin", "ean", "upc", "barcode", "strichcode",
    "quantity", "menge", "qty", "piece", "pieces", "stueck", "stück",
    "order", "bestellung", "supply", "confirmation", "total", "price",
    "preis", "product", "produkt", "please", "siehe",
    "pack", "packs", "packung", "packungen", "set", "sets", "satz",
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
#
# Four more characters are separators for the same reason: a PDF writes the
# minus sign U+2212 or the fullwidth hyphen U+FF0D where the id has a hyphen,
# and a mail client leaves the invisible word joiner U+2060 or a byte-order
# mark U+FEFF inside an id. A character that is invisible cannot end a token.
_ID_JOINERS = ("-./_\u2010\u2011\u2012\u2013\u2014\u2015\u00ad\u200b"
               "\u2212\uff0d\u2060\ufeff")
_ID_JOINER_SET = frozenset(_ID_JOINERS)

# Whitespace plus every joiner above. re.escape keeps "-" literal inside the
# class; "\s" is kept so unicode spaces a table exports are separators too.
_SEPARATORS = re.compile("[" + re.escape(_ID_JOINERS) + r"\s]+")

# A standalone digit run: no digit directly before or after it. The negative
# lookarounds are what stop "920000000000391" (15 digits) from matching as a
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
# An eBay item number has 9 to 13 digits, an AliExpress item id 9 to 17. The
# run is bounded on both sides of the count: a shorter or longer run is not a
# product link. Without the bound every distinct id length became a form length
# in the covered-link index, and one mail of 98 KB cost gigabytes (B1').
_EBAY_ITEM = re.compile(r"/itm/(\d{9,13})(?!\d)")
_ALIEXPRESS_ITEM = re.compile(r"/item/(\d{9,17})(?!\d)")

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
#
# The words are the spellings real mail carries: "GTIN", "EAN", "UPC",
# "Barcode", "Bar-Code", "Strichcode", "Strichcodenummer", and the plurals
# "EANs" and "Barcodes". The length suffix may follow with no separator
# ("EAN13"), so the suffix is part of the value pattern below, not of the word.
# "bar[\s-]*code" is one character class, not two "\s*" in a row: two would let
# every split of a long run of spaces be tried, which is quadratic.
_GTIN_KEYWORD_WORD = r"gtin|ean|upc|bar[\s-]*code|strichcode(?:nummer)?"
_GTIN_KEYWORD = re.compile(
    r"\b(?:%s)s?" % _GTIN_KEYWORD_WORD, re.IGNORECASE)

# A GTIN named by a keyword: the keyword, then at most a length suffix
# ("EAN-13", "EAN13", "EAN 13") or a "Nr."/"No." label, then the digit run.
# Only that run is the GTIN. A line may hold other numbers before or after it,
# and an order number or a telephone number on the same line is not a GTIN.
#
# The suffix must end at a separator, so it never takes digits from the run:
# "EAN13 2000000000015" reads the whole run. The run itself must not sit
# inside a longer digit run.
#
# The colon sits inside one optional group, so a run of spaces has one owner:
# "\s*:?\s*" splits the run between two quantifiers and every split is tried,
# which is quadratic (20,000 spaces after "ean" took 2.8 s). "\s*(?::\s*)?"
# keeps the run with the first quantifier.
#
# The "Nr."/"No." label puts its boundary BEFORE the optional full stop:
# "(?:nr|no)\b\.?". A full stop is not a word character, so a boundary after
# the stop sits between two non-word characters and never matches, and
# "EAN Nr. 2000000000015" was read as no GTIN at all (S8').
_GTIN_VALUE = re.compile(
    r"\b(?:%s)s?"
    r"(?:\s*[-\u2013]\s*(?:8|12|13|14|nr\.?|no\.?)(?=[\s:.]|$)"
    r"|\s*(?:8|12|13|14)(?=[\s:.]|$)"
    r"|\s+(?:nr|no)\b\.?)?"
    r"\s*(?::\s*)?"
    r"(\d{8,14})(?!\d)" % _GTIN_KEYWORD_WORD,
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
# Only the words that name a unit this module knows are here (see
# PIECE_UNIT_WORDS and SET_UNIT_WORDS). "Paar", "VE", "Pack" and "kg" are not
# units of a count of pieces, so a quantity they follow is not read at all.
_QUANTITY_UNIT_WORDS = (
    r"x|×|pcs?\.?|pieces?|stck\.?|stueck|stück|stk\.?|st\.?|units?|sets?")
_QUANTITY_UNIT = re.compile(
    r"\s*(?:%s)\s*$" % _QUANTITY_UNIT_WORDS, re.IGNORECASE)

# A value that is a number with optional separators, and nothing else. A price
# with a currency sign ("12,50 €") is not a quantity and does not match.
_NUMBER_TEXT = re.compile(r"[+-]?[\d.,]+")

# A number with optional group separators: "4", "1.000", "1.000.000",
# "12,50". The left boundary stops a match inside a longer number: without it
# "1.000.000 St\u00fcck" matches its last group "000" and reads as 0. The digit runs
# are bounded so a long run cannot make the pattern backtrack, and so no
# unbounded int() is reachable.
_QUANTITY_NUMBER = (r"(?<![\d.,])\d{1,%d}(?:[.,]\d{1,%d})*"
                    % (MAX_QUANTITY_DIGITS, MAX_QUANTITY_DIGITS))

# A quantity that stands in front of a link on the same line: "4x <link>",
# "1.000 St\u00fcck <link>". Group 2 is the unit word, so the appended item can state
# the source's own unit instead of a fixed one.
_QTY_BEFORE = re.compile(
    r"(%s)\s*"
    r"(%s)\s*$"
    % (_QUANTITY_NUMBER, _QUANTITY_UNIT_WORDS),
    re.IGNORECASE)

# A line that holds nothing but a quantity: "4", "4 x", "4 pcs", "1.000 St\u00fcck".
# Group 2 is the unit word; it is None for a bare number.
_QTY_LINE = re.compile(
    r"^\s*(%s)"
    r"(?:\s*(%s))?\s*$"
    % (_QUANTITY_NUMBER, _QUANTITY_UNIT_WORDS),
    re.IGNORECASE)

# A line that carries money carries no quantity. A price sits on the same line
# above a link that a quantity would sit on, so it needs its own rule.
_CURRENCY = re.compile(
    r"[€$£¥]|\b(?:eur|usd|chf|gbp|preis|price|sum|total|gesamt|netto|brutto"
    r"|mwst|vat)\b",
    re.IGNORECASE)

# "quantity: 28 x 5", "qty 28x5", "Menge: 28 x 5", "Bestellmenge: 28 x 5",
# "Liefermenge: 28 x 5", "Anzahl: 28 x 5", "Qty. 28 x 5", "Menge (VE): 28 x 5".
# The label is required: a bare "28 x 5" is also how a dimension or a pair of
# sizes is written. The longer spellings come first, so "Bestellmenge" is not
# read as "Menge" with a word in front of it.
#
# The separator sits inside one optional group on purpose. "\s*:?\s*" splits a
# run of spaces between two quantifiers, so every split is tried: 20,000 spaces
# after "qty" took 2.9 s. "\s*(?::\s*)?" has one owner for the run. The full
# stop of "Qty." and the "(VE)" unit sit in the same group, and each of them
# must match a character that is not a space, so a run of spaces still has one
# owner.
# The digit runs are bounded and start and end on a digit boundary: a bare
# "(\d+)" retries from every position inside a long run, and int() of a
# 5,000-digit match raises ValueError on Python 3.11+. A set size is a small
# number, so the cap is the quantity cap.
_SET_LINE = re.compile(
    r"\b(?:bestellmenge|liefermenge|anzahl|quantity|qty|menge)\b"
    r"\s*(?:[.:]\s*)?(?:\(\s*ve\s*\)\s*(?:[.:]\s*)?)?"
    r"(?<!\d)(\d{1,%d})\s*[x×]\s*"
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

        gtin_valid("2000000000039")  # True
        gtin_valid("2000000000038")  # False, last digit changed
        gtin_valid("20000011")       # True, GTIN-8

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

    A number is remembered in a set, so the repeat question costs the same
    however many numbers the text holds: 40,000 distinct numbers took 5.7 s
    when the numbers already found were a list.
    """
    if not isinstance(text, str) or not text:
        return []
    found = []
    seen = set()
    for match in _DIGIT_RUN.finditer(text):
        run = match.group(0)
        if (len(run) in GTIN_LENGTHS and run not in seen
                and gtin_valid(run)):
            seen.add(run)
            found.append(run)
    return found


def gtin14(code):
    """Return code as a GTIN-14, or None when it is not a valid GTIN.

    GS1 has one number space, not four: a GTIN-8, -12 and -13 are the GTIN-14
    with leading zeros. Comparing the padded form is what makes "200000000011"
    and "00200000000011" the same GTIN.

        gtin14("200000000011")   # "00200000000011"
        gtin14("20000011")       # "00000020000011"
        gtin14("2000000000038")  # None, bad check digit
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

    An extractor writes the same GTIN as "200000000011", "EAN:200000000011" or
    "00200000000011". All three are the same number; None means the value is not
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
    """Return (flat, boundary, hard, glue) for a source text.

    flat is the text upper-cased with every character that is not a letter or a
    digit removed. boundary[i] is True when flat position i is a token boundary
    in the original text: the start, the end, or a position next to a character
    that is not part of an id.

    The boundaries are the point. A source that holds "AC-4417" also holds the
    substring "AC-441", but not the token: the character after it is a digit, so
    that occurrence is inside a token and grounds nothing.

    hard lists, in order, the flat positions where punctuation that is NOT a
    documented joiner sits: ",", "|", "(", ")", ";", ":". No id may run through
    one of them, so "Pos. 3: AC-4417, 8 Stück" does not ground "AC-44178".

    glue[i] is True when flat position i is a digit that a joiner ties to the
    digit before it. The two are one number: the "-" in "2026-10-26" glues "10"
    to "26", so "1026" is the tail of that number and not an id. A joiner after
    a letter ties nothing ("AC-4417"), and whitespace ties nothing either, so
    "Pos. 1 4417" still holds the id "4417".
    """
    flat = []
    boundary = [True]
    hard = []
    glue = []
    width = 0            # the number of flat characters, not of list entries
    previous = None      # the last letter or digit read, upper-cased
    joined = False       # a joiner stands between `previous` and the next char
    for char in text:
        if not char.isalnum():
            # A joiner or any other separator ends the token before it and
            # starts the token after it: one boundary at this flat position.
            boundary[-1] = True
            if char in _ID_JOINER_SET:
                if previous is not None:
                    joined = True
            else:
                if not char.isspace():
                    # Punctuation that is not a joiner ends the token for good.
                    if not hard or hard[-1] != width:
                        hard.append(width)
                previous = None
                joined = False
            continue
        # upper() does not return one character for every character: "ß"
        # becomes "SS", and the PDF ligatures "ﬁ" and "ﬂ" become "FI" and "FL".
        # One boundary slot per OUTPUT character keeps flat and boundary
        # aligned; one slot per source character shifted every later id.
        upper = char.upper()
        if not upper:
            continue
        tied = (joined and previous is not None and previous.isdigit()
                and upper[0].isdigit())
        flat.append(upper)
        boundary.extend([False] * len(upper))
        glue.extend([tied] * len(upper))
        width += len(upper)
        previous = upper[-1]
        joined = False
    boundary[-1] = True
    return "".join(flat), boundary, hard, glue


def _id_occurs(flat_value, flat_source, boundary, hard=(), glue=()):
    """True when flat_value occurs in flat_source as a whole id.

    The match must start and end on a token boundary, must not run through
    punctuation that is not a documented joiner, and must not start or end
    inside a number that a joiner ties together.
    """
    if not flat_value:
        return False
    size = len(flat_value)
    start = flat_source.find(flat_value)
    while start != -1:
        end = start + size
        if boundary[start] and boundary[end] and _id_span_is_clean(
                start, end, hard, glue):
            return True
        start = flat_source.find(flat_value, start + 1)
    return False


def _id_span_is_clean(start, end, hard, glue):
    """True when the flat span [start, end) holds no hard break and no glue."""
    if glue and (glue[start] or (end < len(glue) and glue[end])):
        return False
    if hard:
        index = bisect_right(hard, start)
        if index < len(hard) and hard[index] < end:
            return False
    return True


def _is_id_char(char):
    """True when char belongs to an id: a letter, a digit or a joiner."""
    return bool(char) and (char.isalnum() or char in _ID_JOINER_SET)


def _id_occurs_verbatim(value, folded_source):
    """True when value's own text occurs in folded_source on a token boundary.

    This is the second way an id is grounded, and it is what a German cable or
    fastener designation needs. "NYM-J 3x1,5", "H07V-K 1x2,5", "SPAX 4,5x40",
    "M8\u00d740" and "AC-4417(B)" hold characters that are not joiners, so the
    flat form of the value runs through them and the token rule alone flagged an
    id that the source prints character for character.

    The rule is the token rule, read on the text as it is written: the id must
    occur case-insensitively, and neither end may sit next to a letter, a digit
    or a joiner. So "NYM-J 3x1,5" is not grounded by "NYM-J 3x1,55" (the
    character after the match is a digit), and the round-2 rule still holds:
    "AC-44178" is not grounded by "Pos. 3: AC-4417, 8 St\u00fcck", because that
    text is not there at all.

    folded_source is the source text casefolded once by the caller, so the cost
    is one str.find per occurrence, linear in the length of the source.
    """
    if not isinstance(value, str):
        value = str(value) if value is not None else ""
    needle = value.casefold()
    if not needle:
        return False
    size = len(needle)
    start = folded_source.find(needle)
    while start != -1:
        end = start + size
        before = folded_source[start - 1] if start else ""
        after = folded_source[end] if end < len(folded_source) else ""
        if not _is_id_char(before) and not _is_id_char(after):
            return True
        start = folded_source.find(needle, start + 1)
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

    An id has the length its marketplace uses: 9 to 13 digits for an eBay item
    number and 9 to 17 for an AliExpress item id. A shorter or longer digit run
    is not a product link and gives None.
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


def ground(extraction, source_text, *, apply=False):
    """Check extraction against source_text. Return (checked, findings).

    checked is a deep copy of extraction with flags and attachments applied.
    The input dict is never modified. findings is a list of finding dicts in a
    stable order; it is empty when the extraction is fully grounded.

    apply selects the mode. With the default apply=False the three checks that
    would write an extraction value — SET_MULTIPLIED, MISSING_GTIN and the
    quantity of an item appended for an UNCOVERED_LINK — record a "suggested"
    finding instead, and every item keeps the value the caller passed in. With
    apply=True they write the value, as "corrected", "attached" and "added".
    A "suggested" finding's new_value is the value apply=True would write.

    A wrong input type raises TypeError. Coercing it would turn a caller's bug
    into an empty, "clean" result: a bare list or a tuple of items would come
    back as {"items": []} with no finding, and bytes as a source would flag
    every id. The arguments are checked, not guessed:

        ground(extraction: dict, source_text: str, *, apply: bool = False)
              -> (dict, list)

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
    # A flag that is not a bool is a caller bug, not a mode. "false" and 0 would
    # both read as a truthy string and an int, and the caller would not see why
    # the module wrote a value it was told to suggest.
    if not isinstance(apply, bool):
        raise TypeError("apply must be a bool, not %s"
                        % type(apply).__name__)

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
    findings.extend(_check_uncovered_links(items, origins, source_text, apply))
    findings.extend(_check_set_multiplied(items, origins, source_text, apply))
    findings.extend(_check_missing_gtins(items, origins, source_text, apply))
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

    Only a documented joiner may sit inside a grounded id. Any other punctuation
    ends the token, so "AC-44178" is not grounded by "Pos. 3: AC-4417, 8 Stück".
    A joiner between two digits ties them into one number, so "1026" is not
    grounded by "2026-10-26".

    An id that occurs verbatim is grounded too, whatever punctuation it holds:
    "NYM-J 3x1,5", "SPAX 4,5x40" and "AC-4417(B)" are what a German supplier
    prints, and the token rule alone flagged them. See _id_occurs_verbatim.
    """
    findings = []
    flat_source, boundary, hard, glue = _source_id_index(source_text)
    folded_source = source_text.casefold()
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
            if len(flat) < MIN_ID_LEN or _id_occurs(
                    flat, flat_source, boundary, hard, glue):
                continue
            if _id_occurs_verbatim(raw, folded_source):
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

    The comparison is made without the query string and the fragment, on both
    sides. A source link is stored cleaned, and an extractor keeps the
    "?spm=..." that almost every AliExpress link carries, so a cut link that
    still holds its query string never ended with the stored suffix. The cut is
    looked for in the cleaned link: a "[" in a query string is not a cut in a
    product path.

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
        bare = _without_query(link)
        cut = bare.find("[")
        if cut < MIN_REDACTED_AT:
            continue
        prefix = bare[:cut]
        close = bare.find("]", cut)
        suffix = bare[close + 1:] if close != -1 else ""
        suffix = suffix.rstrip(_TRAILING_PUNCTUATION)
        matches = [candidate for candidate in links
                   if candidate.startswith(prefix) and candidate.endswith(suffix)]

        item["uncertain"] = True
        if len(matches) != 1:
            findings.append(_finding(
                "REDACTED_LINK", "flagged",
                evidence="item link %s was cut at character %d by a "
                         "redaction placeholder and %s"
                         % (_quote(link), cut,
                            _match_count_note(len(matches))),
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
                     % (_quote(link), cut, _quote(candidate)),
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


def _quote(text, limit=QUOTE_LIMIT):
    """Return a short quote of text for a finding's evidence.

    SPEC-REFERENCE.md says a finding's evidence is one short quotable sentence.
    A hostile mail can put 100 KB on a single line, so the quote is cut: the
    cost of a finding may not grow with the length of the line it quotes.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "\u2026"


def _check_uncovered_links(items, origins, source_text, apply):
    """UNCOVERED_LINK: add an item for a product link that no item covers.

    A product link in the source is a position the extractor missed. The item
    is appended, never inserted, so the extractor's own order stays readable.
    The quantity is the one written in front of THAT link on the same line
    ("4x <link>"), or, for the first link on its line, the quantity on the line
    directly above when that line holds nothing but a quantity and no link above
    owns it, or the quantity on the line directly below when the link is alone
    on its line. Two links on one line therefore get one quantity each, and a
    link with no quantity of its own gets none: a single "4x" in front of two
    links belongs to the first one only. The marketplace id goes into
    other_ids: it is not a part number and not a description.

    The quantity is written only with apply=True. The appended item itself is
    written in both modes, because it holds no extraction value the caller gave
    us: it is the record of a link the extraction missed. With apply=False it
    carries the link, other_ids and uncertain, and the quantity follows as its
    own "suggested" finding directly after the "added" one.

    The links the items already cover are indexed once, in a set of id forms and
    a set of cleaned urls. Asking "does some item cover this link?" is then a
    lookup instead of a scan of every item, and an item this check appended
    extends the index. A mail with one uncovered link per line therefore stays
    linear: the quadratic version rescanned every appended item for every link,
    which took 12 s on 100 KB of lines.
    """
    findings = []
    lines = source_text.splitlines()
    links = _source_links(lines)
    if not links:
        return findings
    form_lengths = set()
    for link in links:
        form_lengths.update(len(form) for form in _link_id_forms(link["link_id"]))
    covered_urls, covered_forms = _link_coverage(items, form_lengths)
    quotes = {}
    for link in links:
        url = link["url"]
        forms = _link_id_forms(link["link_id"])
        if url.lower() in covered_urls:
            continue
        if any(form in covered_forms for form in forms):
            continue
        if link["line_index"] not in quotes:
            quotes[link["line_index"]] = _quote(link["line"])
        found = _link_quantity(lines, link["line_index"], link["segment"],
                               link["first_on_line"], link["only_on_line"])
        quantity, unit = found if found is not None else (None, APPENDED_UNIT)
        appended = {
            "other_ids": [link["link_id"]],
            "link": url,
            "uncertain": True,
        }
        if apply:
            appended["quantity"] = quantity
            appended["unit"] = unit
        items.append(appended)
        origins.append(None)
        covered_urls.add(url.lower())
        _add_flat_forms(covered_forms, link["link_id"], form_lengths)
        findings.append(_finding(
            "UNCOVERED_LINK", "added",
            evidence=quotes[link["line_index"]] or url,
            field="link",
            new_value=url,
            item_index=len(items) - 1,
            input_index=None))
        if apply or quantity is None:
            continue
        findings.append(_finding(
            "UNCOVERED_LINK", "suggested",
            evidence="the source reads a quantity of %s for this link, with "
                     "the unit word \"%s\"" % (quantity, unit),
            field="quantity",
            old_value=None,
            new_value=quantity,
            item_index=len(items) - 1,
            input_index=None))
    return findings


def _source_links(lines):
    """Return the product links of the source, one per url, in source order.

    Each entry holds the line index, the text between the previous link on that
    line and this one, whether this is the first link on its line, whether it is
    the only one, the cleaned url and the marketplace id. The scan is one pass
    over the lines, so the caller can build an index from the result before it
    decides anything.
    """
    found = []
    seen = set()
    for line_index, line in enumerate(lines):
        previous_end = 0
        for match in _URL.finditer(line):
            segment = line[previous_end:match.start()]
            first_on_line = previous_end == 0
            previous_end = match.end()
            only_on_line = first_on_line and _URL.search(line, match.end()) is None
            url = _clean_link(match.group(0))
            if not url or url in seen:
                continue
            link_id = product_link_id(url)
            if link_id is None:
                continue
            seen.add(url)
            found.append({
                "line_index": line_index,
                "line": line,
                "segment": segment,
                "first_on_line": first_on_line,
                "only_on_line": only_on_line,
                "url": url,
                "link_id": link_id,
            })
    return found


def _link_coverage(items, form_lengths):
    """Return (urls, forms): the links the extractor's own items stand for.

    urls holds the cleaned link of every item. forms holds every substring of an
    item's id values that is as long as a link id in the source, so "does this
    item hold that id?" is a set lookup. The index is built once per call, which
    is what keeps a mail with one link per line linear.
    """
    urls = set()
    forms = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        link = item.get("link")
        if isinstance(link, str):
            cleaned = _clean_link(link)
            if cleaned:
                urls.add(cleaned.lower())
        values = [item.get("part_number"), link]
        values.extend(_as_list(item.get("other_ids")))
        for value in values:
            _add_flat_forms(forms, value, form_lengths)
    return urls, forms


def _add_flat_forms(forms, value, form_lengths):
    """Add every wanted-length substring of value's id form to forms."""
    flat = normalize_id(value)
    if not flat:
        return
    for length in form_lengths:
        for start in range(len(flat) - length + 1):
            forms.add(flat[start:start + length])


def _check_set_multiplied(items, origins, source_text, apply):
    """SET_MULTIPLIED: an M-pack ordered N times is N sets, not N*M pieces.

    The source writes "quantity: 28 x 5" for 28 sets of 5. An extractor that
    reports 140 pieces has lost the set, and the quote then prices 140 single
    items. The quantity is set back to the number of sets.

    The write happens only with apply=True. With apply=False the finding is
    "suggested": old_value is the quantity the item holds, new_value the number
    of sets apply=True would write, and the item keeps its own quantity and
    unit. The item is marked uncertain either way, because the source does not
    support the value it holds.

    Three rules keep the correction honest. The pack size comes from the source
    text only: a pack word in the extractor's description is the extractor's
    own claim and proves nothing about the order. An item is corrected at most
    once, so a second set line cannot compound one correction into another
    (8 -> 4 -> 2). And a set line corrects only the item it belongs to.

    The owning item is decided per set line, not per block of set lines. A set
    line belongs to the items grounded on its own line; when its own line
    grounds none, to the items grounded on the line directly above. A stack of
    set lines whose own lines ground nothing shares one owner line (the line
    above the stack, else the line below it), because a supplier writes one
    position's set lines together. The line below a stack is read once for the
    whole stack, and only when no line above it grounds an item: a set line of
    a stack never reaches down to the position below on its own, which would
    split one stack between two positions. A pack marker that names the same
    pack size must sit on the set line or on the owner line. A pack marker on
    another product's line is another product's pack, so a missed correction is
    the safe answer.

    An item on the set line keeps the line even when its quantity does not
    parse. Such an item has nothing this check can correct, and it is the
    item the set line belongs to, so no other item takes the line.

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
    # A line that grounds an item whose quantity field is non-empty but does
    # not parse is that item's own line. The set line belongs to it, and no
    # other item may take the line: the owner's quantity cannot be read, so the
    # set line has nothing to correct.
    unreadable = [
        [index for index in line
         if origins[index] is not None
         and not _quantity_field_is_empty(items[index])
         and _quantity_of(items[index]) is None]
        for line in grounded]
    owner_line = _set_owner_lines(by_line, correctable, unreadable, has_set,
                                 len(bodies))
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
                item["uncertain"] = True
                corrected.add(item_index)
                if apply:
                    item["quantity"] = outer
                    item["unit"] = "set"
                findings.append(_finding(
                    "SET_MULTIPLIED", "corrected" if apply else "suggested",
                    evidence="source says \"%s\": %d sets of %d, so the "
                             "quantity %s %d sets and not %d pieces; "
                             "unit \"%s\" -> \"set\""
                             % (_quote(match.group(0)), outer, pack,
                                "is" if apply else "would be",
                                outer, product,
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
    value is named in the finding, with uncertain set. Nothing is written for a
    conflict, so its new_value is None and the evidence names the value the
    survivor kept. other_ids is a list, so both values are kept and there is no
    conflict.

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
        variant_note = variant.get("note")
        if isinstance(variant_note, str) and variant_note.strip():
            # The variant's own note is data the extractor found. A merge that
            # drops it loses a fact the source states.
            piece = "%s (%s)" % (piece, variant_note.strip())
        note = first.get("note")
        first["note"] = ("%s · %s" % (note, piece)) if note else piece
        first["uncertain"] = True
        writes, conflicts = _merge_variant_values(first, variant)
        merges.append((first, writes, conflicts, piece, input_of[index]))

    removed = set(index for index, _first_index in pairs)
    kept = [index for index in range(len(items)) if index not in removed]
    position_of = {}
    for position, index in enumerate(kept):
        position_of.setdefault(id(items[index]), position)
    items[:] = [items[index] for index in kept]
    origins[:] = [input_of[index] for index in kept]

    findings = []
    for first, writes, conflicts, piece, variant_input in merges:
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
        for key, old, new in writes:
            findings.append(_finding(
                "VARIANT_SPLIT", "merged",
                evidence="the merged line carries %s %s and the surviving item "
                         "held none, so it is written onto item %d"
                         % (key, new, position),
                field=key,
                old_value=old,
                new_value=new,
                item_index=position,
                input_index=variant_input))
        for key, theirs, ours in conflicts:
            findings.append(_finding(
                "VARIANT_SPLIT", "merged",
                evidence="the merged line carries %s %s and the surviving item "
                         "carries %s; the survivor keeps its own value and the "
                         "merged one is reported here"
                         % (key, theirs,
                            ours if ours is not None else "nothing"),
                field=key,
                old_value=theirs,
                new_value=None,
                item_index=position,
                input_index=variant_input))
    findings.sort(key=lambda finding: (finding["item_index"],
                                       finding["input_index"]))
    return findings


def _merge_variant_values(survivor, variant):
    """Move what the variant holds and the survivor lacks.

    Return (writes, conflicts). A write is (field, old_value, new_value): the
    survivor did not hold the value, so it was written onto it. A conflict is
    (field, variant_value, survivor_value): both lines carry a different value,
    the survivor keeps its own and the variant's is reported. Nothing is written
    for a conflict.

    A unit or a price is never written, because a merge cannot prove which one
    the order line means. When the two lines differ, the difference is reported
    as a conflict, so the value is not lost.
    """
    writes = []
    conflicts = []
    for key in ("link", "gtin"):
        theirs = variant.get(key)
        if not isinstance(theirs, str) or not theirs.strip():
            continue
        ours = survivor.get(key)
        if not isinstance(ours, str) or not ours.strip():
            survivor[key] = theirs
            writes.append((key, None, theirs))
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
            for value in added:
                writes.append(("other_ids", None, value))
    for key in ("unit", "price"):
        theirs = variant.get(key)
        if not _has_text(theirs):
            continue
        ours = survivor.get(key)
        if _same_text(theirs, ours):
            continue
        conflicts.append((key, theirs, ours))
    return writes, conflicts


def _has_text(value):
    """True when value is a value a finding can name: not None and not blank."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _same_text(first, second):
    """True when two field values say the same thing, ignoring case and space.

    A number and the same number written as a string compare equal. Two
    different spellings of one unit ("Stück" and "piece") do not, and that is
    deliberate: the module does not own a unit dictionary, so it reports the
    difference instead of guessing that the two are the same.
    """
    if not _has_text(second):
        return False
    return str(first).strip().casefold() == str(second).strip().casefold()


def _gtin_candidates(source_text):
    """Return [(gtin, line_index, bare)] for the marked GTINs, in order.

    Two rules decide what counts, and both exist because a digit run alone
    proves nothing:

    - The line must name the number with a GTIN keyword: GTIN, EAN, UPC,
      Barcode, Strichcode, with an optional length suffix or "Nr."/"No." label.
      The label follows with a hyphen, a space or no separator, and it may end
      in a full stop or a colon: "EAN-Nr.:", "EAN Nr.", "EAN Nr.:", "GTIN No.".
      The GTIN is the digit run that follows the keyword. "Tel. +49 89 12345670"
      and "Order 20261023" are valid check digits and are not GTINs, and
      neither is a number that sits elsewhere on a line that names a GTIN:
      "Bestellung 20261023, ... EAN 2000000000015" names one GTIN.
    - The run must not sit inside a link. A marketplace item number in a URL
      is a product position, not a GTIN.

    bare is True when the line holds nothing but the keyword and the number: a
    supplier often prints the GTIN on a line of its own under the position it
    belongs to. A line that carries any other word ("GTIN ..., part of set")
    names no product either, but it is not a bare GTIN line, so nothing above
    it is borrowed.

    The same number written twice is returned once, compared as a GTIN-14.

    Each line is scanned once. The url spans are walked with one pointer and the
    bare question is answered from the line's first and last alphanumeric
    position, so a line of thousands of GTINs costs its own length and not its
    length times its number count.
    """
    found = []
    seen = set()
    for index, line in enumerate(source_text.splitlines()):
        if not _GTIN_KEYWORD.search(line):
            continue
        spans = [match.span() for match in _URL.finditer(line)]
        span_at = 0
        first_alnum, last_alnum = _alnum_bounds(line)
        for match in _GTIN_VALUE.finditer(line):
            run = match.group(1)
            if len(run) not in GTIN_LENGTHS or not gtin_valid(run):
                continue
            start = match.start(1)
            while span_at < len(spans) and spans[span_at][1] <= start:
                span_at += 1
            if span_at < len(spans) and spans[span_at][0] <= start < spans[span_at][1]:
                continue
            form = gtin14(run)
            if form in seen:
                continue
            seen.add(form)
            bare = (first_alnum >= match.start() and last_alnum < match.end())
            found.append((run, index, bare))
    return found


def _alnum_bounds(line):
    """Return (first, last) index of an alphanumeric character, or (0, -1)."""
    first = None
    last = -1
    for position, char in enumerate(line):
        if char.isalnum():
            if first is None:
                first = position
            last = position
    if first is None:
        return (0, -1)
    return (first, last)


def _check_missing_gtins(items, origins, source_text, apply):
    """MISSING_GTIN: attach a valid GTIN from the source that no item carries.

    The attachment is written only with apply=True. With apply=False the finding
    is "suggested": field and new_value name the key and the value apply=True
    would write, the item is marked uncertain, and its own gtin and other_ids
    stay exactly as the caller gave them. A GTIN that no item can take is
    "reported" in both modes, because neither mode writes anything.

    The target item is the one the GTIN's line names. The part number on the
    line decides first, on a token boundary: a supplier line carries the part
    number of the product it prices, and two siblings can share every
    description word. A part number that the line only mentions in passing does
    not decide: when the line holds one item's part number and exactly one
    OTHER item's description words, the line is ambiguous and the GTIN is
    reported (S9').

    When the line names no part number, the description decides, and only when
    exactly one item matches. Generic words do not count: "part" occurs in half
    the descriptions and in half the source lines, so an overlap on it alone
    must not move a GTIN to another product. A tie, or no match at all, is
    reported with item_index None and nothing is written.

    A bare GTIN line is read with the line above it only when the line below it
    names no item. In a list that prints the GTIN above its own position line,
    the line above belongs to the previous position, and borrowing it gave the
    next position's number to the wrong item (S9').

    An item this module appended is never a target: its fields come from a bare
    link, so it states no product that a GTIN could belong to.

    An item takes at most one attached GTIN. A line that names a thousand
    numbers names one product, so the first number is attached and the rest are
    reported: an item that collects every number on a hostile line is not a
    GTIN, and the mail may not cost more than its own length.

    Each line is tokenised once, and the owner of a line is resolved once, no
    matter how many GTINs stand on it. The quadratic version re-tokenised the
    line for every number: 100 KB of numbers on one line took 186 s.

    Every finding's evidence starts with "GTIN <number>: " and then quotes the
    line. The quote is cut at 200 characters, so a long position line would
    otherwise hide the number the finding is about (S12').
    """
    findings = []
    candidates = _gtin_candidates(source_text)
    if not candidates:
        return findings

    lines = source_text.splitlines()
    carried = _carried_gtins(items)
    item_words = [_words(item.get("description")) if isinstance(item, dict)
                  else set() for item in items]
    facts = {}
    owners = {}
    quotes = {}
    named_below = {}
    attached = set()
    for gtin, line_index, bare in candidates:
        form = gtin14(gtin)
        if form in carried:
            continue
        line = lines[line_index].strip()
        previous = ""
        if line_index and bare:
            # A bare GTIN line takes the line above it only when the line below
            # names no item. A list that prints the GTIN above its own position
            # line otherwise gave the next position's number to the item above
            # it (S9').
            names = named_below.get(line_index)
            if names is None:
                below = (lines[line_index + 1].strip()
                         if line_index + 1 < len(lines) else "")
                names = bool(below) and _names_an_item(
                    items, origins, below, facts, item_words)
                named_below[line_index] = names
            if not names:
                previous = lines[line_index - 1].strip()
        if line_index not in quotes:
            quotes[line_index] = _quote(line)
        key = (line_index, bare)
        if key in owners:
            index, note = owners[key]
        else:
            index, note = _gtin_owner(items, origins, previous or line,
                                      facts, item_words)
            owners[key] = (index, note)
        if index is not None and index in attached:
            note = ("the line names item %d, which already %s a GTIN from the "
                    "source"
                    % (index, "took" if apply
                       else "has a suggestion for"))
            index = None
        # Every MISSING_GTIN evidence starts with the number, so the
        # 200-character quote cut can never drop it (S12').
        if line:
            evidence = "GTIN %s: %s (%s)" % (gtin, quotes[line_index], note)
        else:
            evidence = "GTIN %s occurs in the source text (%s)" % (gtin, note)
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
            field = "other_ids"
        else:
            field = "gtin"
        if apply:
            if field == "other_ids":
                others = _as_list(item.get("other_ids"))
                others.append(gtin)
                item["other_ids"] = others
            else:
                item["gtin"] = gtin
        item["uncertain"] = True
        carried.add(form)
        attached.add(index)
        findings.append(_finding(
            "MISSING_GTIN", "attached" if apply else "suggested",
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
    part_number, or in other_ids with a label ("EAN:00200000000011"). The
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


def _line_tokens(facts, text):
    """Return (flat, boundary, hard, glue, words) for text, computed once.

    facts is a cache owned by one check. A hostile line can carry thousands of
    GTINs, and every one of them asks about the same line, so the line is
    tokenised once per check instead of once per number.
    """
    cached = facts.get(text)
    if cached is None:
        flat, boundary, hard, glue = _source_id_index(text)
        cached = (flat, boundary, hard, glue, _words(text))
        facts[text] = cached
    return cached


def _gtin_owner(items, origins, match_line, facts, item_words):
    """Return (index, note) for the item a GTIN line names, or (None, note).

    The part number on the line decides, on a token boundary. It is the one
    field that separates two siblings whose descriptions read alike, and it is
    what the supplier prints next to the GTIN.

    A part number mentioned in passing does not decide on its own. When the
    line holds the part number of exactly one item but the description words of
    exactly one OTHER item, the line is ambiguous: "Dichtungssatz passend fuer
    AC-44170" prints AC-44170 as the product the gasket fits, not as the
    product on the line. Nothing is written for an ambiguous line (S9').

    A bare GTIN line (keyword and number, nothing else) names no product of its
    own: the supplier prints it under the position it belongs to. Such a line is
    read together with the line directly above it, which the caller passes as
    match_line, and only when the line below names no item.

    When neither line names a part number, the description decides, but only
    when exactly one item reaches MATCH_RATIO. A tie, or no match at all, is
    (None, note): a reported GTIN is cheap, a GTIN on the wrong product is not.

    An item this module appended is never a candidate. It carries a link and no
    product, so it cannot own a GTIN.

    facts is the line-token cache and item_words the description words of each
    item, both built once by the caller: a line of thousands of GTINs is
    tokenised once, not once per number.
    """
    flat, boundary, hard, glue, line_words = _line_tokens(facts, match_line)
    by_part = _items_by_part(items, origins, flat, boundary, hard, glue)
    by_words = _items_by_words(items, origins, item_words, line_words)
    if len(by_part) == 1:
        if len(by_words) == 1 and by_words[0] != by_part[0]:
            return None, ("the line names the part number of one item and the "
                          "description of another, so the product is ambiguous")
        return by_part[0], "the line names that item's part number"
    if len(by_part) > 1:
        return None, ("the line names the part number of %d items, so the "
                      "product is ambiguous" % len(by_part))
    if len(by_words) == 1:
        return by_words[0], "exactly one description matches the line"
    if len(by_words) > 1:
        return None, ("%d descriptions match the line, so the product is "
                      "ambiguous" % len(by_words))
    return None, "the line names no item of the extraction"


def _items_by_part(items, origins, flat, boundary, hard, glue):
    """Return the items whose part number the line holds on a token boundary."""
    found = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or origins[index] is None:
            continue
        form = _id_form(item.get("part_number"))
        if len(form) < MIN_ID_LEN:
            continue
        if _id_occurs(form, flat, boundary, hard, glue):
            found.append(index)
    return found


def _items_by_words(items, origins, item_words, line_words):
    """Return the items whose description reaches MATCH_RATIO on the line."""
    return [index for index, item in enumerate(items)
            if isinstance(item, dict) and origins[index] is not None
            and _overlap(item_words[index], line_words) >= MATCH_RATIO]


def _names_an_item(items, origins, text, facts, item_words):
    """True when text names at least one item of the extraction.

    A line names an item when it holds the item's part number on a token
    boundary, or when the item's description words reach MATCH_RATIO. This is
    the question a bare GTIN line asks about the line below it: a line that
    names an item of its own owns the GTIN, so the number must not be borrowed
    by the line above (S9').
    """
    flat, boundary, hard, glue, line_words = _line_tokens(facts, text)
    if _items_by_part(items, origins, flat, boundary, hard, glue):
        return True
    return bool(_items_by_words(items, origins, item_words, line_words))


# ── link helpers ───────────────────────────────────────────────────────────

def _without_query(url):
    """Return url without its query string and fragment.

    The cut is the first "?" or "#". Both are found with str.find and the
    smaller position wins, so the cost is linear in the length of the link and
    a link with no query is returned unchanged.
    """
    if not isinstance(url, str):
        return ""
    cut = len(url)
    for character in "?#":
        position = url.find(character)
        if position != -1 and position < cut:
            cut = position
    return url[:cut]


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
    return _without_query(url.strip().rstrip(_TRAILING_PUNCTUATION))


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
    split = _split_quantity_text(text)
    if split is None:
        return None
    whole, fraction = split
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

    A repeated separator groups thousands only when the first group has 1 to 3
    digits and every later group has exactly 3: "1.000.000". Anything else is
    not a number, so None is returned and a date such as "26.10.2026" is never
    read as a quantity. A first group of "0" never groups thousands, so
    "0,250" is 0.25 and not 250.
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
        if _groups_thousands(parts):
            return "".join(parts), None
        return None
    head, tail = parts
    if len(tail) == 3 and head != "0":
        return head + tail, None
    return head, tail


def _groups_thousands(parts):
    """True when parts is a thousands grouping of digits.

    The first group has 1 to 3 digits and is not "0"; every later group has
    exactly 3. Anything else is not a grouped number.
    """
    first = parts[0]
    if not 1 <= len(first) <= 3 or first == "0":
        return False
    return all(len(group) == 3 for group in parts[1:])


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


def _claims_the_line_below(lines, line_index):
    """Return True when the link on this line takes the quantity below it.

    A supplier writes the quantity under the position as often as above it, so
    "2 St\u00fcck" below a bare product link is that link's quantity, and the next
    link must not borrow it.

    Only a product link claims anything: a datasheet or an order address is not
    a position, so it owns no quantity line. A link that already took its
    quantity from its own line ("4x <link>") or from the line above it claims
    nothing below either: that number is its quantity, and the line under it
    belongs to the next position.
    """
    line = lines[line_index]
    match = _URL.search(line)
    if match is None:
        return False
    if product_link_id(_clean_link(match.group(0))) is None:
        return False
    segment = _without_markdown_text(line[:match.start()])
    if _QTY_BEFORE.search(segment) is not None:
        return False
    if line_index > 0:
        above = lines[line_index - 1].strip()
        if _previous_line_quantity(above) is not None:
            return False
    return True


def _unit_of_word(word):
    """Return the unit a quantity word names, or None.

    word is the word a quantity pattern matched, so it may carry a full stop
    ("Stk.") and any case ("ST\u00dcCK"). No unit word at all is a count of pieces,
    and so is a piece word. "set" and "sets" are a count of sets. Any other word
    is not a unit this module can name, so the caller reads no quantity.
    """
    if word is None:
        return APPENDED_UNIT
    normal = word.strip().rstrip(".").lower()
    if normal in SET_UNIT_WORDS:
        return SET_UNIT
    if normal in PIECE_UNIT_WORDS:
        return APPENDED_UNIT
    return None


def _link_quantity(lines, line_index, segment, first_on_line, only_on_line):
    """Return (quantity, unit) that belongs to one link, or None.

    segment is the text on the link's own line between the previous link and
    this one, so two links on one line cannot share a quantity. A quantity
    written in front of the link ("4x <link>") belongs to it.

    A link with nothing in front of it on its line looks at the line directly
    above, and only that line: a blank line is layout, and nothing beyond it is
    borrowed. The line must hold nothing but a quantity, and it must not be the
    quantity of the link above it. "2 St\u00fcck" under one position belongs to that
    position, so the next link must not take it.

    A link that is alone on its line also takes the quantity on the line
    directly below it, because a supplier writes the quantity under the
    position as often as above it. The second link of "4x <a> <b>" does not:
    the line below belongs to the line, not to one of two links. The link above
    must not claim that line either: it claims it only when it has no quantity
    of its own and no quantity above itself (_claims_the_line_below).

    The unit is the source's own: a piece word or no unit word is "piece", and
    "set" or "sets" is "set". A quantity word that names no such unit ("Paar",
    "VE", "Pack", "kg") gives None: this module cannot say what it counts.
    """
    match = _QTY_BEFORE.search(_without_markdown_text(segment))
    if match:
        quantity = _to_number(match.group(1))
        if quantity is None:
            return None
        return quantity, _unit_of_word(match.group(2))
    if first_on_line and line_index > 0:
        found = _previous_line_quantity(lines[line_index - 1].strip())
        if found is not None:
            above = line_index - 2
            if not (above >= 0 and _claims_the_line_below(lines, above)):
                return found
    if only_on_line and line_index + 1 < len(lines):
        return _previous_line_quantity(lines[line_index + 1].strip())
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
    """Return (quantity, unit) a line above a link holds, or None.

    The line must hold nothing but a quantity: a whole number ("2"), or a
    number with a quantity word ("4 x", "2 pcs", "1.000 Stück"). A line that
    carries money is never a quantity, and neither is a bare fraction: "12,50"
    on its own line above a link is a price, not a count of 12.5 pieces. A
    quantity word that names no unit this module knows ("1,5 kg") gives None.
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
    unit = _unit_of_word(match.group(2))
    if unit is None:
        return None
    return number, unit


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


def _set_owner_lines(by_line, grounded, unreadable, has_set, line_count):
    """Return {set-line index: owner-line index} for the set check.

    A set line belongs to the items grounded on its own line. When its own line
    grounds no item, it belongs to the line directly above it. A stack of set
    lines whose own lines ground nothing shares one owner line, taken from the
    line above the stack or, failing that, the line below it: a supplier writes
    one position's set lines together, so the stack is one position.

    The line above is read per line, the line below only per stack. A stack of
    set lines is one position, so no set line of a stack may reach down to the
    position below it on its own: that split one stack between two positions
    and rewrote a correct quantity on the position below.

    A line that grounds no item at all is never an owner, so a set line next to
    nothing is dropped instead of reaching across the mail.

    A set line whose own line grounds an item with a quantity this module
    cannot read has an owner, that item, and no other item may take the line.
    The owner states a quantity, so the line is not a stray set line of the
    stack above it.
    """
    owner = {}
    for line_index in by_line:
        if unreadable[line_index]:
            # The item on the set line states a quantity that does not parse.
            # The set line is that item's line, so it takes no other owner.
            continue
        # Own line first, then the line above. Never the line below: a stack of
        # set lines shares one owner line, which the block pass below decides.
        for candidate in (line_index, line_index - 1):
            if 0 <= candidate < line_count and grounded[candidate]:
                owner[line_index] = candidate
                break
    for first, last in _set_blocks(has_set):
        block = [index for index in range(first, last + 1) if index in by_line]
        # A set line that sits on its own item's line keeps that line, so it
        # takes no part in the stack's shared owner.
        block = [index for index in block if not unreadable[index]]
        if not block:
            continue
        first, last = block[0], block[-1]
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
        flat, boundary, hard, _glue = _source_id_index(body)
        gtins14 = set()
        for gtin in find_gtins(body):
            form = gtin14(gtin)
            if form is not None:
                gtins14.add(form)
        facts.append({
            "flat": flat,
            "boundary": boundary,
            "hard": hard,
            "packs": _marked_pack_sizes(body),
            "words": _words(body),
            "links": find_links(body),
            "gtins14": gtins14,
        })
    return facts


def _line_id_forms(fact, lengths):
    """Yield every id form the line holds on a token boundary.

    A match must start and end on a token boundary, so a matchable form is a
    run of the line's letters and digits, or several runs in a row. A form may
    not run through punctuation that is not a documented joiner, for the same
    reason UNGROUNDED_ID refuses one: "AC-4417, 8" does not hold "AC-44178".

    Only a length an indexed id actually has is built, because a form of any
    other length cannot match. Building every length up to the longest id made
    a 100 KB line of one-letter tokens hold millions of forms: 1.86 s and
    568 MB. The forms are yielded, not collected in a set, so the memory is one
    form at a time, and the caller looks each one up in the id index. The
    longest indexed id is still capped at MAX_LINE_ID_LEN by the caller.
    """
    flat = fact["flat"]
    boundary = fact["boundary"]
    hard = fact["hard"]
    size = len(flat)
    stops = [position for position, flag in enumerate(boundary) if flag]
    if not stops or not lengths:
        return
    stop_flags = bytearray(size + 1)
    for position in stops:
        if position <= size:
            stop_flags[position] = 1
    for start in stops:
        index = bisect_right(hard, start)
        limit = hard[index] if index < len(hard) else size + 1
        for length in lengths:
            end = start + length
            if end > size or end > limit:
                continue
            if stop_flags[end]:
                yield flat[start:end]


def _grounded_by_line(items, facts):
    """Return, per line, the sorted item indexes that line grounds.

    A line grounds an item when it holds the item's part number or GTIN on a
    token boundary, when it holds the item's link (or a link with the item's
    marketplace id), or when it shares MATCH_RATIO of the item's description
    words.

    The description rule is only a fallback, and it is what carries an item
    whose part number the extractor did not read. When the item's own id DOES
    occur in the source, only a line that carries that id grounds it: a shared
    description word must never hand a product's line to a different product.
    One word of "Kabelbinder 2er-Pack" reaching MATCH_RATIO for "Schraube M8
    2er-Pack" was enough to rewrite the screws' quantity.

    A line that grounds an item by its id, GTIN or link grounds no other item
    by description words either. The line names one product, so it is that
    product's line: one shared word of a two-word description handed another
    product's set line to an item that has no id in the source, and rewrote a
    correct quantity.

    The lookup is inverted on purpose. A supplier line names one product, so a
    line grounds a few items at most, while a mail holds many. The set check
    then walks the few items a line grounds instead of every item in the mail
    for every set line, which keeps a mail with a thousand positions linear
    enough to run on untrusted text.

    An id longer than MAX_LINE_ID_LEN is not indexed and does not ground a line.
    The line-form scan builds only a form whose length an indexed id has, so one
    absurd id in the extraction can neither make every token on a line build a
    form as long as that id nor add a length no id could have. The bound is
    documented in SPEC-REFERENCE.md.
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
            if MIN_ID_LEN <= len(flat) <= MAX_LINE_ID_LEN:
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
    lengths = sorted(set(len(form) for form in ids))
    # Ids first, over the whole source: an item whose id occurs anywhere is
    # grounded by ids only.
    id_found = []
    id_grounded = set()
    for fact in facts:
        found = set()
        for form in _line_id_forms(fact, lengths):
            found.update(ids.get(form, ()))
        for form in fact["gtins14"]:
            found.update(gtins.get(form, ()))
        for url in fact["links"]:
            found.update(links.get(url, ()))
            link_id = product_link_id(url)
            if link_id is not None:
                found.update(links.get(("id", link_id), ()))
        id_found.append(found)
        id_grounded.update(found)
    grounded = []
    for fact, found in zip(facts, id_found):
        found = set(found)
        if found:
            # The line names an item by its part number, GTIN or link. It is
            # that item's line, so a shared description word must not hand the
            # line to another item as well.
            grounded.append(sorted(found))
            continue
        line_words = fact["words"]
        for word in line_words:
            for index in words_any.get(word, ()):
                if index not in id_grounded:
                    found.add(index)
        for word in line_words:
            for index in words_check.get(word, ()):
                if index in id_grounded:
                    continue
                if _overlap(item_words[index], line_words) >= MATCH_RATIO:
                    found.add(index)
        grounded.append(sorted(found))
    return grounded
