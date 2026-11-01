#!/usr/bin/env python3
"""harness_lint — check recurring agent output against its spec.

No LLM. No network. Runs in seconds. That is the point: a checker that needs a
model to run is a checker that fails exactly when the model is having a bad day.

Each spec is a markdown file with YAML front matter in the specs directory
(default: ./specs, override with HARNESS_SPECS_DIR). For every spec, the newest
artifact matching `artifact_glob` is checked for freshness, size, required
sections and markers, banned patterns, and self-evaluation completeness.

    python3 harness_lint.py --all
    python3 harness_lint.py --all --invariants
    python3 harness_lint.py --spec research-summary
    python3 harness_lint.py --all --json

Exit code is the number of failing harnesses, capped at 125.
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import re
import sys
import time

__version__ = "0.4.0"

SPECS_DIR = os.path.expanduser(os.environ.get(
    "HARNESS_SPECS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "specs")))
# Where scheduled-job records live, for the delivery invariant. One JSON file per
# job runner, each holding a list of jobs (or {"jobs": [...]}).
JOBS_GLOB = os.environ.get("HARNESS_JOBS_GLOB", "")


REGEX_TIMEOUT_S = 5


@contextlib.contextmanager
def _time_limit(seconds):
    """Bound a block with SIGALRM where available; a no-op elsewhere."""
    try:
        import signal
        def _raise(signum, frame):
            raise TimeoutError()
        old = signal.signal(signal.SIGALRM, _raise)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    except (ImportError, ValueError):
        yield
        return
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# ── spec parsing ───────────────────────────────────────────────────────────

# YAML double-quoted escapes the fallback must decode. Single-quoted scalars do
# NOT process backslashes in YAML, so this applies only to double quotes.
_DQ_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r",
               "0": "\0", "/": "/", "'": "'"}


def _decode_double_quoted(v):
    """Decode the escape sequences a double-quoted YAML scalar carries.

    Load-bearing for regexes. A spec writes "\\b" to mean a word boundary; YAML
    turns that into the two characters \b, which re then reads as a boundary.
    Without this the fallback kept all three characters, so the SAME banned
    pattern matched a literal backslash instead of a word boundary — the check
    still ran, but it meant something different depending on whether PyYAML
    happened to be installed. A parity test now compares both parsers over every
    bundled spec so a divergence like this fails loudly.
    """
    out, i = [], 0
    while i < len(v):
        ch = v[i]
        if ch == "\\" and i + 1 < len(v):
            nxt = v[i + 1]
            out.append(_DQ_ESCAPES.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _scalar(v):
    """Coerce a YAML scalar the fallback parser produced."""
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] == '"':
        v = _decode_double_quoted(v[1:-1])
    elif len(v) >= 2 and v[0] == v[-1] and v[0] == "'":
        v = v[1:-1].replace("''", "'")
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def parse_front_matter(text):
    """Return (front_matter_dict, body). Minimal YAML: enough for these specs.

    Deliberately not a full YAML parser — a lint tool that cannot run without a
    third-party dependency will not get run. Uses PyYAML when it is available.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw, body = text[3:end], text[end + 4:]
    try:
        import yaml
        return yaml.safe_load(raw) or {}, body
    except ImportError:
        pass

    # The fallback must handle list-of-maps, because banned_patterns is one:
    #
    #   banned_patterns:
    #     - pattern: "..."
    #       reason: ...
    #
    # An earlier version appended each line as a flat string, so every entry
    # became the literal text 'pattern: "(?i)...'. check_spec then compiled THAT
    # as the regex, which matches nothing real — the "what it must never say"
    # guarantee was silently dead in exactly the zero-dependency environment this
    # parser exists to serve. A safety check that quietly does not run is worse
    # than no check, so this path now parses the nested form and anything it
    # cannot parse is surfaced (see check_spec) rather than passed over.
    fm, key = {}, None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m_item = re.match(r"^\s+-\s+(.*)$", line)
        if m_item and key:
            fm.setdefault(key, [])
            if not isinstance(fm[key], list):
                continue
            item = m_item.group(1).strip()
            kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", item)
            if kv:                                   # list-of-maps entry
                fm[key].append({kv.group(1): _scalar(kv.group(2))})
            else:                                    # plain list entry
                fm[key].append(item.strip('"\''))
            continue
        # An indented "key: value" line continues whatever the current key is.
        # It has two shapes and the second was missed the first time round:
        #
        #   banned_patterns:        max_age_h:
        #     - pattern: "..."        warn_after: 192
        #       reason: ...           error_after: 240
        #   (list-of-maps)          (plain nested mapping)
        #
        # Only the list form was handled, so a nested mapping collapsed to an
        # empty list — and `max_age_h: []` is falsy, so the freshness check just
        # stopped running. No error, no warning, a 200-hour-stale artifact
        # passing: the exact silent-disable failure the banned_patterns fix was
        # written to eliminate, reintroduced by a later feature. Both shapes now
        # have parity tests against the PyYAML path.
        m_cont = re.match(r"^\s{2,}([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m_cont and key:
            k, v = m_cont.group(1), _scalar(m_cont.group(2))
            cur = fm.get(key)
            if isinstance(cur, list) and cur and isinstance(cur[-1], dict):
                cur[-1][k] = v          # continuation of a list-of-maps entry
                continue
            if isinstance(cur, list) and not cur:
                fm[key] = {k: v}        # first line of a plain nested mapping
                continue
            if isinstance(cur, dict):
                cur[k] = v              # subsequent lines of that mapping
                continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            fm[key] = [v.strip().strip('"\'') for v in val[1:-1].split(",") if v.strip()]
        elif val == "":
            fm[key] = []
        else:
            v = val.strip('"\'')
            if v.lower() in ("true", "false"):
                fm[key] = v.lower() == "true"
            elif re.fullmatch(r"-?\d+", v):
                fm[key] = int(v)
            else:
                fm[key] = v
    return fm, body


def expand(pattern):
    """Expand ~ and $VARS in an artifact glob.

    ARTIFACTS_DIR falls back to the bundled examples/ so a fresh clone runs green
    out of the box and the examples double as documentation of what a conforming
    artifact looks like. Point it at your own output directory for real use.
    """
    os.environ.setdefault("ARTIFACTS_DIR", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "examples"))
    os.environ.setdefault("STATE_DIR", os.environ["ARTIFACTS_DIR"])
    return os.path.expanduser(os.path.expandvars(pattern))


SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec.schema.json")

# Unknown keys stay legal — they are useful as documentation. But a key that is
# ALMOST a real one is a typo, and a typo silently disables a check: write
# `max_age` instead of `max_age_h` and the freshness check just stops existing,
# with no error and no output. That is the same silent-disable class as the
# banned_patterns bug. Near-misses are reported; genuinely unrelated keys are not.
def _near_miss_keys(fm, known):
    import difflib
    out = []
    for key in fm:
        if key in known:
            continue
        close = difflib.get_close_matches(key, known, n=1, cutoff=0.82)
        if close:
            out.append((key, close[0]))
    return out


def validate_front_matter(fm):
    """Return a list of problems. Uses jsonschema when available, always does
    the near-miss check (which is the part that catches real mistakes)."""
    problems = []
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            schema = json.load(fh)
    except Exception:
        return problems
    known = list(schema.get("properties", {}))
    for key, suggestion in _near_miss_keys(fm, known):
        problems.append(
            f"unknown key {key!r} — did you mean {suggestion!r}? "
            "(a near-miss key silently disables its check)")
    try:
        import jsonschema
    except ImportError:
        return problems          # near-miss check still ran; that is the load-bearing half
    validator = jsonschema.Draft7Validator(schema)
    for err in sorted(validator.iter_errors(fm), key=lambda e: list(e.path)):
        where = ".".join(str(x) for x in err.path) or "(root)"
        problems.append(f"schema: {where}: {err.message}")
    return problems


# ── the checks ─────────────────────────────────────────────────────────────

def check_spec(spec_path, artifact_override=None):
    name = os.path.splitext(os.path.basename(spec_path))[0]
    result = {"harness": name, "artifact": None, "failures": [], "warnings": []}
    try:
        with open(spec_path, encoding="utf-8") as fh:
            fm, _ = parse_front_matter(fh.read())
    except OSError as e:
        result["failures"].append(f"cannot read spec: {e}")
        return result

    schema_problems = validate_front_matter(fm)
    for problem in schema_problems:
        result["failures" if problem.startswith("schema:") else "warnings"].append(problem)
    if any(p.startswith("schema:") for p in schema_problems):
        # Stop here. The checks below coerce front-matter values (int(), float(),
        # iteration) and a value the schema already rejected will raise rather
        # than be reported — a malformed spec would crash the linter instead of
        # failing cleanly. Reporting the schema error IS the useful output.
        return result

    compat = fm.get("harness_compat")
    if compat:
        want = re.sub(r"^[><=\s]+", "", str(compat)).strip()
        try:
            need = tuple(int(x) for x in want.split("."))
            have = tuple(int(x) for x in __version__.split("."))
            if have[:len(need)] < need:
                result["warnings"].append(
                    f"spec wants harness {compat}, this is {__version__} — "
                    "checks it relies on may not exist here")
        except ValueError:
            result["warnings"].append(f"unparseable harness_compat: {compat!r}")

    pattern = fm.get("artifact_glob")
    if artifact_override:
        artifact = artifact_override
    elif not pattern:
        # An on-demand harness produces output only when asked. Absence is not a
        # failure — reporting it as one trains people to ignore the board.
        result["warnings"].append("no artifact_glob (on-demand harness)")
        return result
    else:
        matches = sorted(glob.glob(expand(str(pattern))), key=os.path.getmtime)
        if not matches:
            if fm.get("optional"):
                result["warnings"].append("no artifacts yet (optional harness)")
            else:
                result["failures"].append(f"no artifact matches glob: {pattern}")
            return result
        artifact = matches[-1]

    result["artifact"] = os.path.basename(artifact)

    # Golden fixtures. A change to this spec, or to the linter, that would make
    # previously-good output fail should surface in CI — not on the next real
    # run, and never by quietly editing the old artifact to match. This is the
    # mechanical form of "never hand-edit a past artifact to turn the board
    # green": the past artifacts are the test.
    golden = fm.get("golden")
    if golden and not artifact_override:
        for path in sorted(glob.glob(expand(str(golden)))):
            if os.path.abspath(path) == os.path.abspath(artifact):
                continue
            sub = check_spec(spec_path, artifact_override=path)
            for f in sub["failures"]:
                result["failures"].append(f"golden {os.path.basename(path)}: {f}")
    try:
        with open(artifact, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        result["failures"].append(f"cannot read artifact: {e}")
        return result

    # Freshness, with an optional two-tier ladder (dbt's warn_after/error_after
    # shape). A brief one day late is a different thing from one five days late,
    # and collapsing both into FAIL is how alert fatigue starts.
    max_age = fm.get("max_age_h")
    warn_after = error_after = 0.0
    try:
        if isinstance(max_age, dict):
            warn_after = float(max_age.get("warn_after") or 0)
            error_after = float(max_age.get("error_after") or 0)
        elif max_age:
            error_after = float(max_age)
    except (TypeError, ValueError):
        result["failures"].append(f"max_age_h is not a number: {max_age!r}")
    if warn_after or error_after:
        age_h = (time.time() - os.path.getmtime(artifact)) / 3600
        if error_after and age_h > error_after:
            result["failures"].append(f"stale: {age_h:.0f}h old > {error_after:.0f}h")
        elif warn_after and age_h > warn_after:
            result["warnings"].append(
                f"ageing: {age_h:.0f}h old > {warn_after:.0f}h (fails at "
                f"{error_after:.0f}h)" if error_after else
                f"ageing: {age_h:.0f}h old > {warn_after:.0f}h")

    try:
        min_bytes = int(fm.get("min_bytes") or 0)
    except (TypeError, ValueError):
        result["failures"].append(f"min_bytes is not a number: {fm.get('min_bytes')!r}")
        min_bytes = 0
    if len(text.encode()) < min_bytes:
        result["failures"].append(f"too small: {len(text.encode())}B < {min_bytes}B")

    for field in ("required_sections", "required_markers"):
        if isinstance(fm.get(field), str):
            # Without jsonschema nothing type-checks this, and a bare string
            # iterates character by character — "missing section: R", "missing
            # section: u"... It fails, but it misleads about why.
            result["failures"].append(
                f"{field} must be a list, got a string: {fm[field]!r}")
            fm[field] = []

    for sec in fm.get("required_sections") or []:
        if not re.search(r"^#{1,4}\s*.*" + re.escape(str(sec)), text, re.M | re.I):
            result["failures"].append(f"missing section: {sec}")

    for marker in fm.get("required_markers") or []:
        if str(marker) not in text:
            result["failures"].append(f"missing marker: {marker!r}")

    for bp in fm.get("banned_patterns") or []:
        if isinstance(bp, dict):
            pat, why = bp.get("pattern"), bp.get("reason", "")
        elif isinstance(bp, str) and bp.lstrip().startswith("pattern:"):
            # A malformed entry means the front matter did not parse as intended.
            # Never fall through and compile this as a regex: it silently matches
            # nothing and the check disappears without a trace.
            result["warnings"].append(
                "banned_pattern did not parse as a mapping — CHECK NOT RUN: "
                f"{bp[:60]!r}")
            continue
        else:
            pat, why = bp, ""
        if not pat:
            result["warnings"].append(f"banned_pattern has no pattern: {bp!r}")
            continue
        # A banned_pattern is untrusted input compiled as a regex. Catastrophic
        # backtracking on a crafted pattern was measured at ~239s for a 32-char
        # subject, so the search is bounded. SIGALRM is main-thread/POSIX only;
        # where it is unavailable the search simply runs unbounded, which is the
        # pre-existing behaviour and is safe for specs you control.
        try:
            with _time_limit(REGEX_TIMEOUT_S):
                hit = re.search(str(pat), text)
        except re.error as e:
            result["warnings"].append(f"bad banned_pattern {pat!r}: {e}")
            continue
        except TimeoutError:
            result["warnings"].append(
                f"banned_pattern timed out after {REGEX_TIMEOUT_S}s — CHECK NOT RUN: "
                f"{str(pat)[:60]!r} (likely catastrophic backtracking)")
            continue
        if hit:
            result["failures"].append(
                f"banned pattern matched {hit.group(0)[:60]!r}" + (f" — {why}" if why else ""))

    # Self-eval completeness. An UNCHECKED box is the system working: the producer
    # honestly reporting a miss. What this catches is a rubric that was never
    # filled in at all. It cannot catch a FALSE tick — nothing mechanical can —
    # which is why a false [x] is the one unforgivable failure in this method.
    try:
        min_boxes = int(fm.get("self_eval_min") or 0)
    except (TypeError, ValueError):
        result["failures"].append(f"self_eval_min is not a number: {fm.get('self_eval_min')!r}")
        min_boxes = 0
    if min_boxes:
        boxes = re.findall(r"^\s*[-*]\s*\[([ xX])\]", text, re.M)
        if len(boxes) < min_boxes:
            result["failures"].append(
                f"self-eval has {len(boxes)} boxes, spec requires {min_boxes}")
    return result


# ── invariants ─────────────────────────────────────────────────────────────

DELIVERY_FAILURE = re.compile(
    r"(?:not delivered|send failed|REFUSED|can't parse entities|delivery failed)",
    re.IGNORECASE)

# Positive evidence that a run reached its channel. Runners name this field
# differently, so several are accepted; absence is treated as "unknown", never
# as success.
DELIVERED_KEYS = ("delivered_at", "delivered", "last_delivery_at",
                  "last_delivered_at", "delivery_ok", "last_delivery_ok")

# Some runners do not record positive delivery at all — they only write an error
# field, and CLEAR it on the next successful run. For those, "ran recently and the
# error fields are empty" IS positive evidence, and warning forever would be alert
# fatigue dressed up as rigour. Declare it explicitly rather than assuming: an
# unset value keeps the conservative "unverified" reading.
#
# Verified true for Hermes on 2026-08-31: a job that had carried a long delivery
# error showed last_error=None and last_delivery_error=None after its next
# successful run, while a still-failing job on the same runner kept its error.
ERRORS_CLEARED_ON_SUCCESS = os.environ.get(
    "HARNESS_ERRORS_CLEARED_ON_SUCCESS", "").strip().lower() in ("1", "true", "yes")

# Dead-man's switch on each job: an enabled job whose newest run is older than
# this fails. 0 disables it. Default 0 because the right value depends on your
# cadences — set it to (longest cadence + grace).
JOB_MAX_AGE_H = float(os.environ.get("HARNESS_JOB_MAX_AGE_H", "0") or 0)


def _parse_ts(value):
    """Best-effort ISO-8601 parse. Returns epoch seconds, or None."""
    if not value or not isinstance(value, str):
        return None
    txt = value.strip().replace("Z", "+00:00")
    try:
        import datetime
        return datetime.datetime.fromisoformat(txt).timestamp()
    except (ValueError, TypeError):
        return None


def _delivery_evidence(job):
    """Return True (delivered), False (explicitly not), or None (unknown)."""
    for key in DELIVERED_KEYS:
        if key not in job:
            continue
        val = job[key]
        if isinstance(val, bool):
            return val
        if isinstance(val, str) and val.strip():
            return True
        if val is None:
            return False
    return None


def check_delivery():
    """Every scheduled job that should have produced output must have DELIVERED it.

    This is the invariant the whole repo exists for. Artifact freshness is not
    delivery: a job can produce a perfect file and fail to hand it to a human,
    and every freshness check will still be green.

    REWRITTEN 2026-08-26 after the first version was shown to have the blind spot
    it was written to close. That version grepped the run record for known failure
    phrases — a FAILURE-SIGNAL model. It caught a delivery that logged its own
    error, and it silently passed two worse cases: a delivery that just dropped
    (nothing arrived, nothing logged) and a job that stopped running altogether.
    Both returned PASS. An invariant that only catches the failures polite enough
    to announce themselves is not a liveness check.

    This version is a dead-man's switch, the pattern the monitoring field settled
    on: require POSITIVE evidence and fail on its absence.

      FAIL  the record names a delivery failure
      FAIL  an enabled job has not run within HARNESS_JOB_MAX_AGE_H (when set)
      FAIL  a run produced output and the record says it was NOT delivered
      WARN  an enabled job has never run          (may be newly created)
      WARN  the runner records no delivery evidence either way

    Warnings are deliberate. Failing on every unknown would make the invariant
    red on any runner that does not record delivery, and an invariant that is
    always red is one people learn to ignore — the same failure as always green.
    """
    name = "delivery (written artifact actually reached its channel)"
    if not JOBS_GLOB:
        return {"invariant": name, "status": "SKIP",
                "detail": "set HARNESS_JOBS_GLOB to your job runner's records"}

    failures, warnings, checked = [], [], 0
    now = time.time()
    # A glob that matches nothing is not evidence of health. For an invariant
    # whose entire purpose is "fail on absence", finding no records at all and
    # reporting PASS is the worst possible outcome — it is the silent-green
    # failure this check exists to prevent, one level up.
    paths = []
    for part in str(JOBS_GLOB).split(os.pathsep):
        if part.strip():
            paths.extend(glob.glob(expand(part.strip())))
    paths = sorted(set(paths))
    if not paths:
        return {"invariant": name, "status": "FAIL",
                "detail": f"HARNESS_JOBS_GLOB matched no files: {JOBS_GLOB!r} — "
                          "cannot verify delivery, so this is not a pass"}
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            # Same reasoning: an unreadable record is unknown, never good.
            failures.append(f"{os.path.basename(path)}: unreadable job record ({e})")
            continue
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        for j in jobs:
            if not j.get("enabled"):
                continue
            checked += 1
            label = j.get("name") or j.get("id") or os.path.basename(path)

            last_run = j.get("last_run_at")
            if not last_run:
                warnings.append(f"{label}: enabled but has never run")
                continue

            if JOB_MAX_AGE_H:
                ts = _parse_ts(last_run)
                if ts is None:
                    warnings.append(f"{label}: unparseable last_run_at {last_run!r}")
                elif (now - ts) / 3600 > JOB_MAX_AGE_H:
                    failures.append(
                        f"{label}: last ran {(now - ts) / 3600:.0f}h ago "
                        f"(> {JOB_MAX_AGE_H:.0f}h) — the job itself has gone quiet")
                    continue

            blob = f"{j.get('last_error') or ''}\n{j.get('last_delivery_error') or ''}"
            if blob.strip() and DELIVERY_FAILURE.search(blob):
                failures.append(f"{label}: wrote output it could not deliver")
                continue

            evidence = _delivery_evidence(j)
            if evidence is False:
                failures.append(f"{label}: ran, but the record shows no delivery")
            elif evidence is None:
                if ERRORS_CLEARED_ON_SUCCESS:
                    # blob was checked above and is clean, so on a runner that
                    # clears its error on success this run did deliver.
                    pass
                else:
                    warnings.append(
                        f"{label}: runner records no delivery evidence "
                        "(set HARNESS_ERRORS_CLEARED_ON_SUCCESS=1 if your runner "
                        "clears its error field on a successful run)")

    if failures:
        return {"invariant": name, "status": "FAIL",
                "detail": "; ".join(sorted(set(failures)))}
    if not checked:
        return {"invariant": name, "status": "WARN",
                "detail": f"{len(paths)} record file(s) found but no enabled jobs "
                          "in them — nothing was verified"}
    detail = f"{checked} enabled job(s) checked, all delivered"
    if warnings:
        detail += " — unverified: " + "; ".join(sorted(set(warnings))[:3])
        return {"invariant": name, "status": "WARN", "detail": detail}
    return {"invariant": name, "status": "PASS", "detail": detail}


INVARIANTS = [check_delivery]


# ── cli ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="check every spec")
    mode.add_argument("--spec", help="check one spec by name")
    ap.add_argument("--artifact",
                    help="check --spec against this file instead of the glob "
                         "(requires --spec)")
    ap.add_argument("--invariants", action="store_true", help="also run invariants")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--version", action="version", version=f"harness_lint {__version__}")
    args = ap.parse_args()
    if args.artifact and not args.spec:
        ap.error("--artifact requires --spec (it names one file for one spec)")

    specs = (sorted(glob.glob(os.path.join(SPECS_DIR, "*.md"))) if args.all
             else [os.path.join(SPECS_DIR, f"{args.spec}.md")])
    if not specs:
        print(f"no specs found in {SPECS_DIR}\n"
              "Point HARNESS_SPECS_DIR at your spec directory, e.g.\n"
              "  HARNESS_SPECS_DIR=./my-specs harness-lint --all\n"
              "(the bundled example specs ship with the repo, not with the "
              "installed package — an installed linter should lint YOUR specs)",
              file=sys.stderr)
        return 1

    results = [check_spec(s, args.artifact if args.spec else None) for s in specs]
    invs = [fn() for fn in INVARIANTS] if args.invariants else []
    fails = sum(1 for r in results if r["failures"]) + sum(1 for i in invs if i["status"] == "FAIL")

    if args.json:
        print(json.dumps({"harnesses": results, "invariants": invs,
                          "fail_count": fails}, indent=2))
        return min(fails, 125)

    for r in results:
        icon = "FAIL" if r["failures"] else ("SKIP" if r["warnings"] and not r["artifact"] else "ok  ")
        print(f"{icon}  {r['harness']:<28} {r['artifact'] or '-'}")
        for f in r["failures"]:
            print(f"        x {f}")
        for w in r["warnings"]:
            print(f"        ~ {w}")
    for i in invs:
        print(f"{i['status']:<4}  invariant: {i['invariant']}")
        print(f"        {i['detail']}")
    print(f"\nfail_count={fails}")
    return min(fails, 125)


if __name__ == "__main__":
    sys.exit(main())
