"""Tests for harness_grounding.

Every fixture here is invented: "Acme Tools" and "Northwind Supply" are made-up
suppliers, and every GTIN passes or fails the GS1 check digit by construction.
No real customer text, no real product data. The GTINs use the GS1 prefix 2,
which is reserved for internal use, so no real product carries them. The
marketplace ids are invented too, and they are chosen so that they are not
valid GTINs: a 12-digit item number with a valid check digit would be picked up
as a GTIN and would blur the check under test.

The tests are split by contract, not by function name:
  - the check-digit rule, on all four GS1 lengths;
  - find_gtins, which must skip what is not a standalone valid GTIN;
  - normalize_id, which decides when two ids are the same id;
  - find_links and product_link_id, the link model;
  - the two identifier checks, positive and negative;
  - the four link, quantity and variant checks, positive and negative;
  - the ground() contract: deep copy, no mutation, missing keys tolerated.

    python3 -m unittest discover -s tests -v
"""
import copy
import os
import re
import sys
import time
import tracemalloc
import unittest
from decimal import Decimal

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import harness_grounding as G  # noqa: E402
import functools  # noqa: E402
# The owner and quantity inference is opt-in (release gate, round 4).
ground_infer = functools.partial(G.ground, infer=True)



def ground_write(*args, **kwargs):
    """Call ground() in write mode.

    Every test written before fix round 3 asserts what ground() WRITES: the
    quantity it corrects, the GTIN it attaches, the item it appends with a
    quantity. Those three checks only write with apply=True now, so those tests
    pass the flag and keep their assertions unchanged. A test of the default
    calls ground_infer() directly; see SuggestOnlyDefaultTest.
    """
    kwargs.setdefault("apply", True)
    kwargs.setdefault("infer", True)
    return ground_infer(*args, **kwargs)


def _measure(call):
    """Return (result, seconds, peak bytes) for one call.

    tracemalloc is started for the call only, so the peak is the memory that
    call allocated and not what the rest of the suite already holds.
    """
    tracemalloc.start()
    try:
        started = time.perf_counter()
        result = call()
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, elapsed, peak


# No digit run of GTIN length: isolates the identifier checks from MISSING_GTIN.
ID_SOURCE = """\
Acme Tools - order confirmation
4 x Hex bolt M8 (part AC-4417)
2 x Washer 12 mm (part WW-2201)
"""

# Two GTINs, each on a line whose words match one item description.
GTIN_SOURCE = """\
Northwind Supply - order confirmation
4 x Hex bolt M8 stainless steel  GTIN 2000000000008
2 x Garden hose 20 m  GTIN 20000004
"""

ONE_GTIN_SOURCE = """\
Northwind Supply - order confirmation
4 x Widget assembly  GTIN 2000000000008
"""

# Invented marketplace links. The eBay and AliExpress ids are 12 and 13 digits
# long and are not valid GTINs, so no GTIN check fires on them by accident.
AMAZON_LINK = "https://www.amazon.de/dp/B0TEST1234"
EBAY_LINK = "https://www.ebay.de/itm/123456789001"
ALIEXPRESS_LINK = "https://de.aliexpress.com/item/1005001234567"

# Two product links, the quantity written the two ways a supplier writes it:
# on the same line ("4x <link>") and on the line above ("2" alone).
LINK_SOURCE = """\
Acme Tools - order confirmation
4x https://www.amazon.de/dp/B0TEST1234
2
https://www.ebay.de/itm/123456789001
"""

# A set order: 28 sets of 5. The item below is an M-pack, so 140 is wrong.
SET_SOURCE = """\
Acme Tools - order confirmation
quantity: 28 x 5
Hex bolt set, 5-pack, part AC-4417
"""

# One input that makes every check fire: an uncovered link, a set line with its
# pack marker, a GTIN no item carries, a cut link the source can restore and a
# variant line with no quantity of its own.
ALL_CHECKS_SOURCE = """\
Acme Tools - order confirmation
4x https://www.amazon.de/dp/B0TEST1234
quantity: 28 x 5
Hex bolt set, 5-pack, part AC-4417
Washer 12 mm  GTIN 2000000000008
%s
""" % ALIEXPRESS_LINK

ALL_CHECKS_EXTRACTION = {"items": [
    {"description": "Hex bolt set, 5-pack", "part_number": "AC-9999",
     "quantity": 140},
    {"description": "Washer 12 mm"},
    {"description": "Widget",
     "link": "https://de.aliexpress.com/item/1[Telefon]234567"},
    {"description": "Hex bolt variant zinc", "part_number": "AC-9999"},
]}

# The one key set every finding has, in every check. See SPEC-REFERENCE.md.
FINDING_KEYS = {"code", "action", "field", "old_value", "new_value",
                "item_index", "input_index", "evidence"}


class GtinCheckDigitTest(unittest.TestCase):
    """The GS1 rule, on every length the module claims to support."""

    def test_gtin13_valid(self):
        self.assertTrue(G.gtin_valid("2000000000008"))

    def test_gtin13_invalid_check_digit(self):
        # Same body, last digit off by one.
        self.assertFalse(G.gtin_valid("4006381333932"))

    def test_gtin8_valid(self):
        self.assertTrue(G.gtin_valid("20000004"))

    def test_gtin8_invalid_check_digit(self):
        self.assertFalse(G.gtin_valid("96385075"))

    def test_gtin12_valid(self):
        self.assertTrue(G.gtin_valid("200000000004"))

    def test_gtin12_invalid_check_digit(self):
        self.assertFalse(G.gtin_valid("200000000012"))

    def test_gtin14_valid(self):
        self.assertTrue(G.gtin_valid("20000000000004"))

    def test_gtin14_invalid_check_digit(self):
        self.assertFalse(G.gtin_valid("20000000000005"))

    def test_gtin_rejects_wrong_lengths(self):
        # Wrong lengths, built from short literals on purpose: a bare 9- or
        # 10-digit literal trips this repo's channel-id scan, and that scan is
        # right to fire on a repo that will be public.
        nine = "12345678" + "9"
        ten = "12345678" + "90"
        eleven = "12345678" + "901"
        fifteen = "400638133393" + "112"
        for code in ("", "9638507", nine, ten, eleven, fifteen):
            self.assertFalse(G.gtin_valid(code), code)

    def test_gtin_rejects_non_digits_and_non_strings(self):
        self.assertFalse(G.gtin_valid("40063813339a1"))
        self.assertFalse(G.gtin_valid("2000000000008x"))
        self.assertFalse(G.gtin_valid(2000000000008))
        self.assertFalse(G.gtin_valid(None))

    def test_gtin_ignores_surrounding_whitespace(self):
        # A value copied out of a table often carries a space. Strip, do not fail.
        self.assertTrue(G.gtin_valid(" 2000000000008\n"))


class FindGtinsTest(unittest.TestCase):
    """find_gtins must return only standalone valid GTINs, in order, unique."""

    def test_find_gtins_in_order_without_repeats(self):
        text = "Acme Tools: 20000004, then 2000000000008, and 20000004 again."
        self.assertEqual(G.find_gtins(text), ["20000004", "2000000000008"])

    def test_find_gtins_skips_invalid_check_digit(self):
        self.assertEqual(G.find_gtins("96385075 4006381333932"), [])

    def test_find_gtins_skips_wrong_length_runs(self):
        # 9 digits, 15 digits, and a 17-digit run that CONTAINS a valid GTIN.
        # A longer number must never be trimmed down into a shorter one.
        # Built from short literals so the channel-id scan stays quiet.
        nine = "12345678" + "9"
        fifteen = "92000000000008" + "1"
        seventeen = "2000000000008" + "9999"
        text = " ".join([nine, fifteen, seventeen])
        self.assertEqual(G.find_gtins(text), [])

    def test_find_gtins_ignores_text_without_gtins(self):
        self.assertEqual(G.find_gtins(""), [])
        self.assertEqual(G.find_gtins("Hex bolt M8, part AC-4417"), [])
        self.assertEqual(G.find_gtins(None), [])

    def test_find_gtins_multiple_lengths(self):
        text = "20000004 200000000004 2000000000008 20000000000004"
        self.assertEqual(G.find_gtins(text),
                         ["20000004", "200000000004", "2000000000008",
                          "20000000000004"])


class NormalizeIdTest(unittest.TestCase):
    """Normalization is what lets a source and an extraction agree on an id."""

    def test_normalize_id(self):
        self.assertEqual(G.normalize_id("ac-44 17/a.b_c"), "AC4417ABC")
        self.assertEqual(G.normalize_id("AC-4417"), G.normalize_id("ac 4417"))

    def test_normalize_id_handles_none_and_numbers(self):
        self.assertEqual(G.normalize_id(None), "")
        self.assertEqual(G.normalize_id(1234), "1234")


class GroundIdentifierTest(unittest.TestCase):
    """UNGROUNDED_ID: flag, never delete."""

    def test_ground_flags_invented_part_number(self):
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC-9999"}]}
        checked, findings = ground_write(extraction, ID_SOURCE)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["code"], "UNGROUNDED_ID")
        self.assertEqual(finding["item_index"], 0)
        self.assertEqual(finding["input_index"], 0)
        self.assertEqual(finding["field"], "part_number")
        self.assertEqual(finding["old_value"], "AC-9999")
        self.assertIsNone(finding["new_value"])
        self.assertEqual(finding["action"], "flagged")
        self.assertTrue(checked["items"][0]["uncertain"])
        # The value stays. A checker that deletes hides the extractor's mistake.
        self.assertEqual(checked["items"][0]["part_number"], "AC-9999")

    def test_ground_flags_ungrounded_gtin(self):
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "gtin": "20000004"}]}
        checked, findings = ground_write(extraction, ID_SOURCE)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["field"], "gtin")
        self.assertEqual(findings[0]["old_value"], "20000004")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_leaves_grounded_id_alone(self):
        # "AC 4417" normalizes to the "AC-4417" that the source holds.
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC 4417"}]}
        checked, findings = ground_write(extraction, ID_SOURCE)
        self.assertEqual(findings, [])
        self.assertNotIn("uncertain", checked["items"][0])

    def test_ground_ignores_short_ids(self):
        # Two characters prove nothing and would flag every M8 in every mail.
        extraction = {"items": [{"description": "Hex bolt", "part_number": "M8"}]}
        checked, findings = ground_write(extraction, ID_SOURCE)
        self.assertEqual(findings, [])
        self.assertNotIn("uncertain", checked["items"][0])


class GroundIdTokenBoundaryTest(unittest.TestCase):
    """UNGROUNDED_ID grounds a whole token, never a substring (QA 8).

    A prefix of a real id is not that id. "AC-441" is not grounded by
    "AC-4417", and the digits "2610" are not grounded by the date 2026-10-26.
    The other half of the rule: the characters a PDF or a mail client puts
    inside an id (en dash, non-breaking hyphen, soft hyphen, zero-width space)
    are separators, so the same id written with one of them stays grounded.
    """

    # Every character a real mail has been seen to put where a hyphen belongs.
    JOINERS = ("\u2013", "\u2011", "\u00ad", "\u200b")

    def test_ground_flags_an_id_that_is_only_the_prefix_of_a_source_id(self):
        source = "Acme Tools\n4 x Hex bolt M8, part AC-4417\n"
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC-441"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "AC-441")
        self.assertEqual(checked["items"][0]["part_number"], "AC-441")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_flags_an_id_that_is_only_the_prefix_of_a_longer_id(self):
        source = "Northwind Supply\n2 x Washer 12 mm, part NW-22019\n"
        extraction = {"items": [{"description": "Washer 12 mm",
                                 "part_number": "NW-2201"}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])

    def test_ground_flags_digits_that_only_occur_inside_a_date(self):
        source = "Acme Tools\nOrder date 2026-10-26\n4 x Hex bolt M8\n"
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "2610"}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "2610")

    def test_ground_grounds_an_id_written_with_a_unicode_dash(self):
        # The source carries the odd character, the extraction the plain one.
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC-4417"}]}
        for joiner in self.JOINERS:
            source = ("Acme Tools\n4 x Hex bolt M8, part AC%s4417\n" % joiner)
            checked, findings = ground_write(extraction, source)
            self.assertEqual(findings, [], "joiner %r" % joiner)
            self.assertNotIn("uncertain", checked["items"][0],
                             "joiner %r" % joiner)

    def test_ground_grounds_a_value_written_with_a_unicode_dash(self):
        # The other way round: the extraction carries the odd character.
        source = "Acme Tools\n4 x Hex bolt M8, part AC-4417\n"
        for joiner in self.JOINERS:
            extraction = {"items": [
                {"description": "Hex bolt M8",
                 "part_number": "AC%s4417" % joiner}]}
            _checked, findings = ground_write(extraction, source)
            self.assertEqual(findings, [], "joiner %r" % joiner)

    def test_ground_grounds_a_token_of_a_source_id(self):
        # "AC-4417" is two tokens. A part number that IS one of those tokens is
        # grounded: the boundary rule stops prefixes, not whole words.
        source = "Acme Tools\n4 x Hex bolt M8, part AC-4417\n"
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "4417"}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])


class IdTokenPunctuationTest(unittest.TestCase):
    """S7: only a documented joiner may sit inside a grounded id.

    Punctuation that is not a joiner ("," "|" "(" ")" ";" ":") ends a token, so
    an id that only occurs by running through one of them is not grounded.
    A joiner between two digits belongs to the number, so a digit run may not
    start or end inside it either.
    """

    def test_a_comma_does_not_ground_the_digits_of_the_next_position(self):
        # "Pos. 3: AC-4417, 8 Stück" is a position and a quantity. AC-44178 is
        # not in the source.
        source = "Pos. 3: AC-4417, 8 St\u00fcck\n"
        extraction = {"items": [{"part_number": "AC-44178", "quantity": 1}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "AC-44178")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_a_pipe_does_not_join_two_cells_of_a_row(self):
        source = "AC-4417 | 8 | St\u00fcck\n"
        extraction = {"items": [{"part_number": "AC-4417-8", "quantity": 1}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "AC-4417-8")

    def test_a_bracket_does_not_join_an_id_to_the_quantity_beside_it(self):
        source = "Art.-Nr. 4417 (10 St\u00fcck)\n"
        extraction = {"items": [{"part_number": "4417-10", "quantity": 1}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])

    def test_a_joiner_between_two_digits_is_one_number(self):
        # The date is one number. "1026" is the tail of it, not an id.
        source = "Order date 2026-10-26\n"
        extraction = {"items": [{"part_number": "1026"}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "1026")

    def test_the_whole_date_is_still_grounded(self):
        # The rule stops a fragment, not a whole number.
        source = "Order date 2026-10-26\n"
        extraction = {"items": [{"part_number": "2026-10-26"}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])

    def test_a_joiner_after_a_letter_still_joins_the_id(self):
        # "AC-4417" is one id and "4417" is a whole token of it, so the rule
        # that stops the date fragment must not flag it.
        source = "Acme Tools\n4 x Hex bolt M8, part AC-4417\n"
        extraction = {"items": [{"part_number": "4417"}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])


class VerbatimIdTest(unittest.TestCase):
    """S7' (fix round 3): an id that occurs verbatim is grounded.

    A German cable or fastener designation carries punctuation that is not a
    joiner: "NYM-J 3x1,5", "SPAX 4,5x40", "M8\u00d740", "AC-4417(B)". Read as
    tokens only, the comma, the multiplication sign and the bracket are a hard
    stop, so an id the source prints character for character was flagged.

    The rule: an id is grounded when its own text occurs in the source,
    case-insensitive, and both its start and its end fall on a token boundary.
    The boundary requirement keeps the round-2 rule: a prefix that occurs
    inside a longer token grounds nothing.

    A minus sign U+2212 and a fullwidth hyphen U+FF0D are joiners, and the
    word joiner U+2060 and a byte-order mark U+FEFF are ignored like the
    zero-width space U+200B, so a PDF or a mail client that swaps the hyphen
    does not break the id.
    """

    def _assert_grounded(self, part_number, source):
        extraction = {"items": [{"part_number": part_number}]}
        for apply in (False, True):
            checked, findings = ground_infer(extraction, source, apply=apply)
            self.assertEqual(findings, [], "%s apply=%s" % (part_number, apply))
            self.assertNotIn("uncertain", checked["items"][0],
                             "%s apply=%s" % (part_number, apply))

    def test_a_cable_designation_with_a_comma_is_grounded(self):
        self._assert_grounded("NYM-J 3x1,5",
                              "Pos. 1: 50 m Mantelleitung NYM-J 3x1,5 grau")

    def test_a_cable_designation_with_a_slash_and_a_comma_is_grounded(self):
        self._assert_grounded("H07V-K 1x2,5",
                              "Pos. 2: 100 m Aderleitung H07V-K 1x2,5 blau")

    def test_a_fastener_designation_with_a_comma_is_grounded(self):
        self._assert_grounded("SPAX 4,5x40",
                              "Pos. 3: 200 St\u00fcck SPAX 4,5x40 verzinkt")

    def test_a_fastener_designation_with_a_multiplication_sign_is_grounded(self):
        self._assert_grounded("M8\u00d740",
                              "Pos. 4: 50 St\u00fcck Sechskantschraube "
                              "M8\u00d740 DIN 933")

    def test_an_id_with_a_bracket_is_grounded(self):
        self._assert_grounded("AC-4417(B)",
                              "Pos. 5: 10 St\u00fcck Ventil AC-4417(B) Messing")

    def test_a_minus_sign_or_a_fullwidth_hyphen_joins_an_id(self):
        # The extraction writes the plain hyphen; the source carries the
        # character a PDF or a fullwidth font leaves behind.
        extraction = {"items": [{"part_number": "AC-44170"}]}
        for joiner in ("\u2212", "\uff0d"):
            source = "Pos. 1: 2 St\u00fcck AC%s44170 Schraube" % joiner
            for apply in (False, True):
                checked, findings = ground_infer(extraction, source, apply=apply)
                self.assertEqual(findings, [], "%r apply=%s" % (joiner, apply))
                self.assertNotIn("uncertain", checked["items"][0],
                                 "%r apply=%s" % (joiner, apply))

    def test_an_invisible_character_does_not_break_an_id(self):
        # A word joiner or a byte-order mark inside an id is ignored, like the
        # zero-width space that is already a joiner.
        extraction = {"items": [{"part_number": "AC-44170"}]}
        for char in ("\u2060", "\ufeff"):
            source = "Pos. 1: 2 St\u00fcck AC%s44170 Schraube" % char
            for apply in (False, True):
                checked, findings = ground_infer(extraction, source, apply=apply)
                self.assertEqual(findings, [], "%r apply=%s" % (char, apply))
                self.assertNotIn("uncertain", checked["items"][0],
                                 "%r apply=%s" % (char, apply))

    def test_a_prefix_that_occurs_verbatim_inside_a_longer_token_is_flagged(self):
        # The verbatim rule needs both ends on a token boundary: the source
        # writes "3x1,55", not "3x1,5".
        extraction = {"items": [{"part_number": "NYM-J 3x1,5"}]}
        for apply in (False, True):
            checked, findings = ground_infer(
                extraction, "Pos. 1: 50 m Mantelleitung NYM-J 3x1,55 grau",
                apply=apply)
            self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"],
                             "apply=%s" % apply)
            self.assertEqual(findings[0]["old_value"], "NYM-J 3x1,5")


class GermanTextAlignmentTest(unittest.TestCase):
    """B1: a character that upper-cases to two characters must not shift ids.

    "ß" upper-cases to "SS", and the PDF ligatures "ﬁ" and "ﬂ" upper-case to
    "FI" and "FL". The flat text grows by one character for each of them, so a
    boundary list with one slot per source character runs out of step: every
    later id is flagged, and ground() can raise IndexError.
    """

    def test_the_boundary_list_stays_aligned_with_the_flat_text(self):
        for text in ("Straße", "Proﬁl", "Anﬂanschplatte",
                     "Grüße aus der Lindenstraße"):
            flat, boundary, hard, glue = G._source_id_index(text)
            self.assertEqual(len(boundary), len(flat) + 1, text)
            self.assertEqual(len(glue), len(flat), text)
            for position in hard:
                self.assertLessEqual(position, len(flat), text)

    def test_a_sharp_s_in_the_address_does_not_flag_a_grounded_id(self):
        # B1 (a) from the 2026-10-27 recheck: spelled "strasse" there is no
        # finding, so "ß" must not create one either.
        source = ("Muster Werkzeug GmbH, Industriestraße 12, 80331 München\n"
                  "Bestellung\n"
                  "Pos. 1: 4 Stück Sechskantschraube M8, Art.-Nr. AC-4417\n")
        extraction = {"items": [{"part_number": "AC-4417", "quantity": 4}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])

    def test_a_sharp_s_before_an_id_does_not_raise(self):
        # B1 (b): the boundary list was shorter than the flat text, so ground()
        # raised IndexError instead of answering.
        source = "Grüße aus der Lindenstraße\nBitte liefern: AC-4417"
        extraction = {"items": [{"part_number": "AC-4417", "quantity": 1}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])

    def test_a_german_signature_above_a_quoted_order_flags_nothing(self):
        # A signature with "Grüße" and "Musterstraße" above a quoted three-line
        # order flagged every part number in the order.
        source = ("Grüße aus Musterstadt\n"
                  "Musterstraße 1\n"
                  "---\n"
                  "> Pos. 1: 4 Stück Sechskantschraube M8, Art.-Nr. AC-44170\n"
                  "> Pos. 2: 2 Stück Unterlegscheibe M8, Art.-Nr. NW-22010\n"
                  "> Pos. 3: 1 Stück Kabelbinder, Art.-Nr. NW-22011\n")
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4},
            {"part_number": "NW-22010", "quantity": 2},
            {"part_number": "NW-22011", "quantity": 1},
        ]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])

    def test_a_sharp_s_does_not_hide_a_multiplied_set(self):
        # The same shift left "AC-44170" flagged and the set line uncorrected.
        source = "Maßband AC-44170 5er-Pack, Menge: 2 x 5"
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 10}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 2)

    def test_a_pdf_fi_ligature_does_not_flag_a_grounded_id(self):
        source = "Proﬁl-Schiene AC-44170, 4 Stück"
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 4}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])

    def test_a_pdf_fl_ligature_does_not_flag_a_grounded_id(self):
        source = "Anﬂanschplatte AC-44170, 4 Stück"
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 4}]}
        _checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])


class GroundMissingGtinTest(unittest.TestCase):
    """MISSING_GTIN: attach what the source proves, report what it cannot place."""

    def test_ground_attaches_missing_gtin_by_description(self):
        extraction = {"items": [
            {"description": "Hex bolt M8"},
            {"description": "Garden hose 20 m"},
        ]}
        checked, findings = ground_write(extraction, GTIN_SOURCE)
        self.assertEqual([f["action"] for f in findings], ["attached", "attached"])
        self.assertEqual(checked["items"][0]["gtin"], "2000000000008")
        self.assertEqual(checked["items"][1]["gtin"], "20000004")
        self.assertTrue(checked["items"][0]["uncertain"])
        self.assertTrue(checked["items"][1]["uncertain"])

    def test_ground_reports_a_gtin_that_one_item_cannot_claim(self):
        # One item is not a reason to attach. The line names no product of the
        # extraction, so the GTIN is reported (S6).
        extraction = {"items": [{"description": "Garden hose"}]}
        checked, findings = ground_write(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(findings[0]["action"], "reported")
        self.assertIsNone(findings[0]["item_index"])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_reports_when_nothing_matches(self):
        extraction = {"items": [
            {"description": "Garden hose"},
            {"description": "Paint brush"},
        ]}
        checked, findings = ground_write(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["code"], "MISSING_GTIN")
        self.assertIsNone(finding["item_index"])
        self.assertIsNone(finding["input_index"])
        self.assertEqual(finding["action"], "reported")
        self.assertEqual(finding["field"], "gtin")
        # Nothing was written, so both value slots are empty. The value the
        # finding is about is named in evidence.
        self.assertIsNone(finding["old_value"])
        self.assertIsNone(finding["new_value"])
        self.assertIn("2000000000008", finding["evidence"])
        # Nothing was attached to either item.
        self.assertNotIn("gtin", checked["items"][0])
        self.assertNotIn("gtin", checked["items"][1])
        self.assertNotIn("uncertain", checked["items"][0])

    def test_ground_reports_when_no_items(self):
        checked, findings = ground_write({"items": []}, ONE_GTIN_SOURCE)
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0]["item_index"])
        self.assertEqual(findings[0]["action"], "reported")
        self.assertEqual(checked["items"], [])

    def test_ground_appends_to_other_ids_when_gtin_taken(self):
        source = ("Northwind Supply\n"
                  "4 x Hex bolt M8  GTIN 2000000000008 and 20000004\n")
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "gtin": "20000004"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"])
        self.assertEqual(checked["items"][0]["gtin"], "20000004")
        self.assertEqual(checked["items"][0]["other_ids"], ["2000000000008"])
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_skips_gtin_already_in_other_ids(self):
        extraction = {"items": [{"description": "Widget assembly",
                                 "other_ids": ["2000000000008"]}]}
        checked, findings = ground_write(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["other_ids"], ["2000000000008"])

    def test_a_gtin_never_goes_to_an_item_whose_part_number_is_printed_elsewhere(self):
        # S10' of the second review. The description fallback lacked the guard
        # the set check has: when an item's own part number occurs in the
        # source, only a line that carries that id grounds it. Pos. 2 prints no
        # part number, so the shared word "Kabelbinder" handed its GTIN to
        # AC-44170 on the line above.
        source = ("Pos. 1: AC-44170 Kabelbinder 200 mm schwarz, 8 St\u00fcck\n"
                  "Pos. 2: Kabelbinder 200 mm wei\u00df, 4 St\u00fcck, "
                  "EAN 2000000000015")
        extraction = {"items": [{"part_number": "AC-44170",
                                 "description": "Kabelbinder 200 mm schwarz",
                                 "quantity": 8}]}
        for apply in (False, True):
            checked, findings = ground_infer(extraction, source, apply=apply)
            self.assertEqual([(f["code"], f["action"]) for f in findings],
                             [("MISSING_GTIN", "reported")])
            self.assertIsNone(findings[0]["item_index"])
            self.assertIsNone(findings[0]["new_value"])
            self.assertNotIn("gtin", checked["items"][0])
            self.assertNotIn("other_ids", checked["items"][0])
            self.assertNotIn("uncertain", checked["items"][0])

    def test_a_ligature_in_the_source_matches_the_description(self):
        # N6' of the second review. _words kept the PDF ligature "\ufb01", so the
        # description "Profilzylinder 30/30 mm" did not match "Pro\ufb01lzylinder"
        # and the GTIN was reported instead of attached.
        source = ("Pos. 1: Pro\ufb01lzylinder 30/30 mm, 2 St\u00fcck, "
                  "EAN 2000000000015")
        extraction = {"items": [{"description": "Profilzylinder 30/30 mm"}]}
        for apply in (False, True):
            checked, findings = ground_infer(extraction, source, apply=apply)
            self.assertEqual(
                [(f["code"], f["action"]) for f in findings],
                [("MISSING_GTIN", "attached" if apply else "suggested")])
            if apply:
                self.assertEqual(checked["items"][0]["gtin"], "2000000000015")

    def test_one_item_takes_at_most_one_attached_gtin(self):
        # Both numbers sit on the one line that names the item. One item can
        # carry one GTIN, so the second number is reported, not attached: an
        # item that collects every number on a hostile line is not a GTIN.
        source = ("Northwind Supply\n"
                  "4 x Hex bolt M8  EAN 2000000000008  EAN 20000004\n")
        extraction = {"items": [{"description": "Hex bolt M8"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["action"] for f in findings],
                         ["attached", "reported"])
        self.assertEqual(checked["items"][0]["gtin"], "2000000000008")
        self.assertNotIn("other_ids", checked["items"][0])
        self.assertIsNone(findings[1]["item_index"])
        self.assertIsNone(findings[1]["new_value"])


class GtinKeywordRuleTest(unittest.TestCase):
    """A digit run is a GTIN only with a GTIN keyword on its line (QA 6).

    Eight to fourteen digits with a valid check digit is not enough. A phone
    number, an order number and a marketplace id all pass that test. The line
    must name the number: GTIN, EAN, UPC, Barcode or Strichcode. A run inside a
    link is never a GTIN, keyword or not.
    """

    def test_ground_does_not_attach_a_phone_number(self):
        source = "Northwind Supply\nTel. +49 89 12345670\n4 x Widget assembly\n"
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_does_not_attach_an_order_number(self):
        source = "Acme Tools\nOrder 20261023\n4 x Widget assembly\n"
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_does_not_attach_a_digit_run_inside_a_link(self):
        # The line carries the keyword; the only digit run on it is in the URL.
        source = ("Acme Tools\n4 x Widget assembly, EAN as printed in the "
                  "shop: https://shop.example.com/p/2000000000046\n")
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_attaches_a_gtin_with_an_english_keyword(self):
        source = "Acme Tools\n4 x Widget assembly  EAN 2000000000046\n"
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"])
        self.assertEqual(findings[0]["new_value"], "2000000000046")
        self.assertEqual(checked["items"][0]["gtin"], "2000000000046")

    def test_ground_attaches_a_gtin_with_a_german_keyword(self):
        source = "Acme Tools\n4 x Widget assembly  Strichcode 2000000000046\n"
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"])
        self.assertEqual(checked["items"][0]["gtin"], "2000000000046")

    def test_ground_takes_only_the_digit_run_after_the_keyword(self):
        # S5 (a) from the 2026-10-27 recheck. The order number comes first on
        # the line and passes the check digit too. Only the run that follows
        # "EAN" is the GTIN.
        source = ("Bestellung 20261023, Sechskantschraube M8 AC-44170, "
                  "EAN 2000000000015\n")
        extraction = {"items": [{"part_number": "AC-44170",
                                 "description": "Sechskantschraube M8",
                                 "quantity": 4}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"])
        self.assertEqual(checked["items"][0]["gtin"], "2000000000015")

    def test_ground_does_not_take_a_phone_number_after_a_gtin_keyword(self):
        # S5 (b): "EAN auf Anfrage" names no number. The telephone number later
        # on the line is not the GTIN of anything.
        source = ("AC-44170 Sechskantschraube M8, EAN auf Anfrage unter "
                  "Tel. 089 12345670\n")
        extraction = {"items": [{"part_number": "AC-44170",
                                 "description": "Sechskantschraube M8",
                                 "quantity": 4}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_reads_the_common_keyword_spellings(self):
        # S5: a supplier writes the keyword with a length suffix or "Nr.".
        for keyword in ("EAN", "EAN:", "EAN-13:", "EAN-Nr.:"):
            source = "Acme Tools\n4 x Widget assembly  %s 2000000000015\n" \
                     % keyword
            extraction = {"items": [{"description": "Widget assembly"}]}
            checked, findings = ground_write(extraction, source)
            self.assertEqual([f["action"] for f in findings], ["attached"],
                             keyword)
            self.assertEqual(checked["items"][0]["gtin"], "2000000000015",
                             keyword)

    # S12 from the 2026-10-27 recheck. The GTIN keyword has more spellings than
    # "EAN" and "GTIN": a supplier attaches the length with no separator, writes
    # the plural, hyphenates "Bar-Code" and uses "Strichcodenummer". Every
    # spelling below must name the number that follows it.
    def _assert_keyword_names_the_gtin(self, keyword):
        source = ("Acme Tools\n4 x Widget assembly  %s 2000000000015\n"
                  % keyword)
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"], keyword)
        self.assertEqual(findings[0]["new_value"], "2000000000015", keyword)
        self.assertEqual(checked["items"][0]["gtin"], "2000000000015", keyword)

    def test_keyword_ean13_without_a_separator(self):
        self._assert_keyword_names_the_gtin("EAN13:")

    def test_keyword_ean13_with_a_hyphen(self):
        self._assert_keyword_names_the_gtin("EAN-13:")

    def test_keyword_gtin13_without_a_separator(self):
        self._assert_keyword_names_the_gtin("GTIN13:")

    def test_keyword_gtin13_with_a_hyphen(self):
        self._assert_keyword_names_the_gtin("GTIN-13:")

    def test_keyword_eans_plural(self):
        self._assert_keyword_names_the_gtin("EANs:")

    def test_keyword_barcodes_plural(self):
        self._assert_keyword_names_the_gtin("Barcodes:")

    def test_keyword_bar_code_with_a_hyphen(self):
        self._assert_keyword_names_the_gtin("Bar-Code:")

    def test_keyword_strichcodenummer(self):
        self._assert_keyword_names_the_gtin("Strichcodenummer:")

    def test_ground_attaches_each_gtin_to_the_part_number_on_its_line(self):
        # S6 (a) from the 2026-10-27 recheck. Two siblings share every
        # description word, so the description alone cannot separate them. Each
        # line names its own part number, and that decides.
        source = ("AC-44108 Sechskantschraube M8 verzinkt, EAN 2000000000015\n"
                  "AC-44110 Sechskantschraube M10 verzinkt, EAN 2000000000022\n")
        extraction = {"items": [
            {"part_number": "AC-44108",
             "description": "Sechskantschraube M8 verzinkt", "quantity": 4},
            {"part_number": "AC-44110",
             "description": "Sechskantschraube M10 verzinkt", "quantity": 2},
        ]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["action"] for f in findings],
                         ["attached", "attached"])
        self.assertEqual(checked["items"][0]["gtin"], "2000000000015")
        self.assertEqual(checked["items"][1]["gtin"], "2000000000022")

    def test_ground_reports_a_gtin_two_descriptions_match(self):
        # S6 (b): the line names an unordered product, and its words match both
        # ordered items. A tie is reported, never attached to the first item.
        source = ("AC-44170 Sechskantschraube M8 verzinkt, 4 St\u00fcck\n"
                  "NW-22010 Unterlegscheibe M8 verzinkt, 4 St\u00fcck\n"
                  "Kabelbinder M8 verzinkt, EAN 2000000000022\n")
        extraction = {"items": [
            {"part_number": "AC-44170",
             "description": "Sechskantschraube M8 verzinkt", "quantity": 4},
            {"part_number": "NW-22010",
             "description": "Unterlegscheibe M8 verzinkt", "quantity": 4},
        ]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "MISSING_GTIN")
        self.assertEqual(findings[0]["action"], "reported")
        self.assertIsNone(findings[0]["item_index"])
        self.assertNotIn("gtin", checked["items"][0])
        self.assertNotIn("gtin", checked["items"][1])

    def test_ground_never_attaches_a_gtin_to_an_appended_item(self):
        # S6 (c): the only item is the placeholder UNCOVERED_LINK appended. The
        # GTIN belongs to no item the extractor produced, so it is reported.
        source = ("Acme Tools\n"
                  "4x https://www.amazon.de/dp/B0TESTAAA1\n"
                  "EAN 2000000000015\n")
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual([f["code"] for f in findings],
                         ["UNCOVERED_LINK", "MISSING_GTIN"])
        self.assertEqual(findings[1]["action"], "reported")
        self.assertIsNone(findings[1]["item_index"])
        self.assertEqual(len(checked["items"]), 1)
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_does_not_move_a_gtin_on_a_generic_word_alone(self):
        # Both descriptions hold "part" and the GTIN line holds "part" too. An
        # overlap on that word alone must not decide where the GTIN goes: the
        # line names no product, so the GTIN is reported, not attached.
        source = ("Acme Tools\n4 x Spare part bracket\n"
                  "GTIN 2000000000008, part of set\n")
        extraction = {"items": [
            {"description": "Spare part bracket"},
            {"description": "Replacement part kit"},
        ]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["code"], "MISSING_GTIN")
        self.assertEqual(finding["action"], "reported")
        self.assertIsNone(finding["item_index"])
        self.assertIsNone(finding["new_value"])
        self.assertIn("2000000000008", finding["evidence"])
        self.assertNotIn("gtin", checked["items"][0])
        self.assertNotIn("gtin", checked["items"][1])


class GtinNumberLabelTest(unittest.TestCase):
    """S8' (fix round 3): "EAN Nr." and "GTIN No." are read.

    The label group put its word boundary after the optional full stop:
    `\\s+(?:nr\\.?|no\\.?)\\b`. A full stop is not a word character, so the
    boundary after "Nr." fell between two non-word characters and the pattern
    matched nothing: a line that said "EAN Nr. 2000000000015" produced no
    finding at all.

    The boundary now stands before the full stop: `(?:nr|no)\\b\\.?`. Both modes
    are tested, because a suggestion that names no value the write mode writes
    is a wrong suggestion.
    """

    ITEMS = {"items": [{"part_number": "AC-44170",
                        "description": "Schraube M8", "quantity": 8}]}

    def _source(self, label):
        return ("AC-44170 Schraube M8, 8 St\u00fcck\n"
                "%s 2000000000015" % label)

    def _assert_label_names_the_gtin(self, label):
        source = self._source(label)
        checked, findings = ground_infer(self.ITEMS, source)
        self.assertEqual([f["code"] for f in findings], ["MISSING_GTIN"], label)
        self.assertEqual(findings[0]["action"], "suggested", label)
        self.assertEqual(findings[0]["field"], "gtin", label)
        self.assertEqual(findings[0]["new_value"], "2000000000015", label)
        self.assertEqual(findings[0]["item_index"], 0, label)
        self.assertNotIn("gtin", checked["items"][0], label)
        self.assertTrue(checked["items"][0]["uncertain"], label)

        checked, findings = ground_infer(self.ITEMS, source, apply=True)
        self.assertEqual([f["code"] for f in findings], ["MISSING_GTIN"], label)
        self.assertEqual(findings[0]["action"], "attached", label)
        self.assertEqual(findings[0]["new_value"], "2000000000015", label)
        self.assertEqual(checked["items"][0]["gtin"], "2000000000015", label)

    def test_ean_nr_with_a_space_and_a_full_stop(self):
        self._assert_label_names_the_gtin("EAN Nr.")

    def test_ean_nr_with_a_space_and_a_colon(self):
        self._assert_label_names_the_gtin("EAN Nr.:")

    def test_gtin_no_with_a_space_and_a_full_stop(self):
        self._assert_label_names_the_gtin("GTIN No.")

    def test_barcode_nr_with_a_space_and_a_full_stop(self):
        self._assert_label_names_the_gtin("Barcode Nr.")


class GtinNeighbourTest(unittest.TestCase):
    """S9' (fix round 3): a GTIN never goes to its neighbour.

    Two regressions from the S6 fix, both about a GTIN that lands on the wrong
    item:

    (a) A bare GTIN line was always read together with the line above it. In a
        list that prints the GTIN above its position line, the number of the
        NEXT position went to the item above: `EAN: G2` after AC-44170's line
        became AC-44170's second GTIN, although the line below it names
        NW-22010. A bare GTIN line now takes the line above only when the line
        below names no item.

    (b) Any part number on the line won, even one mentioned in passing. On
        `Pos. 2: Dichtungssatz passend für AC-44170, ... EAN G2` the part number
        points at AC-44170 but every description word belongs to the
        Dichtungssatz item. When the line's part number points at one item and
        its description words match exactly one other item, the line is
        ambiguous and the GTIN is reported.

    Both parts are tested in both modes: a reported GTIN writes nothing, so the
    finding and the extraction must be the same with and without apply=True.
    """

    # (a) The GTIN stands above its own position line.
    ABOVE_ITEMS = {"items": [
        {"part_number": "AC-44170", "description": "Sechskantschraube M8"},
        {"part_number": "NW-22010", "description": "Kabelbinder 200 mm"},
    ]}
    ABOVE_SOURCE = ("EAN: 2000000000015\n"
                    "AC-44170 Sechskantschraube M8, 8 St\u00fcck\n"
                    "EAN: 2000000000022\n"
                    "NW-22010 Kabelbinder 200 mm, 4 St\u00fcck")

    # (b) The part number is mentioned in passing, the description is another
    # item's.
    PASSING_ITEMS = {"items": [
        {"part_number": "AC-44170", "description": "Druckminderer 1/2 Zoll",
         "gtin": "2000000000015"},
        {"description": "Dichtungssatz"},
    ]}
    PASSING_SOURCE = ("Pos. 1: AC-44170 Druckminderer 1/2 Zoll, 1 St\u00fcck, "
                      "EAN 2000000000015\n"
                      "Pos. 2: Dichtungssatz passend f\u00fcr AC-44170, 2 "
                      "St\u00fcck, EAN 2000000000022")

    def test_a_bare_gtin_line_below_an_item_line_follows_the_mail_layout(self):
        # Card 3.4 reported both numbers here. The mail prints the EAN ABOVE
        # each position, and the anchor (the first line, which has an item line
        # on its lower side only) says so, so each number now goes to the
        # position below it. The wrong attachment card 3.4 fixed stays fixed:
        # G2 never goes to AC-44170 (S9'a follow-up).
        for apply in (False, True):
            checked, findings = ground_infer(self.ABOVE_ITEMS, self.ABOVE_SOURCE,
                                         apply=apply)
            self.assertEqual([f["code"] for f in findings],
                             ["MISSING_GTIN", "MISSING_GTIN"], "apply=%s" % apply)
            self.assertEqual([f["item_index"] for f in findings], [0, 1],
                             "apply=%s" % apply)
            self.assertEqual([f["new_value"] for f in findings],
                             ["2000000000015", "2000000000022"],
                             "apply=%s" % apply)
            self.assertEqual([f["action"] for f in findings],
                             ["attached", "attached"] if apply
                             else ["suggested", "suggested"],
                             "apply=%s" % apply)
            self.assertIn("2000000000015", findings[0]["evidence"])
            self.assertIn("2000000000022", findings[1]["evidence"])
            if apply:
                self.assertEqual(checked["items"][0]["gtin"], "2000000000015")
                self.assertEqual(checked["items"][1]["gtin"], "2000000000022")
            else:
                self.assertNotIn("gtin", checked["items"][0], "apply=%s" % apply)
                self.assertNotIn("gtin", checked["items"][1], "apply=%s" % apply)

    def test_a_gtin_on_a_line_that_describes_another_item_is_reported(self):
        for apply in (False, True):
            checked, findings = ground_infer(self.PASSING_ITEMS, self.PASSING_SOURCE,
                                         apply=apply)
            self.assertEqual([f["code"] for f in findings], ["MISSING_GTIN"],
                             "apply=%s" % apply)
            finding = findings[0]
            self.assertEqual(finding["action"], "reported", "apply=%s" % apply)
            self.assertIsNone(finding["item_index"], "apply=%s" % apply)
            self.assertIsNone(finding["new_value"], "apply=%s" % apply)
            self.assertIn("2000000000022", finding["evidence"])
            self.assertNotIn("other_ids", checked["items"][0],
                             "apply=%s" % apply)
            self.assertEqual(checked["items"][0]["gtin"], "2000000000015",
                             "apply=%s" % apply)
            self.assertNotIn("gtin", checked["items"][1],
                             "apply=%s" % apply)

    def test_the_line_above_a_bare_gtin_line_still_works_when_nothing_follows(self):
        # The quantity-below-a-GTIN layout: the bare GTIN line is the last line
        # of the position, so the line above it still names the item.
        source = ("AC-44170 Sechskantschraube M8, 8 St\u00fcck\n"
                  "EAN: 2000000000015")
        items = {"items": [{"part_number": "AC-44170",
                            "description": "Sechskantschraube M8"}]}
        for apply in (False, True):
            checked, findings = ground_infer(items, source, apply=apply)
            self.assertEqual([f["code"] for f in findings], ["MISSING_GTIN"],
                             "apply=%s" % apply)
            self.assertEqual(findings[0]["item_index"], 0, "apply=%s" % apply)
            if apply:
                self.assertEqual(checked["items"][0]["gtin"],
                                 "2000000000015")
            else:
                self.assertEqual(findings[0]["action"], "suggested")
                self.assertNotIn("gtin", checked["items"][0])


class GtinLayoutTest(unittest.TestCase):
    """S9'a follow-up (fix round 3): a bare GTIN line follows the mail's layout.

    Card 3.4 stopped a bare GTIN line from taking the wrong neighbour by
    reporting it whenever an item line stood below it. That broke the more
    common layout, where the supplier prints the EAN UNDER each position: every
    GTIN except the last one was reported, because each of those lines has an
    item line on both sides.

    The anchored bare line decides the layout. A bare line with an item-naming
    line on exactly ONE side is anchored, and it reads that neighbour. When
    every anchor reads the line above, the mail prints the EAN under its
    position, and a bare line with item lines on BOTH sides reads the line above
    as well. When every anchor reads the line below, the mail prints the EAN
    above its position, and such a line reads the line below. No anchor at all,
    or anchors that disagree, leave the line in the middle reported, as card 3.4
    did.

    Both modes are tested: the default suggests the GTIN, apply=True attaches it.
    """

    G1 = "2000000000015"
    G2 = "2000000000022"
    G3 = "2000000000039"

    TWO_ITEMS = {"items": [
        {"part_number": "AC-44170", "description": "Sechskantschraube M8"},
        {"part_number": "NW-22010", "description": "Kabelbinder 200 mm"},
    ]}
    THREE_ITEMS = {"items": [
        {"part_number": "AC-44170", "description": "Sechskantschraube M8"},
        {"part_number": "NW-22010", "description": "Kabelbinder 200 mm"},
        {"part_number": "XY-90001", "description": "Handschuhe Gr. 9"},
    ]}

    # The EAN sits under its own position. Every GTIN but the last has an item
    # line on both sides, so only the last one is anchored.
    UNDER_TWO = ("AC-44170 Sechskantschraube M8, 8 St\u00fcck\n"
                 "EAN: 2000000000015\n"
                 "NW-22010 Kabelbinder 200 mm, 4 St\u00fcck\n"
                 "EAN: 2000000000022")
    UNDER_THREE = (UNDER_TWO + "\n"
                   "XY-90001 Handschuhe Gr. 9, 2 Paar\n"
                   "EAN: 2000000000039")

    # The EAN sits above its own position: the S9' input.
    ABOVE_TWO = ("EAN: 2000000000015\n"
                 "AC-44170 Sechskantschraube M8, 8 St\u00fcck\n"
                 "EAN: 2000000000022\n"
                 "NW-22010 Kabelbinder 200 mm, 4 St\u00fcck")

    # One anchor reads the line below and one reads the line above, so the mail
    # states no layout and the line in the middle is reported.
    CONFLICTING = (ABOVE_TWO + "\nEAN: 2000000000039")

    # Two products, one bare line between them, and no anchor to say which side
    # it belongs to.
    NO_ANCHOR = ("AC-44170 Sechskantschraube M8, 8 St\u00fcck\n"
                 "EAN: 2000000000015\n"
                 "NW-22010 Kabelbinder 200 mm, 4 St\u00fcck")

    def _assert_both_modes(self, items, source, expected, written):
        """Assert the findings and the written GTINs in both modes.

        expected is (item_index, new_value) per GTIN, in source order. written
        is the gtin field of each item after apply=True.
        """
        for apply in (False, True):
            checked, findings = ground_infer(copy.deepcopy(items), source,
                                         apply=apply)
            label = "apply=%s" % apply
            self.assertEqual([f["code"] for f in findings],
                             ["MISSING_GTIN"] * len(expected), label)
            self.assertEqual([(f["item_index"], f["new_value"])
                              for f in findings], expected, label)
            self.assertEqual([f["action"] for f in findings],
                             ["attached" if index is not None else "reported"
                              for index, _ in expected] if apply else
                             ["suggested" if index is not None else "reported"
                              for index, _ in expected], label)
            self.assertEqual([item.get("gtin") for item in checked["items"]],
                             written if apply else [None] * len(written), label)

    def test_the_ean_under_each_of_two_positions_belongs_to_the_position_above(self):
        self._assert_both_modes(self.TWO_ITEMS, self.UNDER_TWO,
                                [(0, self.G1), (1, self.G2)],
                                [self.G1, self.G2])

    def test_the_ean_under_each_of_three_positions_belongs_to_the_position_above(self):
        self._assert_both_modes(self.THREE_ITEMS, self.UNDER_THREE,
                                [(0, self.G1), (1, self.G2), (2, self.G3)],
                                [self.G1, self.G2, self.G3])

    def test_the_ean_above_each_of_two_positions_belongs_to_the_position_below(self):
        self._assert_both_modes(self.TWO_ITEMS, self.ABOVE_TWO,
                                [(0, self.G1), (1, self.G2)],
                                [self.G1, self.G2])

    def test_anchors_that_disagree_report_the_line_between_them(self):
        # G1 has an item line below it only, G3 an item line above it only. The
        # two anchors state opposite layouts, so the line between them (G2) is
        # reported. The anchored lines still read their own neighbour.
        self._assert_both_modes(self.TWO_ITEMS, self.CONFLICTING,
                                [(0, self.G1), (None, None), (1, self.G3)],
                                [self.G1, self.G3])

    def test_a_bare_gtin_line_with_no_anchor_is_reported(self):
        self._assert_both_modes(self.TWO_ITEMS, self.NO_ANCHOR,
                                [(None, None)], [None, None])


class GtinLeadingZerosTest(unittest.TestCase):
    """A GTIN is one number space, not four (QA 7).

    GTIN-8, -12, -13 and -14 are the same number with different leading zeros.
    The source may hold "200000000004" where the extractor wrote
    "0200000000004". That is the same GTIN: it is grounded, and it is not
    attached a second time under the other spelling.
    """

    SOURCE = "Acme Tools\n4 x Widget assembly  EAN 200000000004\n"

    def test_ground_grounds_a_gtin_written_without_its_leading_zero(self):
        extraction = {"items": [{"description": "Widget assembly",
                                 "gtin": "0200000000004"}]}
        checked, findings = ground_write(extraction, self.SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["gtin"], "0200000000004")
        self.assertNotIn("uncertain", checked["items"][0])

    def test_ground_grounds_a_gtin14_of_a_gtin12_in_the_source(self):
        extraction = {"items": [{"description": "Widget assembly",
                                 "gtin": "00200000000004"}]}
        checked, findings = ground_write(extraction, self.SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["gtin"], "00200000000004")

    def test_ground_does_not_attach_a_gtin_that_part_number_carries(self):
        extraction = {"items": [{"description": "Widget assembly",
                                 "part_number": "200000000004"}]}
        checked, findings = ground_write(extraction, self.SOURCE)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_does_not_attach_a_gtin_that_other_ids_carries(self):
        # The label names the number; it is not part of it.
        extraction = {"items": [{"description": "Widget assembly",
                                 "other_ids": ["EAN:0200000000004"]}]}
        checked, findings = ground_write(extraction, self.SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["other_ids"],
                         ["EAN:0200000000004"])
        self.assertNotIn("gtin", checked["items"][0])


class GroundContractTest(unittest.TestCase):
    """The promises ground() makes to every caller."""

    def test_ground_does_not_mutate_input(self):
        extraction = {"items": [
            {"description": "Hex bolt M8", "part_number": "AC-9999"},
            {"description": "Washer 12 mm", "other_ids": ["WW-9999"]},
        ]}
        snapshot = copy.deepcopy(extraction)
        checked, _findings = ground_write(extraction, GTIN_SOURCE)
        self.assertEqual(extraction, snapshot)
        self.assertIsNot(checked, extraction)
        self.assertIsNot(checked["items"][0], extraction["items"][0])

    def test_ground_missing_keys(self):
        checked, findings = ground_write({"items": [{}, {"gtin": None,
                                                     "other_ids": None}]},
                                     ID_SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"], [{}, {"gtin": None,
                                                 "other_ids": None}])

    def test_ground_extraction_without_items(self):
        checked, findings = ground_write({}, "")
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"], [])

    def test_finding_shape(self):
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC-9999"}]}
        _checked, findings = ground_write(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(len(findings), 2)
        self.assertEqual([f["code"] for f in findings],
                         ["UNGROUNDED_ID", "MISSING_GTIN"])
        for finding in findings:
            self.assertEqual(set(finding), FINDING_KEYS)
            self.assertIsInstance(finding["code"], str)
            self.assertIsInstance(finding["evidence"], str)
            self.assertIsInstance(finding["action"], str)
            self.assertTrue(finding["item_index"] is None
                            or isinstance(finding["item_index"], int))
            self.assertTrue(finding["input_index"] is None
                            or isinstance(finding["input_index"], int))


class FindingContractTest(unittest.TestCase):
    """One finding shape for all six checks, documented in SPEC-REFERENCE.md."""

    def test_every_check_uses_the_documented_keys(self):
        checked, findings = ground_write(copy.deepcopy(ALL_CHECKS_EXTRACTION),
                                     ALL_CHECKS_SOURCE)
        codes = sorted(set(f["code"] for f in findings))
        self.assertEqual(codes, ["MISSING_GTIN", "REDACTED_LINK",
                                 "SET_MULTIPLIED", "UNCOVERED_LINK",
                                 "UNGROUNDED_ID", "VARIANT_SPLIT"])
        for finding in findings:
            self.assertEqual(set(finding), FINDING_KEYS, finding["code"])
            self.assertNotIn("value", finding)
            self.assertIsInstance(finding["action"], str)
            self.assertIsInstance(finding["evidence"], str)
            self.assertTrue(finding["field"] is None
                            or isinstance(finding["field"], str))
            for key in ("item_index", "input_index"):
                self.assertTrue(finding[key] is None
                                or isinstance(finding[key], int), finding["code"])

    def test_item_index_points_into_the_returned_list(self):
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "IO link variant", "part_number": "AC-4417"},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        finding = findings[0]
        self.assertEqual(finding["code"], "VARIANT_SPLIT")
        # The finding concerns the merged-away line, so the two indexes differ.
        self.assertEqual(finding["input_index"], 1)
        self.assertEqual(finding["item_index"], 0)
        self.assertEqual(checked["items"][finding["item_index"]]["quantity"], 4)
        self.assertEqual(finding["field"], "note")
        self.assertIsNone(finding["old_value"])
        self.assertEqual(finding["new_value"], "variant: IO link variant")

    def test_item_index_is_none_only_when_no_item_is_concerned(self):
        checked, findings = ground_write({"items": [{"description": "Paint brush"},
                                                {"description": "Garden hose"}]},
                                     ONE_GTIN_SOURCE)
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0]["item_index"])
        self.assertIsNone(findings[0]["input_index"])


class FindLinksTest(unittest.TestCase):
    """find_links is the link model: what counts as a link, and what does not."""

    def test_find_links_in_order_without_repeats(self):
        text = ("See https://www.ebay.de/itm/123456789001 and "
                "https://www.amazon.de/dp/B0TEST1234, then "
                "https://www.ebay.de/itm/123456789001 again.")
        self.assertEqual(G.find_links(text), [EBAY_LINK, AMAZON_LINK])

    def test_find_links_strips_sentence_punctuation(self):
        # The closing bracket, the full stop and the semicolon belong to the
        # sentence, not to the address.
        text = ("(https://www.amazon.de/dp/B0TEST1234). "
                "[https://www.ebay.de/itm/123456789001];")
        self.assertEqual(G.find_links(text), [AMAZON_LINK, EBAY_LINK])

    def test_find_links_removes_query_and_fragment(self):
        # Two links that differ only in tracking are one product link.
        text = ("https://www.amazon.de/dp/B0TEST1234?tag=acme#reviews "
                "https://www.amazon.de/dp/B0TEST1234?ref=mail")
        self.assertEqual(G.find_links(text), [AMAZON_LINK])

    def test_find_links_ignores_text_without_links(self):
        self.assertEqual(G.find_links(""), [])
        self.assertEqual(G.find_links("Hex bolt M8, part AC-4417"), [])
        self.assertEqual(G.find_links(None), [])

    def test_find_links_strips_prose_punctuation(self):
        # QA 12: a German closing quote, an exclamation mark, an apostrophe,
        # Markdown bold and a colon all follow a link in real mail text.
        text = ("Siehe https://www.amazon.de/dp/B0TEST1234“ "
                "Toll https://www.ebay.de/itm/123456789001! "
                "Kauf https://www.amazon.de/dp/B0TEST5678' "
                "Fett **https://www.ebay.de/itm/123456789002** "
                "Ende https://www.amazon.de/dp/B0TEST9012: "
                "Zitat https://www.ebay.de/itm/123456789003”")
        self.assertEqual(G.find_links(text), [
            "https://www.amazon.de/dp/B0TEST1234",
            "https://www.ebay.de/itm/123456789001",
            "https://www.amazon.de/dp/B0TEST5678",
            "https://www.ebay.de/itm/123456789002",
            "https://www.amazon.de/dp/B0TEST9012",
            "https://www.ebay.de/itm/123456789003",
        ])

    def test_find_links_handles_markdown_link_syntax(self):
        # QA 12: "[url](url)" used to yield one garbled link.
        text = ("[https://www.amazon.de/dp/B0TEST1234]"
                "(https://www.amazon.de/dp/B0TEST1234)")
        self.assertEqual(G.find_links(text), [AMAZON_LINK])
        self.assertEqual(G.find_links(
            "[Hex bolt M8](https://www.ebay.de/itm/123456789001)"), [EBAY_LINK])


class ProductLinkIdTest(unittest.TestCase):
    """product_link_id decides whether a link is a product and what its id is."""

    def test_product_link_id_amazon(self):
        self.assertEqual(G.product_link_id(AMAZON_LINK), "ASIN:B0TEST1234")
        self.assertEqual(G.product_link_id(
            "https://www.amazon.de/gp/product/B0TEST1234"), "ASIN:B0TEST1234")
        # Amazon puts the product title in a path segment before /dp/.
        self.assertEqual(G.product_link_id(
            "https://www.amazon.de/Hex-Bolt-M8/dp/B0TEST1234"),
            "ASIN:B0TEST1234")

    def test_product_link_id_uppercases_the_asin(self):
        self.assertEqual(G.product_link_id("https://www.amazon.de/dp/b0test1234"),
                         "ASIN:B0TEST1234")

    def test_product_link_id_ebay(self):
        self.assertEqual(G.product_link_id(EBAY_LINK), "EBAY:123456789001")

    def test_product_link_id_aliexpress(self):
        self.assertEqual(G.product_link_id(ALIEXPRESS_LINK),
                         "ALIEXPRESS:1005001234567")

    def test_product_link_id_rejects_other_hosts(self):
        # The path looks like a product path, the host is not a marketplace.
        self.assertIsNone(G.product_link_id(
            "https://www.example.com/dp/B0TEST1234"))
        self.assertIsNone(G.product_link_id(
            "https://www.example.com/itm/123456789001"))

    def test_product_link_id_rejects_a_marketplace_name_in_another_domain(self):
        # QA 11: the marketplace must BE the registrable domain. A label that
        # merely contains the name belongs to somebody else.
        for url in ("https://amazon.example.com/dp/B0TEST1234",
                    "https://www.ebay.attacker.example/itm/123456789001",
                    "https://www.amazon.de.attacker.example/dp/B0TEST1234",
                    "https://aliexpress.example.com/item/1005001234567",
                    "https://notamazon.de/dp/B0TEST1234",
                    "https://www.myaliexpress.com/item/1005001234567"):
            self.assertIsNone(G.product_link_id(url), url)

    def test_product_link_id_accepts_every_marketplace_domain(self):
        # Subdomains are fine; the registrable domain is what counts.
        cases = [
            ("https://www.amazon.de/dp/B0TEST1234", "ASIN:B0TEST1234"),
            ("https://amazon.com/dp/B0TEST1234", "ASIN:B0TEST1234"),
            ("https://www.amazon.co.uk/dp/B0TEST1234", "ASIN:B0TEST1234"),
            ("https://www.amazon.com.tr/dp/B0TEST1234", "ASIN:B0TEST1234"),
            ("https://www.ebay.de/itm/123456789001", "EBAY:123456789001"),
            ("https://www.ebay.co.uk/itm/123456789001", "EBAY:123456789001"),
            ("https://www.aliexpress.com/item/1005001234567",
             "ALIEXPRESS:1005001234567"),
            ("https://de.aliexpress.com/item/1005001234567",
             "ALIEXPRESS:1005001234567"),
        ]
        for url, expected in cases:
            self.assertEqual(G.product_link_id(url), expected, url)

    def test_product_link_id_accepts_the_real_marketplace_id_lengths(self):
        # B1': an eBay item number has 9 to 13 digits, an AliExpress item id 9
        # to 17. The bounds are documented in SPEC-REFERENCE.md.
        cases = [
            ("https://www.ebay.de/itm/123456789001", "EBAY:123456789001"),
            ("https://www.ebay.de/itm/1234567890123", "EBAY:1234567890123"),
            ("https://de.aliexpress.com/item/1005001234567",
             "ALIEXPRESS:1005001234567"),
            ("https://de.aliexpress.com/item/1005001234567890",
             "ALIEXPRESS:1005001234567890"),
        ]
        for url, expected in cases:
            self.assertEqual(G.product_link_id(url), expected, url)

    def test_product_link_id_rejects_a_digit_run_that_is_not_an_id(self):
        # B1': a shorter or longer run is not a product link. Without the
        # bound, every distinct id length became a form length in the
        # covered-link index.
        for url in ("https://www.ebay.de/itm/12345678",
                    "https://www.ebay.de/itm/12345678901234",
                    "https://www.ebay.de/itm/1234567890123456789012345",
                    "https://de.aliexpress.com/item/12345678",
                    "https://de.aliexpress.com/item/123456789012345678"):
            self.assertIsNone(G.product_link_id(url), url)

    def test_product_link_id_rejects_short_or_missing_path(self):
        # An ASIN is exactly 10 characters; 9 is not an ASIN.
        self.assertIsNone(G.product_link_id("https://www.amazon.de/dp/B0TEST123"))
        self.assertIsNone(G.product_link_id("https://www.amazon.de/"))
        self.assertIsNone(G.product_link_id(None))

    def test_product_link_id_accepts_an_upper_case_dp_path(self):
        # QA 12: suppliers copy the path out of a browser in either case.
        self.assertEqual(G.product_link_id("https://www.amazon.de/DP/B0TEST1234"),
                         "ASIN:B0TEST1234")
        self.assertEqual(G.product_link_id("https://www.amazon.de/Dp/B0TEST1234"),
                         "ASIN:B0TEST1234")
        self.assertEqual(G.product_link_id(
            "https://www.amazon.de/GP/Product/B0TEST1234"), "ASIN:B0TEST1234")


class LinkRegexCostTest(unittest.TestCase):
    """The link regexes stay linear on untrusted text (QA 15).

    A supplier mail is untrusted input. A regex that backtracks quadratically on
    it turns one mail into a CPU burn, so the cost of the link path is measured
    here. The bound is generous: the linear code needs about a millisecond, the
    quadratic code needed 0.8 s at this length.
    """

    LENGTH = 20000
    BOUND_SECONDS = 0.1

    def test_find_links_is_linear_on_a_long_punctuation_run(self):
        text = "https://a.example.com/x" + ")" * self.LENGTH + "y"
        started = time.perf_counter()
        G.find_links(text)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "find_links took %.3f s on %d characters"
                        % (elapsed, len(text)))

    def test_ground_is_linear_on_a_long_link_line(self):
        source = ("Acme Tools\n4x https://a.example.com/x"
                  + ")" * self.LENGTH + "y\n")
        started = time.perf_counter()
        ground_write({"items": []}, source)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters"
                        % (elapsed, len(source)))


class IdentifierRegexCostTest(unittest.TestCase):
    """The identifier regexes stay linear on untrusted text (QA 15).

    A supplier mail is untrusted input. The quantity-label regex was quadratic
    on a long run of spaces after the label ("qty" followed by 20,000 spaces
    took 1.4 s) because two `\\s*` quantifiers shared the same run. The bound
    below is generous: the linear code needs about a millisecond.
    """

    LENGTH = 20000
    BOUND_SECONDS = 0.1

    def test_ground_is_linear_on_a_long_space_run_after_a_quantity_label(self):
        source = "Acme Tools\nqty" + " " * self.LENGTH
        started = time.perf_counter()
        ground_write({"items": []}, source)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters"
                        % (elapsed, len(source)))


class GtinScanCostTest(unittest.TestCase):
    """find_gtins stays linear on a mail with many distinct numbers (S14).

    The run scan was always linear: a long digit run is skipped whole. The
    de-duplication was not. It asked "is this number already found?" of a list,
    so 20,000 distinct numbers took 1.3 s and 40,000 took 5.2 s. The guard is
    the count of distinct numbers, not the length of one run, because no
    implementation of the run scan was ever quadratic.

    The bound is generous: the set lookup needs about a tenth of a second at
    this size.
    """

    COUNT = 40000
    BOUND_SECONDS = 1.0

    def test_find_gtins_is_linear_on_many_distinct_numbers(self):
        numbers = [_synthetic_gtin8(serial) for serial in range(self.COUNT)]
        text = " ".join(numbers)
        started = time.perf_counter()
        found = G.find_gtins(text)
        elapsed = time.perf_counter() - started
        # Every number is valid and distinct, so the de-duplication has real
        # work to do: the test may not pass by finding nothing.
        self.assertEqual(len(found), self.COUNT)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "find_gtins took %.3f s on %d characters of %d numbers"
                        % (elapsed, len(text), self.COUNT))


class GtinKeywordCostTest(unittest.TestCase):
    """The GTIN keyword scan stays linear on a long run of spaces (S12).

    The value pattern wrote the separator as "\\s*:?\\s*". That splits a run of
    spaces between two quantifiers, so every split is tried and the second
    quantifier walks the rest of the run: 20,000 spaces after "EAN" took 2.8 s.
    "\\s*(?::\\s*)?" keeps the run with one quantifier, and the same line
    finishes in about 5 ms. Reverting the pattern to "\\s*:?\\s*" fails this
    test.
    """

    SPACES = 20000
    BOUND_SECONDS = 1.0

    def test_gtin_scan_is_linear_on_a_long_run_of_spaces(self):
        # The hostile line has the keyword and a long run of spaces but no
        # number behind it: every split of the run is a dead end. The second
        # line holds the real GTIN, so the test cannot pass by finding nothing.
        source = ("Acme Tools\n4 x Widget assembly  EAN%sx\n"
                  "4 x Widget assembly  EAN 2000000000015\n"
                  % (" " * self.SPACES))
        started = time.perf_counter()
        checked, _findings = ground_write(
            {"items": [{"description": "Widget assembly"}]}, source)
        elapsed = time.perf_counter() - started
        self.assertEqual(checked["items"][0].get("gtin"), "2000000000015")
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "the GTIN scan took %.3f s on %d spaces"
                        % (elapsed, self.SPACES))


class SetCheckCostTest(unittest.TestCase):
    """The set check stays linear on untrusted text (S14).

    Two guards, each proven by reverting it locally:

    - the pack-marker patterns are bounded and start on a digit boundary. A
      bare "(\\d+)" retries from every position inside a long digit run, so
      20,000 digits took 11.5 s. The source must hold a set line, because the
      marker scan only runs when the mail has one.
    - the per-line facts are built once per line. Rebuilding them per set line
      costs 28.8 s on 1,000 positions. The set lines must stand in separate
      blocks, one per line, because a block of set lines on one line is walked
      once either way.

    The bound is generous: the linear code needs about a twentieth of a second
    at these sizes.
    """

    LENGTH = 20000
    POSITIONS = 1000
    BOUND_SECONDS = 1.0

    def test_ground_is_linear_on_a_long_digit_run_beside_a_set_line(self):
        source = ("Acme Tools\n"
                  "AC-4417 Sechskantschraube M8 2er-Pack\n"
                  "quantity: 4 x 2\n"
                  + "1" * self.LENGTH + "\n")
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC-4417",
                                 "quantity": 8}]}
        started = time.perf_counter()
        checked, findings = ground_write(extraction, source)
        elapsed = time.perf_counter() - started
        # The set line corrects the item, so the pack-marker scan ran on the
        # digit run: the test may not pass by skipping that scan.
        self.assertEqual([finding["code"] for finding in findings],
                         ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters"
                        % (elapsed, len(source)))

    def test_ground_is_linear_on_a_set_block_per_position(self):
        lines = ["Acme Tools - order confirmation"]
        items = []
        for position in range(self.POSITIONS):
            part_number = "AC-%04d" % position
            lines.append("%s Sechskantschraube M8 2er-Pack" % part_number)
            lines.append("quantity: 4 x 2")
            items.append({"part_number": part_number, "quantity": 8,
                          "description": "Hex bolt M8"})
        source = "\n".join(lines) + "\n"
        started = time.perf_counter()
        checked, findings = ground_write({"items": items}, source)
        elapsed = time.perf_counter() - started
        # Every position is corrected, so the check has real work to do.
        self.assertEqual(len(findings), self.POSITIONS)
        self.assertEqual(set(finding["code"] for finding in findings),
                         set(["SET_MULTIPLIED"]))
        self.assertEqual(set(item["quantity"] for item in checked["items"]),
                         set([4]))
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters in %d positions"
                        % (elapsed, len(source), self.POSITIONS))


def _synthetic_gtin8(serial):
    """A GTIN-8 with the internal-use prefix 2 and a valid check digit."""
    body = "2" + ("%06d" % serial)
    total = sum(int(digit) * (3 if position % 2 == 0 else 1)
                for position, digit in enumerate(body))
    return body + str((10 - total % 10) % 10)


def _synthetic_gtin13(serial):
    """A GTIN-13 with the internal-use prefix 2 and a valid check digit."""
    body = "2" + ("%011d" % serial)
    total = sum(int(digit) * (3 if position % 2 else 1)
                for position, digit in enumerate(body))
    return body + str((10 - total % 10) % 10)


class HostileMailCostTest(unittest.TestCase):
    """One hostile mail cannot cost more than its own length (S2, S3).

    A supplier mail is untrusted input, and the README promises that it stays
    cheap. Three shapes used to cost far more than their length: one distinct
    product link per line (the covered-link check rescanned every appended item
    for every link), many GTINs on one line (the GTIN owner re-tokenised the
    line for every number), and a set line beside a very long extracted id (the
    line-form scan was bounded by the longest id in the mail).

    The bound is generous: the linear code needs about a tenth of a second at
    these sizes, and the quadratic code needed 12 s, 186 s and 1.4 s.
    """

    SIZE = 100 * 1024
    BOUND_SECONDS = 1.0

    def test_ground_is_linear_on_one_link_per_line(self):
        lines = ["Acme Tools - order confirmation", ""]
        length = sum(len(line) + 1 for line in lines)
        index = 0
        while length < self.SIZE:
            line = "4x https://www.ebay.de/itm/2839%06d" % index
            lines.append(line)
            length += len(line) + 1
            index += 1
        source = "\n".join(lines) + "\n"
        started = time.perf_counter()
        checked, findings = ground_write({"items": []}, source)
        elapsed = time.perf_counter() - started
        # Every link is uncovered, so the check has real work to do: it may not
        # pass this test by finding nothing.
        self.assertGreaterEqual(len(checked["items"]), 1000)
        self.assertEqual(len(findings), len(checked["items"]))
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters of links"
                        % (elapsed, len(source)))

    def test_ground_is_linear_on_many_gtins_on_one_line(self):
        parts = []
        length = len("Acme Tools\n")
        serial = 1
        while length < self.SIZE:
            chunk = "EAN %s " % _synthetic_gtin13(serial)
            parts.append(chunk)
            length += len(chunk)
            serial += 1
        source = "Acme Tools\n" + "".join(parts) + "\n"
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC-4417"}]}
        started = time.perf_counter()
        checked, findings = ground_write(extraction, source)
        elapsed = time.perf_counter() - started
        # The line names no part number of the extraction, so every GTIN is
        # reported: the check has real work to do.
        self.assertGreaterEqual(len(findings), 1000)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters of GTINs"
                        % (elapsed, len(source)))

    def test_ground_is_linear_on_a_set_line_beside_a_long_id(self):
        # 3,300 one-letter tokens, and a 1,000-character part number in the
        # extraction: the line-form scan may not be bounded by that id.
        words = " ".join("abcdefghij"[index % 10] for index in range(3300))
        source = ("Acme Tools\n"
                  "Sechskantschraube M8\n"
                  "2er-Pack\n"
                  "quantity: 4 x 2\n"
                  + words + "\n")
        extraction = {"items": [
            {"part_number": "Q" * 1000, "quantity": 8,
             "description": "Hex bolt"},
            {"part_number": "AC-4417", "quantity": 8,
             "description": "Washer"},
        ]}
        started = time.perf_counter()
        ground_write(extraction, source)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters beside a long id"
                        % (elapsed, len(source)))


class MarketplaceLinkCostTest(unittest.TestCase):
    """A mail of hostile links stays linear (B1').

    `_EBAY_ITEM` and `_ALIEXPRESS_ITEM` accepted a digit run of any length, so
    the covered-link index got one form length per distinct id length and built
    every substring of every one of them. The reviewer measured 305 MB at
    24 KB and 3.2 GB at 98 KB. A real marketplace id has 9 to 17 digits, so a
    shorter or longer run is not a product link and never reaches the index.

    The bounds are generous: the bounded code needs about a twentieth of a
    second and a few megabytes at these sizes.
    """

    BOUND_SECONDS = 1.0

    def _hostile_links(self, count):
        """One eBay link per line, line k holding a k-digit id."""
        digits = "1234567" * 60
        return "\n".join("https://www.ebay.de/itm/%s" % digits[:length]
                         for length in range(1, count + 1))

    def test_ninety_eight_kilobytes_of_hostile_links_is_linear(self):
        source = self._hostile_links(420)
        self.assertEqual(len(source.encode()), 98909)
        (checked, findings), elapsed, peak = _measure(
            lambda: ground_write({"items": []}, source))
        # Five of the 420 runs have a real id length, so the check has real
        # work to do: it may not pass by finding nothing.
        self.assertEqual(len(findings), 5)
        self.assertEqual(len(checked["items"]), 5)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters of links"
                        % (elapsed, len(source)))
        self.assertLess(peak, 100 * 1024 * 1024,
                        "ground peaked at %.0f MB on %d characters of links"
                        % (peak / 1e6, len(source)))

    def test_twenty_four_kilobytes_of_hostile_links_stays_small(self):
        source = self._hostile_links(200)
        self.assertEqual(len(source.encode()), 25099)
        # Both modes. The default appends the same five items and suggests no
        # quantity, because no line carries one; apply=True writes no quantity
        # either.
        (checked, findings), elapsed, peak = _measure(
            lambda: ground_infer({"items": []}, source))
        self.assertEqual([finding["action"] for finding in findings],
                         ["added"] * 5)
        self.assertEqual([item.get("quantity") for item in checked["items"]],
                         [None] * 5)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "the default took %.3f s on %d characters of links"
                        % (elapsed, len(source)))
        self.assertLess(peak, 50 * 1024 * 1024,
                        "the default peaked at %.0f MB on %d characters"
                        % (peak / 1e6, len(source)))
        (checked, findings), elapsed, peak = _measure(
            lambda: ground_write({"items": []}, source))
        self.assertEqual(len(findings), 5)
        self.assertEqual([item.get("quantity") for item in checked["items"]],
                         [None] * 5)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "apply=True took %.3f s on %d characters of links"
                        % (elapsed, len(source)))
        self.assertLess(peak, 50 * 1024 * 1024,
                        "apply=True peaked at %.0f MB on %d characters"
                        % (peak / 1e6, len(source)))


class LineFormCostTest(unittest.TestCase):
    """A long line of short tokens costs no memory (N5').

    `_line_id_forms` put every form up to the longest indexed id into a set, so
    a 100 KB line of one-letter tokens beside a 64-character part number built
    millions of forms: 1.86 s and 568 MB. The forms are yielded one at a time
    now, and only for a length an indexed id actually has, so no form that
    cannot match is built and no set is held.

    The bound is generous: the bounded code needs about a twentieth of a second
    and a few megabytes at this size.
    """

    SIZE = 100 * 1024
    BOUND_SECONDS = 1.0
    PEAK_BYTES = 50 * 1024 * 1024
    LONG_ID = "Q" * 64

    def test_a_long_line_of_one_letter_tokens_is_cheap(self):
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        tokens = []
        length = len("Menge: 4 x 2\n")
        index = 0
        while length < self.SIZE:
            tokens.append(letters[index % len(letters)])
            length += 2
            index += 1
        source = ("Menge: 4 x 2\n"
                  + " ".join(tokens) + " 2er-Pack " + self.LONG_ID + "\n")
        extraction = {"items": [{"part_number": self.LONG_ID,
                                 "quantity": 8}]}
        # Both modes. The default suggests the same correction and writes
        # nothing; apply=True writes it.
        (checked, findings), elapsed, peak = _measure(
            lambda: ground_infer(extraction, source))
        self.assertEqual([finding["action"] for finding in findings],
                         ["suggested"])
        self.assertEqual(findings[0]["new_value"], 4)
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "the default took %.3f s on %d characters of tokens"
                        % (elapsed, len(source)))
        self.assertLess(peak, self.PEAK_BYTES,
                        "the default peaked at %.0f MB on %d characters"
                        % (peak / 1e6, len(source)))
        (checked, findings), elapsed, peak = _measure(
            lambda: ground_write(extraction, source))
        # The 64-character id stands on the line, so the line-form scan has real
        # work to do and grounds the item: it may not pass by finding nothing.
        self.assertEqual([finding["code"] for finding in findings],
                         ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "apply=True took %.3f s on %d characters of tokens"
                        % (elapsed, len(source)))
        self.assertLess(peak, self.PEAK_BYTES,
                        "apply=True peaked at %.0f MB on %d characters"
                        % (peak / 1e6, len(source)))


class GroundUncoveredLinkTest(unittest.TestCase):
    """UNCOVERED_LINK: a product link no item covers becomes an item."""

    def test_ground_adds_item_with_quantity_from_same_line(self):
        checked, findings = ground_write({"items": []}, LINK_SOURCE)
        self.assertEqual([f["code"] for f in findings],
                         ["UNCOVERED_LINK", "UNCOVERED_LINK"])
        self.assertEqual(findings[0]["action"], "added")
        self.assertEqual(findings[0]["field"], "link")
        self.assertEqual(findings[0]["new_value"], AMAZON_LINK)
        self.assertEqual(findings[0]["item_index"], 0)
        # An appended item has no place in the caller's input list.
        self.assertIsNone(findings[0]["input_index"])
        item = checked["items"][0]
        self.assertEqual(item["link"], AMAZON_LINK)
        # A marketplace id is a marketplace id, not a part number and not a
        # description. It goes where the data model keeps it.
        self.assertEqual(item["other_ids"], ["ASIN:B0TEST1234"])
        self.assertNotIn("part_number", item)
        self.assertNotIn("description", item)
        self.assertEqual(item["quantity"], 4)
        self.assertEqual(item["unit"], "piece")
        self.assertTrue(item["uncertain"])

    def test_ground_takes_link_quantity_from_previous_line(self):
        # The line above the eBay link holds nothing but "2".
        checked, findings = ground_write({"items": []}, LINK_SOURCE)
        self.assertEqual(findings[1]["new_value"], EBAY_LINK)
        self.assertEqual(findings[1]["item_index"], 1)
        self.assertEqual(checked["items"][1]["quantity"], 2)

    def test_ground_link_without_quantity_gets_none(self):
        source = "Acme Tools\nhttps://www.amazon.de/dp/B0TEST1234\n"
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual(len(findings), 1)
        self.assertIsNone(checked["items"][0]["quantity"])

    def test_a_link_takes_its_own_quantity_line_below(self):
        # S11: the quantity under a position belongs to that position. The
        # second link used to take the first link's 2.
        source = ("https://www.amazon.de/dp/B0TESTAAA1\n"
                  "2 St\u00fcck\n"
                  "https://www.amazon.de/dp/B0TESTAAA2\n"
                  "3 St\u00fcck\n")
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [2, 3])

    def test_a_link_never_takes_the_quantity_of_the_link_above(self):
        # S11: "2 St\u00fcck" sits under the first position, so the second link
        # has no quantity of its own.
        source = ("https://www.amazon.de/dp/B0TESTAAA1\n"
                  "2 St\u00fcck\n"
                  "https://www.amazon.de/dp/B0TESTAAA2\n")
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [2, None])

    def test_a_blank_line_stops_the_quantity_look_up(self):
        # S11: the line directly above is the only one that counts. A blank
        # line is layout, and nothing beyond it is borrowed.
        source = ("https://www.amazon.de/dp/B0TESTAAA1\n"
                  "\n"
                  "2 St\u00fcck\n"
                  "\n"
                  "https://www.amazon.de/dp/B0TESTAAA2\n")
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [None, None])

    def test_a_million_in_front_of_a_link_is_a_million(self):
        # S11: the quantity pattern needs a left boundary, or "1.000.000"
        # matches its last group "000" and reads as 0.
        source = "1.000.000 St\u00fcck https://www.amazon.de/dp/B0TESTAAA1\n"
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual(checked["items"][0]["quantity"], 1000000)

    def test_a_million_above_a_link_is_a_million(self):
        source = ("1.000.000 St\u00fcck\n"
                  "https://www.amazon.de/dp/B0TESTAAA1\n")
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual(checked["items"][0]["quantity"], 1000000)

    def test_the_second_link_on_a_line_takes_no_quantity_from_below(self):
        # S11: two links on one line each get one quantity, and the line below
        # belongs to the line, not to the second link.
        source = ("4x https://www.amazon.de/dp/B0TESTAAA1 "
                  "https://www.amazon.de/dp/B0TESTAAA2\n"
                  "2 St\u00fcck\n")
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [4, None])

    def test_ground_does_not_add_covered_link_by_part_number(self):
        # The item holds the bare ASIN, which is what an extractor writes.
        extraction = {"items": [{"description": "Hex bolt",
                                 "part_number": "B0TEST1234"}]}
        source = "Acme Tools\n4x https://www.amazon.de/dp/B0TEST1234\n"
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 1)

    def test_ground_does_not_add_covered_link_by_same_link(self):
        extraction = {"items": [{"description": "Hex bolt",
                                 "link": AMAZON_LINK}]}
        checked, findings = ground_write(extraction, LINK_SOURCE)
        # Only the eBay link is new.
        self.assertEqual([f["new_value"] for f in findings], [EBAY_LINK])
        self.assertEqual(len(checked["items"]), 2)

    def test_ground_ignores_link_that_is_not_a_product(self):
        source = "Acme Tools\nsee https://www.example.com/orders/4711\n"
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"], [])

    def test_ground_adds_the_clean_link_from_markdown(self):
        # QA 12: the appended item must hold the address, not "](" glue.
        source = ("Acme Tools\n"
                  "4x [Hex bolt M8](https://www.amazon.de/dp/B0TEST1234)\n")
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual([f["new_value"] for f in findings], [AMAZON_LINK])
        self.assertEqual(checked["items"][0]["link"], AMAZON_LINK)
        self.assertEqual(checked["items"][0]["quantity"], 4)


class UncoveredLinkQuantityTest(unittest.TestCase):
    """Fix round 3, card 3.3: an uncovered link takes its own quantity (S5').

    A supplier writes the quantity above the position as often as below it.
    The link above a quantity line already took that number, so it must not
    also claim the line below it. And only a product link claims anything: a
    PDF address is not a position.

    The quantity is only suggested by default and written with apply=True, so
    every layout is read in both modes: the value the default suggests and the
    value apply=True writes must be the same.
    """

    A = "https://www.amazon.de/dp/B0TESTAAA1"
    B = "https://www.amazon.de/dp/B0TESTAAA2"
    C = "https://www.amazon.de/dp/B0TESTAAA3"

    def _quantities(self, source, apply):
        """Return one quantity per appended item, read from that mode's output."""
        checked, findings = ground_infer({"items": []}, source, apply=apply)
        if apply:
            return [item.get("quantity") for item in checked["items"]]
        return [finding["new_value"] for finding in findings
                if finding["action"] == "suggested"]

    def test_a_link_that_took_the_quantity_above_it_claims_no_line_below(self):
        # S5': "2 Stück" above A is A's own quantity, so A claims nothing
        # below. Every link reads the quantity directly above itself.
        source = "2 St\u00fcck\n%s\n3 St\u00fcck\n%s\n4 St\u00fcck\n%s" % (
            self.A, self.B, self.C)
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(self._quantities(source, apply), [2, 3, 4])

    def test_two_links_take_the_quantity_above_each_of_them(self):
        # S5': the second link used to take the line below the first link.
        source = "2 St\u00fcck\n%s\n3 St\u00fcck\n%s" % (self.A, self.B)
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(self._quantities(source, apply), [2, 3])

    def test_an_address_that_is_not_a_product_link_claims_nothing(self):
        # S5': a datasheet URL is not a position, so the link below it reads
        # the quantity line between them.
        source = ("Datenblatt: https://www.example.com/datenblatt-ac44170.pdf\n"
                  "3 St\u00fcck\n%s" % self.A)
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(self._quantities(source, apply), [3])

    def test_the_quantity_below_layout_still_works(self):
        # S5' must not break the layout it was written for: the quantity under
        # a bare link is that link's quantity.
        source = "%s\n2 St\u00fcck\n%s\n3 St\u00fcck" % (self.A, self.B)
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(self._quantities(source, apply), [2, 3])


class UncoveredLinkUnitTest(unittest.TestCase):
    """Fix round 3, card 3.3: an appended item states the source's unit (S6').

    An item appended for an uncovered link used to say unit "piece" whatever the
    source wrote, and the quantity patterns read `Paar`, `VE`, `Pack` and `kg` as
    piece words. Only two units are known: a piece word (or no unit word) is
    "piece", and `set`/`sets` is "set". Any other word is not a unit this module
    can name, so the link gets no quantity: nothing is suggested and nothing is
    written.

    The quantity is only suggested by default and written with apply=True, so
    every input is read in both modes.
    """

    A = "https://www.amazon.de/dp/B0TESTAAA1"

    def _read(self, source, apply):
        """Return (appended items, suggested findings) for one mode."""
        checked, findings = ground_infer({"items": []}, source, apply=apply)
        return checked["items"], [f for f in findings
                                  if f["action"] == "suggested"]

    def _assert_no_quantity(self, source):
        for apply in (False, True):
            with self.subTest(apply=apply):
                items, suggested = self._read(source, apply)
                self.assertEqual(len(items), 1)
                self.assertEqual(suggested, [])
                self.assertIsNone(items[0].get("quantity"))

    def test_a_paar_input_gives_no_quantity(self):
        self._assert_no_quantity("2 Paar %s" % self.A)

    def test_a_ve_input_gives_no_quantity(self):
        self._assert_no_quantity("3 VE %s" % self.A)

    def test_a_kg_line_above_gives_no_quantity(self):
        self._assert_no_quantity("1,5 kg\n%s" % self.A)

    def test_a_kg_prefix_gives_no_quantity(self):
        self._assert_no_quantity("0,250 kg %s" % self.A)

    def test_a_set_input_states_the_unit_set(self):
        source = "%s\n2 Sets" % self.A
        items, suggested = self._read(source, False)
        self.assertEqual(len(suggested), 1)
        self.assertEqual(suggested[0]["new_value"], 2)
        self.assertIn('"set"', suggested[0]["evidence"])
        items, _suggested = self._read(source, True)
        self.assertEqual(items[0]["quantity"], 2)
        self.assertEqual(items[0]["unit"], "set")

    def test_a_piece_word_states_the_unit_piece(self):
        for source in ("2 St\u00fcck %s" % self.A, "%s\n2 pcs" % self.A):
            with self.subTest(source=source):
                items, suggested = self._read(source, False)
                self.assertEqual(len(suggested), 1)
                self.assertEqual(suggested[0]["new_value"], 2)
                self.assertIn('"piece"', suggested[0]["evidence"])
                items, _suggested = self._read(source, True)
                self.assertEqual(items[0]["quantity"], 2)
                self.assertEqual(items[0]["unit"], "piece")


class MissingGtinEvidenceTest(unittest.TestCase):
    """Fix round 3, card 3.3: a MISSING_GTIN names itself in the evidence (S12').

    A finding's evidence quote stops at 200 characters. A position line longer
    than that used to lose the GTIN at its end, so a "reported" finding said
    nothing about which number it was about. Every MISSING_GTIN finding now
    starts with the number, so the cut can never drop it.
    """

    def test_a_reported_gtin_on_a_long_line_names_itself(self):
        line = ("Pos. 9:  " + "Kabelbinder schwarz 200 mm, " * 8
                + "EAN 2000000000015")
        self.assertEqual(len(line), 250)
        # The line names no part number and no word of this item, so no item
        # can take the GTIN and the finding is "reported".
        extraction = {"items": [{"description": "Sechskantschraube M8"}]}
        for apply in (False, True):
            with self.subTest(apply=apply):
                _checked, findings = ground_infer(extraction, line, apply=apply)
                self.assertEqual([f["code"] for f in findings],
                                 ["MISSING_GTIN"])
                finding = findings[0]
                self.assertEqual(finding["action"], "reported")
                self.assertIsNone(finding["item_index"])
                self.assertTrue(finding["evidence"].startswith(
                    "GTIN 2000000000015:"), finding["evidence"])

    def test_an_attached_gtin_names_itself_in_the_evidence(self):
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = ground_write(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(findings[0]["action"], "attached")
        self.assertTrue(findings[0]["evidence"].startswith(
            "GTIN 2000000000008:"), findings[0]["evidence"])
        self.assertEqual(checked["items"][0]["gtin"], "2000000000008")


class EvidenceLengthTest(unittest.TestCase):
    """Fix round 3, card 3.3: every evidence quote is capped (N2').

    A hostile mail can put 100 KB on one line. A finding's evidence is one short
    quotable sentence (SPEC-REFERENCE.md), so no finding may carry the whole
    line: the quote stops at 200 characters.
    """

    def test_a_set_line_with_a_long_space_run_does_not_bloat_the_evidence(self):
        source = ("Acme Tools\nMenge:%s4 x 5\n"
                  "Hex bolt set, 5-pack, part AC-4417\n" % (" " * 50000))
        extraction = {"items": [{"description": "Hex bolt set, 5-pack",
                                 "part_number": "AC-4417", "quantity": 20,
                                 "unit": "piece"}]}
        for apply in (False, True):
            with self.subTest(apply=apply):
                _checked, findings = ground_infer(extraction, source, apply=apply)
                self.assertEqual([f["code"] for f in findings],
                                 ["SET_MULTIPLIED"])
                evidence = findings[0]["evidence"]
                self.assertLess(len(evidence), 500)
                self.assertNotIn(" " * 1000, evidence)

    def test_a_redacted_link_with_a_long_path_does_not_bloat_the_evidence(self):
        path = "https://www.example.com/p/" + "a" * 50000
        source = path + "b" * 10
        extraction = {"items": [{"description": "Widget",
                                 "link": path + "[Telefon]" + "b" * 10}]}
        for apply in (False, True):
            with self.subTest(apply=apply):
                _checked, findings = ground_infer(extraction, source, apply=apply)
                restored = [f for f in findings if f["code"] == "REDACTED_LINK"]
                self.assertEqual([f["action"] for f in restored], ["restored" if apply else "suggested"])
                evidence = restored[0]["evidence"]
                # Two quotes, each capped at 200 characters, plus the fixed
                # sentence: neither the cut link nor the candidate may carry
                # the whole 50 KB path.
                self.assertNotIn("a" * 250, evidence)
                self.assertNotIn("b" * 250, evidence)
                self.assertLess(len(evidence), 600)


class GroundRedactedLinkTest(unittest.TestCase):
    """REDACTED_LINK: restore a product link that a redaction cut in two."""

    def test_ground_restores_redacted_link(self):
        extraction = {"items": [{
            "description": "Widget",
            "link": "https://de.aliexpress.com/item/1[Telefon]234567"}]}
        source = "Acme Tools\n%s\n" % ALIEXPRESS_LINK
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["REDACTED_LINK"])
        self.assertEqual(findings[0]["action"], "restored")
        self.assertEqual(findings[0]["item_index"], 0)
        self.assertEqual(findings[0]["field"], "link")
        self.assertEqual(findings[0]["old_value"],
                         "https://de.aliexpress.com/item/1[Telefon]234567")
        self.assertEqual(findings[0]["new_value"], ALIEXPRESS_LINK)
        self.assertEqual(checked["items"][0]["link"], ALIEXPRESS_LINK)
        # A restore is a data change, so the item is marked uncertain.
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_does_not_restore_the_other_products_link(self):
        # QA 3, reproduced: two eBay links share the prefix
        # "https://www.ebay.de/itm/1". Only the one that also ends with the
        # text after the placeholder is this item's own link.
        extraction = {"items": [{
            "description": "Widget",
            "link": "https://www.ebay.de/itm/1[Telefon]02101"}]}
        source = ("Acme Tools - order confirmation\n"
                  "https://www.ebay.de/itm/123456789001\n"
                  "https://www.ebay.de/itm/198765402101\n")
        checked, findings = ground_write(extraction, source)
        item = checked["items"][0]
        self.assertEqual(item["link"], "https://www.ebay.de/itm/198765402101")
        restored = [f for f in findings if f["code"] == "REDACTED_LINK"]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["action"], "restored")
        self.assertEqual(restored[0]["new_value"],
                         "https://www.ebay.de/itm/198765402101")
        self.assertTrue(item["uncertain"])
        # The other product is a link no item covers, so it is still added.
        added = [f for f in findings if f["code"] == "UNCOVERED_LINK"]
        self.assertEqual([f["new_value"] for f in added],
                         ["https://www.ebay.de/itm/123456789001"])

    def test_does_not_guess_between_two_aliexpress_links(self):
        # Every AliExpress item id starts with 1005..., so a cut after
        # ".../item/100500" matches both products. Nothing is rewritten.
        cut_link = "https://de.aliexpress.com/item/100500[Telefon]4567"
        extraction = {"items": [{"description": "Widget", "link": cut_link}]}
        source = ("Acme Tools - order confirmation\n"
                  "https://de.aliexpress.com/item/1005001234567\n"
                  "https://de.aliexpress.com/item/1005009994567\n")
        checked, findings = ground_write(extraction, source)
        item = checked["items"][0]
        self.assertEqual(item["link"], cut_link)
        flagged = [f for f in findings if f["code"] == "REDACTED_LINK"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["action"], "flagged")
        self.assertIsNone(flagged[0]["new_value"])
        self.assertEqual(flagged[0]["old_value"], cut_link)
        self.assertTrue(item["uncertain"])

    def test_ground_leaves_short_placeholder_alone(self):
        # A "[" inside the first 12 characters is part of the address, not a
        # cut in the middle of a product path.
        extraction = {"items": [{
            "description": "Widget",
            "link": "https://a[1].example.com/x"}]}
        source = "Acme Tools\nhttps://a.example.com/x\n"
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["link"], "https://a[1].example.com/x")

    def test_ground_flags_cut_link_when_no_source_link_matches(self):
        # Nothing in the source both starts with this prefix and ends with
        # this suffix, so the cut link cannot be proven. It stays as it is,
        # the item is marked uncertain and the finding says "flagged".
        extraction = {"items": [{
            "description": "Widget",
            "link": "https://de.aliexpress.com/item/9[Telefon]999999"}]}
        source = "Acme Tools\n%s\n" % EBAY_LINK
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings],
                         ["REDACTED_LINK", "UNCOVERED_LINK"])
        self.assertEqual(findings[0]["action"], "flagged")
        self.assertIsNone(findings[0]["new_value"])
        self.assertEqual(findings[0]["field"], "link")
        self.assertEqual(checked["items"][0]["link"],
                         "https://de.aliexpress.com/item/9[Telefon]999999")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_restores_a_cut_link_that_kept_its_query_string(self):
        # S13 from the 2026-10-27 recheck. Almost every AliExpress link carries
        # "?spm=...", and the extractor keeps it. The source link is compared
        # without its query string, so the cut link is still proven -- and the
        # restored link covers the source link, so UNCOVERED_LINK does not add
        # the same product a second time.
        source_link = ("https://de.aliexpress.com/item/1005001234567.html"
                       "?spm=a2g0o.detail.1000014.3")
        cut_link = ("https://de.aliexpress.com/item/1005[Telefon]001234567.html"
                    "?spm=a2g0o.detail.1000014.3")
        extraction = {"items": [{"description": "Widget", "link": cut_link}]}
        source = "Acme Tools - order confirmation\n%s\n" % source_link
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["REDACTED_LINK"])
        self.assertEqual(findings[0]["action"], "restored")
        self.assertEqual(findings[0]["new_value"],
                         "https://de.aliexpress.com/item/1005001234567.html")
        self.assertEqual(checked["items"][0]["link"],
                         "https://de.aliexpress.com/item/1005001234567.html")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_two_query_string_links_stay_flagged(self):
        # The prefix matches both products, so the cut link is not proof of
        # either one. The link stays as the extractor wrote it, query string
        # included, and both source links are uncovered.
        cut_link = ("https://de.aliexpress.com/item/100500[Telefon]4567.html"
                    "?spm=a2g0o.detail.1000014.3")
        extraction = {"items": [{"description": "Widget", "link": cut_link}]}
        source = ("Acme Tools - order confirmation\n"
                  "https://de.aliexpress.com/item/1005001234567.html"
                  "?spm=a2g0o.detail.1\n"
                  "https://de.aliexpress.com/item/1005009994567.html"
                  "?spm=a2g0o.detail.2\n")
        checked, findings = ground_write(extraction, source)
        item = checked["items"][0]
        self.assertEqual(item["link"], cut_link)
        flagged = [f for f in findings if f["code"] == "REDACTED_LINK"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["action"], "flagged")
        self.assertIsNone(flagged[0]["new_value"])
        self.assertEqual(flagged[0]["old_value"], cut_link)
        added = [f["new_value"] for f in findings
                 if f["code"] == "UNCOVERED_LINK"]
        self.assertEqual(added, [
            "https://de.aliexpress.com/item/1005001234567.html",
            "https://de.aliexpress.com/item/1005009994567.html"])
        self.assertTrue(item["uncertain"])


class GroundSetMultipliedTest(unittest.TestCase):
    """SET_MULTIPLIED: an M-pack ordered N times is N sets, not N*M pieces."""

    def test_an_id_longer_than_64_characters_is_not_used_for_line_matching(self):
        # The set check bounds its work per item by capping the id length it
        # matches on a line. An id of 70 characters is not used, so the line it
        # sits on grounds no item and the quantity stays as the extractor wrote
        # it. The bound is documented in SPEC-REFERENCE.md.
        long_id = "Q" * 70
        source = ("Acme Tools\n"
                  + long_id + " 2er-Pack\n"
                  "quantity: 4 x 2\n")
        extraction = {"items": [{"part_number": long_id, "quantity": 8}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 8)

    def test_an_id_of_64_characters_is_still_used_for_line_matching(self):
        # The cap is a bound, not a ban: an id at the limit still owns its line.
        long_id = "Q" * 64
        source = ("Acme Tools\n"
                  + long_id + " 2er-Pack\n"
                  "quantity: 4 x 2\n")
        extraction = {"items": [{"part_number": long_id, "quantity": 8}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 4)

    def test_ground_corrects_multiplied_set(self):
        extraction = {"items": [{
            "description": "Hex bolt set, 5-pack",
            "part_number": "AC-4417", "quantity": 140, "unit": "piece"}]}
        checked, findings = ground_write(extraction, SET_SOURCE)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["action"], "corrected")
        self.assertEqual(findings[0]["field"], "quantity")
        self.assertEqual(findings[0]["old_value"], 140)
        self.assertEqual(findings[0]["new_value"], 28)
        self.assertEqual(checked["items"][0]["quantity"], 28)
        self.assertEqual(checked["items"][0]["unit"], "set")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_a_link_with_a_query_string_grounds_its_own_line(self):
        # S4' of the second review. The set check indexed the item's link as the
        # caller wrote it and looked it up among the cleaned source links, so an
        # item whose only grounding is its link was not on the line when that
        # link carried a query string: the quantity stayed 140 with no finding.
        # The link is the only thing that grounds this item, because its line
        # holds no part number and the item has no description.
        link = ("https://de.aliexpress.com/item/1005001234567.html"
                "?spm=a2g0o.detail.0.0")
        source = "Pos. 1: Kabelbinder 5er-Pack %s\nMenge: 28 x 5" % link
        extraction = {"items": [{"link": link, "quantity": 140}]}

        checked, findings = ground_infer(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["action"], "suggested")
        self.assertEqual(findings[0]["old_value"], 140)
        self.assertEqual(findings[0]["new_value"], 28)
        self.assertEqual(checked["items"][0]["quantity"], 140)

        checked, findings = ground_infer(extraction, source, apply=True)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["action"], "corrected")
        self.assertEqual(checked["items"][0]["quantity"], 28)
        self.assertEqual(checked["items"][0]["unit"], "set")

    def test_ground_corrects_pack_of_form(self):
        source = "Acme Tools\nMenge: 12 x 5\nWasher pack of 5\n"
        extraction = {"items": [{"description": "Washer pack of 5",
                                 "quantity": 60}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 12)

    # S10 from the 2026-10-27 recheck. A supplier does not write "Menge:" only.
    # Each test below writes one label that real mail carries, and the set line
    # must be read the same way for every one of them: 140 pieces is 28 sets of
    # 5, not 140 pieces.
    def _assert_label_corrects_the_set(self, label):
        source = ("Acme Tools - order confirmation\n"
                  "%s 28 x 5\n"
                  "Hex bolt set, 5-pack, part AC-4417\n" % label)
        extraction = {"items": [{"part_number": "AC-4417",
                                 "description": "Hex bolt set, 5-pack",
                                 "quantity": 140}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"],
                         label)
        self.assertEqual(findings[0]["new_value"], 28, label)
        self.assertEqual(checked["items"][0]["quantity"], 28, label)
        self.assertEqual(checked["items"][0]["unit"], "set", label)

    def test_bestellmenge_label_marks_a_set_line(self):
        self._assert_label_corrects_the_set("Bestellmenge:")

    def test_liefermenge_label_marks_a_set_line(self):
        self._assert_label_corrects_the_set("Liefermenge:")

    def test_anzahl_label_marks_a_set_line(self):
        self._assert_label_corrects_the_set("Anzahl:")

    def test_qty_with_a_full_stop_marks_a_set_line(self):
        # "Qty." is the abbreviation with its full stop. The stop belongs to the
        # label and must not hide the number behind it.
        self._assert_label_corrects_the_set("Qty.")

    def test_menge_with_the_packaging_unit_marks_a_set_line(self):
        # "Menge (VE)" names the quantity in Verpackungseinheiten.
        self._assert_label_corrects_the_set("Menge (VE):")

    def test_pack_word_in_the_description_is_not_evidence(self):
        # The extractor wrote "5-pack"; the source never says pack. A pack size
        # the source does not state is not a correction, it is a guess.
        source = ("Acme Tools - order confirmation\n"
                  "quantity: 28 x 5\n"
                  "Hex bolt, part AC-4417\n")
        extraction = {"items": [{"description": "Hex bolt 5-pack",
                                 "part_number": "AC-4417", "quantity": 140}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 140)
        self.assertNotIn("uncertain", checked["items"][0])

    def test_corrects_only_once_per_item(self):
        # Two set lines both describe the same item. The first correction turns
        # 8 into 4; the second line must not turn 4 into 2.
        source = ("Acme Tools - order confirmation\n"
                  "quantity: 4 x 2\n"
                  "quantity: 2 x 2\n"
                  "Washer 12 mm, 2-pack, part WW-2201\n")
        extraction = {"items": [{"description": "Washer 12 mm, 2-pack",
                                 "part_number": "WW-2201", "quantity": 8}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(findings[0]["old_value"], 8)
        self.assertEqual(findings[0]["new_value"], 4)

    def test_recognises_every_pack_marker_form(self):
        # The pack marker must be in the source, in any of the ways a supplier
        # writes it.
        for marker in ("5 pack", "5-pack", "5er-Pack", "5er Pack"):
            source = ("Acme Tools - order confirmation\n"
                      "quantity: 28 x 5\n"
                      "Hex bolt set, %s, part AC-4417\n" % marker)
            extraction = {"items": [{"description": "Hex bolt set",
                                     "part_number": "AC-4417",
                                     "quantity": 140}]}
            checked, findings = ground_write(extraction, source)
            self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"],
                             marker)
            self.assertEqual(checked["items"][0]["quantity"], 28, marker)

    def test_ignores_pack_marker_of_another_size(self):
        # The source says 10-pack, the set line says 5. They are not the same
        # pack, so the item is left alone.
        source = ("Acme Tools - order confirmation\n"
                  "quantity: 28 x 5\n"
                  "Hex bolt set, 10-pack, part AC-4417\n")
        extraction = {"items": [{"description": "Hex bolt set",
                                 "part_number": "AC-4417", "quantity": 140}]}
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 140)

    def test_ground_ignores_pack_when_quantity_is_not_multiplied(self):
        extraction = {"items": [{
            "description": "Hex bolt set, 5-pack",
            "part_number": "AC-4417", "quantity": 28}]}
        checked, findings = ground_write(extraction, SET_SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 28)

    def test_a_set_line_does_not_correct_another_products_item(self):
        # QA 10 follow-up. The "2er-Pack" on the second line belongs to the
        # Kabelbinder, and the set line on that line belongs to it too. The
        # screws are 8 pieces, and no check may rewrite that from another
        # product's line.
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 8},
            {"part_number": "NW-22010", "quantity": 4, "unit": "set"},
        ]}
        source = ("AC-44170 Schraube 8 St\u00fcck\n"
                  "NW-22010 Kabelbinder 2er-Pack, Menge: 4 x 2")
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertNotIn("unit", checked["items"][0])
        self.assertNotIn("uncertain", checked["items"][0])

    def test_a_pack_marker_of_another_product_is_not_evidence(self):
        # QA 10 follow-up. The only pack marker in the mail sits on the
        # Kabelbinder line, two lines away from the screws. The set line names
        # no item of its own, so the screws keep their 8 pieces.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 8}]}
        source = ("AC-44170 Schraube\n"
                  "Menge: 4 x 2\n"
                  "NW-22010 Kabelbinder 2er-Pack, 1 St\u00fcck")
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertNotIn("uncertain", checked["items"][0])

    def test_a_pack_marker_on_the_items_own_line_still_corrects(self):
        # The pack marker and the part number are on one line, so the set line
        # below it belongs to this item.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 8}]}
        source = "AC-44170 2-pack\nMenge: 4 x 2"
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(checked["items"][0]["unit"], "set")

    def test_a_set_line_corrects_the_item_on_the_line_below(self):
        # The window works in both directions: the item line may follow the set
        # line, which is how a supplier writes a position.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 8}]}
        source = "Menge: 4 x 2\nAC-44170 2-pack"
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(checked["items"][0]["unit"], "set")

    def test_a_set_line_on_another_products_line_corrects_nothing(self):
        # S1 (a) from the 2026-10-27 recheck. Both lines carry a 2er-Pack, and
        # the set line sits on the Kabelbinder's own line. It belongs to that
        # product, so the screws keep their 8 pieces.
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 8},
            {"part_number": "NW-22010", "quantity": 4, "unit": "set"},
        ]}
        source = ("AC-44170 Schraube 2er-Pack, 8 St\u00fcck\n"
                  "NW-22010 Kabelbinder 2er-Pack, Menge: 4 x 2")
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertNotIn("unit", checked["items"][0])
        self.assertNotIn("uncertain", checked["items"][0])

    def test_a_set_line_corrects_the_product_on_its_own_line_only(self):
        # S1 (b): the set line sits between two products. It belongs to the
        # Kabelbinder on the line above it, never to the screws two lines below.
        extraction = {"items": [
            {"part_number": "NW-22010", "quantity": 3},
            {"part_number": "AC-44170", "quantity": 6},
        ]}
        source = ("NW-22010 Kabelbinder 2er-Pack\n"
                  "Menge: 3 x 2\n"
                  "AC-44170 Schraube M8, 6 Packungen \u00e0 2er-Pack\n")
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 3)
        self.assertEqual(checked["items"][1]["quantity"], 6)

    def test_a_description_word_does_not_own_an_item_whose_part_number_is_elsewhere(self):
        # S1 follow-up: the reviewer's input (a) WITH descriptions. The word
        # "Pack" in "Kabelbinder 2er-Pack" reached MATCH_RATIO for the screws,
        # so another product's line owned AC-44170 and its correct 6 became 3.
        extraction = {"items": [
            {"part_number": "NW-22010", "description": "Kabelbinder 2er-Pack",
             "quantity": 3, "unit": "set"},
            {"part_number": "AC-44170", "description": "Schraube M8 2er-Pack",
             "quantity": 6, "unit": "pack"},
        ]}
        source = ("NW-22010 Kabelbinder 2er-Pack\n"
                  "Menge: 3 x 2\n"
                  "AC-44170 Schraube M8, 6 Packungen \u00e0 2er-Pack\n")
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 3)
        self.assertEqual(checked["items"][1]["quantity"], 6)
        self.assertEqual(checked["items"][1]["unit"], "pack")

    def test_a_description_still_carries_an_item_with_no_id_in_the_source(self):
        # S1 follow-up: the description fallback is for the item whose part
        # number the extractor did not read. It must keep working.
        extraction = {"items": [
            {"part_number": "NW-22010", "quantity": 8},
            {"description": "Kabelbinder 2er-Pack", "quantity": 8},
        ]}
        source = ("NW-22010 Schraube M8, 8 St\u00fcck\n"
                  "Kabelbinder 2er-Pack\n"
                  "Menge: 4 x 2\n")
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["item_index"], 1)
        self.assertEqual(checked["items"][1]["quantity"], 4)
        self.assertEqual(checked["items"][0]["quantity"], 8)

    def test_a_block_of_set_lines_corrects_only_its_own_item(self):
        # S1 (c), rewritten for S1' of the second review. SPEC-REFERENCE.md says
        # a stack of set lines whose own lines ground nothing shares ONE owner
        # line: the line above the stack, else the line below it. This test
        # asserted the opposite split (each set line taking the product next to
        # it), which the docs never claimed. The stack belongs to NW-22010
        # above it, so only NW-22010 is corrected; AC-44170 keeps its 8.
        extraction = {"items": [
            {"part_number": "NW-22010", "quantity": 8},
            {"part_number": "AC-44170", "quantity": 8},
        ]}
        source = ("NW-22010 Kabelbinder 2er-Pack, 8 St\u00fcck\n"
                  "Menge: 3 x 2\n"
                  "Menge: 4 x 2\n"
                  "AC-44170 Schraube 2er-Pack, 8 St\u00fcck\n")
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["item_index"], 0)
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(checked["items"][1]["quantity"], 8)

    def test_a_stack_of_set_lines_corrects_only_the_item_above_it(self):
        # S1' from the 2026-10-27 second review. The per-line owner pass ran
        # before the block pass, so the second set line of the stack took the
        # position below it and both items were rewritten to 4 sets. A stack
        # shares the line above it, so only AC-44170 is corrected.
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 8, "unit": "piece"},
            {"part_number": "NW-22010", "quantity": 8, "unit": "piece"},
        ]}
        source = ("Pos. 1: AC-44170 Sechskantschraube M8, 2er-Pack\n"
                  "Bestellmenge: 4 x 2\n"
                  "Liefermenge: 4 x 2\n"
                  "Pos. 2: NW-22010 Kabelbinder 200 mm, 2er-Pack, 8 St\u00fcck")
        checked, findings = ground_infer(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["action"], "suggested")
        self.assertEqual(findings[0]["item_index"], 0)
        self.assertEqual(findings[0]["new_value"], 4)
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertEqual(checked["items"][1]["quantity"], 8)
        self.assertNotIn("uncertain", checked["items"][1])
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["action"], "corrected")
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(checked["items"][0]["unit"], "set")
        self.assertEqual(checked["items"][1]["quantity"], 8)
        self.assertEqual(checked["items"][1]["unit"], "piece")

    def test_a_stack_of_set_lines_does_not_reach_the_position_below(self):
        # S1' on the realistic German mail of the review. Pos. 40 carries a
        # stack of two set lines and Pos. 50 states "6 St\u00fcck" of a 2er-Pack.
        # The stack belongs to Pos. 40, so Pos. 50 keeps its 6 pieces.
        extraction = {"items": [
            {"part_number": "ZZ-40000",
             "description": "Profilzylinder 30/30 2er-Pack",
             "quantity": 6, "unit": "piece"},
            {"description": "D\u00fcbel 6 mm 2er-Pack", "quantity": 6,
             "unit": "piece"},
        ]}
        source = ("Pos. 40: ZZ-40000 Profilzylinder 30/30, 2er-Pack\n"
                  "Bestellmenge: 3 x 2\n"
                  "Liefermenge: 3 x 2\n"
                  "Pos. 50: D\u00fcbel 6 mm, 2er-Pack, 6 St\u00fcck\n")
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["item_index"], 0)
        self.assertEqual(checked["items"][0]["quantity"], 3)
        self.assertEqual(checked["items"][1]["quantity"], 6)
        self.assertNotIn("uncertain", checked["items"][1])

    def test_a_set_line_on_an_item_whose_quantity_does_not_parse_corrects_nothing(self):
        # S2' from the 2026-10-27 second review. The set line sits on the
        # AC-44170 line, whose quantity is "4 x 2" and does not parse. The
        # correctable filter then let the set line fall through to the item
        # above it, and NW-22010's correct 8 became 4 sets. The set line
        # belongs to the item on the set line, so no other item takes it.
        extraction = {"items": [
            {"part_number": "NW-22010", "quantity": 8, "unit": "piece"},
            {"part_number": "AC-44170", "quantity": "4 x 2", "unit": "piece"},
        ]}
        source = ("Pos. 1: NW-22010 Kabelbinder 200 mm, 2er-Pack, 8 St\u00fcck\n"
                  "Pos. 2: AC-44170 Sechskantschraube M8, 2er-Pack, "
                  "Menge: 4 x 2")
        checked, findings = ground_infer(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertEqual(checked["items"][1]["quantity"], "4 x 2")
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertEqual(checked["items"][0]["unit"], "piece")
        self.assertNotIn("uncertain", checked["items"][0])
        self.assertEqual(checked["items"][1]["quantity"], "4 x 2")

    def test_a_line_that_names_an_item_by_id_does_not_ground_another_by_description(self):
        # S3' from the 2026-10-27 second review. Line 1 names NW-22010 by its
        # part number and the second item shares one description word
        # ("Kabelbinder") with it, which is enough for a two-word description.
        # The set line on that line belongs to NW-22010, so the 8 pieces of
        # "Kabelbinder schwarz" stay 8.
        extraction = {"items": [
            {"part_number": "NW-22010", "description": "Kabelbinder 200 mm",
             "quantity": 4, "unit": "set"},
            {"description": "Kabelbinder schwarz", "quantity": 8,
             "unit": "piece"},
        ]}
        source = ("Pos. 1: NW-22010 Kabelbinder 200 mm, 2er-Pack, "
                  "Menge: 4 x 2\n"
                  "Pos. 2: Kabelbinder schwarz, 2er-Pack, 8 St\u00fcck")
        checked, findings = ground_infer(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(checked["items"][1]["quantity"], 8)
        self.assertNotIn("uncertain", checked["items"][1])
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(checked["items"][1]["quantity"], 8)
        self.assertEqual(checked["items"][1]["unit"], "piece")

    def test_a_placeholder_on_the_owner_line_takes_no_set_correction(self):
        # N3' from the 2026-10-27 second review. The appended item for the
        # uncovered link sits on the owner line and, with apply=True, already
        # holds the quantity 8 the link took from that line. The correction
        # loop walked every item the owner line grounds, so the placeholder was
        # rewritten to 4 sets as well. SPEC-REFERENCE.md says a placeholder this
        # module appended never takes a set line.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 8,
                                 "unit": "piece"}]}
        source = ("AC-44170 Schraube 2er-Pack, 8 St\u00fcck "
                  "https://www.amazon.de/dp/B0TESTAAA1\n"
                  "Menge: 4 x 2\n")
        checked, findings = ground_infer(extraction, source)
        set_findings = [f for f in findings if f["code"] == "SET_MULTIPLIED"]
        self.assertEqual([f["item_index"] for f in set_findings], [0])
        self.assertEqual(checked["items"][1].get("quantity"), None)
        checked, findings = ground_write(extraction, source)
        set_findings = [f for f in findings if f["code"] == "SET_MULTIPLIED"]
        self.assertEqual([f["item_index"] for f in set_findings], [0])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(checked["items"][1]["quantity"], 8)
        # The unit is the one the uncovered-link check wrote from the source,
        # not "set": the set check wrote nothing for the placeholder.
        self.assertEqual(checked["items"][1]["unit"], "piece")

    def test_the_evidence_names_the_unit_change(self):
        # S1: the finding says what the unit becomes, not only the number.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 8}]}
        _checked, findings = ground_write(extraction, "AC-44170 2-pack\nMenge: 4 x 2")
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertIn('unit "piece" -> "set"', findings[0]["evidence"])

    def test_a_non_string_other_id_does_not_crash_the_set_check(self):
        # S4: other_ids is a caller-supplied list, so an entry may be any type.
        # A dict is not an id: the set check skips it instead of raising
        # TypeError: unhashable type: 'dict'.
        extraction = {"items": [{
            "part_number": "AC-44170", "quantity": 10,
            "other_ids": [{"asin": "B0TESTAAA1"}, "EBAY:1"],
        }]}
        source = "AC-44170 Schraube 5er-Pack, Menge: 2 x 5\n"
        checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 2)
        self.assertEqual(checked["items"][0]["other_ids"],
                         [{"asin": "B0TESTAAA1"}, "EBAY:1"])

    def test_every_non_string_other_id_is_skipped(self):
        # A tuple, a list, a set and None are not ids either.
        for value in ({"asin": "B0TESTAAA1"}, ["EBAY:1"], (1, 2), {1, 2},
                      None, 4, b"EBAY:1"):
            extraction = {"items": [{"part_number": "AC-44170", "quantity": 10,
                                     "other_ids": [value]}]}
            checked, _findings = ground_write(extraction,
                                          "AC-44170 Schraube 5er-Pack, "
                                          "Menge: 2 x 5\n")
            self.assertEqual(checked["items"][0]["quantity"], 2,
                             "%r" % (value,))


class GroundVariantSplitTest(unittest.TestCase):
    """VARIANT_SPLIT: one part number under one quantity is one item."""

    def test_ground_merges_variant_into_first_item(self):
        extraction = {"items": [
            {"description": "Hex bolt set, part AC-4417",
             "part_number": "AC-4417", "quantity": 4},
            {"description": "IO link variant", "part_number": "ac 4417"},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual([f["code"] for f in findings], ["VARIANT_SPLIT"])
        self.assertEqual(findings[0]["action"], "merged")
        self.assertEqual(findings[0]["item_index"], 0)
        self.assertEqual(len(checked["items"]), 1)
        self.assertEqual(checked["items"][0]["note"], "variant: IO link variant")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_appends_variant_to_existing_note(self):
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4, "note": "checked with supplier"},
            {"description": "IO link variant", "part_number": "AC-4417"},
        ]}
        checked, _findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(checked["items"][0]["note"],
                         "checked with supplier · variant: IO link variant")

    def test_ground_keeps_variant_with_its_own_quantity(self):
        # A second position with its own quantity is a second order line, not a
        # variant of the first.
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "IO link variant", "part_number": "AC-4417",
             "quantity": 2},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 2)

    def test_a_line_with_an_unreadable_quantity_is_never_merged(self):
        # B2 from the 2026-10-27 recheck. "2 St." states a quantity that the
        # parser could not read, so the line was folded into item 0 and the
        # second position vanished from the quote.
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": "4 St.",
             "description": "Sechskantschraube M8 verzinkt"},
            {"part_number": "AC-44170", "quantity": "2 St.",
             "description": "Sechskantschraube M8 Edelstahl"},
        ]}
        source = ("Pos. 1  AC-44170 Sechskantschraube M8 verzinkt   4 St.\n"
                  "Pos. 2  AC-44170 Sechskantschraube M8 Edelstahl  2 St.\n")
        checked, findings = ground_write(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 2)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         ["4 St.", "2 St."])

    def test_ground_does_not_merge_short_part_number(self):
        # "M8" occurs on half the parts in the world. It proves nothing.
        extraction = {"items": [
            {"description": "Hex bolt", "part_number": "M8", "quantity": 4},
            {"description": "Washer", "part_number": "M8"},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\n")
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 2)

    def test_indexes_point_at_the_surviving_items(self):
        # Two part-number groups, each with its own variant line. Every merge
        # is recorded before a later pop, so a naive index goes stale.
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417"},
            {"description": "Washer 12 mm", "part_number": "WW-2201",
             "quantity": 2},
            {"description": "Washer 12 mm brass", "part_number": "WW-2201"},
        ]}
        source = "Acme Tools\npart AC-4417\npart WW-2201\n"
        checked, findings = ground_write(extraction, source)
        self.assertEqual(len(checked["items"]), 2)
        self.assertEqual([f["code"] for f in findings],
                         ["VARIANT_SPLIT", "VARIANT_SPLIT"])
        self.assertEqual([f["item_index"] for f in findings], [0, 1])
        self.assertEqual([f["input_index"] for f in findings], [1, 3])
        for finding in findings:
            self.assertLess(finding["item_index"], 2)
            item = checked["items"][finding["item_index"]]
            self.assertEqual(item["note"], finding["new_value"])
        self.assertEqual(checked["items"][0]["part_number"], "AC-4417")
        self.assertEqual(checked["items"][1]["part_number"], "WW-2201")

    def test_merge_moves_link_gtin_and_other_ids_to_the_survivor(self):
        # The variant line is the only place these values occur. A merge that
        # drops them loses data the extractor found.
        source = ("Acme Tools\npart AC-4417\nGTIN 2000000000008\n%s\n"
                  % AMAZON_LINK)
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "link": AMAZON_LINK, "gtin": "2000000000008",
             "other_ids": ["SUP-77"]},
        ]}
        checked, findings = ground_write(extraction, source)
        item = checked["items"][0]
        self.assertEqual(item["link"], AMAZON_LINK)
        self.assertEqual(item["gtin"], "2000000000008")
        self.assertEqual(item["other_ids"], ["SUP-77"])
        self.assertTrue(item["uncertain"])
        # S8: one finding per field the merge writes, and the input_index names
        # the merged line the value came from.
        merged = [f for f in findings if f["code"] == "VARIANT_SPLIT"]
        self.assertEqual([f["field"] for f in merged],
                         ["note", "link", "gtin", "other_ids"])
        self.assertEqual([f["input_index"] for f in merged], [1, 1, 1, 1])
        self.assertEqual([f["item_index"] for f in merged], [0, 0, 0, 0])
        self.assertEqual([f["new_value"] for f in merged[1:]],
                         [AMAZON_LINK, "2000000000008", "SUP-77"])
        self.assertEqual([f["old_value"] for f in merged[1:]],
                         [None, None, None])

    def test_a_moved_gtin_is_reported_as_a_write(self):
        # S8 input from the 2026-10-27 recheck: the gtin moved from input item
        # 1 onto item 0, and nothing recorded the write.
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4},
            {"part_number": "AC-44170", "gtin": "2000000000015"},
        ]}
        source = "Acme Tools\nPos. 1: AC-44170 Schraube M8, 4 St\u00fcck\n"
        checked, findings = ground_write(extraction, source)
        self.assertEqual(checked["items"][0]["gtin"], "2000000000015")
        writes = [f for f in findings
                  if f["code"] == "VARIANT_SPLIT" and f["field"] == "gtin"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["new_value"], "2000000000015")
        self.assertEqual(writes[0]["old_value"], None)
        self.assertEqual(writes[0]["input_index"], 1)
        self.assertEqual(writes[0]["item_index"], 0)

    def test_the_variants_own_note_is_appended_not_lost(self):
        extraction = {"items": [
            {"part_number": "AC-4417", "quantity": 4, "note": "checked"},
            {"part_number": "AC-4417", "note": "zinc plated"},
        ]}
        checked, _findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(checked["items"][0]["note"],
                         "checked · variant: AC4417 (zinc plated)")

    def test_a_differing_unit_is_reported(self):
        extraction = {"items": [
            {"part_number": "AC-4417", "quantity": 4, "unit": "piece"},
            {"part_number": "AC-4417", "unit": "set"},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(checked["items"][0]["unit"], "piece")
        unit = [f for f in findings if f["field"] == "unit"]
        self.assertEqual(len(unit), 1)
        self.assertEqual(unit[0]["old_value"], "set")
        self.assertIsNone(unit[0]["new_value"])
        self.assertEqual(unit[0]["input_index"], 1)

    def test_a_differing_price_is_reported(self):
        extraction = {"items": [
            {"part_number": "AC-4417", "quantity": 4, "price": "12,50"},
            {"part_number": "AC-4417", "price": "11,90"},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(checked["items"][0]["price"], "12,50")
        price = [f for f in findings if f["field"] == "price"]
        self.assertEqual(len(price), 1)
        self.assertEqual(price[0]["old_value"], "11,90")
        self.assertIsNone(price[0]["new_value"])

    def test_an_equal_unit_and_price_are_not_reported(self):
        # Only a value that differs is news.
        extraction = {"items": [
            {"part_number": "AC-4417", "quantity": 4, "unit": "piece",
             "price": "12,50"},
            {"part_number": "AC-4417", "unit": "Piece", "price": "12,50"},
        ]}
        _checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual([f["field"] for f in findings], ["note"])

    def test_merge_keeps_survivor_value_and_reports_the_variant(self):
        # Both lines carry a link and the two differ. The survivor keeps its
        # own; the variant's link is not lost, it is named in the finding.
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4, "link": AMAZON_LINK},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "link": EBAY_LINK},
        ]}
        source = "Acme Tools\npart AC-4417\n%s\n" % AMAZON_LINK
        checked, findings = ground_write(extraction, source)
        merged = [f for f in findings if f["code"] == "VARIANT_SPLIT"]
        self.assertEqual(checked["items"][0]["link"], AMAZON_LINK)
        self.assertTrue(checked["items"][0]["uncertain"])
        conflicts = [f for f in merged if f["field"] == "link"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["old_value"], EBAY_LINK)

    def test_a_conflict_finding_writes_nothing_so_it_has_no_new_value(self):
        # N4 from the 2026-10-27 recheck. The survivor keeps its own link, so
        # nothing was written: new_value must be None, and the value the
        # survivor kept is named in the evidence.
        extraction = {"items": [
            {"part_number": "AC-4417", "quantity": 4, "link": AMAZON_LINK},
            {"part_number": "AC-4417", "link": EBAY_LINK},
        ]}
        source = "Acme Tools\npart AC-4417\n%s\n" % AMAZON_LINK
        checked, findings = ground_write(extraction, source)
        conflict = [f for f in findings if f["field"] == "link"]
        self.assertEqual(len(conflict), 1)
        self.assertIsNone(conflict[0]["new_value"])
        self.assertEqual(conflict[0]["old_value"], EBAY_LINK)
        self.assertEqual(checked["items"][0]["link"], AMAZON_LINK)
        self.assertIn(AMAZON_LINK, conflict[0]["evidence"])

    def test_merge_keeps_gtin_conflict_in_the_finding(self):
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4, "gtin": "2000000000008"},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "gtin": "20000004"},
        ]}
        source = "Acme Tools\npart AC-4417\nGTIN 2000000000008 20000004\n"
        checked, findings = ground_write(extraction, source)
        self.assertEqual(checked["items"][0]["gtin"], "2000000000008")
        conflicts = [f for f in findings
                     if f["code"] == "VARIANT_SPLIT" and f["field"] == "gtin"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["old_value"], "20000004")

    def test_variant_notes_keep_input_order(self):
        # The supplier lists plain, zinc, brass. The note must read the same
        # way round as the source, not backwards.
        extraction = {"items": [
            {"description": "Hex nut M8 plain", "part_number": "NW-2202",
             "quantity": 6},
            {"description": "Hex nut M8 zinc", "part_number": "NW-2202"},
            {"description": "Hex nut M8 brass", "part_number": "NW-2202"},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart NW-2202\n")
        self.assertEqual(checked["items"][0]["note"],
                         "variant: Hex nut M8 zinc · variant: Hex nut M8 brass")
        self.assertEqual([f["input_index"] for f in findings], [1, 2])


class VariantMergeLossTest(unittest.TestCase):
    """S11': a merge never loses data (README.md, "Every change is reported").

    A value the survivor cannot take without loss blocks the merge. Both items
    stay, and one VARIANT_SPLIT finding says which value blocked it: action
    "flagged", field the blocking key, new_value None, old_value the variant's
    value that would have been lost.
    """

    SOURCE = "Acme Tools\nPos. 1: AC-44170 Schraube M8, 4 St\u00fcck\n"

    def _assert_blocked(self, extraction, field, variant_value):
        checked, findings = ground_infer(extraction, self.SOURCE)
        self.assertEqual(len(checked["items"]), 2)
        self.assertEqual(checked["items"][1].get(field), variant_value)
        self.assertNotIn("note", checked["items"][0])
        split = [f for f in findings if f["code"] == "VARIANT_SPLIT"]
        self.assertEqual(len(split), 1)
        finding = split[0]
        self.assertEqual(finding["action"], "flagged")
        self.assertEqual(finding["field"], field)
        self.assertEqual(finding["old_value"], variant_value)
        self.assertIsNone(finding["new_value"])
        self.assertEqual(finding["item_index"], 0)
        self.assertEqual(finding["input_index"], 1)
        return finding

    def test_a_link_that_differs_in_its_query_string_blocks_the_merge(self):
        # The two links clean to the same product, so the merge saw them as
        # equal and dropped the variant's own URL without a finding.
        survivor = ("https://de.aliexpress.com/item/1005001234567.html"
                    "?sku_id=12000000001")
        variant = ("https://de.aliexpress.com/item/1005001234567.html"
                   "?sku_id=12000000002")
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4, "link": survivor},
            {"part_number": "AC-44170", "link": variant},
        ]}
        finding = self._assert_blocked(extraction, "link", variant)
        self.assertIn("1005001234567", finding["evidence"])

    def test_a_gtin_that_is_not_a_string_blocks_the_merge(self):
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4},
            {"part_number": "AC-44170", "gtin": 2000000000015},
        ]}
        self._assert_blocked(extraction, "gtin", 2000000000015)

    def test_an_unknown_key_blocks_the_merge(self):
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4},
            {"part_number": "AC-44170", "colour": "silber"},
        ]}
        self._assert_blocked(extraction, "colour", "silber")

    def test_an_unknown_key_both_lines_share_does_not_block_the_merge(self):
        # A value both lines already hold is not a loss, so it does not stop
        # the merge: the survivor keeps it.
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4, "colour": "silber"},
            {"part_number": "AC-44170", "colour": "Silber"},
        ]}
        checked, findings = ground_infer(extraction, self.SOURCE)
        self.assertEqual(len(checked["items"]), 1)
        self.assertEqual(checked["items"][0]["colour"], "silber")
        self.assertEqual([f["action"] for f in findings], ["merged"])

    def test_the_note_finding_names_the_note_the_survivor_held(self):
        # N1' of the second review. The contract says old_value is what the key
        # held before this module touched it. The note finding always reported
        # None, even when the survivor already held a note.
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4,
             "note": "Lieferung KW 45"},
            {"part_number": "AC-44170", "description": "Schraube M8 silber"},
        ]}
        checked, findings = ground_infer(extraction, self.SOURCE)
        note = [f for f in findings if f["field"] == "note"]
        self.assertEqual(len(note), 1)
        self.assertEqual(note[0]["old_value"], "Lieferung KW 45")
        self.assertEqual(note[0]["new_value"], "variant: Schraube M8 silber")
        self.assertEqual(checked["items"][0]["note"],
                         "Lieferung KW 45 · variant: Schraube M8 silber")

    def test_the_note_finding_has_no_old_value_when_the_survivor_had_no_note(self):
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4},
            {"part_number": "AC-44170", "description": "Schraube M8 silber"},
        ]}
        _checked, findings = ground_infer(extraction, self.SOURCE)
        note = [f for f in findings if f["field"] == "note"]
        self.assertEqual(len(note), 1)
        self.assertIsNone(note[0]["old_value"])

    def test_a_variant_with_no_unmergeable_value_is_still_merged(self):
        # The guard must not stop the ordinary merge: a description is the
        # variant's label and a note is folded into the survivor's note.
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 4},
            {"part_number": "AC-44170", "description": "Schraube M8 silber",
             "note": "zinc plated"},
        ]}
        checked, findings = ground_infer(extraction, self.SOURCE)
        self.assertEqual(len(checked["items"]), 1)
        self.assertEqual([f["action"] for f in findings], ["merged"])
        self.assertEqual(checked["items"][0]["note"],
                         "variant: Schraube M8 silber (zinc plated)")


class IndexDivergenceTest(unittest.TestCase):
    """S9: after a merge, every later finding can name two different indexes.

    README.md and SPEC-REFERENCE.md said that VARIANT_SPLIT is the one code
    where the two indexes differ. That is false: a merge removes an item, so
    every finding recorded after it can carry two different numbers.
    """

    def test_a_later_finding_after_a_merge_names_two_different_indexes(self):
        extraction = {"items": [
            {"part_number": "AC-4417", "quantity": 4},
            {"part_number": "AC-4417"},
            {"part_number": "ZZ-9999", "quantity": 2},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(len(checked["items"]), 2)
        flagged = [f for f in findings if f["code"] == "UNGROUNDED_ID"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["item_index"], 1)
        self.assertEqual(flagged[0]["input_index"], 2)

    def test_the_docs_do_not_call_variant_split_the_only_divergent_code(self):
        for name in ("README.md", "SPEC-REFERENCE.md"):
            with open(os.path.join(REPO, name)) as handle:
                text = handle.read()
            self.assertNotIn("is the one code where the two indexes differ",
                             text, name)
            self.assertIn("after a merge, every later finding can", text, name)


class GroundLinkContractTest(unittest.TestCase):
    """The new checks keep the promises ground() already made."""

    def test_ground_does_not_mutate_input_with_links_and_variants(self):
        extraction = {"items": [
            {"description": "Hex bolt set, 5-pack", "part_number": "AC-4417",
             "quantity": 140},
            {"description": "IO link variant", "part_number": "AC-4417"},
            {"description": "Widget",
             "link": "https://de.aliexpress.com/item/1[Telefon]234567"},
        ]}
        source = ("Acme Tools - order confirmation\n"
                  "4x https://www.amazon.de/dp/B0TEST1234\n"
                  "quantity: 28 x 5\n"
                  "%s\n" % ALIEXPRESS_LINK)
        snapshot = copy.deepcopy(extraction)
        checked, _findings = ground_write(extraction, source)
        self.assertEqual(extraction, snapshot)
        self.assertIsNot(checked, extraction)
        self.assertIsNot(checked["items"][0], extraction["items"][0])

    def test_finding_shape_for_link_checks(self):
        extraction = {"items": [{
            "description": "Hex bolt set, 5-pack",
            "part_number": "AC-4417", "quantity": 140}]}
        source = ("Acme Tools\n"
                  "4x https://www.amazon.de/dp/B0TEST1234\n"
                  "quantity: 28 x 5\n"
                  "Hex bolt set, 5-pack, part AC-4417\n")
        _checked, findings = ground_write(extraction, source)
        self.assertEqual([f["code"] for f in findings],
                         ["UNCOVERED_LINK", "SET_MULTIPLIED"])
        for finding in findings:
            self.assertEqual(set(finding), FINDING_KEYS)
            self.assertIsInstance(finding["evidence"], str)
            self.assertIsInstance(finding["action"], str)
            self.assertTrue(finding["item_index"] is None
                            or isinstance(finding["item_index"], int))


class QuantityFormatTest(unittest.TestCase):
    """QA 5: a quantity counts whole pieces, so a 3-digit group is not a fraction.

    The rule, documented in README.md and SPEC-REFERENCE.md: a separator with
    exactly three digits behind it groups thousands ("1.000 Stück" and
    "1,000 x" are both 1000); one, two, four or more digits behind it are a
    fraction ("1,5" is 1.5, "12,50" is 12.5). When both separators occur, the
    last one is the decimal separator.
    """

    def test_dot_group_with_a_unit_word_is_a_thousand(self):
        # German writes 1000 pieces as "1.000 Stück". One piece is wrong.
        source = ("Acme Tools - order confirmation\n"
                  "1.000 St\u00fcck\n"
                  "https://www.amazon.de/dp/B0TEST1234\n")
        checked, _findings = ground_write({"items": []}, source)
        self.assertEqual(checked["items"][0]["quantity"], 1000)

    def test_comma_group_is_a_thousand(self):
        source = ("Acme Tools - order confirmation\n"
                  "1,000 x\n"
                  "https://www.amazon.de/dp/B0TEST1234\n")
        checked, _findings = ground_write({"items": []}, source)
        self.assertEqual(checked["items"][0]["quantity"], 1000)

    def test_three_digits_behind_a_separator_are_a_group(self):
        self.assertEqual(G._to_number("1.000"), 1000)
        self.assertEqual(G._to_number("1,000"), 1000)
        self.assertEqual(G._to_number("1.000.000"), 1000000)
        self.assertEqual(G._to_number("1,000 St\u00fcck"), 1000)

    def test_one_or_two_digits_behind_a_separator_are_a_fraction(self):
        self.assertEqual(G._to_number("1,5"), 1.5)
        self.assertEqual(G._to_number("12,50"), 12.5)
        self.assertEqual(G._to_number("1.5"), 1.5)

    def test_both_separators_make_the_last_one_the_decimal(self):
        self.assertEqual(G._to_number("1.234,56"), 1234.56)
        self.assertEqual(G._to_number("1,234.56"), 1234.56)

    def test_a_price_on_the_line_above_is_never_a_quantity(self):
        # A price sits where a quantity would sit: on its own line above a
        # link. It is money, not a count.
        for price in ("12,50 \u20ac", "12,50", "EUR 12,50", "Preis 12,50",
                      "total 12,50"):
            source = ("Acme Tools\n%s\nhttps://www.amazon.de/dp/B0TEST1234\n"
                      % price)
            checked, _findings = ground_write({"items": []}, source)
            self.assertIsNone(checked["items"][0]["quantity"], price)

    def test_a_price_value_is_not_a_quantity(self):
        self.assertIsNone(G._to_number("12,50 \u20ac"))

    def test_two_links_on_one_line_each_take_their_own_quantity(self):
        source = ("Acme Tools\n"
                  "4x https://www.amazon.de/dp/B0TEST1234 "
                  "2x https://www.ebay.de/itm/123456789001\n")
        checked, _findings = ground_write({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [4, 2])

    def test_one_quantity_for_two_links_leaves_the_second_without(self):
        source = ("Acme Tools\n"
                  "4x https://www.amazon.de/dp/B0TEST1234 "
                  "https://www.ebay.de/itm/123456789001\n")
        checked, _findings = ground_write({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [4, None])

    def test_pcs_string_quantity_keeps_the_variant_line(self):
        # A line with a quantity of its own is a second position, not a
        # variant. "2 pcs" is a quantity, so the line must survive.
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "quantity": "2 pcs"},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 2)
        self.assertEqual(G._to_number("2 pcs"), 2)
        self.assertEqual(G._to_number("2 St\u00fcck"), 2)

    def test_german_and_english_unit_words_parse(self):
        # B2: a supplier writes "St.", "Stck.", "ST" and "Stueck". Each is a
        # piece word, not part of the number.
        for text in ("2 St.", "2 Stck.", "2 ST", "2 Stueck", "2 St\u00fcck",
                     "2 stk.", "2 pcs", "2 pieces", "2 units", "2 x"):
            self.assertEqual(G._to_number(text), 2, text)

    def test_a_unit_word_this_module_cannot_name_is_not_a_quantity(self):
        # S6': "VE", "Paar", "Pack" and "kg" do not name a count of pieces, so
        # a value that carries one holds no quantity this module can read.
        # Before, every one of them read as a number of pieces.
        for text in ("2 VE", "2 Paar", "2 Pack", "2,5 kg"):
            self.assertIsNone(G._to_number(text), text)

    def test_decimal_quantity_keeps_the_variant_line(self):
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "quantity": Decimal("2")},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 2)
        self.assertEqual(G._to_number(Decimal("2")), 2)
        self.assertEqual(G._to_number(Decimal("2.5")), 2.5)

    def test_a_huge_int_quantity_does_not_raise(self):
        # float(10 ** 400) raises OverflowError on every Python.
        self.assertIsNone(G._to_number(10 ** 400))
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "quantity": 10 ** 400},
        ]}
        checked, findings = ground_write(extraction, "Acme Tools\npart AC-4417\n")
        # An out-of-range number is not a quantity, but the field is not empty.
        # A line that states a quantity is a second position, so it survives:
        # VARIANT_SPLIT must never delete a line it cannot read (B2).
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 2)

    def test_a_five_thousand_digit_run_does_not_raise(self):
        # int() of a long digit string raises ValueError on Python 3.11+.
        run = "9" * 5000
        self.assertIsNone(G._to_number(run))
        self.assertIsNone(G._to_number(run + " pcs"))
        source = "Acme Tools\nquantity: %s x 5\n" % run
        checked, findings = ground_write({"items": []}, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"], [])

    def test_a_quantity_above_the_cap_is_ignored(self):
        self.assertEqual(G._to_number(10 ** 9), 10 ** 9)
        self.assertIsNone(G._to_number(10 ** 9 + 1))
        self.assertIsNone(G._to_number("1" + "0" * 8 + "1"))
        self.assertIsNone(G._to_number("9" * 13))


class DateIsNotAQuantityTest(unittest.TestCase):
    """A date on its own line is never a quantity (B2').

    `_split_quantity_text` joined any repeated separator, so "26.10.2026" read
    as 26102026 and a delivery date alone on a line became an order of 26
    million pieces. A repeated separator groups thousands only when the first
    group has 1 to 3 digits and every later group has exactly 3; anything else
    is not a number.
    """

    A = "https://www.amazon.de/dp/B0TESTAAA1"

    def _assert_no_quantity(self, source):
        # Default mode: nothing is written, so no quantity is suggested.
        checked, findings = ground_infer({"items": []}, source)
        self.assertIsNone(checked["items"][0].get("quantity"))
        self.assertEqual(
            [finding for finding in findings if finding["field"] == "quantity"],
            [])
        # apply=True: the value written is None.
        checked, findings = ground_write({"items": []}, source)
        self.assertIsNone(checked["items"][0].get("quantity"))

    def test_a_delivery_date_above_a_link_is_not_a_quantity(self):
        self._assert_no_quantity("Liefertermin:\n26.10.2026\n%s\n" % self.A)

    def test_a_delivery_date_below_a_link_is_not_a_quantity(self):
        self._assert_no_quantity(
            "Bitte zus\u00e4tzlich bestellen:\n%s\n26.10.2026\n" % self.A)

    def test_a_date_is_not_a_number(self):
        for text in ("26.10.2026", "1.5.2026", "13.11.2026"):
            self.assertIsNone(G._to_number(text), text)

    def test_a_real_grouped_number_keeps_its_value(self):
        cases = [("1.000.000", 1000000), ("1.000", 1000), ("1,5", 1.5),
                 ("12,50", 12.5), ("1.234,56", 1234.56)]
        for text, expected in cases:
            self.assertEqual(G._to_number(text), expected, text)

    def test_a_leading_zero_never_groups_thousands(self):
        self.assertEqual(G._to_number("0,250"), 0.25)


class GroundInputTypeTest(unittest.TestCase):
    """QA 13: a wrong input type is a caller bug, not an empty clean result."""

    def test_ground_rejects_an_extraction_that_is_not_a_dict(self):
        for bad in ([], (), "items", None, 4, b"{}"):
            with self.assertRaises(TypeError) as caught:
                ground_write(bad, "")
            message = str(caught.exception)
            self.assertIn("extraction", message)
            self.assertIn(type(bad).__name__, message)

    def test_ground_rejects_items_that_is_not_a_list(self):
        for bad in ((), "items", {}, None, 4):
            with self.assertRaises(TypeError) as caught:
                ground_write({"items": bad}, "")
            message = str(caught.exception)
            self.assertIn("items", message)
            self.assertIn(type(bad).__name__, message)

    def test_ground_rejects_a_source_that_is_not_a_str(self):
        for bad in (b"text", None, 4, ["text"]):
            with self.assertRaises(TypeError) as caught:
                ground_write({"items": []}, bad)
            message = str(caught.exception)
            self.assertIn("source_text", message)
            self.assertIn(type(bad).__name__, message)

    def test_ground_still_tolerates_a_missing_items_key(self):
        checked, findings = ground_write({}, "Acme Tools\n")
        self.assertEqual(checked, {"items": []})
        self.assertEqual(findings, [])


class ChangelogFixRoundTwoTest(unittest.TestCase):
    """The changelog names every behaviour that fix round 2 changed.

    Fix round 2 (the cards of 2026-10-27) changed six checks. A user who reads
    only the changelog must find each change, so every item of the recheck
    carries one sentence with a phrase this test pins.
    """

    # (item code from the 2026-10-27 recheck, a phrase its entry must hold)
    ITEMS = (
        ("B1", "no longer shifts the id index"),
        ("B2", "no longer deletes an order line it cannot read"),
        ("S1", "corrects only the item the set line belongs to"),
        ("S2", "index their work once"),
        ("S3", "64 characters"),
        ("S4", "non-string `other_ids` entry"),
        ("S5", "only the run that follows the keyword"),
        ("S6", "the item its line names"),
        ("S7", "documented joiners"),
        ("S8", "one finding per field"),
        ("S9", "`item_index` and `input_index` can differ"),
        ("S10", "Bestellmenge"),
        ("S11", "its own quantity line"),
        ("S12", "Strichcodenummer"),
        ("S13", "without its query string"),
        ("S14", "could not fail"),
        ("N4", "writes nothing"),
    )

    def test_every_item_of_fix_round_two_has_an_entry(self):
        with open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8") as handle:
            changelog = re.sub(r"\s+", " ", handle.read())
        missing = [code for code, phrase in self.ITEMS
                   if phrase not in changelog]
        self.assertEqual(missing, [], "no changelog entry for: %s"
                         % ", ".join(missing))


class ChangelogFixRoundThreeTest(unittest.TestCase):
    """Every item of fix round 3 has one line and a commit or an open reason.

    The round has 20 items, written with a prime to keep them apart from the
    round-2 codes. A reader of the changelog must be able to find, for each one,
    the commit that fixed it, or the reason it is still open.
    """

    # (item code, one phrase the line's text must hold)
    ITEMS = (
        ("B1′", "marketplace id"),
        ("B2′", "date"),
        ("S1′", "stack of set lines"),
        ("S2′", "unreadable quantity"),
        ("S3′", "grounds no other by description"),
        ("S4′", "query string"),
        ("S5′", "claims the line below"),
        ("S6′", "source's unit"),
        ("S7′", "verbatim"),
        ("S8′", "labels are read after a space"),
        ("S9′", "bare GTIN line"),
        ("S10′", "printed elsewhere"),
        ("S11′", "merge never loses data"),
        ("S12′", "evidence starts with the GTIN"),
        ("N1′", "note the survivor held"),
        ("N2′", "quoted value"),
        ("N3′", "placeholder on the owner line"),
        ("N4′", "tests that can fail"),
        ("N5′", "line-form scan"),
        ("N6′", "ligature"),
    )

    def test_every_item_has_one_line_with_a_commit_or_an_open_reason(self):
        with open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        for code, phrase in self.ITEMS:
            starts = [line for line in lines
                      if line.startswith("- %s — " % code)]
            self.assertEqual(len(starts), 1,
                             "%s needs exactly one index line" % code)
            line = starts[0]
            self.assertIn(phrase, line, "%s: %s" % (code, line))
            has_commit = re.search(r"`[0-9a-f]{7}`", line) is not None
            has_open = "open — " in line
            self.assertTrue(has_commit or has_open,
                            "%s names no commit and no open reason" % code)


class PublicGtinTest(unittest.TestCase):
    """Every GTIN a published file shows is an invented prefix-2 number.

    The repo goes public. A GTIN on a real GS1 prefix names a real product, so
    an example would point at a real catalogue entry. The GS1 prefix 2 is
    reserved for internal use, so no real product carries these numbers.

    Three numbers stay: they are the examples that prove a valid check digit is
    not enough to make a GTIN. "12345670" is a telephone number, "20261023" is
    an order number, and "123456789012" is an eBay item id.

    A GTIN-14 form of a prefix-2 number starts with zeros (00200000000011), so
    the test strips the padding before it reads the prefix.
    """

    NOT_A_GTIN = frozenset({"12345670", "20261023", "123456789012"})

    # The files a reader can open. A GTIN in one of them is a public example.
    FILES = (
        "harness_grounding.py",
        "README.md",
        "SPEC-REFERENCE.md",
        "CHANGELOG.md",
        "OVERVIEW.md",
        "bench/grounding/cases.json",
        "examples/grounding_example.py",
        "tests/test_grounding.py",
        "tests/test_grounding_bench.py",
        "tests/test_harness_lint.py",
    )

    # A digit run a reader could take for a GTIN, and a URL, because a digit run
    # inside a link is a marketplace id, not a GTIN.
    RUN = re.compile(r"(?<!\d)\d{8,14}(?!\d)")
    URL = re.compile(r"https?://\S+")

    def _gtins(self, path):
        """Yield (line number, digit run) for every GTIN-shaped run in path."""
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        urls = [(match.start(), match.end())
                for match in self.URL.finditer(text)]
        for match in self.RUN.finditer(text):
            if any(start <= match.start() and match.end() <= end
                   for start, end in urls):
                continue
            code = match.group(0)
            if len(code) not in G.GTIN_LENGTHS or not G.gtin_valid(code):
                continue
            yield text[:match.start()].count("\n") + 1, code

    def test_every_gtin_in_a_published_file_uses_the_reserved_prefix(self):
        offenders = []
        for name in self.FILES:
            path = os.path.join(REPO, name)
            for line, code in self._gtins(path):
                if code in self.NOT_A_GTIN:
                    continue
                if not code.lstrip("0").startswith("2"):
                    offenders.append("%s:%d %s" % (name, line, code))
        self.assertEqual(offenders, [], "GTINs off the reserved prefix 2:\n  "
                         + "\n  ".join(offenders))

    def test_the_three_numbers_that_are_not_gtins_are_still_used(self):
        # The allowlist above is not a dumping ground: each entry must still
        # appear in a file, so a stale exemption fails here instead of hiding a
        # real GTIN later.
        text = ""
        for name in self.FILES:
            with open(os.path.join(REPO, name), encoding="utf-8") as handle:
                text += handle.read()
        for code in self.NOT_A_GTIN:
            self.assertIn(code, text)


class SuggestOnlyDefaultTest(unittest.TestCase):
    """Fix round 3, card 3.1: the three value-writing checks only suggest.

    ground() writes no extraction value for SET_MULTIPLIED, MISSING_GTIN and the
    quantity of an UNCOVERED_LINK item unless the caller passes apply=True. The
    finding is then "suggested": its new_value is the value apply=True would
    write, and nothing was written. A producer can show it to a human and decide.

    Everything those checks do that writes no extraction value still happens in
    both modes: the item is marked uncertain, the placeholder item is appended,
    and a GTIN no item can take is still reported.

    The two modes are tested on the same input, because a suggestion that does
    not name the value the write mode writes is a wrong suggestion.
    """

    SET_ITEMS = {"items": [{"description": "Hex bolt set, 5-pack",
                            "part_number": "AC-4417", "quantity": 140,
                            "unit": "piece"}]}
    GTIN_ITEMS = {"items": [{"description": "Widget assembly",
                             "quantity": 4, "unit": "piece"}]}
    LINK_ITEMS = {"items": []}

    @staticmethod
    def _without_uncertain(extraction):
        """Return a deep copy with every "uncertain" flag removed.

        ground() marks an item uncertain in both modes. That flag is the
        module's own bookkeeping, not an extraction value, so the comparison
        that proves "nothing was written" ignores it.
        """
        flat = copy.deepcopy(extraction)
        for item in flat["items"]:
            item.pop("uncertain", None)
        return flat

    def test_the_default_leaves_a_multiplied_set_as_the_extractor_wrote_it(self):
        checked, findings = ground_infer(self.SET_ITEMS, SET_SOURCE)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        finding = findings[0]
        self.assertEqual(finding["action"], "suggested")
        self.assertEqual(finding["field"], "quantity")
        self.assertEqual(finding["old_value"], 140)
        self.assertEqual(finding["new_value"], 28)
        self.assertEqual(finding["item_index"], 0)
        self.assertEqual(finding["input_index"], 0)
        # The quantity and the unit are exactly what the caller passed in.
        self.assertEqual(self._without_uncertain(checked),
                         self._without_uncertain(self.SET_ITEMS))
        self.assertEqual(checked["items"][0]["quantity"], 140)
        self.assertEqual(checked["items"][0]["unit"], "piece")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_apply_true_corrects_the_multiplied_set(self):
        checked, findings = ground_infer(self.SET_ITEMS, SET_SOURCE, apply=True)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["action"], "corrected")
        self.assertEqual(findings[0]["new_value"], 28)
        self.assertEqual(checked["items"][0]["quantity"], 28)
        self.assertEqual(checked["items"][0]["unit"], "set")

    def test_the_default_leaves_a_missing_gtin_unattached(self):
        checked, findings = ground_infer(self.GTIN_ITEMS, ONE_GTIN_SOURCE)
        self.assertEqual([f["code"] for f in findings], ["MISSING_GTIN"])
        finding = findings[0]
        self.assertEqual(finding["action"], "suggested")
        self.assertEqual(finding["field"], "gtin")
        self.assertEqual(finding["new_value"], "2000000000008")
        self.assertEqual(finding["item_index"], 0)
        self.assertEqual(finding["input_index"], 0)
        self.assertNotIn("gtin", checked["items"][0])
        self.assertEqual(self._without_uncertain(checked),
                         self._without_uncertain(self.GTIN_ITEMS))
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_apply_true_attaches_the_missing_gtin(self):
        checked, findings = ground_infer(self.GTIN_ITEMS, ONE_GTIN_SOURCE, apply=True)
        self.assertEqual(findings[0]["action"], "attached")
        self.assertEqual(checked["items"][0]["gtin"], "2000000000008")

    def test_the_default_appends_the_link_without_its_quantity(self):
        checked, findings = ground_infer(self.LINK_ITEMS, LINK_SOURCE)
        # The item, then the quantity it suggests, then the next link.
        self.assertEqual([f["code"] for f in findings],
                         ["UNCOVERED_LINK"] * 4)
        self.assertEqual([f["action"] for f in findings],
                         ["added", "suggested", "added", "suggested"])
        self.assertEqual([f["item_index"] for f in findings], [0, 0, 1, 1])
        added, suggested = findings[0], findings[1]
        self.assertEqual(added["field"], "link")
        self.assertEqual(added["new_value"], AMAZON_LINK)
        self.assertIsNone(added["input_index"])
        self.assertEqual(suggested["field"], "quantity")
        self.assertIsNone(suggested["old_value"])
        self.assertEqual(suggested["new_value"], 4)
        self.assertIsNone(suggested["input_index"])
        # The evidence names the unit word apply=True writes.
        self.assertIn("piece", suggested["evidence"])
        item = checked["items"][0]
        self.assertEqual(item["link"], AMAZON_LINK)
        self.assertEqual(item["other_ids"], ["ASIN:B0TEST1234"])
        self.assertNotIn("quantity", item)
        self.assertNotIn("unit", item)
        self.assertTrue(item["uncertain"])

    def test_apply_true_appends_the_link_with_its_quantity(self):
        checked, findings = ground_infer(self.LINK_ITEMS, LINK_SOURCE, apply=True)
        self.assertEqual([f["code"] for f in findings],
                         ["UNCOVERED_LINK", "UNCOVERED_LINK"])
        self.assertEqual([f["action"] for f in findings], ["added", "added"])
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [4, 2])
        self.assertEqual([item["unit"] for item in checked["items"]],
                         ["piece", "piece"])

    def test_a_reported_gtin_is_the_same_in_both_modes(self):
        # No item can take this GTIN, so neither mode writes anything and the
        # finding is "reported" in both. The card leaves it unchanged.
        extraction = {"items": [{"description": "Garden hose"}]}
        default, default_findings = ground_infer(extraction, ONE_GTIN_SOURCE)
        applied, applied_findings = ground_infer(extraction, ONE_GTIN_SOURCE,
                                             apply=True)
        self.assertEqual(default, applied)
        self.assertEqual(default_findings, applied_findings)
        self.assertEqual(default_findings[0]["action"], "reported")

    def test_a_second_gtin_on_one_item_is_reported_in_both_modes(self):
        # One item takes at most one GTIN. In the default mode the first one is
        # only suggested, and the finding for the second says so: nothing was
        # attached, so the note may not claim a write.
        source = ("Northwind Supply\n"
                  "4 x Hex bolt M8  EAN 2000000000008  EAN 20000004\n")
        extraction = {"items": [{"description": "Hex bolt M8"}]}
        checked, findings = ground_infer(extraction, source)
        self.assertEqual([f["action"] for f in findings],
                         ["suggested", "reported"])
        self.assertIn("suggestion", findings[1]["evidence"])
        self.assertNotIn("gtin", checked["items"][0])

    def test_apply_must_be_a_bool(self):
        for bad in ("yes", 1, None, [], {}):
            with self.assertRaises(TypeError) as caught:
                ground_infer({"items": []}, "", apply=bad)
            message = str(caught.exception)
            self.assertIn("apply", message)
            self.assertIn(type(bad).__name__, message)

    def test_apply_is_keyword_only(self):
        # A third positional argument would be read as a flag by accident.
        with self.assertRaises(TypeError):
            ground_infer({"items": []}, "", True)


class FlagOnlyDefaultTest(unittest.TestCase):
    """Round 4, card 4.2: the flag-only default, and the infer=True opt-in.

    The release gate (bench/gate) measures four checks that propose an owner, a
    quantity or a merge the source does not prove. On 2026ab9 the gate counted
    427 wrong MISSING_GTIN owners, 35 wrong UNCOVERED_LINK quantities, 176 wrong
    SET_MULTIPLIED owners and 8 wrong VARIANT_SPLIT merges. Those four are
    flag-only by default: they report or flag, and they write nothing. The old
    inference stays behind infer=True, for measurement.

    REDACTED_LINK measured 0 wrong, so it keeps its suggestion: the default
    suggests the link, apply=True restores it.

    Every test calls G.ground() directly, never the ground_infer() partial, and
    runs both modes unless the test says otherwise.
    """

    def test_a_missing_gtin_is_reported_and_never_attached(self):
        extraction = {"items": [{"part_number": "AC-4417", "quantity": 4}]}
        source = "Pos. 1: AC-4417 Bohrschrauber\nEAN: 2000000000015\n"
        for apply in (False, True):
            with self.subTest(apply=apply):
                checked, findings = G.ground(extraction, source, apply=apply)
                missing = [f for f in findings if f["code"] == "MISSING_GTIN"]
                self.assertEqual(len(missing), 1)
                finding = missing[0]
                self.assertEqual(finding["action"], "reported")
                self.assertIsNone(finding["item_index"])
                self.assertIsNone(finding["input_index"])
                self.assertIsNone(finding["new_value"])
                # The GTIN comes first, so the 200-character quote cut can
                # never drop the number the finding is about.
                self.assertTrue(finding["evidence"].startswith(
                    "GTIN 2000000000015: "))
                item = checked["items"][0]
                self.assertNotIn("gtin", item)
                self.assertNotIn("uncertain", item)

    def test_an_uncovered_link_is_added_without_a_quantity(self):
        source = "2 Stück https://www.amazon.de/dp/B0TEST1234\n"
        for apply in (False, True):
            with self.subTest(apply=apply):
                checked, findings = G.ground({}, source, apply=apply)
                self.assertEqual([f["action"] for f in findings], ["added"])
                self.assertNotIn("suggested",
                                 [f["action"] for f in findings])
                # The placeholder carries the link and its marketplace id and
                # nothing else: no quantity, no unit, no invented description.
                self.assertEqual(set(checked["items"][0]),
                                 {"link", "other_ids", "uncertain"})

    def test_a_multiplied_set_is_reported_and_not_corrected(self):
        extraction = {"items": [{"part_number": "AC-4417",
                                 "description": "Kabelbinder 2er-Pack",
                                 "quantity": 8, "unit": "piece"}]}
        source = "AC-4417 Kabelbinder 2er-Pack\nMenge: 4 x 2\n"
        for apply in (False, True):
            with self.subTest(apply=apply):
                checked, findings = G.ground(extraction, source, apply=apply)
                self.assertEqual(len(findings), 1)
                finding = findings[0]
                self.assertEqual(finding["code"], "SET_MULTIPLIED")
                self.assertEqual(finding["action"], "reported")
                self.assertIsNone(finding["item_index"])
                self.assertIsNone(finding["new_value"])
                self.assertIn("4 x 2", finding["evidence"])
                item = checked["items"][0]
                self.assertEqual(item["quantity"], 8)
                self.assertEqual(item["unit"], "piece")

    def test_a_variant_line_is_flagged_and_not_merged(self):
        extraction = {"items": [
            {"part_number": "AC-44170", "quantity": 20},
            {"part_number": "AC-44170", "description": "Ausführung verzinkt"},
        ]}
        source = "AC-44170 Schraube, 20 Stück\nAusführung: verzinkt\n"
        for apply in (False, True):
            with self.subTest(apply=apply):
                checked, findings = G.ground(extraction, source, apply=apply)
                # Both lines stay. No merge means no item is removed, so
                # item_index and input_index still agree.
                self.assertEqual(len(checked["items"]), 2)
                splits = [f for f in findings if f["code"] == "VARIANT_SPLIT"]
                self.assertEqual(len(splits), 1)
                finding = splits[0]
                self.assertEqual(finding["action"], "flagged")
                self.assertEqual(finding["field"], "part_number")
                self.assertEqual(finding["item_index"], 1)
                self.assertEqual(finding["input_index"], 1)
                self.assertIsNone(finding["new_value"])
                self.assertTrue(checked["items"][1]["uncertain"])
                self.assertNotIn("note", checked["items"][0])

    def test_a_redacted_link_is_suggested_then_restored(self):
        extraction = {"items": [{"link": "https://www.ebay.de/itm/[Telefon]",
                                 "quantity": 2}]}
        source = "Bitte 2 Stück https://www.ebay.de/itm/123456789012\n"
        restored = "https://www.ebay.de/itm/123456789012"
        for apply in (False, True):
            with self.subTest(apply=apply):
                checked, findings = G.ground(extraction, source, apply=apply)
                redacted = [f for f in findings
                            if f["code"] == "REDACTED_LINK"]
                self.assertEqual(len(redacted), 1)
                finding = redacted[0]
                self.assertEqual(finding["new_value"], restored)
                item = checked["items"][0]
                self.assertTrue(item["uncertain"])
                if apply:
                    self.assertEqual(finding["action"], "restored")
                    self.assertEqual(item["link"], restored)
                else:
                    self.assertEqual(finding["action"], "suggested")
                    self.assertEqual(item["link"],
                                     "https://www.ebay.de/itm/[Telefon]")
                # A suggested link is the item's own link once a person
                # accepts it, so it covers the source link (S8″).
                self.assertNotIn("UNCOVERED_LINK",
                                 [f["code"] for f in findings])

    def test_infer_must_be_a_bool(self):
        # 2026ab9 raises TypeError too, for the unknown keyword. The message is
        # what proves the flag is checked, so assert on the message.
        for bad in ("yes", 1):
            with self.subTest(infer=bad):
                with self.assertRaisesRegex(TypeError,
                                            "infer must be a bool"):
                    G.ground({"items": []}, "", infer=bad)

    def test_a_non_string_key_in_a_variant_line_raises_nothing(self):
        # N2″: the merge must not crash on a key it cannot write.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 20},
                                {"part_number": "AC-44170", 1: "x"}]}
        source = "AC-44170 Schraube, 20 Stück\n"
        for apply in (False, True):
            for infer in (False, True):
                with self.subTest(apply=apply, infer=infer):
                    _checked, findings = G.ground(
                        extraction, source, apply=apply, infer=infer)
                    self.assertIsInstance(findings, list)

    def test_infer_true_keeps_the_owner_and_quantity_inference(self):
        # The README example, and the extraction of
        # examples/grounding_example.py: a set line with its pack marker and a
        # GTIN the extractor dropped.
        extraction = {"items": [{
            "quantity": 12,
            "unit": "piece",
            "description": "Cordless impact driver 18 V 4-pack",
            "part_number": "AC-9999",
        }]}
        source = ("Example Tool Supply - order request\n"
                  "Cordless impact driver 18 V 4-pack, part AC-4417, "
                  "GTIN 2000000000008\n"
                  "quantity: 3 x 4\n")
        _checked, findings = G.ground(extraction, source, infer=True)
        actions = {f["code"]: f["action"] for f in findings}
        self.assertEqual(actions["SET_MULTIPLIED"], "suggested")
        self.assertEqual(actions["MISSING_GTIN"], "suggested")
        checked, findings = G.ground(extraction, source, infer=True,
                                     apply=True)
        actions = {f["code"]: f["action"] for f in findings}
        self.assertEqual(actions["SET_MULTIPLIED"], "corrected")
        self.assertEqual(actions["MISSING_GTIN"], "attached")
        self.assertEqual(checked["items"][0]["quantity"], 3)
        self.assertEqual(checked["items"][0]["unit"], "set")
        self.assertEqual(checked["items"][0]["gtin"], "2000000000008")


if __name__ == "__main__":
    unittest.main()
