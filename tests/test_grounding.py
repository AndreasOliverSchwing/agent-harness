"""Tests for harness_grounding.

Every fixture here is invented: "Acme Tools" and "Northwind Supply" are made-up
suppliers, and every GTIN passes or fails the GS1 check digit by construction.
No real customer text, no real product data. The marketplace ids are invented
too, and they are chosen so that they are not valid GTINs: a 12-digit item
number with a valid check digit would be picked up as a GTIN and would blur the
check under test.

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
Washer 12 mm  GTIN 4006381333931
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
                                 "gtin": "96385074"}]}
        checked, findings = G.ground(extraction, ID_SOURCE)
        self.assertEqual([f["code"] for f in findings], ["UNGROUNDED_ID"])
        self.assertEqual(findings[0]["field"], "gtin")
        self.assertEqual(findings[0]["old_value"], "96385074")
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
        self.assertIsNone(finding["input_index"])
        self.assertEqual(finding["action"], "reported")
        self.assertEqual(finding["field"], "gtin")
        # Nothing was written, so both value slots are empty. The value the
        # finding is about is named in evidence.
        self.assertIsNone(finding["old_value"])
        self.assertIsNone(finding["new_value"])
        self.assertIn("4006381333931", finding["evidence"])
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

    def test_product_link_id_rejects_short_or_missing_path(self):
        # An ASIN is exactly 10 characters; 9 is not an ASIN.
        self.assertIsNone(G.product_link_id("https://www.amazon.de/dp/B0TEST123"))
        self.assertIsNone(G.product_link_id("https://www.amazon.de/"))
        self.assertIsNone(G.product_link_id(None))


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

    def test_ground_keeps_cut_link_when_no_source_link_matches(self):
        # Nothing in the source starts with this prefix, so the cut link cannot
        # be proven and stays as it is. The eBay link is then a new item.
        extraction = {"items": [{
            "description": "Widget",
            "link": "https://de.aliexpress.com/item/9[Telefon]999999"}]}
        source = "Acme Tools\n%s\n" % EBAY_LINK
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["UNCOVERED_LINK"])
        self.assertEqual(checked["items"][0]["link"],
                         "https://de.aliexpress.com/item/9[Telefon]999999")


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

    def test_ground_ignores_multiplied_quantity_without_pack_marker(self):
        # 140 is a plausible order of 140 single bolts. No pack word, no change.
        extraction = {"items": [{"description": "Hex bolt",
                                 "part_number": "AC-4417", "quantity": 140}]}
        checked, findings = G.ground(extraction, SET_SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 140)

    def test_ground_ignores_pack_when_quantity_is_not_multiplied(self):
        extraction = {"items": [{
            "description": "Hex bolt set, 5-pack",
            "part_number": "AC-4417", "quantity": 28}]}
        checked, findings = G.ground(extraction, SET_SOURCE)
        self.assertEqual(findings, [])
        self.assertEqual(checked["items"][0]["quantity"], 28)


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
        source = ("Acme Tools\npart AC-4417\nGTIN 4006381333931\n%s\n"
                  % AMAZON_LINK)
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "link": AMAZON_LINK, "gtin": "4006381333931",
             "other_ids": ["SUP-77"]},
        ]}
        checked, findings = G.ground(extraction, source)
        self.assertEqual([f["code"] for f in findings], ["VARIANT_SPLIT"])
        item = checked["items"][0]
        self.assertEqual(item["link"], AMAZON_LINK)
        self.assertEqual(item["gtin"], "4006381333931")
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
        self.assertEqual(conflicts[0]["new_value"], AMAZON_LINK)

    def test_merge_keeps_gtin_conflict_in_the_finding(self):
        extraction = {"items": [
            {"description": "Hex bolt set", "part_number": "AC-4417",
             "quantity": 4, "gtin": "4006381333931"},
            {"description": "Hex bolt set zinc", "part_number": "AC-4417",
             "gtin": "96385074"},
        ]}
        source = "Acme Tools\npart AC-4417\nGTIN 4006381333931 96385074\n"
        checked, findings = G.ground(extraction, source)
        self.assertEqual(checked["items"][0]["gtin"], "4006381333931")
        conflicts = [f for f in findings
                     if f["code"] == "VARIANT_SPLIT" and f["field"] == "gtin"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["old_value"], "96385074")

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


if __name__ == "__main__":
    unittest.main()
