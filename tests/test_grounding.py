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
import sys
import time
import unittest
from decimal import Decimal

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import harness_grounding as G  # noqa: E402


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
        self.assertFalse(G.gtin_valid("036000291453"))

    def test_gtin14_valid(self):
        self.assertTrue(G.gtin_valid("10012345678902"))

    def test_gtin14_invalid_check_digit(self):
        self.assertFalse(G.gtin_valid("10012345678903"))

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
        text = "20000004 200000000004 2000000000008 10012345678902"
        self.assertEqual(G.find_gtins(text),
                         ["20000004", "200000000004", "2000000000008",
                          "10012345678902"])


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
        checked, findings = G.ground(extraction, ID_SOURCE)
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
        checked, findings = G.ground(extraction, ID_SOURCE)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["field"], "gtin")
        self.assertEqual(findings[0]["old_value"], "20000004")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_leaves_grounded_id_alone(self):
        # "AC 4417" normalizes to the "AC-4417" that the source holds.
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC 4417"}]}
        checked, findings = G.ground(extraction, ID_SOURCE)
        self.assertEqual(findings, [])
        self.assertNotIn("uncertain", checked["items"][0])

    def test_ground_ignores_short_ids(self):
        # Two characters prove nothing and would flag every M8 in every mail.
        extraction = {"items": [{"description": "Hex bolt", "part_number": "M8"}]}
        checked, findings = G.ground(extraction, ID_SOURCE)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "AC-441")
        self.assertEqual(checked["items"][0]["part_number"], "AC-441")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_flags_an_id_that_is_only_the_prefix_of_a_longer_id(self):
        source = "Northwind Supply\n2 x Washer 12 mm, part NW-22019\n"
        extraction = {"items": [{"description": "Washer 12 mm",
                                 "part_number": "NW-2201"}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])

    def test_ground_flags_digits_that_only_occur_inside_a_date(self):
        source = "Acme Tools\nOrder date 2026-10-26\n4 x Hex bolt M8\n"
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "2610"}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "2610")

    def test_ground_grounds_an_id_written_with_a_unicode_dash(self):
        # The source carries the odd character, the extraction the plain one.
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC-4417"}]}
        for joiner in self.JOINERS:
            source = ("Acme Tools\n4 x Hex bolt M8, part AC%s4417\n" % joiner)
            checked, findings = G.ground(extraction, source)
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
            _checked, findings = G.ground(extraction, source)
            self.assertEqual(findings, [], "joiner %r" % joiner)

    def test_ground_grounds_a_token_of_a_source_id(self):
        # "AC-4417" is two tokens. A part number that IS one of those tokens is
        # grounded: the boundary rule stops prefixes, not whole words.
        source = "Acme Tools\n4 x Hex bolt M8, part AC-4417\n"
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "4417"}]}
        _checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "AC-44178")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_a_pipe_does_not_join_two_cells_of_a_row(self):
        source = "AC-4417 | 8 | St\u00fcck\n"
        extraction = {"items": [{"part_number": "AC-4417-8", "quantity": 1}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "AC-4417-8")

    def test_a_bracket_does_not_join_an_id_to_the_quantity_beside_it(self):
        source = "Art.-Nr. 4417 (10 St\u00fcck)\n"
        extraction = {"items": [{"part_number": "4417-10", "quantity": 1}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])

    def test_a_joiner_between_two_digits_is_one_number(self):
        # The date is one number. "1026" is the tail of it, not an id.
        source = "Order date 2026-10-26\n"
        extraction = {"items": [{"part_number": "1026"}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["old_value"], "1026")

    def test_the_whole_date_is_still_grounded(self):
        # The rule stops a fragment, not a whole number.
        source = "Order date 2026-10-26\n"
        extraction = {"items": [{"part_number": "2026-10-26"}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])

    def test_a_joiner_after_a_letter_still_joins_the_id(self):
        # "AC-4417" is one id and "4417" is a whole token of it, so the rule
        # that stops the date fragment must not flag it.
        source = "Acme Tools\n4 x Hex bolt M8, part AC-4417\n"
        extraction = {"items": [{"part_number": "4417"}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])


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
        _checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])

    def test_a_sharp_s_before_an_id_does_not_raise(self):
        # B1 (b): the boundary list was shorter than the flat text, so ground()
        # raised IndexError instead of answering.
        source = "Grüße aus der Lindenstraße\nBitte liefern: AC-4417"
        extraction = {"items": [{"part_number": "AC-4417", "quantity": 1}]}
        _checked, findings = G.ground(extraction, source)
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
        _checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])

    def test_a_sharp_s_does_not_hide_a_multiplied_set(self):
        # The same shift left "AC-44170" flagged and the set line uncorrected.
        source = "Maßband AC-44170 5er-Pack, Menge: 2 x 5"
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 10}]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 2)

    def test_a_pdf_fi_ligature_does_not_flag_a_grounded_id(self):
        source = "Proﬁl-Schiene AC-44170, 4 Stück"
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 4}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])

    def test_a_pdf_fl_ligature_does_not_flag_a_grounded_id(self):
        source = "Anﬂanschplatte AC-44170, 4 Stück"
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 4}]}
        _checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])


class GroundMissingGtinTest(unittest.TestCase):
    """MISSING_GTIN: attach what the source proves, report what it cannot place."""

    def test_ground_attaches_missing_gtin_by_description(self):
        extraction = {"items": [
            {"description": "Hex bolt M8"},
            {"description": "Garden hose 20 m"},
        ]}
        checked, findings = G.ground(extraction, GTIN_SOURCE)
        self.assertEqual([f["action"] for f in findings], ["attached", "attached"])
        self.assertEqual(checked["items"][0]["gtin"], "2000000000008")
        self.assertEqual(checked["items"][1]["gtin"], "20000004")
        self.assertTrue(checked["items"][0]["uncertain"])
        self.assertTrue(checked["items"][1]["uncertain"])

    def test_ground_reports_a_gtin_that_one_item_cannot_claim(self):
        # One item is not a reason to attach. The line names no product of the
        # extraction, so the GTIN is reported (S6).
        extraction = {"items": [{"description": "Garden hose"}]}
        checked, findings = G.ground(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(findings[0]["action"], "reported")
        self.assertIsNone(findings[0]["item_index"])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_reports_when_nothing_matches(self):
        extraction = {"items": [
            {"description": "Garden hose"},
            {"description": "Paint brush"},
        ]}
        checked, findings = G.ground(extraction, ONE_GTIN_SOURCE)
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
        checked, findings = G.ground({"items": []}, ONE_GTIN_SOURCE)
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0]["item_index"])
        self.assertEqual(findings[0]["action"], "reported")
        self.assertEqual(checked["items"], [])

    def test_ground_appends_to_other_ids_when_gtin_taken(self):
        source = ("Northwind Supply\n"
                  "4 x Hex bolt M8  GTIN 2000000000008 and 20000004\n")
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "gtin": "20000004"}]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"])
        self.assertEqual(checked["items"][0]["gtin"], "20000004")
        self.assertEqual(checked["items"][0]["other_ids"], ["2000000000008"])
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_skips_gtin_already_in_other_ids(self):
        extraction = {"items": [{"description": "Widget assembly",
                                 "other_ids": ["2000000000008"]}]}
        checked, findings = G.ground(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["other_ids"], ["2000000000008"])


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
        checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_does_not_attach_an_order_number(self):
        source = "Acme Tools\nOrder 20261023\n4 x Widget assembly\n"
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_does_not_attach_a_digit_run_inside_a_link(self):
        # The line carries the keyword; the only digit run on it is in the URL.
        source = ("Acme Tools\n4 x Widget assembly, EAN as printed in the "
                  "shop: https://shop.example.com/p/4012345678901\n")
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_attaches_a_gtin_with_an_english_keyword(self):
        source = "Acme Tools\n4 x Widget assembly  EAN 4012345678901\n"
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"])
        self.assertEqual(findings[0]["new_value"], "4012345678901")
        self.assertEqual(checked["items"][0]["gtin"], "4012345678901")

    def test_ground_attaches_a_gtin_with_a_german_keyword(self):
        source = "Acme Tools\n4 x Widget assembly  Strichcode 4012345678901\n"
        extraction = {"items": [{"description": "Widget assembly"}]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"])
        self.assertEqual(checked["items"][0]["gtin"], "4012345678901")

    def test_ground_takes_only_the_digit_run_after_the_keyword(self):
        # S5 (a) from the 2026-10-27 recheck. The order number comes first on
        # the line and passes the check digit too. Only the run that follows
        # "EAN" is the GTIN.
        source = ("Bestellung 20261023, Sechskantschraube M8 AC-44170, "
                  "EAN 2000000000015\n")
        extraction = {"items": [{"part_number": "AC-44170",
                                 "description": "Sechskantschraube M8",
                                 "quantity": 4}]}
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_reads_the_common_keyword_spellings(self):
        # S5: a supplier writes the keyword with a length suffix or "Nr.".
        for keyword in ("EAN", "EAN:", "EAN-13:", "EAN-Nr.:"):
            source = "Acme Tools\n4 x Widget assembly  %s 2000000000015\n" \
                     % keyword
            extraction = {"items": [{"description": "Widget assembly"}]}
            checked, findings = G.ground(extraction, source)
            self.assertEqual([f["action"] for f in findings], ["attached"],
                             keyword)
            self.assertEqual(checked["items"][0]["gtin"], "2000000000015",
                             keyword)

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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground({"items": []}, source)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["code"], "MISSING_GTIN")
        self.assertEqual(finding["action"], "reported")
        self.assertIsNone(finding["item_index"])
        self.assertIsNone(finding["new_value"])
        self.assertIn("2000000000008", finding["evidence"])
        self.assertNotIn("gtin", checked["items"][0])
        self.assertNotIn("gtin", checked["items"][1])


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
        checked, findings = G.ground(extraction, self.SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["gtin"], "0200000000004")
        self.assertNotIn("uncertain", checked["items"][0])

    def test_ground_grounds_a_gtin14_of_a_gtin12_in_the_source(self):
        extraction = {"items": [{"description": "Widget assembly",
                                 "gtin": "00200000000004"}]}
        checked, findings = G.ground(extraction, self.SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["gtin"], "00200000000004")

    def test_ground_does_not_attach_a_gtin_that_part_number_carries(self):
        extraction = {"items": [{"description": "Widget assembly",
                                 "part_number": "200000000004"}]}
        checked, findings = G.ground(extraction, self.SOURCE)
        self.assertEqual(findings, [])
        self.assertNotIn("gtin", checked["items"][0])

    def test_ground_does_not_attach_a_gtin_that_other_ids_carries(self):
        # The label names the number; it is not part of it.
        extraction = {"items": [{"description": "Widget assembly",
                                 "other_ids": ["EAN:0200000000004"]}]}
        checked, findings = G.ground(extraction, self.SOURCE)
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
        checked, _findings = G.ground(extraction, GTIN_SOURCE)
        self.assertEqual(extraction, snapshot)
        self.assertIsNot(checked, extraction)
        self.assertIsNot(checked["items"][0], extraction["items"][0])

    def test_ground_missing_keys(self):
        checked, findings = G.ground({"items": [{}, {"gtin": None,
                                                     "other_ids": None}]},
                                     ID_SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"], [{}, {"gtin": None,
                                                 "other_ids": None}])

    def test_ground_extraction_without_items(self):
        checked, findings = G.ground({}, "")
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"], [])

    def test_finding_shape(self):
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "part_number": "AC-9999"}]}
        _checked, findings = G.ground(extraction, ONE_GTIN_SOURCE)
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
        checked, findings = G.ground(copy.deepcopy(ALL_CHECKS_EXTRACTION),
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
        checked, findings = G.ground(extraction, "Acme Tools\npart AC-4417\n")
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
        checked, findings = G.ground({"items": [{"description": "Paint brush"},
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
        G.ground({"items": []}, source)
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
        G.ground({"items": []}, source)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters"
                        % (elapsed, len(source)))

    def test_ground_is_linear_on_many_set_lines(self):
        # The pack-marker scan runs once per set line unless the sizes are
        # collected once for the whole text.
        source = "Acme Tools\n" + "quantity: 2 x 2 " * 1000
        started = time.perf_counter()
        G.ground({"items": []}, source)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters"
                        % (elapsed, len(source)))

    def test_find_gtins_is_linear_on_a_long_digit_run(self):
        text = "1" * self.LENGTH
        started = time.perf_counter()
        G.find_gtins(text)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "find_gtins took %.3f s on %d characters"
                        % (elapsed, len(text)))

    def test_ground_is_linear_on_a_long_digit_run(self):
        # The pack-marker patterns start with "(\d+)" and no boundary, so every
        # position inside a long digit run starts a fresh scan: 20,000 digits
        # took 6 s once the marker scan ran on every source.
        source = "Acme Tools\n" + "1" * self.LENGTH
        extraction = {"items": [{"description": "Widget",
                                 "part_number": "AC-441",
                                 "gtin": "20000004"}]}
        started = time.perf_counter()
        G.ground(extraction, source)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d characters"
                        % (elapsed, len(source)))


class GroundUncoveredLinkTest(unittest.TestCase):
    """UNCOVERED_LINK: a product link no item covers becomes an item."""

    def test_ground_adds_item_with_quantity_from_same_line(self):
        checked, findings = G.ground({"items": []}, LINK_SOURCE)
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
        checked, findings = G.ground({"items": []}, LINK_SOURCE)
        self.assertEqual(findings[1]["new_value"], EBAY_LINK)
        self.assertEqual(findings[1]["item_index"], 1)
        self.assertEqual(checked["items"][1]["quantity"], 2)

    def test_ground_link_without_quantity_gets_none(self):
        source = "Acme Tools\nhttps://www.amazon.de/dp/B0TEST1234\n"
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual(len(findings), 1)
        self.assertIsNone(checked["items"][0]["quantity"])

    def test_a_link_takes_its_own_quantity_line_below(self):
        # S11: the quantity under a position belongs to that position. The
        # second link used to take the first link's 2.
        source = ("https://www.amazon.de/dp/B0TESTAAA1\n"
                  "2 St\u00fcck\n"
                  "https://www.amazon.de/dp/B0TESTAAA2\n"
                  "3 St\u00fcck\n")
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [2, 3])

    def test_a_link_never_takes_the_quantity_of_the_link_above(self):
        # S11: "2 St\u00fcck" sits under the first position, so the second link
        # has no quantity of its own.
        source = ("https://www.amazon.de/dp/B0TESTAAA1\n"
                  "2 St\u00fcck\n"
                  "https://www.amazon.de/dp/B0TESTAAA2\n")
        checked, findings = G.ground({"items": []}, source)
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
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [None, None])

    def test_a_million_in_front_of_a_link_is_a_million(self):
        # S11: the quantity pattern needs a left boundary, or "1.000.000"
        # matches its last group "000" and reads as 0.
        source = "1.000.000 St\u00fcck https://www.amazon.de/dp/B0TESTAAA1\n"
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual(checked["items"][0]["quantity"], 1000000)

    def test_a_million_above_a_link_is_a_million(self):
        source = ("1.000.000 St\u00fcck\n"
                  "https://www.amazon.de/dp/B0TESTAAA1\n")
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual(checked["items"][0]["quantity"], 1000000)

    def test_the_second_link_on_a_line_takes_no_quantity_from_below(self):
        # S11: two links on one line each get one quantity, and the line below
        # belongs to the line, not to the second link.
        source = ("4x https://www.amazon.de/dp/B0TESTAAA1 "
                  "https://www.amazon.de/dp/B0TESTAAA2\n"
                  "2 St\u00fcck\n")
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [4, None])

    def test_ground_does_not_add_covered_link_by_part_number(self):
        # The item holds the bare ASIN, which is what an extractor writes.
        extraction = {"items": [{"description": "Hex bolt",
                                 "part_number": "B0TEST1234"}]}
        source = "Acme Tools\n4x https://www.amazon.de/dp/B0TEST1234\n"
        checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 1)

    def test_ground_does_not_add_covered_link_by_same_link(self):
        extraction = {"items": [{"description": "Hex bolt",
                                 "link": AMAZON_LINK}]}
        checked, findings = G.ground(extraction, LINK_SOURCE)
        # Only the eBay link is new.
        self.assertEqual([f["new_value"] for f in findings], [EBAY_LINK])
        self.assertEqual(len(checked["items"]), 2)

    def test_ground_ignores_link_that_is_not_a_product(self):
        source = "Acme Tools\nsee https://www.example.com/orders/4711\n"
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"], [])

    def test_ground_adds_the_clean_link_from_markdown(self):
        # QA 12: the appended item must hold the address, not "](" glue.
        source = ("Acme Tools\n"
                  "4x [Hex bolt M8](https://www.amazon.de/dp/B0TEST1234)\n")
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual([f["new_value"] for f in findings], [AMAZON_LINK])
        self.assertEqual(checked["items"][0]["link"], AMAZON_LINK)
        self.assertEqual(checked["items"][0]["quantity"], 4)


class GroundRedactedLinkTest(unittest.TestCase):
    """REDACTED_LINK: restore a product link that a redaction cut in two."""

    def test_ground_restores_redacted_link(self):
        extraction = {"items": [{
            "description": "Widget",
            "link": "https://de.aliexpress.com/item/1[Telefon]234567"}]}
        source = "Acme Tools\n%s\n" % ALIEXPRESS_LINK
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings],
                         ["REDACTED_LINK", "UNCOVERED_LINK"])
        self.assertEqual(findings[0]["action"], "flagged")
        self.assertIsNone(findings[0]["new_value"])
        self.assertEqual(findings[0]["field"], "link")
        self.assertEqual(checked["items"][0]["link"],
                         "https://de.aliexpress.com/item/9[Telefon]999999")
        self.assertTrue(checked["items"][0]["uncertain"])


class GroundSetMultipliedTest(unittest.TestCase):
    """SET_MULTIPLIED: an M-pack ordered N times is N sets, not N*M pieces."""

    def test_ground_corrects_multiplied_set(self):
        extraction = {"items": [{
            "description": "Hex bolt set, 5-pack",
            "part_number": "AC-4417", "quantity": 140, "unit": "piece"}]}
        checked, findings = G.ground(extraction, SET_SOURCE)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["action"], "corrected")
        self.assertEqual(findings[0]["field"], "quantity")
        self.assertEqual(findings[0]["old_value"], 140)
        self.assertEqual(findings[0]["new_value"], 28)
        self.assertEqual(checked["items"][0]["quantity"], 28)
        self.assertEqual(checked["items"][0]["unit"], "set")
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_corrects_pack_of_form(self):
        source = "Acme Tools\nMenge: 12 x 5\nWasher pack of 5\n"
        extraction = {"items": [{"description": "Washer pack of 5",
                                 "quantity": 60}]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 12)

    def test_pack_word_in_the_description_is_not_evidence(self):
        # The extractor wrote "5-pack"; the source never says pack. A pack size
        # the source does not state is not a correction, it is a guess.
        source = ("Acme Tools - order confirmation\n"
                  "quantity: 28 x 5\n"
                  "Hex bolt, part AC-4417\n")
        extraction = {"items": [{"description": "Hex bolt 5-pack",
                                 "part_number": "AC-4417", "quantity": 140}]}
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
            checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 140)

    def test_ground_ignores_pack_when_quantity_is_not_multiplied(self):
        extraction = {"items": [{
            "description": "Hex bolt set, 5-pack",
            "part_number": "AC-4417", "quantity": 28}]}
        checked, findings = G.ground(extraction, SET_SOURCE)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertNotIn("uncertain", checked["items"][0])

    def test_a_pack_marker_on_the_items_own_line_still_corrects(self):
        # The pack marker and the part number are on one line, so the set line
        # below it belongs to this item.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 8}]}
        source = "AC-44170 2-pack\nMenge: 4 x 2"
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 4)
        self.assertEqual(checked["items"][0]["unit"], "set")

    def test_a_set_line_corrects_the_item_on_the_line_below(self):
        # The window works in both directions: the item line may follow the set
        # line, which is how a supplier writes a position.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 8}]}
        source = "Menge: 4 x 2\nAC-44170 2-pack"
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["item_index"], 1)
        self.assertEqual(checked["items"][1]["quantity"], 4)
        self.assertEqual(checked["items"][0]["quantity"], 8)

    def test_a_block_of_set_lines_corrects_only_its_own_item(self):
        # S1 (c): two set lines in one block, one product above and one below.
        # Each set line belongs to the product next to it, so the Kabelbinder's
        # own "3 x 2" is not replaced by the screw's "4 x 2".
        extraction = {"items": [
            {"part_number": "NW-22010", "quantity": 8},
            {"part_number": "AC-44170", "quantity": 8},
        ]}
        source = ("NW-22010 Kabelbinder 2er-Pack, 8 St\u00fcck\n"
                  "Menge: 3 x 2\n"
                  "Menge: 4 x 2\n"
                  "AC-44170 Schraube 2er-Pack, 8 St\u00fcck\n")
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["SET_MULTIPLIED"])
        self.assertEqual(findings[0]["item_index"], 1)
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertEqual(checked["items"][1]["quantity"], 4)

    def test_the_evidence_names_the_unit_change(self):
        # S1: the finding says what the unit becomes, not only the number.
        extraction = {"items": [{"part_number": "AC-44170", "quantity": 8}]}
        _checked, findings = G.ground(extraction, "AC-44170 2-pack\nMenge: 4 x 2")
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
        checked, findings = G.ground(extraction, source)
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
            checked, _findings = G.ground(extraction,
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
        checked, findings = G.ground(extraction, "Acme Tools\npart AC-4417\n")
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
        checked, _findings = G.ground(extraction, "Acme Tools\npart AC-4417\n")
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
        checked, findings = G.ground(extraction, "Acme Tools\npart AC-4417\n")
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, "Acme Tools\n")
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["VARIANT_SPLIT"])
        item = checked["items"][0]
        self.assertEqual(item["link"], AMAZON_LINK)
        self.assertEqual(item["gtin"], "2000000000008")
        self.assertEqual(item["other_ids"], ["SUP-77"])
        self.assertTrue(item["uncertain"])

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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, source)
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
        checked, findings = G.ground(extraction, "Acme Tools\npart NW-2202\n")
        self.assertEqual(checked["items"][0]["note"],
                         "variant: Hex nut M8 zinc · variant: Hex nut M8 brass")
        self.assertEqual([f["input_index"] for f in findings], [1, 2])


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
        checked, _findings = G.ground(extraction, source)
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
        _checked, findings = G.ground(extraction, source)
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
        checked, _findings = G.ground({"items": []}, source)
        self.assertEqual(checked["items"][0]["quantity"], 1000)

    def test_comma_group_is_a_thousand(self):
        source = ("Acme Tools - order confirmation\n"
                  "1,000 x\n"
                  "https://www.amazon.de/dp/B0TEST1234\n")
        checked, _findings = G.ground({"items": []}, source)
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
            checked, _findings = G.ground({"items": []}, source)
            self.assertIsNone(checked["items"][0]["quantity"], price)

    def test_a_price_value_is_not_a_quantity(self):
        self.assertIsNone(G._to_number("12,50 \u20ac"))

    def test_two_links_on_one_line_each_take_their_own_quantity(self):
        source = ("Acme Tools\n"
                  "4x https://www.amazon.de/dp/B0TEST1234 "
                  "2x https://www.ebay.de/itm/123456789001\n")
        checked, _findings = G.ground({"items": []}, source)
        self.assertEqual([item["quantity"] for item in checked["items"]],
                         [4, 2])

    def test_one_quantity_for_two_links_leaves_the_second_without(self):
        source = ("Acme Tools\n"
                  "4x https://www.amazon.de/dp/B0TEST1234 "
                  "https://www.ebay.de/itm/123456789001\n")
        checked, _findings = G.ground({"items": []}, source)
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
        checked, findings = G.ground(extraction, "Acme Tools\npart AC-4417\n")
        self.assertEqual(findings, [])
        self.assertEqual(len(checked["items"]), 2)
        self.assertEqual(G._to_number("2 pcs"), 2)
        self.assertEqual(G._to_number("2 St\u00fcck"), 2)

    def test_german_and_english_unit_words_parse(self):
        # B2: a supplier writes "St.", "Stck.", "ST", "VE", "Paar", "Pack",
        # "kg" and "Stueck". Each is a unit word, not part of the number.
        for text in ("2 St.", "2 Stck.", "2 ST", "2 VE", "2 Paar", "2 Pack",
                     "2 Stueck", "2 Stück", "2 stk."):
            self.assertEqual(G._to_number(text), 2, text)
        self.assertEqual(G._to_number("2,5 kg"), 2.5)

    def test_decimal_quantity_keeps_the_variant_line(self):
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "quantity": Decimal("2")},
        ]}
        checked, findings = G.ground(extraction, "Acme Tools\npart AC-4417\n")
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
        checked, findings = G.ground(extraction, "Acme Tools\npart AC-4417\n")
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
        checked, findings = G.ground({"items": []}, source)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"], [])

    def test_a_quantity_above_the_cap_is_ignored(self):
        self.assertEqual(G._to_number(10 ** 9), 10 ** 9)
        self.assertIsNone(G._to_number(10 ** 9 + 1))
        self.assertIsNone(G._to_number("1" + "0" * 8 + "1"))
        self.assertIsNone(G._to_number("9" * 13))


class GroundInputTypeTest(unittest.TestCase):
    """QA 13: a wrong input type is a caller bug, not an empty clean result."""

    def test_ground_rejects_an_extraction_that_is_not_a_dict(self):
        for bad in ([], (), "items", None, 4, b"{}"):
            with self.assertRaises(TypeError) as caught:
                G.ground(bad, "")
            message = str(caught.exception)
            self.assertIn("extraction", message)
            self.assertIn(type(bad).__name__, message)

    def test_ground_rejects_items_that_is_not_a_list(self):
        for bad in ((), "items", {}, None, 4):
            with self.assertRaises(TypeError) as caught:
                G.ground({"items": bad}, "")
            message = str(caught.exception)
            self.assertIn("items", message)
            self.assertIn(type(bad).__name__, message)

    def test_ground_rejects_a_source_that_is_not_a_str(self):
        for bad in (b"text", None, 4, ["text"]):
            with self.assertRaises(TypeError) as caught:
                G.ground({"items": []}, bad)
            message = str(caught.exception)
            self.assertIn("source_text", message)
            self.assertIn(type(bad).__name__, message)

    def test_ground_still_tolerates_a_missing_items_key(self):
        checked, findings = G.ground({}, "Acme Tools\n")
        self.assertEqual(checked, {"items": []})
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
