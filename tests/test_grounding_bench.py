"""Tests for the grounding benchmark scoring in bench/grounding/run.py.

Two tiny inline cases, one with an error and one without. They check the
scoring functions only: what counts as a catch, what counts as a correction,
and what counts as a false alarm.

The shipped case file is asserted for its SHAPE, not its scores: 34 cases, one
expected item on every injected error, synthetic data only. The scores
themselves are measured and reported, because a benchmark that fails when a
check gets worse cannot also be a regression gate without hiding the number it
exists to report. The one exception is the README consistency test, which
asserts that the figures the docs quote are the figures the bench produces.

    python3 -m unittest discover -s tests -v
"""
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import harness_grounding as G  # noqa: E402

RUN_PATH = os.path.join(REPO, "bench", "grounding", "run.py")

# A digit run a case could mean as a GTIN: the four GTIN lengths, not glued to
# another digit.
GTIN_RUN = re.compile(r"(?<!\d)(?:\d{8}|\d{12,14})(?!\d)")
_spec = importlib.util.spec_from_file_location("grounding_bench_run", RUN_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - file is in repo
    raise ImportError("cannot load %s" % RUN_PATH)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


# One item, one GTIN in the source, the GTIN missing from the extraction.
BROKEN = {
    "id": "inline-missing-gtin",
    "error_type": "omit_gtin",
    "expect_item": 0,
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

    def test_a_finding_on_another_item_is_not_a_catch(self):
        # The right code on the wrong item has not found the injected error:
        # the case names item 0, so a finding on item 1 is no catch.
        findings = [{"code": "MISSING_GTIN", "action": "attached",
                     "field": "gtin", "old_value": None,
                     "new_value": "4000000000006", "item_index": 1,
                     "input_index": 1, "evidence": "test"}]
        self.assertFalse(R.is_caught(BROKEN, findings))

        findings[0]["item_index"] = 0
        self.assertTrue(R.is_caught(BROKEN, findings))

    def test_a_case_without_expect_item_is_never_a_catch(self):
        nameless = dict(BROKEN)
        del nameless["expect_item"]
        findings = [{"code": "MISSING_GTIN", "action": "attached",
                     "item_index": 0, "evidence": "test"}]
        self.assertFalse(R.is_caught(nameless, findings))

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
    def test_the_shipped_case_file_holds_34_known_cases(self):
        cases = R.load_cases()
        self.assertEqual(len(cases), 34)
        counts = {}
        for case in cases:
            counts[case["error_type"]] = counts.get(case["error_type"], 0) + 1
        self.assertEqual(counts, {
            "omit_gtin": 6,
            "invented_part_number": 6,
            "redacted_link": 4,
            "missed_link": 6,
            "set_multiplied": 4,
            "variant_split": 4,
            "clean": 4,
        })

    def test_every_injected_error_names_its_item(self):
        for case in R.load_cases():
            if case["error_type"] == "clean":
                self.assertNotIn("expect_item", case)
            else:
                self.assertIsInstance(case["expect_item"], int, case["id"])
                self.assertGreaterEqual(case["expect_item"], 0, case["id"])

    def test_a_case_without_expect_item_is_rejected(self):
        case = copy.deepcopy(BROKEN)
        del case["expect_item"]
        path = os.path.join(self.tmp, "cases.json")
        with open(path, "w") as handle:
            json.dump([case], handle)
        with self.assertRaises(ValueError):
            R.load_cases(path)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_every_case_id_is_unique(self):
        ids = [case["id"] for case in R.load_cases()]
        self.assertEqual(len(ids), len(set(ids)))


class RedactedLinkCasesTest(unittest.TestCase):
    """The four REDACTED_LINK cases, and what each one must prove."""

    def setUp(self):
        self.cases = {case["id"]: case for case in R.load_cases()
                      if case["error_type"] == "redacted_link"}

    def test_four_redacted_link_cases_ship(self):
        self.assertEqual(len(self.cases), 4)

    def test_a_shared_prefix_stays_flagged_and_is_not_restored(self):
        case = self.cases["redacted-02"]
        checked, findings = G.ground(case["extractor_output"],
                                     case["source_text"])
        self.assertEqual([f["action"] for f in findings], ["flagged"])
        self.assertEqual([f["item_index"] for f in findings], [0])
        # The cut link is left exactly as the extractor wrote it.
        self.assertEqual(checked["items"][0]["link"],
                         case["extractor_output"]["items"][0]["link"])
        self.assertEqual(R.strip_ignored(checked), case["gold"])

    def test_a_unique_match_is_restored(self):
        case = self.cases["redacted-01"]
        checked, findings = G.ground(case["extractor_output"],
                                     case["source_text"])
        self.assertEqual([f["action"] for f in findings], ["restored"])
        self.assertEqual(R.strip_ignored(checked), case["gold"])
        self.assertNotIn("[", checked["items"][1]["link"])

    def test_two_cut_links_are_restored_on_their_own_item(self):
        case = self.cases["redacted-04"]
        _checked, findings = G.ground(case["extractor_output"],
                                      case["source_text"])
        self.assertEqual([f["action"] for f in findings],
                         ["restored", "restored"])
        self.assertEqual([f["item_index"] for f in findings], [0, 1])


class DocFiguresTest(unittest.TestCase):
    """Every benchmark figure in the docs must be the figure the bench prints.

    The bench is run in a temp copy, so the test never writes into the repo and
    the numbers come from an actual run, not from an import of the runner.
    """

    NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        bench = os.path.join(self.tmp, "bench", "grounding")
        os.makedirs(bench)
        shutil.copy(os.path.join(REPO, "harness_grounding.py"), self.tmp)
        for name in ("run.py", "cases.json"):
            shutil.copy(os.path.join(REPO, "bench", "grounding", name),
                        os.path.join(bench, name))
        proc = subprocess.run([sys.executable, "run.py"], cwd=bench,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(bench, "RESULTS.md")) as handle:
            self.generated = handle.read()
        with open(os.path.join(REPO, "bench", "grounding", "RESULTS.md")) as handle:
            self.committed = handle.read()
        with open(os.path.join(REPO, "README.md")) as handle:
            self.readme = handle.read()
        with open(os.path.join(REPO, "CHANGELOG.md")) as handle:
            self.changelog = handle.read()

    def _rows(self, text):
        """Return the score table as (type, n, caught, corrected) tuples."""
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) != 4 or cells[0] in ("error type", "---"):
                continue
            if not cells[1].isdigit():
                continue
            kind = cells[0].replace("`", "").replace(" (control)", "")
            rows.append((kind, int(cells[1]), cells[2], int(cells[3])))
        return rows

    def _figures(self):
        rows = self._rows(self.generated)
        self.assertTrue(rows, "the generated RESULTS.md has no table")
        clean = [row for row in rows if row[0] == "clean"]
        self.assertEqual(len(clean), 1, "RESULTS.md needs one clean control row")
        clean_n = clean[0][1]
        injected_rows = [row for row in rows if row[0] != "clean"]
        injected = sum(row[1] for row in injected_rows)
        total = injected + clean_n
        caught = sum(int(row[2]) for row in injected_rows)
        corrected = sum(row[3] for row in injected_rows)
        corrected_total = corrected + clean[0][3]
        false_alarms = re.search(
            r"False alarms: (\d+) of (\d+) error-free cases produced a finding",
            self.generated)
        self.assertIsNotNone(false_alarms, "RESULTS.md has no false-alarm line")
        return {
            "rows": rows,
            "types": len(injected_rows),
            "clean": clean_n,
            "injected": injected,
            "total": total,
            "caught": caught,
            "corrected": corrected,
            "corrected_total": corrected_total,
            "false_alarms": int(false_alarms.group(1)),
            "false_alarm_base": int(false_alarms.group(2)),
        }

    @staticmethod
    def _flat(text):
        """Collapse markdown line wrapping so a sentence matches anywhere."""
        return re.sub(r"\s+", " ", text)

    def _sentences(self, doc):
        """Return every figure sentence in doc as (name, groups)."""
        found = []
        patterns = (
            ("caught", r"(\d+) of (\d+) injected errors produced a finding "
                       r"with the expected code on the expected item\."),
            ("false alarms", r"(\d+) of (\d+) error-free controls produced "
                             r"any finding\."),
            ("corrected", r"(\d+) of (\d+) injected errors reached the gold "
                          r"extraction exactly; (\d+) of (\d+) cases did, "
                          r"counting the controls\."),
        )
        for name, pattern in patterns:
            for match in re.finditer(pattern, self._flat(doc)):
                found.append((name, tuple(int(group)
                                          for group in match.groups())))
        return found

    def test_the_committed_results_file_is_what_the_bench_generates(self):
        self.assertEqual(self.committed, self.generated)

    def test_the_readme_table_equals_the_generated_table(self):
        figures = self._figures()
        self.assertEqual(self._rows(self.readme), figures["rows"])

    def test_every_figure_sentence_in_the_docs_is_true(self):
        figures = self._figures()
        expected = {
            "caught": (figures["caught"], figures["injected"]),
            "false alarms": (figures["false_alarms"], figures["clean"]),
            "corrected": (figures["corrected"], figures["injected"],
                          figures["corrected_total"], figures["total"]),
        }
        for name, doc in (("README.md", self.readme),
                          ("CHANGELOG.md", self.changelog)):
            sentences = self._sentences(doc)
            for kind in expected:
                self.assertIn(kind, [kind for kind, _groups in sentences],
                              "%s states no %s figure" % (name, kind))
            for kind, groups in sentences:
                self.assertEqual(groups, expected[kind],
                                 "%s: %s figure %r != %r"
                                 % (name, kind, groups, expected[kind]))

    def test_the_readme_states_how_many_cases_are_errors_and_controls(self):
        figures = self._figures()
        pattern = (r"holds (\d+) invented order requests: (\d+) injected "
                   r"errors across (\w+) error types, four to six cases each, "
                   r"plus (\d+) error-free controls\.")
        match = re.search(pattern, self._flat(self.readme))
        self.assertIsNotNone(match, "README.md does not state the case counts")
        self.assertEqual(
            (int(match.group(1)), int(match.group(2)), match.group(3),
             int(match.group(4))),
            (figures["total"], figures["injected"],
             self.NUMBER_WORDS[figures["types"]], figures["clean"]))

    def test_the_changelog_states_how_many_cases_are_errors_and_controls(self):
        figures = self._figures()
        pattern = (r"The set now holds (\d+) cases — (\d+) injected errors "
                   r"across (\w+) error types plus (\d+) error-free controls")
        match = re.search(pattern, self._flat(self.changelog))
        self.assertIsNotNone(match, "CHANGELOG.md does not state the case counts")
        self.assertEqual(
            (int(match.group(1)), int(match.group(2)), match.group(3),
             int(match.group(4))),
            (figures["total"], figures["injected"],
             self.NUMBER_WORDS[figures["types"]], figures["clean"]))

    def test_the_readme_says_how_many_injected_errors_cannot_reach_gold(self):
        figures = self._figures()
        match = re.search(r"The other (\d+) injected errors cannot, by design",
                          self._flat(self.readme))
        self.assertIsNotNone(match, "README.md does not count the uncorrected")
        self.assertEqual(int(match.group(1)),
                         figures["injected"] - figures["corrected"])


class CasePrivacyTest(unittest.TestCase):
    """The repo goes public. The case file must stay inside its own fixtures."""

    def test_every_product_link_in_a_case_is_declared_by_that_case(self):
        # No case may carry a marketplace id that its own gold or extractor
        # output does not declare. Every id in the file was invented for it.
        for case in R.load_cases():
            declared = json.dumps([case["gold"], case["extractor_output"]])
            for url in G.find_links(case["source_text"]):
                self.assertIn(url, declared, "%s: %s" % (case["id"], url))

    def test_every_gtin_shaped_number_outside_a_link_is_a_valid_gtin(self):
        # find_gtins() returns only valid GTINs, so asserting over its output
        # could never fail. Walk the raw source text instead: every 8-, 12-,
        # 13- or 14-digit run outside a link is a number the case means as a
        # GTIN, so a typo in a check digit fails here. A digit run inside a
        # link is a marketplace id and is exempt.
        for case in R.load_cases():
            source = case["source_text"]
            spans = []
            for url in G.find_links(source):
                start = source.find(url)
                if start != -1:
                    spans.append((start, start + len(url)))
            for match in GTIN_RUN.finditer(source):
                if any(start <= match.start() and match.end() <= end
                       for start, end in spans):
                    continue
                self.assertTrue(G.gtin_valid(match.group(0)),
                                "%s: %s" % (case["id"], match.group(0)))

    def test_every_gtin_a_case_declares_is_a_valid_gtin(self):
        for case in R.load_cases():
            for side in ("gold", "extractor_output"):
                for item in case[side]["items"]:
                    gtin = item.get("gtin")
                    if gtin is None:
                        continue
                    self.assertTrue(G.gtin_valid(gtin),
                                    "%s %s: %r" % (case["id"], side, gtin))

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
