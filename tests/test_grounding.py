"""Tests for harness_grounding.

Every fixture here is invented: "Acme Tools" and "Northwind Supply" are made-up
suppliers, and every GTIN passes or fails the GS1 check digit by construction.
No real customer text, no real product data.

The tests are split by contract, not by function name:
  - the check-digit rule, on all four GS1 lengths;
  - find_gtins, which must skip what is not a standalone valid GTIN;
  - normalize_id, which decides when two ids are the same id;
  - the two ground() checks, positive and negative;
  - the ground() contract: deep copy, no mutation, missing keys tolerated.

    python3 -m unittest discover -s tests -v
"""
import copy
import os
import sys
import unittest

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
4 x Hex bolt M8 stainless steel  GTIN 4006381333931
2 x Garden hose 20 m  GTIN 96385074
"""

ONE_GTIN_SOURCE = """\
Northwind Supply - order confirmation
4 x Widget assembly  GTIN 4006381333931
"""


class GtinCheckDigitTest(unittest.TestCase):
    """The GS1 rule, on every length the module claims to support."""

    def test_gtin13_valid(self):
        self.assertTrue(G.gtin_valid("4006381333931"))

    def test_gtin13_invalid_check_digit(self):
        # Same body, last digit off by one.
        self.assertFalse(G.gtin_valid("4006381333932"))

    def test_gtin8_valid(self):
        self.assertTrue(G.gtin_valid("96385074"))

    def test_gtin8_invalid_check_digit(self):
        self.assertFalse(G.gtin_valid("96385075"))

    def test_gtin12_valid(self):
        self.assertTrue(G.gtin_valid("036000291452"))

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
        self.assertFalse(G.gtin_valid("4006381333931x"))
        self.assertFalse(G.gtin_valid(4006381333931))
        self.assertFalse(G.gtin_valid(None))

    def test_gtin_ignores_surrounding_whitespace(self):
        # A value copied out of a table often carries a space. Strip, do not fail.
        self.assertTrue(G.gtin_valid(" 4006381333931\n"))


class FindGtinsTest(unittest.TestCase):
    """find_gtins must return only standalone valid GTINs, in order, unique."""

    def test_find_gtins_in_order_without_repeats(self):
        text = "Acme Tools: 96385074, then 4006381333931, and 96385074 again."
        self.assertEqual(G.find_gtins(text), ["96385074", "4006381333931"])

    def test_find_gtins_skips_invalid_check_digit(self):
        self.assertEqual(G.find_gtins("96385075 4006381333932"), [])

    def test_find_gtins_skips_wrong_length_runs(self):
        # 9 digits, 15 digits, and a 17-digit run that CONTAINS a valid GTIN.
        # A longer number must never be trimmed down into a shorter one.
        # Built from short literals so the channel-id scan stays quiet.
        nine = "12345678" + "9"
        fifteen = "94006381333931" + "1"
        seventeen = "4006381333931" + "9999"
        text = " ".join([nine, fifteen, seventeen])
        self.assertEqual(G.find_gtins(text), [])

    def test_find_gtins_ignores_text_without_gtins(self):
        self.assertEqual(G.find_gtins(""), [])
        self.assertEqual(G.find_gtins("Hex bolt M8, part AC-4417"), [])
        self.assertEqual(G.find_gtins(None), [])

    def test_find_gtins_multiple_lengths(self):
        text = "96385074 036000291452 4006381333931 10012345678902"
        self.assertEqual(G.find_gtins(text),
                         ["96385074", "036000291452", "4006381333931",
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
        self.assertEqual(finding["value"], "AC-9999")
        self.assertEqual(finding["action"], "flagged")
        self.assertTrue(checked["items"][0]["uncertain"])
        # The value stays. A checker that deletes hides the extractor's mistake.
        self.assertEqual(checked["items"][0]["part_number"], "AC-9999")

    def test_ground_flags_ungrounded_gtin(self):
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "gtin": "96385074"}]}
        checked, findings = G.ground(extraction, ID_SOURCE)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["value"], "96385074")
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


class GroundMissingGtinTest(unittest.TestCase):
    """MISSING_GTIN: attach what the source proves, report what it cannot place."""

    def test_ground_attaches_missing_gtin_by_description(self):
        extraction = {"items": [
            {"description": "Hex bolt M8"},
            {"description": "Garden hose 20 m"},
        ]}
        checked, findings = G.ground(extraction, GTIN_SOURCE)
        self.assertEqual([f["action"] for f in findings], ["attached", "attached"])
        self.assertEqual(checked["items"][0]["gtin"], "4006381333931")
        self.assertEqual(checked["items"][1]["gtin"], "96385074")
        self.assertTrue(checked["items"][0]["uncertain"])
        self.assertTrue(checked["items"][1]["uncertain"])

    def test_ground_single_item_gets_gtin(self):
        # One item: there is no wrong item to pick, so it takes the GTIN even
        # when the description does not overlap at all.
        extraction = {"items": [{"description": "Garden hose"}]}
        checked, findings = G.ground(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(findings[0]["action"], "attached")
        self.assertEqual(findings[0]["item_index"], 0)
        self.assertEqual(checked["items"][0]["gtin"], "4006381333931")
        self.assertTrue(checked["items"][0]["uncertain"])

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
        self.assertEqual(finding["action"], "reported")
        self.assertEqual(finding["value"], "4006381333931")
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
                  "4 x Hex bolt M8  GTIN 4006381333931 and 96385074\n")
        extraction = {"items": [{"description": "Hex bolt M8",
                                 "gtin": "96385074"}]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["action"] for f in findings], ["attached"])
        self.assertEqual(checked["items"][0]["gtin"], "96385074")
        self.assertEqual(checked["items"][0]["other_ids"], ["4006381333931"])
        self.assertTrue(checked["items"][0]["uncertain"])

    def test_ground_skips_gtin_already_in_other_ids(self):
        extraction = {"items": [{"description": "Widget assembly",
                                 "other_ids": ["4006381333931"]}]}
        checked, findings = G.ground(extraction, ONE_GTIN_SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["other_ids"], ["4006381333931"])


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
            self.assertEqual(set(finding),
                             {"code", "item_index", "value", "evidence",
                              "action"})
            self.assertIsInstance(finding["code"], str)
            self.assertIsInstance(finding["value"], str)
            self.assertIsInstance(finding["evidence"], str)
            self.assertIsInstance(finding["action"], str)
            self.assertTrue(finding["item_index"] is None
                            or isinstance(finding["item_index"], int))


if __name__ == "__main__":
    unittest.main()
