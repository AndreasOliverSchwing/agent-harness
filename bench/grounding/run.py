#!/usr/bin/env python3
"""Synthetic benchmark for harness_grounding.ground().

Runs every case in cases.json through ground() and scores the result. Standard
library only, no network, no LLM.

    python3 bench/grounding/run.py

Three scores per case:

    caught      a finding with the code the injected error should produce
    corrected   the checked output equals gold once "uncertain" and "note"
                are removed from every item
    false alarm any finding on a case that holds no error

The scores measure the CHECKS, not an extractor. The cases are synthetic and
the errors were injected by hand, so the numbers say what the checks can and
cannot repair on text written to exercise them.
"""
from __future__ import annotations

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import harness_grounding as G  # noqa: E402

CASES_PATH = os.path.join(HERE, "cases.json")
RESULTS_PATH = os.path.join(HERE, "RESULTS.md")

# The finding code each injected error must produce. "clean" injects nothing,
# so any finding at all on such a case is a false alarm.
EXPECTED_CODES = {
    "omit_gtin": ("MISSING_GTIN",),
    "invented_part_number": ("UNGROUNDED_ID",),
    "missed_link": ("UNCOVERED_LINK",),
    "set_multiplied": ("SET_MULTIPLIED",),
    "variant_split": ("VARIANT_SPLIT",),
    "clean": (),
}

# Table order: the errors in the order the checks run, then the control group.
TYPE_ORDER = (
    "variant_split",
    "invented_part_number",
    "missed_link",
    "set_multiplied",
    "omit_gtin",
    "clean",
)

# Fields the comparison ignores. ground() sets them to explain itself; they are
# not part of the extraction a caller would quote.
IGNORED_FIELDS = ("uncertain", "note")


def load_cases(path=CASES_PATH):
    """Return the case list from path. Raise ValueError on a malformed file."""
    with open(path) as handle:
        cases = json.load(handle)
    if not isinstance(cases, list) or not cases:
        raise ValueError("%s must hold a non-empty list of cases" % path)
    for case in cases:
        for key in ("id", "error_type", "source_text", "gold",
                    "extractor_output"):
            if key not in case:
                raise ValueError("case %r has no %r" % (case.get("id"), key))
        if case["error_type"] not in EXPECTED_CODES:
            raise ValueError("case %r has unknown error_type %r"
                             % (case["id"], case["error_type"]))
    return cases


def expected_codes(error_type):
    """Return the finding codes that count as a catch for error_type."""
    return EXPECTED_CODES[error_type]


def strip_ignored(extraction):
    """Return a deep copy of extraction without the fields the score ignores."""
    flat = copy.deepcopy(extraction)
    if not isinstance(flat, dict):
        return {}
    items = flat.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                for field in IGNORED_FIELDS:
                    item.pop(field, None)
    return flat


def is_caught(case, findings):
    """True when a finding carries a code the injected error should produce."""
    wanted = set(expected_codes(case["error_type"]))
    if not wanted:
        return False
    return any(finding.get("code") in wanted for finding in findings)


def is_corrected(case, checked):
    """True when checked equals gold once uncertain and note are removed."""
    return strip_ignored(checked) == strip_ignored(case["gold"])


def is_false_alarm(case, findings):
    """True when an error-free case produced any finding at all."""
    return case["error_type"] == "clean" and bool(findings)


def score_case(case):
    """Run ground() on one case and return its scores as a dict."""
    checked, findings = G.ground(case["extractor_output"], case["source_text"])
    return {
        "id": case["id"],
        "error_type": case["error_type"],
        "codes": [finding.get("code") for finding in findings],
        "caught": is_caught(case, findings),
        "corrected": is_corrected(case, checked),
        "false_alarm": is_false_alarm(case, findings),
    }


def summarise(scored):
    """Group scored cases by error type: n, caught, corrected, false alarms."""
    summary = {}
    for error_type in TYPE_ORDER:
        summary[error_type] = {"n": 0, "caught": 0, "corrected": 0,
                               "false_alarms": 0}
    for result in scored:
        row = summary.setdefault(result["error_type"],
                                 {"n": 0, "caught": 0, "corrected": 0,
                                  "false_alarms": 0})
        row["n"] += 1
        row["caught"] += 1 if result["caught"] else 0
        row["corrected"] += 1 if result["corrected"] else 0
        row["false_alarms"] += 1 if result["false_alarm"] else 0
    return summary


def total_false_alarms(scored):
    """Count the error-free cases that produced at least one finding."""
    return sum(1 for result in scored if result["false_alarm"])


def render_table(summary):
    """Return the caught/corrected table as markdown."""
    lines = [
        "| error type | n | caught | corrected |",
        "| --- | --- | --- | --- |",
    ]
    for error_type in TYPE_ORDER:
        row = summary.get(error_type)
        if not row or not row["n"]:
            continue
        # The control group injects nothing, so it has nothing to catch.
        caught = str(row["caught"]) if EXPECTED_CODES[error_type] else "—"
        lines.append("| %s | %d | %s | %d |"
                     % (error_type, row["n"], caught, row["corrected"]))
    return "\n".join(lines)


# Why an error type cannot reach `gold`, in one sentence. Only types that were
# caught but not corrected need an entry.
NOT_CORRECTED_REASON = {
    "invented_part_number":
        "`UNGROUNDED_ID` flags the value and sets `uncertain`. It never rewrites "
        "a part number, because the source does not prove the right one.",
    "missed_link":
        "`UNCOVERED_LINK` appends a placeholder item: `description` and "
        "`part_number` set to the link id, `unit` \"piece\". `gold` holds none "
        "of those fields. When the item exists but lost its link, the result is "
        "a second item, not a link put back on the first.",
}


def render_results(summary, scored):
    """Return the full RESULTS.md text."""
    missed = {}
    uncaught = []
    for result in scored:
        if result["error_type"] == "clean":
            continue
        if not result["caught"]:
            uncaught.append(result)
        elif not result["corrected"]:
            missed.setdefault(result["error_type"], []).append(result)

    parts = [
        "# Grounding benchmark — results",
        "",
        "Generated by `python3 bench/grounding/run.py`. Do not edit by hand.",
        "",
        "## Table",
        "",
        render_table(summary),
        "",
        "False alarms: %d of %d error-free cases produced a finding."
        % (total_false_alarms(scored),
           summary.get("clean", {}).get("n", 0)),
        "",
        "`caught` means a finding with the expected code. `corrected` means the"
        " checked output equals `gold` once `uncertain` and `note` are removed.",
        "The `clean` row injects no error, so it has nothing to catch. Its"
        " `corrected` count shows that `ground()` left a correct extraction"
        " untouched.",
        "",
        "## Method",
        "",
        "1. `cases.json` holds 30 order requests. Each case has `source_text`,"
        " `gold` and `extractor_output`.",
        "2. `run.py` calls `ground(extractor_output, source_text)` on each case.",
        "3. `caught` asks for the finding code the injected error should raise:"
        " `MISSING_GTIN`, `UNGROUNDED_ID`, `UNCOVERED_LINK`, `SET_MULTIPLIED`"
        " or `VARIANT_SPLIT`.",
        "4. `corrected` compares the returned items with `gold`. The comparison"
        " drops `uncertain` and `note`, because `ground()` sets them to explain"
        " itself and they are not extraction values.",
        "5. `false alarm` counts the 4 error-free cases where any finding"
        " appeared at all.",
        "",
        "## Limits",
        "",
        "- The cases are synthetic. A person wrote them to exercise the checks.",
        "- The errors were injected by design. Real extractor errors are messier",
        "  and often overlap: one item can lose a GTIN and invent a part number.",
        "- The benchmark measures the checks, not an LLM. It says nothing about",
        "  how often a real extractor makes these errors.",
        "- `corrected` is a strict comparison. `ground()` adds a placeholder item",
        "  for an uncovered link and never rewrites an unsupported part number,",
        "  so those two error types cannot reach `corrected` by design.",
        "- All 30 cases were written to fall inside the scope of the checks, so a",
        "  high `caught` count is expected. It is not a measure of recall on real",
        "  extractor output.",
        "- The case set is small. One case is one data point.",
        "",
        "## What the checks did not correct",
        "",
    ]
    if missed:
        parts.append("Caught, but the output still differs from `gold`:")
        parts.append("")
        parts.append("| error type | cases | why |")
        parts.append("| --- | --- | --- |")
        for error_type in TYPE_ORDER:
            results = missed.get(error_type)
            if not results:
                continue
            parts.append("| %s | %d | %s |"
                         % (error_type, len(results),
                            NOT_CORRECTED_REASON.get(error_type, "not stated")))
        parts.append("")
        parts.append("Cases: %s."
                     % ", ".join("`%s`" % result["id"]
                                 for error_type in TYPE_ORDER
                                 for result in missed.get(error_type, [])))
    else:
        parts.append("Every caught error also reached `gold`.")
    parts.append("")
    if uncaught:
        parts.append("Not caught at all: %s."
                     % ", ".join("`%s` (%s)" % (result["id"],
                                                result["error_type"])
                                 for result in uncaught))
        parts.append("")
    else:
        parts.append("No case went uncaught.")
        parts.append("")
    return "\n".join(parts)


def main():
    cases = load_cases()
    scored = [score_case(case) for case in cases]
    summary = summarise(scored)
    print(render_table(summary))
    print("")
    print("false alarms: %d of %d error-free cases"
          % (total_false_alarms(scored), summary.get("clean", {}).get("n", 0)))
    with open(RESULTS_PATH, "w") as handle:
        handle.write(render_results(summary, scored))
    print("wrote %s" % RESULTS_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
