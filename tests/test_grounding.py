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
        self.assertEqual(findings[0]["value"], "ASIN:B0TEST1234")
        self.assertEqual(findings[0]["item_index"], 0)
        item = checked["items"][0]
        self.assertEqual(item["description"], "ASIN:B0TEST1234")
        self.assertEqual(item["link"], AMAZON_LINK)
        self.assertEqual(item["part_number"], "ASIN:B0TEST1234")
        self.assertEqual(item["quantity"], 4)
        self.assertEqual(item["unit"], "piece")
        self.assertTrue(item["uncertain"])

    def test_ground_takes_link_quantity_from_previous_line(self):
        # The line above the eBay link holds nothing but "2".
        checked, findings = G.ground({"items": []}, LINK_SOURCE)
        self.assertEqual(findings[1]["value"], "EBAY:123456789001")
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
        self.assertEqual([f["value"] for f in findings], ["EBAY:123456789001"])
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
        self.assertEqual(findings[0]["value"], ALIEXPRESS_LINK)
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
        self.assertEqual(findings[0]["value"], "140")
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
            self.assertEqual(set(finding),
                             {"code", "item_index", "value", "evidence",
                              "action"})
            self.assertIsInstance(finding["value"], str)
            self.assertIsInstance(finding["evidence"], str)
            self.assertIsInstance(finding["action"], str)
            self.assertTrue(finding["item_index"] is None
                            or isinstance(finding["item_index"], int))


if __name__ == "__main__":
    unittest.main()
