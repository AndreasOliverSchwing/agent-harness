#!/usr/bin/env python3
"""Score harness_grounding against the frozen release gate.

    python3 score.py <code dir> [--cases cases.json] [--json out.json] [--show N]

<code dir> holds harness_grounding.py: a `git archive` export or a checkout.
The code is imported from there; nothing here depends on its internals. The
cases come from generate.build(), checked against the digest cases.sha256 pins,
or from a cases.json that generate.py wrote.

Every value ground() proposes is checked against the case's truth (see
generate.py for the truth keys):

    correct   the value the truth states, on the item the truth names
    WRONG     anything else: another item, another value, or a value where the
              truth states none (None, or no entry at all)

A truth entry that gets no proposal is an abstention. It costs recall and is
reported, but it never fails the gate: the gate is about values a caller would
quote, and a check that cannot tell must say so instead of guessing.

The modes follow the signature of ground():
    legacy   no `apply` keyword (before fix round 3): it always writes
    default  apply=False: a caller who passes no flag
    apply    apply=True
    infer    apply=True, infer=True, when ground() has an `infer` keyword:
             the inference the gate has not cleared, measured but not gated

THE GATE passes when, in every mode a caller gets without opting into
inference (legacy, or default and apply), no proposal is WRONG, no case raises,
the caller's input is never mutated, every finding has the eight contract keys,
and the default mode changes no value the caller passed in (it may set
"uncertain" and append a placeholder that carries only link, other_ids and
uncertain). Exit status 0 when it passes, 1 when it fails.
"""
import argparse
import copy
import importlib
import importlib.util
import inspect
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT_KEYS = {"code", "action", "field", "old_value", "new_value", "item_index", "input_index", "evidence"}
CHECKS = ("MISSING_GTIN", "UNCOVERED_LINK", "SET_MULTIPLIED", "VARIANT_SPLIT", "REDACTED_LINK")
PLACEHOLDER_KEYS = {"link", "other_ids", "uncertain"}
WRITE_ACTIONS = {"attached", "corrected", "restored", "merged", "added"}


def load_engine(code_dir):
    sys.path.insert(0, os.path.abspath(code_dir))
    module = importlib.import_module("harness_grounding")
    if not os.path.abspath(module.__file__).startswith(os.path.abspath(code_dir)):
        sys.exit("imported %s, not the one in %s" % (module.__file__, code_dir))
    return module


def load_cases(path=None):
    """The frozen set: a cases.json when given, else generate.build() checked against cases.sha256."""
    if path:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    spec = importlib.util.spec_from_file_location("gate_generate", os.path.join(HERE, "generate.py"))
    generate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generate)
    cases = generate.build()
    with open(os.path.join(HERE, "cases.sha256")) as handle:
        pinned = handle.read().split()[0]
    built = generate.digest(cases)
    if built != pinned:
        raise SystemExit("generate.py builds %s but cases.sha256 pins %s: the gate set changed. Pin a new set "
                         "only on purpose, in its own commit." % (built[:16], pinned[:16]))
    return cases


def modes_for(engine):
    params = inspect.signature(engine.ground).parameters
    if "apply" not in params:
        return [("legacy", {}, True)]
    modes = [("default", {"apply": False}, True), ("apply", {"apply": True}, True)]
    if "infer" in params:
        modes.append(("infer", {"apply": True, "infer": True}, False))
    return modes


def empty(value):
    return value is None or (isinstance(value, (str, list, dict)) and not value) or (
        isinstance(value, str) and not value.strip())


def as_list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def same_gtin(left, right):
    return str(left).lstrip("0") == str(right).lstrip("0")


def clean_link(url):
    return str(url).rstrip(".,;:)>]!'\"*")


def dumps(value):
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def prepare_items(case):
    items = copy.deepcopy(case["items"])
    for index, key in case["truth"].get("py_int_keys", []):
        items[index][int(key)] = items[index].pop(key)
    return items


def merge_loss(survivor_in, survivor_out, variant_in, reports):
    """Values a merge dropped without a finding that names them."""
    lost = []
    for key, value in survivor_in.items():
        if key in ("uncertain", "note", "other_ids") or empty(value):
            continue
        if survivor_out.get(key) != value:
            lost.append("survivor %s %s -> %s" % (key, dumps(value), dumps(survivor_out.get(key))))
    for value in as_list(survivor_in.get("other_ids")):
        if value not in as_list(survivor_out.get("other_ids")):
            lost.append("survivor other_ids %s" % dumps(value))
    if not empty(survivor_in.get("note")) and str(survivor_in["note"]) not in str(survivor_out.get("note") or ""):
        lost.append("survivor note")
    note_out = str(survivor_out.get("note") or "")
    for key, value in variant_in.items():
        if key in ("part_number", "uncertain", "quantity") or empty(value):
            continue
        if key in ("description", "note"):
            if str(value).strip() not in note_out:
                lost.append("variant %s" % key)
            continue
        values = as_list(value) if key == "other_ids" else [value]
        for one in values:
            if key != "other_ids" and survivor_out.get(key) == one:
                continue
            if one in as_list(survivor_out.get("other_ids")) or (key, dumps(one)) in reports:
                continue
            lost.append("variant %s %s" % (key, dumps(one)))
    return lost


def score_case(case, items_in, checked, findings, mode):
    """Return (proposals, violations, detection) for one run."""
    truth = case["truth"]
    items_out = checked.get("items", []) if isinstance(checked, dict) else []
    proposals, violations = [], []
    det = defaultdict(Counter)

    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != CONTRACT_KEYS:
            violations.append(("contract", "finding keys %s" % sorted(finding) if isinstance(finding, dict) else "x"))

    merged = {}
    for f in findings:
        if f.get("code") == "VARIANT_SPLIT" and f.get("action") == "merged":
            merged.setdefault(f["input_index"], f["item_index"])
    surviving = [i for i in range(len(items_in)) if i not in merged]

    def proposal(check, ok, detail):
        proposals.append((check, "correct" if ok else "WRONG", detail))

    owners = truth.get("gtin_owner") or {}
    for f in findings:
        if f["code"] == "MISSING_GTIN" and f["action"] in ("attached", "suggested"):
            target, value = f["input_index"], f["new_value"]
            owner = next((o for g, o in owners.items() if same_gtin(g, value)), "absent")
            proposal("MISSING_GTIN", owner not in ("absent", None) and owner == target,
                     "GTIN %s to item %s, truth %s" % (value, target, owner))

    quantities = truth.get("link_quantity") or {}
    link_q = {clean_link(k): v for k, v in quantities.items()}
    for f in findings:
        if f["code"] != "UNCOVERED_LINK":
            continue
        index = f["item_index"]
        item = items_out[index] if isinstance(index, int) and 0 <= index < len(items_out) else {}
        link = clean_link(item.get("link", ""))
        if f["action"] == "suggested" and f["field"] == "quantity":
            got, unit = f["new_value"], None
        elif f["action"] == "added" and mode != "default" and item.get("quantity") is not None:
            got, unit = item.get("quantity"), item.get("unit")
        else:
            continue
        want = link_q.get(link, "absent")
        ok = want not in ("absent", None) and want[0] == got and (unit is None or want[1] == unit)
        proposal("UNCOVERED_LINK", ok, "%s qty %s %s, truth %s" % (link[-24:], got, unit or "", want))

    fixes = truth.get("set_fix") or {}
    for f in findings:
        if f["code"] == "SET_MULTIPLIED" and f["action"] in ("corrected", "suggested"):
            target, value = f["input_index"], f["new_value"]
            want = fixes.get(str(target), "absent") if target is not None else "absent"
            proposal("SET_MULTIPLIED", want != "absent" and want == value,
                     "item %s -> %s, truth %s" % (target, value, want))

    restored = truth.get("restored_link") or {}
    for f in findings:
        if f["code"] == "REDACTED_LINK" and f["action"] in ("restored", "suggested"):
            target, value = f["input_index"], f["new_value"]
            want = restored.get(str(target), "absent")
            proposal("REDACTED_LINK", want not in ("absent", None) and want == value,
                     "item %s -> %s, truth %s" % (target, value, want))

    variant_of = truth.get("variant_of") or {}
    for line, survivor_ret in sorted(merged.items()):
        survivor_in = surviving[survivor_ret] if isinstance(survivor_ret, int) and survivor_ret < len(surviving) else None
        right_owner = str(line) in variant_of and variant_of[str(line)] == survivor_in
        reports = {(f["field"], dumps(f["old_value"])) for f in findings
                   if f["code"] == "VARIANT_SPLIT" and f["input_index"] == line and f["new_value"] is None}
        lost = []
        if survivor_in is not None and survivor_ret < len(items_out):
            lost = merge_loss(items_in[survivor_in], items_out[survivor_ret], items_in[line], reports)
        detail = "line %s into item %s, truth %s" % (line, survivor_in, variant_of.get(str(line), "no merge"))
        proposal("VARIANT_SPLIT", right_owner and not lost, detail + ("; lost " + ", ".join(lost) if lost else ""))

    if mode == "default":
        if merged:
            violations.append(("VARIANT_SPLIT", "default mode removed %d item(s)" % len(merged)))
        for position, index in enumerate(surviving):
            if position >= len(items_out):
                violations.append(("removed", "item %d missing" % index))
                continue
            before, after = items_in[index], items_out[position]
            for key in set(before) | set(after):
                if key == "uncertain" or before.get(key) == after.get(key):
                    continue
                # The check that wrote it: a write finding on this item and key,
                # else the merge (its findings carry the merged line's index).
                codes = sorted({f["code"] for f in findings if f["input_index"] == index and f["field"] == key
                                and f["action"] in WRITE_ACTIONS})
                codes = codes or (["VARIANT_SPLIT"] if merged else ["?"])
                violations.append(("/".join(codes), "default mode changed item %d %s" % (index, key)))
        for item in items_out[len(surviving):]:
            extra = sorted(set(item) - PLACEHOLDER_KEYS)
            if extra:
                violations.append(("UNCOVERED_LINK", "default placeholder carries %s" % extra))

    # Detection: does a finding of the right code fire where the truth has an entry?
    for gtin in owners:
        hit = any(f["code"] == "MISSING_GTIN" and (same_gtin(f["new_value"] or "", gtin) or gtin in str(f["evidence"]))
                  for f in findings)
        det["MISSING_GTIN"]["hit" if hit else "miss"] += 1
    for f in findings:
        if f["code"] == "MISSING_GTIN":
            # Older commits put no "GTIN <n>:" prefix on the evidence; any GTIN-length run in it names the number.
            found = re.search(r"(?<!\d)(\d{8,14})(?!\d)", str(f["evidence"]))
            number = f["new_value"] or (found.group(1) if found else None)
            if not any(same_gtin(number or "", g) for g in owners):
                det["MISSING_GTIN"]["false"] += 1
    added = [clean_link(f["new_value"]) for f in findings if f["code"] == "UNCOVERED_LINK" and f["action"] == "added"]
    for link in link_q:
        det["UNCOVERED_LINK"]["hit" if link in added else "miss"] += 1
    det["UNCOVERED_LINK"]["false"] += sum(1 for link in added if link not in link_q)
    set_findings = [f for f in findings if f["code"] == "SET_MULTIPLIED"]
    if fixes:
        det["SET_MULTIPLIED"]["hit" if set_findings else "miss"] += 1
    elif set_findings:
        det["SET_MULTIPLIED"]["false"] += 1
    split = [f for f in findings if f["code"] == "VARIANT_SPLIT"]
    for line in variant_of:
        det["VARIANT_SPLIT"]["hit" if any(f["input_index"] == int(line) for f in split) else "miss"] += 1
    det["VARIANT_SPLIT"]["false"] += len({f["input_index"] for f in split if str(f["input_index"]) not in variant_of})
    red = [f for f in findings if f["code"] == "REDACTED_LINK"]
    for key in restored:
        det["REDACTED_LINK"]["hit" if any(f["input_index"] == int(key) for f in red) else "miss"] += 1
    det["REDACTED_LINK"]["false"] += len({f["input_index"] for f in red if str(f["input_index"]) not in restored})
    if "ungrounded" in truth:
        flagged = {(f["input_index"], f["field"]) for f in findings if f["code"] == "UNGROUNDED_ID"}
        want = {tuple(pair) for pair in truth["ungrounded"]}
        det["UNGROUNDED_ID"]["hit"] += len(flagged & want)
        det["UNGROUNDED_ID"]["miss"] += len(want - flagged)
        det["UNGROUNDED_ID"]["false"] += len(flagged - want)
    return proposals, violations, det


def determinable(case):
    """Truth entries a correct proposal could exist for, per check."""
    t = case["truth"]
    return {
        "MISSING_GTIN": sum(1 for o in (t.get("gtin_owner") or {}).values() if o is not None),
        "UNCOVERED_LINK": sum(1 for v in (t.get("link_quantity") or {}).values() if v is not None),
        "SET_MULTIPLIED": len(t.get("set_fix") or {}),
        "VARIANT_SPLIT": len(t.get("variant_of") or {}),
        "REDACTED_LINK": sum(1 for v in (t.get("restored_link") or {}).values() if v is not None),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("code_dir")
    parser.add_argument("--cases", help="a cases.json from generate.py; default: build the pinned set")
    parser.add_argument("--json")
    parser.add_argument("--show", type=int, default=12, help="WRONG examples to print per check and mode")
    args = parser.parse_args()
    engine = load_engine(args.code_dir)
    cases = load_cases(args.cases)
    modes = modes_for(engine)
    report = {"code_dir": args.code_dir, "cases": len(cases), "modes": {}}
    gate_ok = True
    for mode, kwargs, gated in modes:
        tally = defaultdict(Counter)
        wrong = defaultdict(list)
        violations = Counter()
        violation_examples = defaultdict(list)
        crashes = []
        mutated = 0
        detection = defaultdict(Counter)
        possible = Counter()
        for case in cases:
            items_in = prepare_items(case)
            call_items = copy.deepcopy(items_in)
            try:
                checked, findings = engine.ground({"items": call_items}, case["source_text"], **kwargs)
            except Exception as exc:  # a crash is a gate failure
                crashes.append("%s: %s: %s" % (case["id"], type(exc).__name__, exc))
                continue
            if call_items != items_in:
                mutated += 1
            props, viols, det = score_case(case, items_in, checked, findings, mode)
            for check, verdict, detail in props:
                tally[check][verdict] += 1
                if verdict == "WRONG":
                    wrong[check].append("%s: %s" % (case["id"], detail))
            for kind, detail in viols:
                violations[kind] += 1
                if len(violation_examples[kind]) < 5:
                    violation_examples[kind].append("%s: %s" % (case["id"], detail))
            for code, counts in det.items():
                detection[code].update(counts)
            for check, count in determinable(case).items():
                possible[check] += count
        mode_ok = not crashes and not mutated and not violations and not any(t["WRONG"] for t in tally.values())
        if gated:
            gate_ok = gate_ok and mode_ok
        print("\n## mode %s (%s)%s" % (mode, ", ".join("%s=%s" % kv for kv in kwargs.items()) or "always writes",
                                       "" if gated else "  [opt-in inference: measured, not gated]"))
        print("%-15s %8s %8s %8s %10s" % ("check", "correct", "WRONG", "possible", "recall"))
        for check in CHECKS:
            t = tally[check]
            recall = ("%5.1f%%" % (100.0 * t["correct"] / possible[check])) if possible[check] else "   -"
            print("%-15s %8d %8d %8d %10s" % (check, t["correct"], t["WRONG"], possible[check], recall))
        print("crashes %d, mutated inputs %d, contract/default-mode violations %d" % (
            len(crashes), mutated, sum(violations.values())))
        for kind, count in violations.most_common():
            print("  violation %-28s %5d  e.g. %s" % (kind, count, violation_examples[kind][0]))
        for line in crashes[:args.show]:
            print("  crash", line)
        print("detection (any finding of the code)   hit   miss  false")
        for code in CHECKS + ("UNGROUNDED_ID",):
            c = detection[code]
            print("  %-34s %5d %6d %6d" % (code, c["hit"], c["miss"], c["false"]))
        for check in CHECKS:
            if wrong[check]:
                print("  WRONG %s (%d), first %d:" % (check, len(wrong[check]), min(args.show, len(wrong[check]))))
                for line in wrong[check][:args.show]:
                    print("    " + line)
        print("mode verdict: %s" % ("PASS" if mode_ok else "FAIL"))
        report["modes"][mode] = {
            "gated": gated, "kwargs": kwargs, "tally": {k: dict(v) for k, v in tally.items()},
            "possible": dict(possible), "wrong": dict(wrong), "crashes": crashes, "mutated": mutated,
            "violations": dict(violations), "violation_examples": dict(violation_examples),
            "detection": {k: dict(v) for k, v in detection.items()}, "pass": mode_ok}
    report["gate_pass"] = gate_ok
    print("\nGATE: %s" % ("PASS" if gate_ok else "FAIL"))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1, sort_keys=True)
    sys.exit(0 if gate_ok else 1)


if __name__ == "__main__":
    main()
