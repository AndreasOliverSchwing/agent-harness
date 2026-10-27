#!/usr/bin/env python3
"""Run the grounding checks on one invented order request.

Run from the repo root:

    python3 examples/grounding_example.py

The extractor output below is well-formed: every field has the right type, the
quantity is a number and the part number looks like a part number. It is still
wrong in three ways. ground() finds all three.

It runs twice. The first run is the default: ground() suggests the values it
would write and writes nothing, so a producer can show the suggestions to a
human. The second run passes apply=True and writes them.

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


def main():
    print("source text:")
    for line in SOURCE.splitlines():
        print("  " + line)

    print("\nextractor output:")
    print(json.dumps(EXTRACTION, indent=2))

    print("\ndefault mode: ground() suggests, it writes nothing")
    suggested, suggest_findings = g.ground(EXTRACTION, SOURCE)
    print_findings(suggest_findings)
    print("\nchecked output (the extractor's own values):")
    print(json.dumps(suggested, indent=2))

    print("\nwrite mode: ground(..., apply=True) writes what it suggested")
    checked, findings = g.ground(EXTRACTION, SOURCE, apply=True)
    print_findings(findings)
    print("\nchecked output (input left unchanged):")
    print(json.dumps(checked, indent=2))

    if not suggest_findings or not findings:
        print("\nno findings: this example is broken", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
