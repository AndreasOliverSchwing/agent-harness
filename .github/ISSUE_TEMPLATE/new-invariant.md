---
name: New invariant
about: Propose a cross-cutting assertion no single spec owns
labels: invariant
---

## The known failure this reproduces

<!-- Required, and deliberately the first field.

An invariant you have not seen go red is not evidence — it is decoration. Before
proposing one, point it at the incident that motivated it and confirm it fails.
Describe that incident here: what happened, what the existing checks reported at
the time, and why they were green. -->

## What it asserts

## What it does NOT catch

<!-- Every invariant has a blind spot. The delivery invariant shipped with one:
it only matched failures that logged themselves. Naming the gap up front is how
the next person avoids trusting it further than it goes. -->

## FAIL vs WARN

<!-- Which conditions fail, and which only warn? An invariant that is red on
every setup that does not happen to record what it needs gets ignored exactly
like one that is always green. -->
