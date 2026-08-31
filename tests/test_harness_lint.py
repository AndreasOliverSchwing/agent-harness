"""Tests for harness_lint.

The point of most of these is parity: every check must behave identically with
and without PyYAML. That is not a theoretical concern — the fallback parser
silently disabled the banned_patterns check in the first published version, and
no test existed to catch it. Each check therefore gets a fixture that SHOULD fail
it, run through both parser paths.

    python3 -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import harness_lint as H  # noqa: E402


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(text).lstrip())
    return path


def run_without_yaml(snippet):
    """Run a snippet in a subprocess where importing yaml raises ImportError."""
    prelude = textwrap.dedent(f"""
        import sys
        class _Block:
            def find_module(self, name, path=None):
                return self if name == "yaml" else None
            def load_module(self, name):
                raise ImportError("no yaml")
        sys.meta_path.insert(0, _Block())
        sys.path.insert(0, {REPO!r})
        import harness_lint as H
    """)
    out = subprocess.run([sys.executable, "-c", prelude + textwrap.dedent(snippet)],
                         capture_output=True, text=True, cwd=REPO)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return out.stdout.strip()


class SpecFixture:
    """A spec + artifact pair on disk, for one check at a time."""

    def __init__(self, tmp, front_matter, artifact_body):
        self.dir = tmp
        self.artifact = write(os.path.join(tmp, "artifact.md"), artifact_body)
        self.spec = write(os.path.join(tmp, "spec.md"),
                          "---\n" + textwrap.dedent(front_matter).strip() +
                          f"\nartifact_glob: {self.artifact}\n---\n\n# Spec\n")


class TestChecksFire(unittest.TestCase):
    """Every check must FAIL on an artifact that violates it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def failures(self, front_matter, body):
        f = SpecFixture(self.tmp, front_matter, body)
        return H.check_spec(f.spec)["failures"]

    def test_min_bytes_fires(self):
        self.assertTrue(any("too small" in f
                            for f in self.failures("harness: t\nmin_bytes: 500", "tiny")))

    def test_min_bytes_passes_when_large_enough(self):
        self.assertFalse(any("too small" in f
                             for f in self.failures("harness: t\nmin_bytes: 5", "x" * 50)))

    def test_required_section_fires(self):
        self.assertTrue(any("missing section" in f for f in self.failures(
            "harness: t\nrequired_sections:\n  - Rubric Self-Check", "# Title\n\nno rubric here")))

    def test_required_marker_fires(self):
        self.assertTrue(any("missing marker" in f for f in self.failures(
            'harness: t\nrequired_markers:\n  - "Generated"', "# Title\n\nnothing")))

    def test_self_eval_min_fires(self):
        self.assertTrue(any("self-eval" in f for f in self.failures(
            "harness: t\nself_eval_min: 5", "# T\n\n- [x] one\n- [ ] two")))

    def test_self_eval_counts_unchecked_boxes(self):
        # An UNCHECKED box is the system working — it must still count toward the
        # rubric being present. Only a rubric that was never filled in should fail.
        self.assertFalse(any("self-eval" in f for f in self.failures(
            "harness: t\nself_eval_min: 2", "# T\n\n- [ ] one\n- [ ] two")))

    def test_missing_artifact_fires(self):
        spec = write(os.path.join(self.tmp, "s2.md"),
                     "---\nharness: t\nartifact_glob: /nonexistent/xyz-*.md\n---\n")
        self.assertTrue(any("no artifact" in f for f in H.check_spec(spec)["failures"]))

    def test_optional_harness_warns_instead_of_failing(self):
        spec = write(os.path.join(self.tmp, "s3.md"),
                     "---\nharness: t\noptional: true\nartifact_glob: /nonexistent/xyz-*.md\n---\n")
        r = H.check_spec(spec)
        self.assertEqual(r["failures"], [])
        self.assertTrue(r["warnings"])

    def test_no_glob_is_a_warning_not_a_failure(self):
        spec = write(os.path.join(self.tmp, "s4.md"), "---\nharness: t\n---\n")
        r = H.check_spec(spec)
        self.assertEqual(r["failures"], [])
        self.assertTrue(any("on-demand" in w for w in r["warnings"]))


class TestBannedPatternParity(unittest.TestCase):
    """The regression suite for the bug that shipped: banned_patterns must fire
    identically with and without PyYAML."""

    FRONT = '''
        harness: t
        banned_patterns:
          - pattern: "(?i)studies show"
            reason: authority claims need a source
    '''
    BODY = "# Brief\n\nStudies show this is great.\n"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = SpecFixture(self.tmp, self.FRONT, self.BODY)

    def test_fires_with_pyyaml(self):
        self.assertTrue(any("banned pattern" in f
                            for f in H.check_spec(self.f.spec)["failures"]))

    def test_fires_without_pyyaml(self):
        out = run_without_yaml(f"""
            r = H.check_spec({self.f.spec!r})
            print(any("banned pattern" in x for x in r["failures"]))
        """)
        self.assertEqual(out, "True", "banned_patterns silently dead without PyYAML")

    def test_front_matter_parses_to_mapping_without_pyyaml(self):
        out = run_without_yaml(f"""
            fm, _ = H.parse_front_matter(open({self.f.spec!r}).read())
            bp = fm["banned_patterns"][0]
            print(isinstance(bp, dict) and "pattern" in bp and "reason" in bp)
        """)
        self.assertEqual(out, "True")

    def test_clean_artifact_passes_both_paths(self):
        clean = SpecFixture(tempfile.mkdtemp(), self.FRONT, "# Brief\n\nAll fine here.\n")
        self.assertFalse(any("banned pattern" in f
                             for f in H.check_spec(clean.spec)["failures"]))
        out = run_without_yaml(f"""
            r = H.check_spec({clean.spec!r})
            print(any("banned pattern" in x for x in r["failures"]))
        """)
        self.assertEqual(out, "False")


class TestParserParity(unittest.TestCase):
    """The two front-matter parsers must produce the SAME structure.

    This is the general form of a bug that has now shipped twice. First
    `banned_patterns` (list-of-maps) collapsed to strings, killing the
    banned-pattern check. Then `max_age_h` (nested mapping) collapsed to an empty
    list, killing the freshness check — a 200-hour-stale artifact passed. Both
    were silent: no error, no warning, the check simply stopped existing.

    Per-feature parity tests catch the shapes someone thought of. This one
    compares the whole parsed structure for every bundled spec, so a NEW key in a
    NEW shape fails here rather than silently disabling itself in production.
    """

    def _parsed_with_yaml(self, path):
        import yaml  # noqa: F401  — skip if the environment has no PyYAML
        with open(path, encoding="utf-8") as fh:
            return H.parse_front_matter(fh.read())[0]

    def _parsed_without_yaml(self, path):
        out = run_without_yaml(
            "import json\n"
            "fm, _ = H.parse_front_matter(open(%r, encoding='utf-8').read())\n"
            "print(json.dumps(fm, sort_keys=True, default=str))\n" % path)
        import json
        return json.loads(out)

    def test_every_bundled_spec_parses_identically(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML absent; parity is trivially satisfied")
        import glob as _glob
        specs = sorted(_glob.glob(os.path.join(REPO, "specs", "*.md")))
        self.assertTrue(specs, "no bundled specs found")
        for spec in specs:
            with self.subTest(spec=os.path.basename(spec)):
                import json
                a = json.loads(json.dumps(self._parsed_with_yaml(spec),
                                          sort_keys=True, default=str))
                b = self._parsed_without_yaml(spec)
                self.assertEqual(
                    a, b,
                    "front matter differs between parsers for "
                    f"{os.path.basename(spec)} — a shape the fallback cannot read "
                    "silently disables whatever check depends on it")

    def test_nested_mapping_shape_survives_the_fallback(self):
        # The specific shape that broke: max_age_h as a block mapping.
        tmp = tempfile.mkdtemp()
        spec = write(os.path.join(tmp, "s.md"),
                     "---\nharness: t\nmax_age_h:\n  warn_after: 192\n"
                     "  error_after: 240\n---\n")
        out = run_without_yaml(
            "fm, _ = H.parse_front_matter(open(%r).read())\n"
            "print(type(fm['max_age_h']).__name__, sorted(fm['max_age_h'].items())"
            " if isinstance(fm['max_age_h'], dict) else fm['max_age_h'])\n" % spec)
        self.assertIn("dict", out, f"fallback produced {out!r}, not a mapping")
        self.assertIn("warn_after", out)
        self.assertIn("error_after", out)


class TestHostileSpec(unittest.TestCase):
    def test_redos_pattern_is_bounded_and_reported(self):
        tmp = tempfile.mkdtemp()
        f = SpecFixture(tmp, '''
            harness: t
            banned_patterns:
              - pattern: "(a+)+$"
                reason: catastrophic backtracking
        ''', "a" * 40 + "!")
        import time
        t0 = time.time()
        r = H.check_spec(f.spec)
        self.assertLess(time.time() - t0, H.REGEX_TIMEOUT_S + 3)
        self.assertTrue(any("timed out" in w for w in r["warnings"]),
                        "a hostile pattern must be reported, never silently skipped")


class TestSpecValidation(unittest.TestCase):
    """Front-matter validation.

    The near-miss key check is the load-bearing half: it runs with or without the
    optional jsonschema dependency, and it catches the class of mistake that
    silently disables a check — `max_age` instead of `max_age_h` does not error,
    it just makes the freshness check stop existing.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.art = write(os.path.join(self.tmp, "a.md"), "# T\n\n" + "x" * 300)
        self.n = 0

    def spec(self, front_matter):
        self.n += 1
        body = ("---\n" + textwrap.dedent(front_matter).strip() +
                "\nartifact_glob: " + self.art + "\n---\n")
        return write(os.path.join(self.tmp, "s%d.md" % self.n), body)

    def test_typo_key_is_reported(self):
        r = H.check_spec(self.spec("harness: t\nmax_age: 24"))
        self.assertTrue(any("max_age_h" in w for w in r["warnings"]))

    def test_unrelated_keys_are_not_flagged(self):
        r = H.check_spec(self.spec("harness: t\nowner: someone\nnotes: free text"))
        self.assertEqual(r["warnings"], [])

    def test_bad_scalars_fail_cleanly_rather_than_raising(self):
        for fm in ('harness: t\nmin_bytes: "lots"',
                   'harness: t\nself_eval_min: "many"',
                   'harness: t\nmax_age_h: "soon"'):
            r = H.check_spec(self.spec(fm))
            self.assertTrue(r["failures"], "%r should fail, not raise" % fm)

    def test_harness_compat_newer_than_linter_warns(self):
        r = H.check_spec(self.spec('harness: t\nharness_compat: ">=99.0"'))
        self.assertTrue(any("harness" in w for w in r["warnings"]))

    def test_schema_file_is_valid_json(self):
        import json
        with open(H.SCHEMA_PATH, encoding="utf-8") as fh:
            self.assertIn("properties", json.load(fh))


class TestFreshnessLadder(unittest.TestCase):
    """dbt's warn_after/error_after shape. A brief one day late is a different
    thing from one five days late; collapsing both into FAIL starts alert
    fatigue, which is the failure mode this whole method is trying to avoid."""

    def setUp(self):
        import time
        self.tmp = tempfile.mkdtemp()
        self.art = write(os.path.join(self.tmp, "a.md"), "# T\n\n" + "x" * 300)
        old = time.time() - 200 * 3600
        os.utime(self.art, (old, old))
        self.n = 0

    def spec(self, front_matter):
        self.n += 1
        body = ("---\n" + textwrap.dedent(front_matter).strip() +
                "\nartifact_glob: " + self.art + "\n---\n")
        return write(os.path.join(self.tmp, "s%d.md" % self.n), body)

    def test_between_thresholds_warns_but_does_not_fail(self):
        r = H.check_spec(self.spec("harness: t\nmax_age_h:\n  warn_after: 192\n  error_after: 240"))
        self.assertEqual(r["failures"], [])
        self.assertTrue(any("ageing" in w for w in r["warnings"]))

    def test_past_error_threshold_fails(self):
        r = H.check_spec(self.spec("harness: t\nmax_age_h:\n  warn_after: 100\n  error_after: 150"))
        self.assertTrue(any("stale" in f for f in r["failures"]))

    def test_plain_scalar_still_behaves_as_a_hard_limit(self):
        # Back-compat: every existing spec uses the single-number form and must
        # not change meaning on upgrade.
        self.assertTrue(any("stale" in f for f in
                            H.check_spec(self.spec("harness: t\nmax_age_h: 150"))["failures"]))
        self.assertEqual(H.check_spec(self.spec("harness: t\nmax_age_h: 300"))["failures"], [])


class TestGoldenFixtures(unittest.TestCase):
    def test_a_failing_golden_artifact_fails_the_spec(self):
        tmp = tempfile.mkdtemp()
        write(os.path.join(tmp, "golden-good.md"), "# T\n\n" + "x" * 900)
        write(os.path.join(tmp, "golden-bad.md"), "# T\n\ntiny")
        spec = write(os.path.join(tmp, "s.md"),
                     "---\nharness: t\nmin_bytes: 500\n"
                     "artifact_glob: " + tmp + "/golden-good.md\n"
                     "golden: " + tmp + "/golden-*.md\n---\n")
        r = H.check_spec(spec)
        self.assertTrue(any("golden" in f for f in r["failures"]),
                        "a previously-good artifact that now fails must be caught in CI")


class TestDeliveryInvariant(unittest.TestCase):
    """The dead-man's-switch behaviour.

    The first version of check_delivery only matched known failure phrases in the
    run record. It caught a delivery that logged its own error and silently passed
    a delivery that just dropped and a job that had stopped running — the two
    worse cases. These tests exist so that cannot regress.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "jobs.json")
        H.JOBS_GLOB = self.path
        self._old_age = H.JOB_MAX_AGE_H
        H.JOB_MAX_AGE_H = 0

    def tearDown(self):
        H.JOBS_GLOB = ""
        H.JOB_MAX_AGE_H = self._old_age

    def run_with(self, *jobs):
        import json
        with open(self.path, "w") as f:
            json.dump({"jobs": list(jobs)}, f)
        return H.check_delivery()

    def test_logged_delivery_failure_fails(self):
        r = self.run_with({"id": "a", "enabled": True, "last_run_at": "2026-08-22T09:30:00Z",
                           "last_error": "FAIL: written but NOT delivered"})
        self.assertEqual(r["status"], "FAIL")

    def test_silent_drop_fails(self):
        # Ran, nothing logged, record says it was not delivered. The first
        # version returned PASS here.
        r = self.run_with({"id": "b", "enabled": True, "last_run_at": "2026-08-22T09:30:00Z",
                           "last_error": "", "delivered": False})
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("no delivery", r["detail"])

    def test_job_that_never_ran_is_not_silently_green(self):
        r = self.run_with({"id": "c", "enabled": True, "last_run_at": None})
        self.assertNotEqual(r["status"], "PASS")

    def test_job_gone_quiet_fails_when_max_age_set(self):
        H.JOB_MAX_AGE_H = 1
        r = self.run_with({"id": "d", "enabled": True, "last_run_at": "2020-01-01T00:00:00Z"})
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("gone quiet", r["detail"])

    def test_healthy_delivery_passes(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        r = self.run_with({"id": "e", "enabled": True, "last_run_at": now,
                           "delivered_at": now})
        self.assertEqual(r["status"], "PASS")

    def test_unknown_evidence_warns_rather_than_failing(self):
        # A runner that records nothing either way must not make the invariant
        # permanently red — an always-red gate is ignored like an always-green one.
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        r = self.run_with({"id": "f", "enabled": True, "last_run_at": now})
        self.assertEqual(r["status"], "WARN")

    def test_disabled_jobs_are_not_counted_as_verified(self):
        # Changed 2026-08-26: this used to assert PASS. It should not. If every
        # job in the record is disabled, nothing was checked — and an invariant
        # whose purpose is "fail on absence" must not report success for a run it
        # did not verify. WARN says exactly what happened.
        r = self.run_with({"id": "g", "enabled": False, "last_run_at": None})
        self.assertEqual(r["status"], "WARN")
        self.assertIn("no enabled jobs", r["detail"])

    def test_glob_matching_nothing_fails(self):
        H.JOBS_GLOB = os.path.join(self.tmp, "no-such-file-*.json")
        r = H.check_delivery()
        self.assertEqual(r["status"], "FAIL",
                         "no records at all is not evidence of health")

    def test_unreadable_record_fails(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as f:
            f.write("{not json")
        H.JOBS_GLOB = bad
        r = H.check_delivery()
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("unreadable", r["detail"])

    def test_multiple_globs_are_supported(self):
        import json as _json
        a = os.path.join(self.tmp, "a.json")
        b = os.path.join(self.tmp, "b.json")
        with open(a, "w") as f:
            _json.dump({"jobs": [{"id": "1", "enabled": True,
                                  "last_run_at": "2026-08-26T00:00:00Z",
                                  "delivered": True}]}, f)
        with open(b, "w") as f:
            _json.dump({"jobs": [{"id": "2", "enabled": True,
                                  "last_run_at": "2026-08-26T00:00:00Z",
                                  "delivered": False}]}, f)
        H.JOBS_GLOB = a + os.pathsep + b
        r = H.check_delivery()
        self.assertEqual(r["status"], "FAIL", "the second file's failure must be seen")

    def test_clean_run_counts_as_delivered_only_when_declared(self):
        # Some runners never write a positive delivery field and instead clear
        # their error field on success. Warning forever on those is alert fatigue;
        # assuming it silently would be the false-green this check exists to stop.
        # So it is an explicit declaration, and both readings are pinned here.
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        job = {"id": "z", "enabled": True, "last_run_at": now}
        old = H.ERRORS_CLEARED_ON_SUCCESS
        try:
            H.ERRORS_CLEARED_ON_SUCCESS = False
            self.assertEqual(self.run_with(job)["status"], "WARN")
            H.ERRORS_CLEARED_ON_SUCCESS = True
            self.assertEqual(self.run_with(job)["status"], "PASS")
        finally:
            H.ERRORS_CLEARED_ON_SUCCESS = old

    def test_declaration_never_masks_a_real_failure(self):
        # The declaration must not turn a logged delivery failure green.
        old = H.ERRORS_CLEARED_ON_SUCCESS
        try:
            H.ERRORS_CLEARED_ON_SUCCESS = True
            r = self.run_with({"id": "y", "enabled": True,
                               "last_run_at": "2026-08-30T02:27:40Z",
                               "last_delivery_error": "Telegram send failed: Forbidden"})
            self.assertEqual(r["status"], "FAIL")
        finally:
            H.ERRORS_CLEARED_ON_SUCCESS = old

    def test_skips_cleanly_when_unconfigured(self):
        H.JOBS_GLOB = ""
        self.assertEqual(H.check_delivery()["status"], "SKIP")


class TestCli(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "harness_lint.py", *args],
                              capture_output=True, text=True, cwd=REPO)

    def test_bundled_specs_pass_with_no_configuration(self):
        r = self.cli("--all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_all_and_spec_are_mutually_exclusive(self):
        r = self.cli("--all", "--spec", "research-summary")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not allowed with", r.stderr)

    def test_json_output_is_valid(self):
        import json
        r = self.cli("--all", "--json")
        json.loads(r.stdout)

    def test_exit_code_counts_failures(self):
        r = self.cli("--spec", "definitely-not-a-spec")
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
