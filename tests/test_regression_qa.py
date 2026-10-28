"""Permanent regression tests: the 2026-10-26 G1 QA replays, QA 1-16.

The reviewer ran 31 probes against the module from a script outside the repo.
A probe that passed once could regress with nothing to catch it, so every probe
now lives here as one test method. The input and the PASS condition are the
script's; only the mode is new.

The script calls ``ground()`` without ``apply``. Since fix round 3 the three
checks that write an extraction value (``SET_MULTIPLIED``, ``MISSING_GTIN`` and
the quantity of an item appended for an ``UNCOVERED_LINK``) only write with
``apply=True``. A probe that expects a written value therefore runs twice:

  - with ``apply=True``, where it asserts the value the check writes;
  - with the default ``apply=False``, where it asserts that the item keeps the
    caller's value and that one ``suggested`` finding names the value the
    write mode would produce.

A probe that expects no value asserts the absence in both modes.

Every fixture is invented: made-up part numbers, made-up marketplace ids and
GTINs on the GS1 prefix 2, which is reserved for internal use.

    python3 -m unittest discover -s tests -q
"""
import decimal
import os
import sys
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import harness_grounding as G  # noqa: E402

KEYS = {"code", "action", "field", "old_value", "new_value",
        "item_index", "input_index", "evidence"}


def check_digit(body):
    total = sum(int(d) * (3 if i % 2 == 0 else 1)
                for i, d in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def gtin(body):
    return body + check_digit(body)


EAN13 = gtin("200123456789")
UPC12 = gtin("02001234567")
EAN8 = gtin("2001234")


def write(items, source):
    """ground() in write mode: the mode that corrects and attaches."""
    checked, findings = G.ground({"items": items}, source, apply=True)
    return checked["items"], findings


def plain(items, source):
    """ground() in the default mode: it only suggests."""
    checked, findings = G.ground({"items": items}, source)
    return checked["items"], findings


def codes(findings, code=None):
    return [f for f in findings if code is None or f["code"] == code]


class QaReplayTest(unittest.TestCase):
    """One test method per probe of the QA replay script."""

    def suggested(self, findings, code, field, index=0):
        """Return the one 'suggested' finding for (code, field, item index)."""
        got = [f for f in findings
               if f["code"] == code and f["field"] == field
               and f["item_index"] == index]
        self.assertEqual(len(got), 1,
                         "expected one %s/%s finding on item %d, got %r"
                         % (code, field, index, findings))
        self.assertEqual(got[0]["action"], "suggested",
                         "the default mode must not write: %r" % (got[0],))
        return got[0]

    def assert_written_and_suggested(self, items, source, expectations, code):
        """Assert what apply=True writes and what the default mode suggests.

        expectations is a list of (item_index, field, value). Each one is the
        value the write mode must produce. In the default mode the item must
        keep the caller's value and one 'suggested' finding must name it.
        """
        written, _wf = write(items, source)
        plain_items, pf = plain(items, source)
        for index, field, value in expectations:
            self.assertEqual(written[index].get(field), value,
                             "apply=True must write %s=%r on item %d, got %r"
                             % (field, value, index, written[index]))
            self.assertEqual(plain_items[index].get(field),
                             items[index].get(field),
                             "the default mode must leave item %d unchanged"
                             % index)
            finding = self.suggested(pf, code, field, index)
            self.assertEqual(finding["new_value"], value)

    # ── QA 2 ────────────────────────────────────────────────────────────────

    def test_qa02_variant_indexes_point_into_the_returned_list(self):
        """Two variant groups give 2 items; every index is inside the list."""
        items = [
            {"part_number": "AC-44170", "quantity": 2, "description": "Hex bolt"},
            {"part_number": "AC-44170", "description": "brass"},
            {"part_number": "NW-22010", "quantity": 3, "description": "Washer"},
            {"part_number": "NW-22010", "description": "steel"},
        ]
        src = "AC-44170 Hex bolt 2 x\nAC-44170 brass\nNW-22010 Washer 3 x\nNW-22010 steel"
        for mode, call in (("default", plain), ("apply", write)):
            out, fs = call(items, src)
            self.assertEqual(len(out), 2, mode)
            bad = [f for f in fs if f["item_index"] is not None
                   and not 0 <= f["item_index"] < len(out)]
            self.assertEqual(bad, [], "%s: out-of-range indexes" % mode)

    # ── QA 3 ────────────────────────────────────────────────────────────────

    def test_qa03_unique_match_restores_the_link_without_a_duplicate(self):
        """One source link matches the cut text: restore it, append nothing."""
        items = [{"link": "https://www.ebay.de/itm/123456789001", "quantity": 2},
                 {"link": "https://www.ebay.de/itm/1[Telefon]02101", "quantity": 1}]
        src = ("Artikel 1: https://www.ebay.de/itm/123456789001 2 Stück\n"
               "Artikel 2: https://www.ebay.de/itm/198765402101 1 Stück")
        out, fs = plain(items, src)
        self.assertEqual(out[1].get("link"), "https://www.ebay.de/itm/198765402101")
        self.assertIs(out[1].get("uncertain"), True)
        self.assertEqual(codes(fs, "UNCOVERED_LINK"), [])
        self.assertEqual(len(out), 2)

    def test_qa03_two_matches_are_flagged_not_rewritten(self):
        """Two source links fit the cut text: flag it and keep the placeholder."""
        items = [{"link": "https://www.aliexpress.com/item/1005[Telefon].html",
                  "quantity": 1}]
        src = ("https://www.aliexpress.com/item/1005001111111111.html\n"
               "https://www.aliexpress.com/item/1005002222222222.html")
        out, fs = plain(items, src)
        red = codes(fs, "REDACTED_LINK")
        self.assertTrue(red)
        self.assertEqual(red[0]["action"], "flagged")
        self.assertIn("[Telefon]", out[0]["link"])
        self.assertIs(out[0].get("uncertain"), True)

    # ── QA 5 ────────────────────────────────────────────────────────────────

    def test_qa05_quantity_formats_and_huge_numbers(self):
        """German thousands, string/Decimal quantities, huge numbers."""
        self.assertEqual(G._to_number("1.000"), 1000)
        self.assertEqual(G._to_number("1,000"), 1000)
        for value in ("2 pcs", decimal.Decimal("2")):
            items = [{"part_number": "AC-44170", "quantity": 2},
                     {"part_number": "AC-44170", "quantity": value,
                      "description": "brass"}]
            for mode, call in (("default", plain), ("apply", write)):
                out, _fs = call(items, "AC-44170 2 x\nAC-44170 brass 2 pcs")
                self.assertEqual(len(out), 2,
                                 "%s: quantity %r treated as none, line merged"
                                 % (mode, value))
        for value in (10 ** 400, 10 ** 5000 - 1):
            # A quantity this large must not raise: the check only reports.
            # The 5,000-digit value is built by arithmetic because
            # int("9" * 5000) raises on Python 3.11+ (sys.set_int_max_str_digits),
            # and this test must run on every version the package supports.
            plain([{"part_number": "AC-44170", "quantity": value}], "AC-44170")
            write([{"part_number": "AC-44170", "quantity": value}], "AC-44170")

    # ── QA 6 ────────────────────────────────────────────────────────────────

    def _assert_gtin_probe(self, source, expect_attach):
        """QA 6's probe: is a GTIN attached to the item, or not?"""
        items = [{"part_number": "AC-44170", "quantity": 1}]
        src = "AC-44170\n" + source
        written, _wf = write(items, src)
        plain_items, pf = plain(items, src)
        if expect_attach:
            self.assertEqual(written[0].get("gtin"), EAN13)
            self.assertIsNone(plain_items[0].get("gtin"),
                              "the default mode must not attach")
            self.assertEqual(self.suggested(pf, "MISSING_GTIN", "gtin")["new_value"],
                             EAN13)
        else:
            self.assertIsNone(written[0].get("gtin"))
            self.assertIsNone(plain_items[0].get("gtin"))

    def test_qa06_phone_number_is_not_a_gtin(self):
        self._assert_gtin_probe("Tel. +49 89 %s" % EAN8, False)

    def test_qa06_order_date_is_not_a_gtin(self):
        self._assert_gtin_probe("Bestellung 20261023", False)

    def test_qa06_digits_inside_a_link_are_not_a_gtin(self):
        self._assert_gtin_probe("https://shop.example/p/%s?x=1 EAN" % EAN13, False)

    def test_qa06_keyword_beside_a_valid_gtin_attaches_it(self):
        """Positive control: the rule still fires when the line names a GTIN."""
        self._assert_gtin_probe("EAN: %s" % EAN13, True)

    # ── QA 7 ────────────────────────────────────────────────────────────────

    def test_qa07_leading_zero_upc_is_the_same_number(self):
        """'0'+UPC-A and UPC-A are one number space: no id or GTIN finding."""
        items = [{"part_number": "AC-44170", "gtin": "0" + UPC12, "quantity": 1}]
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call(items, "AC-44170 UPC %s" % UPC12)
            bad = [f["code"] for f in fs
                   if f["code"] in ("UNGROUNDED_ID", "MISSING_GTIN")]
            self.assertEqual(bad, [], mode)

    def test_qa07_ean_prefixed_other_id_is_not_attached_again(self):
        items = [{"part_number": "AC-44170", "other_ids": ["EAN:%s" % EAN13],
                  "quantity": 1}]
        for mode, call in (("default", plain), ("apply", write)):
            out, fs = call(items, "AC-44170 EAN %s" % EAN13)
            self.assertEqual(codes(fs, "MISSING_GTIN"), [], mode)
            self.assertIsNone(out[0].get("gtin"), mode)

    def test_qa07_gtin_in_part_number_is_not_attached_again(self):
        items = [{"part_number": EAN13, "quantity": 1}]
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call(items, "Artikel %s EAN" % EAN13)
            self.assertEqual(codes(fs, "MISSING_GTIN"), [], mode)

    # ── QA 8 ────────────────────────────────────────────────────────────────

    def _assert_grounded(self, item_id, source, expect_flag):
        """QA 8's probe: is UNGROUNDED_ID raised, or not?"""
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call([{"part_number": item_id, "quantity": 1}], source)
            self.assertEqual(bool(codes(fs, "UNGROUNDED_ID")), expect_flag, mode)

    def test_qa08_short_id_is_not_grounded_by_a_longer_one(self):
        self._assert_grounded("AC-441", "Bitte AC-4417 liefern", True)

    def test_qa08_short_id_is_not_grounded_by_a_digit_suffix(self):
        self._assert_grounded("NW-2201", "NW-22019, 3 Stück", True)

    def test_qa08_a_date_does_not_ground_a_short_id(self):
        """'2610' is shorter than MIN_ID_LEN, so a date does not ground it."""
        self._assert_grounded("2610", "Datum 2026-10-26", True)

    def test_qa08_en_dash_still_grounds(self):
        self._assert_grounded("AC-4417", "Bitte AC\u20134417 liefern", False)

    def test_qa08_non_breaking_hyphen_still_grounds(self):
        self._assert_grounded("AC-4417", "Bitte AC\u20114417 liefern", False)

    def test_qa08_soft_hyphen_still_grounds(self):
        self._assert_grounded("AC-4417", "Bitte AC\u00ad4417 liefern", False)

    def test_qa08_zero_width_space_still_grounds(self):
        self._assert_grounded("AC-4417", "Bitte AC\u200b4417 liefern", False)

    # ── QA 9 ────────────────────────────────────────────────────────────────

    def test_qa09_variant_link_is_kept_and_reported(self):
        """The survivor keeps its link, takes the GTIN, and is marked uncertain."""
        items = [
            {"part_number": "AC-44170", "quantity": 2,
             "link": "https://www.amazon.de/dp/B0TESTAAAA"},
            {"part_number": "AC-44170", "link": "https://www.amazon.de/dp/B0TESTBBBB",
             "gtin": EAN13},
        ]
        src = ("AC-44170 2 x https://www.amazon.de/dp/B0TESTAAAA\n"
               "AC-44170 https://www.amazon.de/dp/B0TESTBBBB EAN %s" % EAN13)
        out, fs = plain(items, src)
        survivor = out[0]
        self.assertEqual(survivor.get("link"),
                         "https://www.amazon.de/dp/B0TESTAAAA")
        self.assertEqual(survivor.get("gtin"), EAN13)
        self.assertIs(survivor.get("uncertain"), True)
        link_f = [f for f in codes(fs, "VARIANT_SPLIT") if f["field"] == "link"]
        self.assertTrue(link_f)
        self.assertTrue(link_f[0]["old_value"].endswith("B0TESTBBBB"))

    def test_qa09_variant_notes_keep_input_order(self):
        items = [
            {"part_number": "AC-44170", "quantity": 2, "description": "Hex bolt"},
            {"part_number": "AC-44170", "description": "zinc"},
            {"part_number": "AC-44170", "description": "brass"},
        ]
        out, _fs = plain(items, "AC-44170 Hex bolt 2 x zinc brass")
        note = out[0].get("note") or ""
        self.assertTrue(0 <= note.find("zinc") < note.find("brass"),
                        "note=%r" % note)

    # ── QA 10 ───────────────────────────────────────────────────────────────

    def test_qa10_corrected_at_most_once(self):
        """8 pieces of a 2-pack corrected by two set lines gives 4, not 2."""
        items = [{"part_number": "AC-44170", "quantity": 8,
                  "description": "bolt 2-pack"}]
        src = "AC-44170 2-pack\nMenge: 4 x 2\nMenge: 2 x 2"
        written, wf = write(items, src)
        self.assertEqual(written[0]["quantity"], 4)
        self.assertLessEqual(len(codes(wf, "SET_MULTIPLIED")), 1)
        self.assert_written_and_suggested(items, src, [(0, "quantity", 4)],
                                          "SET_MULTIPLIED")

    def test_qa10_another_products_set_line_does_not_rewrite_a_correct_item(self):
        """B's set line must not rewrite A, whose 8 pieces are correct."""
        items = [{"part_number": "AC-44170", "quantity": 8},
                 {"part_number": "NW-22010", "quantity": 4, "unit": "set"}]
        src = "AC-44170 Schraube 8 Stück\nNW-22010 Kabelbinder 2er-Pack, Menge: 4 x 2"
        out, fs = write(items, src)
        hit = [f["item_index"] for f in codes(fs, "SET_MULTIPLIED")]
        self.assertNotIn(0, hit)
        self.assertEqual(out[0]["quantity"], 8)

    def test_qa10_pack_word_only_in_the_description_is_ignored(self):
        """The pack word is in the extractor's description, not in the source."""
        items = [{"part_number": "AC-44170", "quantity": 16,
                  "description": "bolt 4-pack"}]
        for mode, call in (("default", plain), ("apply", write)):
            out, fs = call(items, "AC-44170 bolt\nMenge: 4 x 4")
            self.assertEqual(codes(fs, "SET_MULTIPLIED"), [], mode)
            self.assertEqual(out[0]["quantity"], 16, mode)

    def test_qa10_pack_marker_forms_are_recognised(self):
        """'5 pack', '5-pack', '5er-Pack' and '5er Pack' all read as 5."""
        for marker in ("5 pack", "5-pack", "5er-Pack", "5er Pack"):
            items = [{"part_number": "AC-44170", "quantity": 10}]
            src = "AC-44170 %s\nqty 2 x 5" % marker
            written, _wf = write(items, src)
            self.assertEqual(written[0]["quantity"], 2, marker)
            self.assert_written_and_suggested(items, src, [(0, "quantity", 2)],
                                              "SET_MULTIPLIED")

    def test_qa10_pack_marker_on_another_line_does_not_count(self):
        """The card asked for the marker on the item's own SOURCE line."""
        items = [{"part_number": "AC-44170", "quantity": 8},
                 {"part_number": "NW-22010", "quantity": 1}]
        src = ("AC-44170 Schraube\nMenge: 4 x 2\n"
               "NW-22010 Kabelbinder 2er-Pack, 1 Stück")
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call(items, src)
            self.assertEqual(codes(fs, "SET_MULTIPLIED"), [], mode)

    # ── QA 11 ───────────────────────────────────────────────────────────────

    def test_qa11_only_real_marketplace_hosts_are_product_links(self):
        fakes = ("https://amazon.example.com/dp/B0TESTAAAA",
                 "https://www.ebay.attacker.example/itm/123456789012",
                 "https://amazon.de.evil.example/dp/B0TESTAAAA")
        accepted = [u for u in fakes if G.product_link_id(u)]
        self.assertEqual(accepted, [])
        self.assertTrue(G.product_link_id("https://www.amazon.de/dp/B0TESTAAAA"))

    # ── QA 12 ───────────────────────────────────────────────────────────────

    def test_qa12_link_punctuation_and_markdown_forms(self):
        want = "https://www.amazon.de/dp/B0TESTAAAA"
        cases = {
            "german quotes": "\u201e%s\u201c" % want,
            "trailing !": "%s!" % want,
            "apostrophe": "'%s'" % want,
            "markdown bold": "**%s**" % want,
            "colon": "%s:" % want,
            "markdown link": "[%s](%s)" % (want, want),
            "upper /DP/": want.replace("/dp/", "/DP/"),
        }
        for name, text in cases.items():
            links = G.find_links(text)
            ids = {G.product_link_id(u) for u in links}
            self.assertEqual(ids, {"ASIN:B0TESTAAAA"}, name)
            for link in links:
                self.assertEqual(link.lower(), want.lower(), name)

    # ── QA 13 ───────────────────────────────────────────────────────────────

    def test_qa13_wrong_input_types_raise_type_error(self):
        cases = (("bare list", [{"part_number": "AC-44170"}], "AC-44170"),
                 ("items tuple", {"items": ({"part_number": "AC-44170"},)},
                  "AC-44170"),
                 ("bytes source", {"items": []}, b"AC-44170"),
                 ("None source", {"items": []}, None))
        for name, extraction, source in cases:
            with self.assertRaises(TypeError, msg=name):
                G.ground(extraction, source)

    # ── QA 14 ───────────────────────────────────────────────────────────────

    def test_qa14_appended_item_carries_the_id_in_other_ids(self):
        """The placeholder has no part number and no description."""
        out, _fs = plain([], "Bitte 2 x https://www.amazon.de/dp/B0TESTCCCC")
        self.assertTrue(out)
        item = out[-1]
        self.assertFalse(item.get("part_number"))
        self.assertFalse(item.get("description"))
        self.assertIn("ASIN:B0TESTCCCC", item.get("other_ids") or [])

    def test_qa14_every_finding_has_exactly_the_eight_contract_keys(self):
        """The contract holds for every code, in both modes.

        The script checked the findings its earlier probes happened to leave in
        a global list, so the population depended on the order it ran them.
        This test runs one input per code, in both modes, and asserts that all
        six codes appear. A code that stops producing a finding fails here.
        """
        corpus = [
            ([{"part_number": "AC-441", "quantity": 1}], "Bitte AC-4417 liefern"),
            ([{"part_number": "AC-44170", "quantity": 2,
               "link": "https://www.amazon.de/dp/B0TESTAAAA"},
              {"part_number": "AC-44170",
               "link": "https://www.amazon.de/dp/B0TESTBBBB"}],
             "AC-44170 2 x https://www.amazon.de/dp/B0TESTAAAA\n"
             "AC-44170 https://www.amazon.de/dp/B0TESTBBBB"),
            ([{"link": "https://www.aliexpress.com/item/1005[Telefon].html",
               "quantity": 1}],
             "https://www.aliexpress.com/item/1005001111111111.html\n"
             "https://www.aliexpress.com/item/1005002222222222.html"),
            ([], "Bitte 2 x https://www.amazon.de/dp/B0TESTCCCC"),
            ([{"part_number": "AC-44170", "quantity": 8}],
             "AC-44170 2-pack\nMenge: 4 x 2"),
            ([{"part_number": "AC-44170"}],
             "AC-44170 Schraube, EAN: 2000000000015"),
        ]
        seen = set()
        for mode, call in (("default", plain), ("apply", write)):
            for items, source in corpus:
                _out, fs = call(items, source)
                for finding in fs:
                    self.assertEqual(set(finding), KEYS,
                                     "%s: %r" % (mode, finding))
                    seen.add(finding["code"])
        self.assertEqual(seen, {"UNGROUNDED_ID", "VARIANT_SPLIT",
                                "REDACTED_LINK", "UNCOVERED_LINK",
                                "SET_MULTIPLIED", "MISSING_GTIN"})

    # ── QA 15 ───────────────────────────────────────────────────────────────

    def test_qa15_hostile_inputs_finish_fast(self):
        """A 20,000-character hostile input must not backtrack."""
        hostile = {
            "url run": "https://" + "a" * 20000,
            "close parens": "https://www.amazon.de/dp/B0TESTAAAA" + ")" * 20000,
            "qty spaces": "qty" + " " * 20000 + "x",
            "digit run": "1" * 20000,
            "dash run": "A-" * 10000,
            "pack lines": "Menge: 4 x 2 2-pack\n" * 1000,
        }
        items = [{"part_number": "AC-44170", "quantity": 8}]
        for name, text in hostile.items():
            started = time.perf_counter()
            write(items, text)
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.5, "%s took %.2fs" % (name, elapsed))


if __name__ == "__main__":
    unittest.main()
