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

ground() returns (checked, findings). checked is a deep copy of the input, so
the caller keeps the original and the module never mutates what it was given.
Findings come in a stable order: all UNGROUNDED_ID findings in item order, then
all MISSING_GTIN findings in source order.

    import harness_grounding as g
    checked, findings = g.ground(extraction, source_text)
"""
from __future__ import annotations

import copy
import re

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

_SEPARATORS = re.compile(r"[\s\-./_]+")

# A standalone digit run: no digit directly before or after it. The negative
# lookarounds are what stop "940063813339311" (15 digits) from matching as a
# 14-digit GTIN plus a stray digit.
_DIGIT_RUN = re.compile(r"(?<!\d)\d{8,14}(?!\d)")

# Letter runs only. Digits and underscores split words, which is what we want
# for descriptions such as "Hex bolt M8".
_WORD = re.compile(r"[^\W\d_]+")


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
    findings.extend(_check_ids(items, flat_source))
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
