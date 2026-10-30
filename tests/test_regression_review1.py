"""Permanent regression tests: the 2026-10-27 review's 31 probes.

The reviewer checked every finding of the first independent review against the
module from a script outside the repo. A probe that passed once could regress
with nothing to catch it, so every probe now lives here. The input and the PASS
condition are the script's; only the mode is new.

The script calls ``ground()`` without ``apply``. Since fix round 3 the three
checks that write an extraction value (``SET_MULTIPLIED``, ``MISSING_GTIN`` and
the quantity of an item appended for an ``UNCOVERED_LINK``) only write with
``apply=True``. A probe that expects a written value therefore runs twice:

  - with ``apply=True``, where it asserts the value the check writes;
  - with the default ``apply=False``, where it asserts that the item keeps the
    caller's value and that one ``suggested`` finding names the value the
    write mode would produce.

Three probes are still open on this branch. Each one carries
``@unittest.expectedFailure`` and a comment that names the open item. The
expected result is the reviewer's, not the module's current output: the test
fails today and will pass when the item is fixed.

Every fixture is invented: made-up part numbers, made-up marketplace ids and
GTINs on the GS1 prefix 2, which is reserved for internal use.

    python3 -m unittest discover -s tests -q
"""
import os
import sys
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import harness_grounding as G  # noqa: E402
import functools  # noqa: E402
# The owner and quantity inference is opt-in (release gate, round 4).
ground_infer = functools.partial(G.ground, infer=True)


A = "https://www.amazon.de/dp/B0TESTAAA1"
B = "https://www.amazon.de/dp/B0TESTAAA2"


def gtin(body):
    total = sum(int(d) * (3 if i % 2 == 0 else 1)
                for i, d in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


def write(items, source):
    """ground() in write mode: the mode that corrects and attaches."""
    checked, findings = ground_infer({"items": items}, source, apply=True)
    return checked["items"], findings


def plain(items, source):
    """ground() in the default mode: it only suggests."""
    checked, findings = ground_infer({"items": items}, source)
    return checked["items"], findings


def codes(findings, code):
    return [f for f in findings if f["code"] == code]


class ReviewOneTest(unittest.TestCase):
    """One test method per probe of the first review's check script."""

    def suggested(self, findings, code, field, index):
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

    # ── B1 ──────────────────────────────────────────────────────────────────

    def test_b1_sharp_s_in_an_address_raises_no_flag(self):
        items = [{"part_number": "AC-4417", "quantity": 4}]
        src = ("Muster Werkzeug GmbH, Industriestraße 12, 80331 München\n"
               "Bestellung\n"
               "Pos. 1: 4 Stück Sechskantschraube M8, Art.-Nr. AC-4417\n")
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call(items, src)
            self.assertEqual(fs, [], mode)

    def test_b1_sharp_s_near_the_end_does_not_crash(self):
        items = [{"part_number": "AC-4417", "quantity": 4}]
        src = "Grüße aus der Lindenstraße\nBitte liefern: AC-4417"
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call(items, src)
            self.assertEqual(fs, [], mode)

    def test_b1_signature_above_a_three_line_order(self):
        src = ("Viele Grüße\nMax Muster\nMusterstraße 1\n\n"
               "> Pos. 1 AC-44170 4 Stück\n> Pos. 2 NW-22010 2 Stück\n"
               "> Pos. 3 KB-55120 1 Stück\n")
        items = [{"part_number": p, "quantity": q}
                 for p, q in (("AC-44170", 4), ("NW-22010", 2), ("KB-55120", 1))]
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call(items, src)
            self.assertEqual(codes(fs, "UNGROUNDED_ID"), [], mode)

    def test_b1_massband_set_line_is_corrected_without_a_flag(self):
        items = [{"part_number": "AC-44170", "quantity": 10}]
        src = "Maßband AC-44170 5er-Pack, Menge: 2 x 5"
        written, wf = write(items, src)
        self.assertEqual(written[0]["quantity"], 2)
        self.assertEqual(codes(wf, "UNGROUNDED_ID"), [])
        self.assert_written_and_suggested(items, src, [(0, "quantity", 2)],
                                          "SET_MULTIPLIED")

    def test_b1_fi_ligature_raises_no_flag(self):
        items = [{"part_number": "AC-4417", "quantity": 1}]
        src = "Proﬁl-Schiene AC-4417"
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call(items, src)
            self.assertEqual(fs, [], mode)

    # ── B2 ──────────────────────────────────────────────────────────────────

    def test_b2_unparsed_quantity_keeps_its_line(self):
        """'2 St.' is not a number, so the two lines stay two items."""
        items = [{"part_number": "AC-44170", "quantity": "4 St.",
                  "description": "Sechskantschraube M8 verzinkt"},
                 {"part_number": "AC-44170", "quantity": "2 St.",
                  "description": "Sechskantschraube M8 Edelstahl"}]
        src = ("Pos. 1  AC-44170 Sechskantschraube M8 verzinkt   4 St.\n"
               "Pos. 2  AC-44170 Sechskantschraube M8 Edelstahl  2 St.\n")
        for mode, call in (("default", plain), ("apply", write)):
            out, fs = call(items, src)
            self.assertEqual(len(out), 2, mode)
            self.assertEqual(codes(fs, "VARIANT_SPLIT"), [], mode)

    # ── S1 ──────────────────────────────────────────────────────────────────

    def test_s1a_neighbour_below_is_untouched(self):
        items = [{"part_number": "NW-22010", "description": "Kabelbinder 2er-Pack",
                  "quantity": 3, "unit": "set"},
                 {"part_number": "AC-44170", "description": "Schraube M8 2er-Pack",
                  "quantity": 6, "unit": "pack"}]
        src = ("NW-22010 Kabelbinder 2er-Pack\nMenge: 3 x 2\n"
               "AC-44170 Schraube M8, 6 Packungen à 2er-Pack\n")
        out, _fs = write(items, src)
        self.assertEqual(out[1]["quantity"], 6)
        self.assertEqual(out[1].get("unit"), "pack")

    def test_s1b_spec_example_screw_stays_at_eight(self):
        items = [{"part_number": "AC-44170", "quantity": 8},
                 {"part_number": "NW-22010", "quantity": 4, "unit": "set"}]
        src = ("AC-44170 Schraube 2er-Pack, 8 Stück\n"
               "NW-22010 Kabelbinder 2er-Pack, Menge: 4 x 2")
        for mode, call in (("default", plain), ("apply", write)):
            out, _fs = call(items, src)
            self.assertEqual(out[0]["quantity"], 8, mode)

    def test_s1c_two_set_lines_each_correct_their_own_item(self):
        items = [{"part_number": "AC-44170", "quantity": 8},
                 {"part_number": "NW-22010", "quantity": 6}]
        src = ("AC-44170 Schraube 2er-Pack, Menge: 4 x 2\n"
               "NW-22010 Kabelbinder 2er-Pack, Menge: 3 x 2")
        written, _wf = write(items, src)
        self.assertEqual((written[0]["quantity"], written[1]["quantity"]),
                         (4, 3))
        self.assert_written_and_suggested(
            items, src, [(0, "quantity", 4), (1, "quantity", 3)],
            "SET_MULTIPLIED")

    def test_s1d_own_two_pack_line_is_still_corrected(self):
        items = [{"part_number": "AC-44170", "quantity": 8}]
        src = "AC-44170 2-pack\nMenge: 4 x 2"
        written, _wf = write(items, src)
        self.assertEqual(written[0]["quantity"], 4)
        self.assert_written_and_suggested(items, src, [(0, "quantity", 4)],
                                          "SET_MULTIPLIED")

    # ── S2 ──────────────────────────────────────────────────────────────────

    def test_s2_a_hundred_kb_of_ebay_links_is_fast(self):
        src = "".join("https://www.ebay.de/itm/%012d\n" % (100000000000 + i)
                      for i in range(3000))
        started = time.perf_counter()
        write([], src)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0, "%.2fs for %d KB" % (elapsed, len(src) // 1024))

    def test_s2_a_hundred_kb_gtin_line_is_fast_and_attaches_once(self):
        src = "EAN " + " ".join(gtin("2%011d" % i) for i in range(7000))
        started = time.perf_counter()
        _out, fs = write([{"part_number": "AC-44170"}], src)
        elapsed = time.perf_counter() - started
        attached = [f for f in fs
                    if f["code"] == "MISSING_GTIN" and f["action"] == "attached"]
        self.assertLess(elapsed, 1.0, "%.2fs" % elapsed)
        self.assertLessEqual(len(attached), 1)

    # ── S3 ──────────────────────────────────────────────────────────────────

    def test_s3_a_long_id_with_many_tokens_is_fast(self):
        src = "Menge: 2 x 5\n" + " ".join("x%04d" % i for i in range(3300))
        started = time.perf_counter()
        write([{"part_number": "Q" * 3000, "quantity": 10}], src)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0, "%.2fs" % elapsed)

    # ── S4 ──────────────────────────────────────────────────────────────────

    def test_s4_a_dict_in_other_ids_does_not_crash(self):
        items = [{"part_number": "AC-44170", "quantity": 8,
                  "other_ids": [{"type": "EAN", "value": "2000000000015"}]}]
        src = "AC-44170 Schraube 2er-Pack\nMenge: 4 x 2\n"
        for mode, call in (("default", plain), ("apply", write)):
            call(items, src)

    # ── S5 ──────────────────────────────────────────────────────────────────

    def test_s5a_the_order_number_on_the_ean_line_is_not_the_gtin(self):
        items = [{"description": "Sechskantschraube M8",
                  "part_number": "AC-44170"}]
        src = ("Bestellung 20261023, Sechskantschraube M8 AC-44170, "
               "EAN 2000000000015")
        written, _wf = write(items, src)
        self.assertEqual(written[0].get("gtin"), "2000000000015")
        self.assert_written_and_suggested(
            items, src, [(0, "gtin", "2000000000015")], "MISSING_GTIN")

    def test_s5b_a_phone_number_after_ean_auf_anfrage_is_not_attached(self):
        items = [{"part_number": "NW-22010", "description": "Kabelbinder"}]
        src = ("NW-22010 Kabelbinder 200 mm - EAN auf Anfrage unter "
               "Tel. 089 12345670")
        for mode, call in (("default", plain), ("apply", write)):
            out, _fs = call(items, src)
            self.assertIsNone(out[0].get("gtin"), mode)

    # ── S6 ──────────────────────────────────────────────────────────────────

    def test_s6a_sibling_sizes_get_their_own_gtins(self):
        items = [{"part_number": "AC-44108",
                  "description": "Sechskantschraube M8 verzinkt"},
                 {"part_number": "AC-44110",
                  "description": "Sechskantschraube M10 verzinkt"}]
        src = ("AC-44108 Sechskantschraube M8 verzinkt EAN 2000000000015\n"
               "AC-44110 Sechskantschraube M10 verzinkt EAN 2000000000022")
        written, _wf = write(items, src)
        self.assertEqual((written[0].get("gtin"), written[1].get("gtin")),
                         ("2000000000015", "2000000000022"))
        self.assert_written_and_suggested(
            items, src,
            [(0, "gtin", "2000000000015"), (1, "gtin", "2000000000022")],
            "MISSING_GTIN")

    def test_s6b_another_products_ean_is_reported_not_attached(self):
        items = [{"part_number": "AC-44170",
                  "description": "Sechskantschraube M8"}]
        src = ("AC-44170 Sechskantschraube M8, 4 Stück\n"
               "NW-22010 Kabelbinder schwarz 200 mm, 2 Stück, "
               "EAN 2000000000022")
        for mode, call in (("default", plain), ("apply", write)):
            out, fs = call(items, src)
            self.assertIsNone(out[0].get("gtin"), mode)
            self.assertTrue(codes(fs, "MISSING_GTIN"), mode)

    def test_s6c_an_appended_placeholder_never_takes_a_gtin(self):
        src = ("1x %s\nNW-22010 Kabelbinder schwarz, 2 Stück, "
               "EAN 2000000000015" % A)
        for mode, call in (("default", plain), ("apply", write)):
            out, _fs = call([], src)
            self.assertTrue(out, mode)
            self.assertEqual([i.get("gtin") for i in out],
                             [None] * len(out), mode)

    # ── S7 ──────────────────────────────────────────────────────────────────

    def test_s7a_a_longer_id_does_not_ground_a_shorter_one(self):
        """'AC-4417, 8 Stück' must not ground 'AC-44178'."""
        items = [{"part_number": "AC-44178", "quantity": 8}]
        src = "Pos. 3: AC-4417, 8 Stück"
        for mode, call in (("default", plain), ("apply", write)):
            _out, fs = call(items, src)
            self.assertTrue(codes(fs, "UNGROUNDED_ID"), mode)

    def test_s7b_punctuation_joins_do_not_ground(self):
        cases = (("AC-4417-8", "AC-4417 | 8 | Schraube M8"),
                 ("4417-10", "Art.-Nr. 4417 (10 Stück)"),
                 ("1026", "Liefertermin 2026-10-26"))
        for part_number, src in cases:
            for mode, call in (("default", plain), ("apply", write)):
                _out, fs = call([{"part_number": part_number, "quantity": 1}],
                                src)
                self.assertTrue(codes(fs, "UNGROUNDED_ID"),
                                "%s (%s)" % (part_number, mode))

    # ── S8 ──────────────────────────────────────────────────────────────────

    def test_s8a_a_moved_gtin_is_reported_with_the_source_input_index(self):
        items = [{"part_number": "AC-44170", "quantity": 4},
                 {"part_number": "AC-44170", "gtin": "2000000000015"}]
        _out, fs = plain(items, "AC-44170 Schraube 4 Stück\n")
        moved = [f for f in codes(fs, "VARIANT_SPLIT") if f["field"] == "gtin"]
        self.assertTrue(moved)
        self.assertEqual(moved[0]["input_index"], 1)

    def test_s8b_a_variants_note_unit_and_price_are_not_lost(self):
        items = [{"part_number": "AC-44170", "quantity": 4, "unit": "piece",
                  "price": 10.0},
                 {"part_number": "AC-44170", "note": "Lieferung bis Freitag",
                  "unit": "set", "price": 12.5}]
        out, fs = plain(items, "AC-44170 Schraube 4 Stück\n")
        text = repr(out) + repr(fs)
        for value in ("Lieferung bis Freitag", "set", "12.5"):
            self.assertIn(value, text)

    # ── N4 ──────────────────────────────────────────────────────────────────

    def test_n4_a_conflict_finding_writes_nothing(self):
        items = [{"part_number": "AC-44170", "quantity": 2,
                  "link": "https://www.amazon.de/dp/B0TESTAAAA"},
                 {"part_number": "AC-44170",
                  "link": "https://www.amazon.de/dp/B0TESTBBBB"}]
        src = ("AC-44170 2 x https://www.amazon.de/dp/B0TESTAAAA\n"
               "AC-44170 https://www.amazon.de/dp/B0TESTBBBB")
        _out, fs = plain(items, src)
        conflicts = [f for f in codes(fs, "VARIANT_SPLIT")
                     if f["field"] == "link"]
        self.assertTrue(conflicts)
        for finding in conflicts:
            self.assertIsNone(finding["new_value"])

    # ── S10 ─────────────────────────────────────────────────────────────────

    def test_s10_common_quantity_labels_drive_the_set_correction(self):
        for label in ("Bestellmenge:", "Qty.", "Anzahl:", "Liefermenge",
                      "Menge (VE):"):
            items = [{"part_number": "AC-44170", "quantity": 8}]
            src = "AC-44170 Schraube M8 2er-Pack, %s 4 x 2" % label
            written, _wf = write(items, src)
            self.assertEqual(written[0]["quantity"], 4, label)
            self.assert_written_and_suggested(items, src, [(0, "quantity", 4)],
                                              "SET_MULTIPLIED")

    # ── S11 ─────────────────────────────────────────────────────────────────

    def test_s11a_separate_lines_each_keep_their_own_quantity(self):
        src = "%s\n2 Stück\n%s\n3 Stück\n" % (A, B)
        out, _fs = write([], src)
        self.assertEqual([i.get("quantity") for i in out], [2, 3])

    # open — S11 (first review): two links on ONE line share one quantity.
    # "A 2 Stück B 3 Stück" gives [None, 2] today, so B takes A's quantity.
    # The reviewer's expected result is [2, 3].
    @unittest.expectedFailure
    def test_s11b_two_links_on_one_line_each_keep_their_own_quantity(self):
        src = "%s 2 Stück %s 3 Stück" % (A, B)
        out, _fs = write([], src)
        self.assertEqual([i.get("quantity") for i in out], [2, 3])

    def test_s11c_a_million_pieces_is_a_million_not_zero(self):
        out, _fs = write([], "1.000.000 Stück %s" % A)
        self.assertEqual(out[0].get("quantity"), 1000000)

    # ── S12 ─────────────────────────────────────────────────────────────────

    def test_s12_gtin_keyword_spellings(self):
        for keyword in ("EAN13:", "GTIN13", "EANs:", "Barcodes:", "Bar-Code:",
                        "Strichcodenummer"):
            items = [{"part_number": "AC-44170", "description": "Schraube"}]
            src = "AC-44170 Schraube %s 2000000000015" % keyword
            written, _wf = write(items, src)
            self.assertEqual(written[0].get("gtin"), "2000000000015", keyword)
            self.assert_written_and_suggested(
                items, src, [(0, "gtin", "2000000000015")], "MISSING_GTIN")

    # ── S13 ─────────────────────────────────────────────────────────────────

    def test_s13_a_cut_aliexpress_link_with_a_query_string_is_restored(self):
        items = [{"link": "https://de.aliexpress.com/item/1005[Telefon]234567.html"
                          "?spm=a2g0o.detail", "quantity": 1}]
        src = "1x https://de.aliexpress.com/item/1005001234567.html?spm=a2g0o.detail"
        out, fs = write(items, src)
        red = codes(fs, "REDACTED_LINK")
        self.assertTrue(red)
        self.assertEqual(red[0]["action"], "restored")
        self.assertEqual(len(out), 1)

    # ── N1 ──────────────────────────────────────────────────────────────────

    # open — N1 (first review): REDACTED_LINK restores a link that is shorter
    # than the text around the cut. The restored eBay id here has 7 digits and
    # an eBay item number has 9 to 13, so the restore is impossible and the
    # finding must be "flagged", not "restored".
    @unittest.expectedFailure
    def test_n1_an_impossible_length_restore_is_flagged(self):
        items = [{"link": "https://www.ebay.de/itm/1234[Telefon]34567",
                  "quantity": 1}]
        src = "Bitte 1x https://www.ebay.de/itm/1234567 bestellen"
        _out, fs = plain(items, src)
        red = codes(fs, "REDACTED_LINK")
        self.assertTrue(red)
        self.assertEqual(red[0]["action"], "flagged")

    # ── N3 ──────────────────────────────────────────────────────────────────

    # open — N3 (first review): "EAN: 0000000000000" is a common "no EAN"
    # placeholder and must never be attached to an item.
    @unittest.expectedFailure
    def test_n3_a_zero_gtin_is_never_attached(self):
        items = [{"part_number": "AC-44170"}]
        src = "AC-44170 Schraube, EAN: 0000000000000"
        out, _fs = write(items, src)
        self.assertIsNone(out[0].get("gtin"))


if __name__ == "__main__":
    unittest.main()
