"""Permanent regression tests: the 2026-10-27 second review's inputs.

The second independent review of ``harness_grounding.py`` (at ``f8ecaf9``) found
2 blockers, 12 should-fix items and 6 nits: 20 inputs, each with the exact
failing text and the result the reviewer expected. Fix round 3 fixed 19 of
them, and cards 3.2 to 3.6 added the reviewer's own input as a test in
``tests/test_grounding.py``. This file adds what those cards could not:

  - the one input that had no test at all (N4');
  - the realistic German mail of ``review2-probes/p_realistic_de.py``, with the
    full expected result per item;
  - the 100 KB cost shapes of ``review2-probes/``, so a shape that was fast
    once cannot become slow with nothing to catch it.

Where each input already lives, and is therefore not repeated here. Cards 3.2
to 3.6 wrote these in ``tests/test_grounding.py``; the module is named in full so
a class name that also exists here (``LineFormCostTest``) cannot be confused
with it. Every one of them is run and green in this suite:

  B1'  MarketplaceLinkCostTest.test_ninety_eight_kilobytes_of_hostile_links_is_linear
  B2'  DateIsNotAQuantityTest.test_a_delivery_date_above_a_link_is_not_a_quantity
       and .test_a_delivery_date_below_a_link_is_not_a_quantity
  S1'  GroundSetMultipliedTest.test_a_stack_of_set_lines_corrects_only_the_item_above_it
  S2'  GroundSetMultipliedTest.test_a_set_line_on_an_item_whose_quantity_does_not_parse_corrects_nothing
  S3'  GroundSetMultipliedTest.test_a_line_that_names_an_item_by_id_does_not_ground_another_by_description
  S4'  GroundSetMultipliedTest.test_a_link_with_a_query_string_grounds_its_own_line
  S5'  UncoveredLinkQuantityTest (three inputs)
  S6'  UncoveredLinkUnitTest (five inputs)
  S7'  VerbatimIdTest (five ids, plus the invisible characters)
  S8'  GtinNumberLabelTest (four spellings)
  S9'  GtinNeighbourTest.test_a_gtin_on_a_line_that_describes_another_item_is_reported
       (the reviewer's S9'b input) and
       .test_a_bare_gtin_line_below_an_item_line_follows_the_mail_layout
       (S9'a, card 3.4b)
  S10' GroundMissingGtinTest.test_a_gtin_never_goes_to_an_item_whose_part_number_is_printed_elsewhere
  S11' VariantMergeLossTest (three values that blocked a merge)
  S12' MissingGtinEvidenceTest.test_a_reported_gtin_on_a_long_line_names_itself
  N1'  VariantMergeLossTest.test_the_note_finding_names_the_note_the_survivor_held
  N2'  EvidenceLengthTest (two inputs)
  N3'  GroundSetMultipliedTest.test_a_placeholder_on_the_owner_line_takes_no_set_correction
  N5'  tests/test_grounding.py LineFormCostTest
       .test_a_long_line_of_one_letter_tokens_is_cheap
  N6'  GroundMissingGtinTest.test_a_ligature_in_the_source_matches_the_description

Card 3.9b adds one input of its own, for the second branch of the guard that N4'
proved: `SecondReviewInputTest`
`.test_a_shared_word_does_not_reach_an_item_whose_id_is_printed`. That branch is
`if index in id_grounded: continue` on the `words_check` bucket, for a
description of three or more content words, and the test fails when the branch
is deleted.

Every fixture is invented: made-up part numbers, made-up marketplace ids and
GTINs on the GS1 prefix 2, which is reserved for internal use. The realistic
mail is synthetic German supplier mail with an invented customer, an invented
supplier and an invented phone number.

    python3 -m unittest discover -s tests -q
"""
import os
import random
import string
import sys
import time
import tracemalloc
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import harness_grounding as G  # noqa: E402
import functools  # noqa: E402
# The owner and quantity inference is opt-in (release gate, round 4).
ground_infer = functools.partial(G.ground, infer=True)


A = "https://www.amazon.de/dp/B0TESTAAA1"


def gtin(body):
    """Return body + the GS1 check digit, so the fixture is a valid GTIN."""
    total = sum(int(digit) * (3 if position % 2 == 0 else 1)
                for position, digit in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


G1 = gtin("200000000001")
G2 = gtin("200000000002")
G3 = gtin("200000000003")


def _measure(call):
    """Return (result, seconds, peak bytes) for one call.

    tracemalloc is started for the call only, so the peak is what that call
    allocated and not what the rest of the suite already holds.
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


def _codes(findings):
    return [finding["code"] for finding in findings]


class SecondReviewInputTest(unittest.TestCase):
    """The one input of the second review that no card had a test for (N4').

    N4' says three guards of the module had no test of their own: the S1
    follow-up ``id_grounded`` guard, the hard rule in ``_line_id_forms`` and the
    suffix ``rstrip`` in the redacted-link restore. The reviewer proved the
    first one matters with the input below: without the guard, the word
    "Packungen" of another product's line owned AC-44170 and its correct 6
    became 3 sets. The test passes on this branch and fails when the guard is
    removed, which is what a guard test is for.
    """

    def test_a_shared_pack_word_does_not_own_another_products_line(self):
        # The reviewer's N4' input, with the one change the guard needs to be
        # the only guard that can act: the owner line names its product by
        # description, not by part number. Card 3.5's S3' rule already stops a
        # line that names an item by its id from grounding another item by
        # description, so the reviewer's line with "NW-22010" on it passes
        # without this guard. Here the owner line holds no id, so only the
        # "an item whose own part number occurs in the source is grounded by
        # ids only" guard keeps AC-44170's 6 pieces. Without it, the word
        # "Kabelbinder" of the owner line reaches MATCH_RATIO for AC-44170 and
        # the set line rewrites its correct 6 to 3 sets.
        extraction = {"items": [
            {"description": "Kabelbinder schwarz 2er-Pack", "quantity": 3,
             "unit": "set"},
            {"part_number": "AC-44170", "description": "Kabelbinder wei\u00df",
             "quantity": 6, "unit": "pack"},
        ]}
        source = ("Kabelbinder schwarz 2er-Pack\n"
                  "Menge: 3 x 2\n"
                  "AC-44170 Kabelbinder wei\u00df, 6 Packungen \u00e0 2er-Pack\n")
        for apply in (False, True):
            with self.subTest(apply=apply):
                checked, findings = ground_infer(extraction, source, apply=apply)
                self.assertEqual(findings, [], "apply=%s" % apply)
                self.assertEqual(checked["items"][0]["quantity"], 3)
                self.assertEqual(checked["items"][1]["quantity"], 6)
                self.assertEqual(checked["items"][1]["unit"], "pack")

    def test_a_shared_word_does_not_reach_an_item_whose_id_is_printed(self):
        # The second branch of the same guard in _grounded_by_line(): the
        # words_check bucket, which holds the descriptions of three or more
        # content words. Both descriptions here hold three ("Kabelbinder",
        # "schwarz" or "wei\u00df", "best\u00e4ndig"), so neither item is read from
        # the words_any bucket, and only this branch can keep the two lines
        # apart. AC-44170's id is printed on line 3, so it is in id_grounded.
        # Line 1 shares two of AC-44170's three words, which is 2/3 and above
        # MATCH_RATIO: without the branch line 1 grounds both items and the set
        # line rewrites AC-44170's correct 6 to 3 sets.
        extraction = {"items": [
            {"part_number": "NW-22010",
             "description": "Kabelbinder schwarz UV-best\u00e4ndig 2er-Pack",
             "quantity": 6, "unit": "piece"},
            {"part_number": "AC-44170",
             "description": "Kabelbinder wei\u00df UV-best\u00e4ndig 2er-Pack",
             "quantity": 6, "unit": "piece"},
        ]}
        source = ("Kabelbinder schwarz UV-best\u00e4ndig 2er-Pack\n"
                  "Menge: 3 x 2\n"
                  "AC-44170 Kabelbinder wei\u00df UV-best\u00e4ndig, 6 Packungen "
                  "\u00e0 2er-Pack\n")
        # The default suggests only: NW-22010's 6 pieces would be 3 sets, and
        # AC-44170 gets no finding at all. NW-22010's part number is not in the
        # source, so its UNGROUNDED_ID stands above the suggestion.
        checked, findings = ground_infer(extraction, source)
        self.assertEqual(_codes(findings), ["UNGROUNDED_ID", "SET_MULTIPLIED"])
        suggestion = findings[1]
        self.assertEqual(suggestion["action"], "suggested")
        self.assertEqual(suggestion["item_index"], 0)
        self.assertEqual(suggestion["old_value"], 6)
        self.assertEqual(suggestion["new_value"], 3)
        self.assertEqual([finding["item_index"] for finding in findings], [0, 0],
                         "AC-44170 must get no finding")
        self.assertEqual([item["quantity"] for item in checked["items"]], [6, 6])
        self.assertEqual([item["unit"] for item in checked["items"]],
                         ["piece", "piece"])

        # apply=True writes the one correction and leaves AC-44170 alone.
        checked, findings = ground_infer(extraction, source, apply=True)
        self.assertEqual(_codes(findings), ["UNGROUNDED_ID", "SET_MULTIPLIED"])
        correction = findings[1]
        self.assertEqual(correction["action"], "corrected")
        self.assertEqual(correction["item_index"], 0)
        self.assertEqual([finding["item_index"] for finding in findings], [0, 0],
                         "AC-44170 must get no finding")
        self.assertEqual([item["quantity"] for item in checked["items"]], [3, 6])
        self.assertEqual([item["unit"] for item in checked["items"]],
                         ["set", "piece"])

    def test_a_form_does_not_run_through_punctuation_that_is_not_a_joiner(self):
        # The hard rule of _line_id_forms. The flat text of
        # "Pos. 1: AC-4417, 8 St\u00fcck 2er-Pack" holds "AC44178", so without the
        # rule the line would ground the item "AC-44178" and the set line below
        # it would rewrite that item's correct 8 to 4 sets.
        #
        # The owner line carries the pack marker, and it has to: a set line
        # corrects only an item whose line names the pack size, so without the
        # marker this test would pass with the hard rule deleted (N4'). The
        # control below writes the same id contiguously and the set line acts.
        extraction = {"items": [{"part_number": "AC-44178", "quantity": 8,
                                 "unit": "piece"}]}
        source = ("Pos. 1: AC-4417, 8 St\u00fcck 2er-Pack\n"
                  "Menge: 4 x 2\n")
        for apply in (False, True):
            with self.subTest(apply=apply):
                checked, findings = ground_infer(extraction, source, apply=apply)
                self.assertEqual(_codes(findings), ["UNGROUNDED_ID"],
                                 "apply=%s" % apply)
                self.assertEqual(checked["items"][0]["quantity"], 8)
                self.assertEqual(checked["items"][0]["unit"], "piece")

        contiguous = source.replace("AC-4417, 8", "AC-44178, 8")
        checked, findings = ground_infer(extraction, contiguous)
        self.assertEqual(_codes(findings), ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 8)
        checked, findings = ground_infer(extraction, contiguous, apply=True)
        self.assertEqual(_codes(findings), ["SET_MULTIPLIED"])
        self.assertEqual(checked["items"][0]["quantity"], 4)

    def test_a_cut_link_is_restored_when_the_suffix_ends_a_sentence(self):
        # The suffix rstrip of the redacted-link restore. The extractor kept the
        # full stop that closes the sentence around the link, so the suffix is
        # "AAA1." and no source link ends with it. Without the rstrip the link
        # is flagged instead of restored.
        extraction = {"items": [{
            "description": "Widget",
            "link": "https://www.amazon.de/dp/B0TEST[Telefon]AAA1."}]}
        source = "Bitte 1x https://www.amazon.de/dp/B0TESTAAA1\n"
        for apply in (False, True):
            with self.subTest(apply=apply):
                checked, findings = ground_infer(extraction, source, apply=apply)
                self.assertEqual(_codes(findings), ["REDACTED_LINK"],
                                 "apply=%s" % apply)
                self.assertEqual(findings[0]["action"], "restored" if apply else "suggested",
                                 "apply=%s" % apply)
                self.assertEqual(checked["items"][0]["link"] if apply else findings[0]["new_value"], A)


class RealisticGermanMailTest(unittest.TestCase):
    """The reviewer's realistic German supplier mail, item by item.

    The mail is ``review2-probes/p_realistic_de.py``: a position table with an
    EAN column, a position with a stack of set lines, a position with a pack
    marker and a plain quantity, and an Amazon link with its quantity on the
    line below.

    The expected result per item comes from the review and the docs:

    - Items 0 to 2 keep what the extractor read. Their numbers stand in the
      table's EAN column and the only "EAN" the mail prints is the column
      header, so no GTIN is read from the table. The reviewer records this as a
      documented limit, not a defect.
    - Item 3 (Pos. 40) carries a stack of two set lines, "Bestellmenge: 3 x 2"
      and "Liefermenge: 3 x 2". The stack belongs to the position above it, so
      the quantity becomes 3 sets (S1'). Its part number ZZ-40000 occurs
      nowhere in the mail, so UNGROUNDED_ID flags it.
    - Item 4 (Pos. 50) states "6 St\u00fcck" of a 2er-Pack. That is six pieces,
      not three sets, and the stack above must not reach it (S1').
    - Item 5 is the item this module appends for the uncovered Amazon link. The
      link takes the "2 St\u00fcck" below it and states the unit "piece" (S6').
      The default mode appends it without a quantity and suggests 2, because
      card 3.1 moved that write behind apply=True.

    The quoted reply at the end of the mail ("Bitte liefern: AC-44170 8
    St\u00fcck, ...") changes nothing: no check reads a quoted block, and the
    docs do not say that one counts.
    """

    SOURCE = """Sehr geehrte Damen und Herren,

vielen Dank f\u00fcr Ihre Bestellung 4500-012345 vom 26.10.2026. Wir best\u00e4tigen:

Pos.  Menge   Art.-Nr.    Bezeichnung                     EAN
10    8 St.   AC-44170    Sechskantschraube DIN 933 M8x40 {g1}
20    4 St.   NW-22010    Kabelbinder 200 mm schwarz      {g2}
30    2 Paar  XY-90001    Schutzhandschuhe Gr. 9          {g3}

Pos. 40: Profilzylinder 30/30, 2er-Pack
Bestellmenge: 3 x 2
Liefermenge: 3 x 2
Pos. 50: D\u00fcbel 6 mm, 2er-Pack, 6 St\u00fcck

Zus\u00e4tzlich wie besprochen:
https://www.amazon.de/dp/B0TESTAAA1
2 St\u00fcck

Liefertermin:
13.11.2026

Mit freundlichen Gr\u00fc\u00dfen
Max Mustermann
Beispiel Werkzeughandel GmbH \u00b7 Industriestra\u00dfe 12 \u00b7 80331 M\u00fcnchen
Tel. +49 89 12345670 \u00b7 USt-IdNr. DE 123 456 789

> Am 25.10.2026 schrieb Einkauf:
> Bitte liefern: AC-44170 8 St\u00fcck, NW-22010 4 St\u00fcck
""".format(g1=G1, g2=G2, g3=G3)

    EXTRACTION = {"items": [
        {"part_number": "AC-44170",
         "description": "Sechskantschraube DIN 933 M8x40",
         "quantity": 8, "unit": "piece"},
        {"part_number": "NW-22010",
         "description": "Kabelbinder 200 mm schwarz",
         "quantity": 4, "unit": "piece"},
        {"part_number": "XY-90001", "description": "Schutzhandschuhe Gr. 9",
         "quantity": 2, "unit": "Paar"},
        {"part_number": "ZZ-40000",
         "description": "Profilzylinder 30/30 2er-Pack",
         "quantity": 6, "unit": "piece"},
        {"description": "D\u00fcbel 6 mm 2er-Pack", "quantity": 6,
         "unit": "piece"},
    ]}

    def test_the_mail_reads_the_same_in_both_modes(self):
        # The default mode: it suggests the two writes and makes neither.
        checked, findings = ground_infer(self.EXTRACTION, self.SOURCE)
        items = checked["items"]
        self.assertEqual(_codes(findings),
                         ["UNGROUNDED_ID", "UNCOVERED_LINK", "UNCOVERED_LINK",
                          "SET_MULTIPLIED"])
        self.assertEqual([finding["action"] for finding in findings],
                         ["flagged", "added", "suggested", "suggested"])
        self.assertEqual([finding["item_index"] for finding in findings],
                         [3, 5, 5, 3])

        # Items 0 to 2: the extractor's own values, no GTIN from the table.
        self.assertEqual([items[index].get("part_number")
                          for index in range(3)],
                         ["AC-44170", "NW-22010", "XY-90001"])
        self.assertEqual([items[index].get("quantity")
                          for index in range(3)], [8, 4, 2])
        self.assertEqual([items[index].get("unit") for index in range(3)],
                         ["piece", "piece", "Paar"])

        # Item 3: the stack belongs to Pos. 40. The default only suggests.
        self.assertEqual(items[3].get("quantity"), 6)
        self.assertEqual(items[3].get("unit"), "piece")
        self.assertTrue(items[3].get("uncertain"))
        set_finding = [f for f in findings if f["code"] == "SET_MULTIPLIED"][0]
        self.assertEqual(set_finding["old_value"], 6)
        self.assertEqual(set_finding["new_value"], 3)

        # Item 4: Pos. 50 states six pieces of a 2er-Pack, and keeps them.
        self.assertEqual(items[4].get("quantity"), 6)
        self.assertEqual(items[4].get("unit"), "piece")
        self.assertNotIn("uncertain", items[4])

        # Item 5: the appended item for the Amazon link. The default appends it
        # with the link and its marketplace id, and suggests the quantity.
        self.assertEqual(len(items), 6)
        self.assertEqual(items[5].get("link"), A)
        self.assertEqual(items[5].get("other_ids"), ["ASIN:B0TESTAAA1"])
        self.assertIsNone(items[5].get("quantity"))
        self.assertIsNone(items[5].get("unit"))
        self.assertTrue(items[5].get("uncertain"))
        suggested = [f for f in findings
                     if f["code"] == "UNCOVERED_LINK"
                     and f["action"] == "suggested"][0]
        self.assertEqual(suggested["field"], "quantity")
        self.assertEqual(suggested["new_value"], 2)

        # No GTIN anywhere: the only "EAN" is the column header.
        self.assertEqual([finding for finding in findings
                          if finding["code"] == "MISSING_GTIN"], [])
        for index, item in enumerate(items):
            self.assertNotIn("gtin", item, "item %d took a GTIN" % index)

    def test_the_mail_writes_the_same_two_values_with_apply(self):
        checked, findings = ground_infer(self.EXTRACTION, self.SOURCE, apply=True)
        items = checked["items"]
        self.assertEqual(_codes(findings),
                         ["UNGROUNDED_ID", "UNCOVERED_LINK", "SET_MULTIPLIED"])
        self.assertEqual([finding["action"] for finding in findings],
                         ["flagged", "added", "corrected"])
        self.assertEqual([finding["item_index"] for finding in findings],
                         [3, 5, 3])

        self.assertEqual([items[index].get("quantity")
                          for index in range(3)], [8, 4, 2])
        self.assertEqual([items[index].get("unit") for index in range(3)],
                         ["piece", "piece", "Paar"])

        # Pos. 40: three sets of two.
        self.assertEqual(items[3].get("quantity"), 3)
        self.assertEqual(items[3].get("unit"), "set")
        self.assertTrue(items[3].get("uncertain"))

        # Pos. 50: six pieces, untouched by the stack above.
        self.assertEqual(items[4].get("quantity"), 6)
        self.assertEqual(items[4].get("unit"), "piece")
        self.assertNotIn("uncertain", items[4])

        # The appended item took its own quantity line and the source's unit.
        self.assertEqual(items[5].get("link"), A)
        self.assertEqual(items[5].get("other_ids"), ["ASIN:B0TESTAAA1"])
        self.assertEqual(items[5].get("quantity"), 2)
        self.assertEqual(items[5].get("unit"), "piece")

        self.assertEqual([finding for finding in findings
                          if finding["code"] == "MISSING_GTIN"], [])
        for index, item in enumerate(items):
            self.assertNotIn("gtin", item, "item %d took a GTIN" % index)


class ShapeCostTest(unittest.TestCase):
    """Every 100 KB shape of review2-probes/p_shapes.py finishes in under 1 s.

    A supplier mail is untrusted input, so a shape that takes seconds is a
    denial of service on the producer. The reviewer measured these shapes at
    ``f8ecaf9`` and found all of them under a second. The bound is the one the
    rest of the suite uses.

    Two of them were over the bound on this branch and are fixed with the tests
    (see CHANGELOG.md, card 3.8):

    - "no-boundary letters" scanned every occurrence of the id in a 100 KB run
      of one letter, which is quadratic in the run: 2.2 s.
    - "GTIN line per position" rebuilt the comparable form of every item's part
      number once per source line: 1.0 s.
    """

    SIZE = 100 * 1024
    BOUND_SECONDS = 1.0

    @staticmethod
    def _items(count, **extra):
        return {"items": [dict({"part_number": "AC-%05d" % index,
                                "quantity": 8,
                                "description": "Schraube Typ%d" % index},
                               **extra) for index in range(count)]}

    def shapes(self):
        """Return {name: (source, extraction)} exactly as the probe builds them."""
        size = self.SIZE
        shapes = {}
        lines = []
        index = 0
        while sum(map(len, lines)) < size:
            lines.append("AC-%05d EAN %s"
                         % (index, gtin("2%011d" % index)))
            index += 1
        shapes["GTIN line per position, 200 items"] = (
            "\n".join(lines), self._items(200))
        shapes["one line of distinct GTINs"] = (
            " ".join("EAN %s" % gtin("2%011d" % index)
                     for index in range(size // 18)), self._items(5))
        shapes["set line per line, 200 items"] = (
            "\n".join("AC-%05d 2er-Pack Menge: 4 x 2" % index
                      for index in range(size // 30)), self._items(200))
        shapes["one line of set lines"] = (
            " ".join("Menge: 4 x 2er" for _ in range(size // 15)),
            self._items(5))
        shapes["digits"] = ("1" * size + "\nMenge: 4 x 2\n", self._items(5))
        shapes["joined digits"] = ("1-" * (size // 2) + "\nMenge: 4 x 2\n",
                                   self._items(5))
        shapes["sharp s"] = ("\u00df" * size + "\nMenge: 4 x 2\n",
                             self._items(5))
        shapes["no-boundary letters, 50 items AAAA"] = (
            "A" * size, {"items": [{"part_number": "AAAA"}
                                   for _ in range(50)]})
        shapes["pack markers"] = ("2er " * (size // 4) + "\nMenge: 4 x 2\n",
                                  self._items(5))
        shapes["brackets in link"] = (
            "https://www.ebay.de/itm/" + "1" * 20 + "[" * size,
            {"items": [{"link": "https://www.ebay.de/itm/1[" + "]" * size}]})
        shapes["many 1-word descriptions, words line"] = (
            " ".join("Wort%d" % (index % 3000) for index in range(size // 8))
            + "\nMenge: 4 x 2\n",
            {"items": [{"description": "Wort%d" % index, "quantity": 8}
                       for index in range(3000)]})
        return shapes

    def test_every_shape_finishes_in_under_a_second(self):
        shapes = self.shapes()
        # Every shape the probe builds, so a new shape in the probe must be
        # added here too.
        self.assertEqual(len(shapes), 11)
        for name, (source, extraction) in shapes.items():
            with self.subTest(shape=name):
                started = time.perf_counter()
                ground_infer(extraction, source)
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, self.BOUND_SECONDS,
                                "%s took %.3f s on %d bytes"
                                % (name, elapsed, len(source.encode())))


class FindLinksCostTest(unittest.TestCase):
    """9,212 links in 100 KB are read in under a second, on one line and on many.

    ``find_links`` de-duplicates by scanning a list, so the cost of the scan is
    not linear in the number of links. The reviewer measured 0.31 s for 9,212
    links in 100 KB and recorded it as minor; the bound here is the one the rest
    of the suite uses, so a change that makes the scan quadratic fails.
    """

    SIZE = 100000
    BOUND_SECONDS = 1.0

    @staticmethod
    def _links(size):
        """Return the probe's links: base-36 hosts, one per link, until size."""
        alphabet = string.digits + string.ascii_lowercase
        parts = []
        total = 0
        index = 0
        while total < size:
            number = index
            digits = ""
            while True:
                number, rest = divmod(number, 36)
                digits = alphabet[rest] + digits
                if number == 0:
                    break
            part = "http://%s" % digits
            parts.append(part)
            total += len(part) + 1
            index += 1
        return parts

    def test_one_line_of_links_is_linear(self):
        source = " ".join(self._links(self.SIZE))
        self.assertEqual(len(source.encode()), 99999)
        started = time.perf_counter()
        found = G.find_links(source)
        elapsed = time.perf_counter() - started
        # The links are distinct, so the de-duplication has real work to do.
        self.assertEqual(len(found), 9212)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "find_links took %.3f s on %d links"
                        % (elapsed, len(found)))
        started = time.perf_counter()
        ground_infer({"items": [{"part_number": "AC-44170", "quantity": 8}]},
                 source + "\nMenge: 4 x 2\n")
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d links"
                        % (elapsed, len(found)))

    def test_one_link_per_line_is_linear(self):
        source = "\n".join(self._links(self.SIZE))
        started = time.perf_counter()
        found = G.find_links(source)
        elapsed = time.perf_counter() - started
        self.assertEqual(len(found), 9212)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "find_links took %.3f s on %d links"
                        % (elapsed, len(found)))
        started = time.perf_counter()
        ground_infer({"items": [{"part_number": "AC-44170", "quantity": 8}]},
                 source + "\nMenge: 4 x 2\n")
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d links"
                        % (elapsed, len(found)))


class LineFormCostReview2Test(unittest.TestCase):
    """review2-probes/p_lineforms.py: a 100 KB line of tokens beside a 64-char id.

    The probe's own input: 50,000 one-letter tokens built from a fixed random
    seed, one "Menge: 4 x 2" line and one 64-character part number. N5' is the
    same shape with the letters in alphabetical order, and card 3.2 bounded it
    at 50 MB. This test reads the probe's random letters, so it also covers a
    token run the alphabetical shape never builds.
    """

    SIZE = 100 * 1024
    ID_LEN = 64
    BOUND_SECONDS = 1.0
    PEAK_BYTES = 50 * 1024 * 1024

    def _source_and_extraction(self):
        random.seed(3)
        tokens = []
        total = 0
        while total < self.SIZE:
            tokens.append(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
            total += 2
        source = "Menge: 4 x 2\n" + " ".join(tokens) + "\n"
        extraction = {"items": [{"part_number": "A" * self.ID_LEN,
                                 "quantity": 8}]}
        return source, extraction

    def test_the_probes_shape_is_fast_and_small(self):
        source, extraction = self._source_and_extraction()
        (checked, findings), elapsed, peak = _measure(
            lambda: ground_infer(extraction, source))
        # The 64-character id occurs nowhere in the source, so the id scan has
        # real work to do: it may not pass by finding nothing. The set line
        # above the token line owns no item, so it corrects nothing.
        self.assertEqual(_codes(findings), ["UNGROUNDED_ID"])
        self.assertEqual(checked["items"][0]["quantity"], 8)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d bytes"
                        % (elapsed, len(source)))
        self.assertLess(peak, self.PEAK_BYTES,
                        "ground peaked at %.0f MB on %d bytes"
                        % (peak / 1e6, len(source)))


class LinksBlowupCostTest(unittest.TestCase):
    """review2-probes/p_links_blowup.py: 420 eBay links, line k has a k-digit id.

    B1' is the same input with a fixed digit run and card 3.2 bounded it. This
    test reads the probe's own input: random digits from a fixed seed, so a
    line's run may start with a zero or repeat another line's id, and the
    covered-link index sees a different set of lengths.
    """

    LINKS = 420
    BOUND_SECONDS = 1.0
    PEAK_BYTES = 100 * 1024 * 1024

    def _source(self):
        random.seed(7)
        lines = []
        for length in range(1, self.LINKS + 1):
            digits = "".join(random.choice(string.digits)
                             for _ in range(length))
            lines.append("https://www.ebay.de/itm/%s" % digits)
        return "\n".join(lines)

    def test_the_probes_shape_is_fast_and_small(self):
        source = self._source()
        self.assertEqual(len(source.encode()), 98909)
        (checked, findings), elapsed, peak = _measure(
            lambda: ground_infer({"items": []}, source))
        # Only the 9 to 13 digit runs are marketplace ids, so the check has real
        # work to do: it may not pass by finding nothing.
        self.assertEqual(len(findings), 5)
        self.assertEqual(len(checked["items"]), 5)
        self.assertLess(elapsed, self.BOUND_SECONDS,
                        "ground took %.3f s on %d bytes"
                        % (elapsed, len(source)))
        self.assertLess(peak, self.PEAK_BYTES,
                        "ground peaked at %.0f MB on %d bytes"
                        % (peak / 1e6, len(source)))


if __name__ == "__main__":
    unittest.main()
