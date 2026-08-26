# agent-harness — overview

## The problem

A scheduled agent can produce a perfect artifact, on time, every time, and still
fail the only thing that mattered. That is the reason this project exists.

The incident that started it is worth stating plainly because it generalises. A
weekly digest was written to disk every Saturday for three weeks and rejected by
the messaging API every Saturday. The file was present, fresh, and well-formed,
so every freshness and formatting check passed. The recipient saw nothing for
three weeks, and the entire downstream workflow sat at zero without a single
alarm.

Artifact freshness is not delivery. For any output that must reach a human,
completion is delivery, not a file path — and the moment you measure only the
artifact, every check calls a non-delivery green.

Most agent tooling answers "how do I run this?". This project answers the harder
question: "how do I know it worked?" It is a contract layer for recurring agent
output — a small linter, no LLM, no network, seconds to run, that checks
scheduled output against a spec and fails loudly when the spec is broken.

## The three ideas

**A spec per recurring output.** Each recurring artifact — a daily brief, a
weekly digest, a research memo — is governed by a markdown file with YAML front
matter that declares what the output must contain, how fresh it must be, what it
must never say, and a self-evaluation rubric. The front matter is
machine-checked; the prose below it is guidance for whoever writes the producer's
prompt. The two are deliberately in the same file, because a spec is as much a
prompting document as a contract.

**A self-evaluation rubric the producer fills in honestly.** Every run appends a
checklist and ticks it honestly. An unchecked box is the system working — it is
the producer reporting a real miss. A false `[x]` is the one unforgivable
failure, because it is the only thing that makes the whole layer worthless: no
mechanical check can catch a lie, so the honesty of the tick is what the method
rests on. The corollary matters as much: never hand-edit a past artifact to turn
the board green. Patch the spec or the producer, and let the next scheduled run
prove it.

**Invariants — cross-cutting assertions no single spec owns.** Exactly one ships:
the delivery dead-man's switch, which asserts that scheduled output actually
reached a human. It requires positive delivery evidence and fails on absence. It
was rewritten after the first version turned out to have the blind spot it was
written to close: that version matched known failure phrases, so it caught a
delivery that logged its own error and silently passed a delivery that just
dropped and a job that had stopped running. An invariant that only catches the
failures polite enough to announce themselves is not a liveness check. Only one
invariant ships on purpose — an invariant is worth having when you have seen it
go red against a real incident, and a list of untested ones would be exactly the
decoration this method is meant to replace.

## How to use it in five minutes

Clone the repo and run it from the root. Nothing needs to be installed — the
linter is standard-library Python and the bundled examples run green out of the
box.

```
git clone https://github.com/AndreasOliverSchwing/agent-harness
cd agent-harness

python3 harness_lint.py --all --invariants
```

This checks every bundled spec and the delivery invariant. Exit code 0 means
everything passed; the exit code is the number of failing harnesses plus failing
invariants, capped at 125.

Check one spec:

```
python3 harness_lint.py --spec research-summary
```

Check a specific file against a spec before it ships (the `--artifact` flag
overrides the spec's glob):

```
python3 harness_lint.py --spec research-summary --artifact path/to/brief.md
```

Get machine-readable output for your own monitoring:

```
python3 harness_lint.py --all --invariants --json
```

Point it at your own outputs. The one knob is `ARTIFACTS_DIR`, which the specs'
`artifact_glob` patterns resolve against. Default it points at the bundled
`examples/` so a fresh clone runs green; set it to where your output actually
lands:

```
ARTIFACTS_DIR=/path/to/your/outputs python3 harness_lint.py --all
```

The delivery invariant reads your job runner's run records. Set `HARNESS_JOBS_GLOB`
to the record file (one JSON file listing enabled jobs with `delivered_at` /
`delivered` fields), and `HARNESS_JOB_MAX_AGE_H` to your longest cadence plus a
grace window so a job that silently stops running also fails:

```
HARNESS_JOBS_GLOB=/path/to/jobs.json HARNESS_JOB_MAX_AGE_H=192 \
    python3 harness_lint.py --all --invariants
```

Run the test suite with:

```
python3 -m unittest discover -s tests
```

To write your own spec, copy one from `specs/` and edit the front matter. The
full key reference is in `SPEC-REFERENCE.md`; the JSON Schema in `spec.schema.json`
validates the front matter in editors and CI.

## What this is not

Not an agent framework, orchestrator, or scheduler. It assumes you already have
those, and it does not replace them.

Not a quality scorer. It does not judge whether an output is good — it asserts
that a contract was kept: present, fresh, shaped right, honestly self-evaluated,
actually delivered. If you need semantic grading of content, this is the wrong
tool.

Not for one-off output. A spec is worth writing only for something produced on a
cadence, where a silent miss compounds.

Not a data-quality platform. There is no results store, no dashboard, no anomaly
detection. It prints to stdout and exits with a count; your existing monitoring
consumes the `--json` output.

Not a proof of delivery. It reads whatever delivery evidence your runner records.
If your runner records nothing, the invariant can only warn, not prove — and if
your output has no human recipient, the delivery invariant is meaningless.

Not a security boundary. Specs are executable input: `artifact_glob` is expanded
and read, `banned_patterns` are compiled as regexes. Run it only against specs
you control.

## Where it sits

The LLM evaluation harnesses — promptfoo, Braintrust, LangSmith, DeepEval —
grade model responses against a rubric, mostly with an LLM-as-judge, run on
demand against a defined test set, and surface results in a dashboard the human
must still go look at. This project does none of that: it is offline, mechanical,
and asserts recurrence and delivery, which those tools do not model — it is not a
competitor to them, it sits in front of them and answers the question they do not
ask. The data-contract tools — Great Expectations, Soda, dbt tests — contract
tabular data: columns, rows, table freshness. This is the same idea applied to
agent-authored prose: a declarative assertion about a produced thing, where the
thing is a document and the assertion that matters most is "a human received it."
It borrows dbt's `warn_after`/`error_after` freshness ladder and Great
Expectations' severity notion, but it is a single-file linter, not a platform,
and delivery is out of scope for all of them.
