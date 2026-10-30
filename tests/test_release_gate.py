"""The release gate: no check may propose a WRONG value on the frozen case set.

bench/gate/generate.py builds 3,448 extractions with the truth stated by
construction (bench/gate/README.md says how). This test fails when, in the
default mode or with apply=True,

- any value a check proposes disagrees with the truth: another item, another
  value, or a value where the truth states none;
- any case raises, or ground() mutates the caller's input;
- a finding carries other keys than the eight of the contract;
- the default mode changes a value the caller passed in. It may set
  "uncertain" and append a placeholder that holds only link, other_ids and
  uncertain.

The opt-in inference (infer=True) is measured by bench/gate/score.py and not
asserted here: it does not pass the gate, which is why it is opt-in.

The set is frozen. cases.sha256 pins the digest of what generate.build()
returns, and the first test fails when they differ, so the set cannot move to
fit the code. Changing it is a decision of its own: regenerate, pin the new
digest, and say why in the same commit.

    python3 -m unittest tests.test_release_gate -v
"""
import copy
import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "bench", "gate")
sys.path.insert(0, REPO)
import harness_grounding as G  # noqa: E402


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(GATE, filename))
    if spec is None or spec.loader is None:  # pragma: no cover - files are in the repo
        raise ImportError("cannot load %s" % filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATE = _load("gate_generate", "generate.py")
SCORE = _load("gate_score", "score.py")
SHOW = 10


class ReleaseGateTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.cases = GENERATE.build()

    def test_the_case_set_is_the_pinned_one(self):
        with open(os.path.join(GATE, "cases.sha256")) as handle:
            pinned = handle.read().split()[0]
        self.assertEqual(GENERATE.digest(self.cases), pinned,
                         "generate.py no longer builds the pinned set")
        self.assertEqual(len(self.cases), 3448)

    def _run(self, **kwargs):
        mode = "apply" if kwargs.get("apply") else "default"
        wrong, crashes, violations, mutated = [], [], [], []
        for case in self.cases:
            items_in = SCORE.prepare_items(case)
            call_items = copy.deepcopy(items_in)
            try:
                checked, findings = G.ground({"items": call_items}, case["source_text"], **kwargs)
            except Exception as exc:  # a crash fails the gate, so it is collected, not raised
                crashes.append("%s: %s: %s" % (case["id"], type(exc).__name__, exc))
                continue
            if call_items != items_in:
                mutated.append(case["id"])
            proposals, case_violations, _ = SCORE.score_case(case, items_in, checked, findings, mode)
            wrong.extend("%s %s: %s" % (case["id"], check, detail)
                         for check, verdict, detail in proposals if verdict == "WRONG")
            violations.extend("%s %s: %s" % (case["id"], kind, detail) for kind, detail in case_violations)
        return wrong, crashes, violations, mutated

    def _assert_gate(self, **kwargs):
        wrong, crashes, violations, mutated = self._run(**kwargs)
        self.assertEqual(crashes[:SHOW], [], "%d cases raise" % len(crashes))
        self.assertEqual(mutated[:SHOW], [], "%d cases mutate the input" % len(mutated))
        self.assertEqual(violations[:SHOW], [], "%d contract or default-mode violations" % len(violations))
        self.assertEqual(wrong[:SHOW], [], "%d WRONG values" % len(wrong))

    def test_the_default_mode_proposes_no_wrong_value(self):
        self._assert_gate()

    def test_apply_writes_no_wrong_value(self):
        self._assert_gate(apply=True)

    def test_the_scorer_fails_a_wrong_owner_and_a_default_write(self):
        # The gate must be able to fail. The EAN under "Pos. 1" belongs to the
        # position the extraction missed, so attaching it to the only item the
        # extraction kept is WRONG, and writing it in the default mode is a
        # violation as well.
        case = next(c for c in self.cases if c["id"] == "gtin/pos/below/n2/first/drop")
        items_in = SCORE.prepare_items(case)
        gtin = next(iter(case["truth"]["gtin_owner"]))
        checked = {"items": copy.deepcopy(items_in)}
        checked["items"][0]["gtin"] = gtin
        findings = [{"code": "MISSING_GTIN", "action": "attached", "field": "gtin", "old_value": None,
                     "new_value": gtin, "item_index": 0, "input_index": 0, "evidence": "GTIN %s" % gtin}]
        proposals, violations, _ = SCORE.score_case(case, items_in, checked, findings, "default")
        self.assertEqual([(check, verdict) for check, verdict, _ in proposals], [("MISSING_GTIN", "WRONG")])
        self.assertEqual([kind for kind, _ in violations], ["MISSING_GTIN"])


if __name__ == "__main__":
    unittest.main()
