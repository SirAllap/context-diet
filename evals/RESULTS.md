# What has been measured, and what has not

One run, 2026-09-20: five cases, 12 runs per arm, 120 runs, $30. The ablation
arm is a no-plugin baseline, so every row is a delta rather than a mark.

| case | with | without | Δ | trust it? |
|---|---:|---:|---:|---|
| `root-delegates-the-reading` | 0.67 | 0.25 | **+0.42** | yes |
| `small-stays-here` | 1.00 | 1.00 | 0.00 | yes |
| `builder-keeps-to-its-contract` | 0.83 | 0.83 | 0.00 | yes |
| `scout-answers-every-part` | 0.08 | 0.00 | +0.08 | no — grader rewritten since |
| `verifier-returns-counts` | 0.25 | 0.42 | −0.17 | no — grader rewritten since |

## The one that matters

**`root-delegates-the-reading`, Δ +0.42.** Faced with a multi-file change, the
main thread sent the reading to a worker in 8 of 12 runs with the plugin and 3
of 12 without it. That case's criterion asks whether the reading was delegated
at all, not whether a particular agent was named, so both arms could satisfy
it — which is what makes the comparison fair and the number worth keeping.

**`small-stays-here`, Δ 0.00 at 1.00 both arms.** A one-word typo was never
delegated, with the plugin or without. This case exists to fail a plugin that
delegates everything, because that plugin costs more than no plugin: it holds.

**`builder-keeps-to-its-contract`, Δ 0.00 at 0.83.** Claude already writes a
contract and keeps a signature stable about four times in five. The plugin
neither helps nor hurts here, and saying so is more useful than not measuring.

## The two that do not count

Both had the same design fault, found by reading the numbers rather than
liking them: the grader asked whether a *named plugin agent* was dispatched —
and that agent does not exist in the baseline arm. So one arm was scored
against something the other structurally cannot do, and the judge's leniency
about it decided the result. In `verifier-returns-counts` that leniency put
the **baseline ahead**, which is not a finding about either arm.

Both graders now split the dispatch out as a `with_only` indicator at weight
0, leaving the score to measure what both arms can do: did the log stay out of
the thread, was the answer honest, was a worker's plain list re-typeset into a
table.

**Neither has been re-run.** The numbers above are what the old graders said
and they are kept here so the change is visible, not because they mean
anything. Whoever runs the suite next gets the first clean reading.

## What an eval cannot measure here

Which model a delegation lands on is not a judgement the model makes: it is a
line of frontmatter. `tests/test_tally.py` asserts it instead — that `scout`
and `verifier` stay on the cheap model, that neither drifts to the expensive
one, and that an agent added later cannot ship without a pin and inherit the
session default in silence.

Measured on a real machine the day this was written: 206 subagent turns on
Haiku after installing, against Sonnet and Opus before, with nothing else
changed. Haiku reads cache at a fifth of what Opus does.

## Running it

```
claude plugin eval . --trust-plugin --allow-tools Bash Task Edit Write -j 8 --runs 12
```

The sandbox starts with an empty working directory. That is deliberate: what
is graded is the decision, which is visible in the transcript whether the
directory is full or empty. An earlier suite shipped a fixture through
`scaffold_script`, which runs outside the sandbox the agent works in, so the
agents correctly went looking elsewhere and the graders failed them for it.
Three rounds of scores measured the harness and not the plugin.
