"""Tests for the grounding benchmark scoring in bench/grounding/run.py.

Two tiny inline cases, one with an error and one without. They check the
scoring functions only: what counts as a catch, what counts as a correction,
and what counts as a false alarm. The 30-case file is measured, not asserted
here — a benchmark that fails when a check gets worse cannot also be run as a
regression gate without hiding the number it exists to report.

    python3 -m unittest discover -s tests -v
"""
import copy
import importlib.util
import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import harness_grounding as G  # noqa: E402

RUN_PATH = os.path.join(REPO, "bench", "grounding", "run.py")
_spec = importlib.util.spec_from_file_location("grounding_bench_run", RUN_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - file is in repo
    raise ImportError("cannot load %s" % RUN_PATH)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


# One item, one GTIN in the source, the GTIN missing from the extraction.
BROKEN = {
    "id": "inline-missing-gtin",
    "error_type": "omit_gtin",
    "source_text": "Acme Tools - order request\n"
                   "Hex bolt M8 zinc plated, part AC-4417, GTIN 4000000000006\n"
                   "quantity: 40\n",
    "gold": {"items": [{"quantity": 40, "unit": "piece",
                        "description": "Hex bolt M8 zinc plated",
                        "part_number": "AC-4417", "gtin": "4000000000006"}]},
    "extractor_output": {"items": [{"quantity": 40, "unit": "piece",
                                    "description": "Hex bolt M8 zinc plated",
                                    "part_number": "AC-4417"}]},
}

# The same extraction, complete. ground() must change nothing.
SOUND = {
    "id": "inline-sound",
    "error_type": "clean",
    "source_text": BROKEN["source_text"],
    "gold": copy.deepcopy(BROKEN["gold"]),
    "extractor_output": copy.deepcopy(BROKEN["gold"]),
}


class StripIgnoredTest(unittest.TestCase):
    def test_removes_uncertain_and_note_and_leaves_the_input_alone(self):
        extraction = {"items": [{"quantity": 1, "uncertain": True,
                                 "note": "variant: grey"}]}
        before = copy.deepcopy(extraction)
        flat = R.strip_ignored(extraction)
        self.assertEqual(flat, {"items": [{"quantity": 1}]})
        self.assertEqual(extraction, before)

    def test_keeps_every_other_field(self):
        extraction = {"items": [{"gtin": "4000000000006", "part_number": "AC-4417",
                                 "link": "https://www.amazon.de/dp/B0ACM10001"}]}
        self.assertEqual(R.strip_ignored(extraction), extraction)


class ScoreCaseTest(unittest.TestCase):
    def test_an_injected_error_is_caught_and_corrected(self):
        result = R.score_case(BROKEN)
        self.assertTrue(result["caught"])
        self.assertTrue(result["corrected"])
        self.assertFalse(result["false_alarm"])
        self.assertEqual(result["codes"], ["MISSING_GTIN"])

    def test_a_sound_extraction_raises_no_finding(self):
        result = R.score_case(SOUND)
        self.assertFalse(result["caught"])
        self.assertTrue(result["corrected"])
        self.assertFalse(result["false_alarm"])
        self.assertEqual(result["codes"], [])

    def test_an_unexpected_finding_on_a_sound_case_is_a_false_alarm(self):
        findings = [{"code": "UNGROUNDED_ID", "item_index": 0, "value": "X",
                     "evidence": "test", "action": "flagged"}]
        self.assertTrue(R.is_false_alarm(SOUND, findings))
        self.assertFalse(R.is_false_alarm(BROKEN, findings))

    def test_a_finding_with_the_wrong_code_is_not_a_catch(self):
        findings = [{"code": "VARIANT_SPLIT", "item_index": 0, "value": "X",
                     "evidence": "test", "action": "merged"}]
        self.assertFalse(R.is_caught(BROKEN, findings))
        self.assertFalse(R.is_caught(SOUND, findings))

    def test_corrected_ignores_uncertain_and_note_but_not_a_wrong_value(self):
        wrong = copy.deepcopy(BROKEN["extractor_output"])
        wrong["items"][0]["uncertain"] = True
        wrong["items"][0]["note"] = "checked"
        wrong["items"][0]["gtin"] = "4000000000006"
        self.assertTrue(R.is_corrected(BROKEN, wrong))

        wrong["items"][0]["gtin"] = "4000000000013"
        self.assertFalse(R.is_corrected(BROKEN, wrong))


class SummariseTest(unittest.TestCase):
    def test_counts_per_error_type_and_false_alarms(self):
        scored = [R.score_case(BROKEN), R.score_case(SOUND)]
        summary = R.summarise(scored)
        self.assertEqual(summary["omit_gtin"]["n"], 1)
        self.assertEqual(summary["omit_gtin"]["caught"], 1)
        self.assertEqual(summary["omit_gtin"]["corrected"], 1)
        self.assertEqual(summary["clean"]["n"], 1)
        self.assertEqual(summary["clean"]["caught"], 0)
        self.assertEqual(R.total_false_alarms(scored), 0)

    def test_table_holds_the_control_row_with_no_catch_column(self):
        table = R.render_table(R.summarise([R.score_case(BROKEN),
                                            R.score_case(SOUND)]))
        self.assertIn("| omit_gtin | 1 | 1 | 1 |", table)
        self.assertIn("| clean | 1 | — | 1 |", table)


class LoadCasesTest(unittest.TestCase):
    def test_the_shipped_case_file_holds_30_known_cases(self):
        cases = R.load_cases()
        self.assertEqual(len(cases), 30)
        counts = {}
        for case in cases:
            counts[case["error_type"]] = counts.get(case["error_type"], 0) + 1
        self.assertEqual(counts, {
            "omit_gtin": 6,
            "invented_part_number": 6,
            "missed_link": 6,
            "set_multiplied": 4,
            "variant_split": 4,
            "clean": 4,
        })

    def test_every_case_id_is_unique(self):
        ids = [case["id"] for case in R.load_cases()]
        self.assertEqual(len(ids), len(set(ids)))


class CasePrivacyTest(unittest.TestCase):
    """The repo goes public. The case file must stay inside its own fixtures."""

    def test_every_product_link_in_a_case_is_declared_by_that_case(self):
        # No case may carry a marketplace id that its own gold or extractor
        # output does not declare. Every id in the file was invented for it.
        for case in R.load_cases():
            declared = json.dumps([case["gold"], case["extractor_output"]])
            for url in G.find_links(case["source_text"]):
                self.assertIn(url, declared, "%s: %s" % (case["id"], url))

    def test_every_gtin_in_a_case_is_valid_by_construction(self):
        for case in R.load_cases():
            for gtin in G.find_gtins(case["source_text"]):
                self.assertTrue(G.gtin_valid(gtin), case["id"])

    def test_no_marketplace_id_is_also_a_valid_gtin(self):
        # A marketplace item number with a valid GS1 check digit would be read
        # as a GTIN and would blur the check under test.
        for case in R.load_cases():
            for url in G.find_links(case["source_text"]):
                link_id = G.product_link_id(url)
                if link_id is None:
                    continue
                bare = link_id.split(":", 1)[1]
                if len(bare) in G.GTIN_LENGTHS:
                    self.assertFalse(G.gtin_valid(bare),
                                     "%s: %s" % (case["id"], bare))


if __name__ == "__main__":
    unittest.main()
