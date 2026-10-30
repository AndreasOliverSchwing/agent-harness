#!/usr/bin/env python3
"""Run the grounding checks on one invented order request.

Run from the repo root:

    python3 examples/grounding_example.py

The extractor output below is well-formed: every field has the right type, the
quantity is a number and the part number looks like a part number. It is still
wrong in three ways. ground() finds all three.

It runs three times, one per level of the contract:

1. the default is flag-only: no check proposes an owner, a quantity or a merge,
   so SET_MULTIPLIED and MISSING_GTIN report and nothing is written;
2. apply=True writes only what the default suggests. Here that is a cut link,
   and this order has none, so the findings are the same as run 1;
3. infer=True, apply=True adds the owner and quantity inference the release
   gate (bench/gate) does not clear, so the set is corrected and the GTIN is
   attached.

Every name, part number and GTIN here is invented. The GTIN uses the GS1
prefix 2, which is reserved for internal use, so no real product carries it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness_grounding as g

SOURCE = """Example Tool Supply - order request
Cordless impact driver 18 V 4-pack, part AC-4417, GTIN 2000000000008
quantity: 3 x 4
"""

# The extractor read "3 x 4" as 12 pieces, invented part number AC-9999 and
# dropped the GTIN.
EXTRACTION = {
    "items": [
        {
            "quantity": 12,
            "unit": "piece",
            "description": "Cordless impact driver 18 V 4-pack",
            "part_number": "AC-9999",
        }
    ]
}


def print_findings(findings):
    """Print one line per finding, plus its evidence."""
    print("findings: %d" % len(findings))
    for finding in findings:
        print("  %-14s %-10s %-11s %s -> %s" % (
            finding["code"],
            finding["action"],
            finding["field"],
            finding["old_value"],
            finding["new_value"],
        ))
        print("  %-14s %s" % ("", finding["evidence"]))


def run(label, **flags):
    """Call ground() once, print its findings and the item it returns."""
    print("\n%s" % label)
    checked, findings = g.ground(EXTRACTION, SOURCE, **flags)
    print_findings(findings)
    print("\nchecked output:")
    print(json.dumps(checked["items"], indent=2))
    return checked, findings


def main():
    print("source text:")
    for line in SOURCE.splitlines():
        print("  " + line)

    print("\nextractor output:")
    print(json.dumps(EXTRACTION, indent=2))

    flagged, flag_findings = run(
        "run 1, default: flag-only, no check proposes a value")
    applied, apply_findings = run(
        "run 2, apply=True: writes what the default suggests", apply=True)
    inferred, infer_findings = run(
        "run 3, infer=True, apply=True: the inference writes", infer=True,
        apply=True)

    if not flag_findings or not apply_findings or not infer_findings:
        print("\nno findings: this example is broken", file=sys.stderr)
        return 1
    # The default must not write the values runs 2 and 3 write.
    if flagged["items"][0]["quantity"] != 12:
        print("\nthe default wrote a quantity: this example is broken",
              file=sys.stderr)
        return 1
    if applied["items"][0].get("gtin") is not None:
        print("\napply=True inferred a GTIN: this example is broken",
              file=sys.stderr)
        return 1
    if inferred["items"][0]["quantity"] != 3:
        print("\ninfer=True did not correct the set: this example is broken",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
