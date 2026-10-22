# Runbook

## Before the first trial

```bash
uv venv .venv && . .venv/bin/activate && uv pip install -e .
```

1. **Write the contract.** Start from `examples/toy-metric-blindness/contract.toml`.
   The `goal` field must be a sentence you would defend to a reviewer. If you
   cannot write it, you are not ready to run.

2. **Establish the surrogate baseline, and the adjudicated one.**

   ```bash
   ratchet baseline \
     --cmd "python evaluate.py --model incumbent" \
     --canonical-cmd "python evaluate.py --model incumbent --split sealed" \
     --commit $(git rev-parse --short HEAD) \
     --runs 3
   ```

   `--runs 3` is the default and it is not decoration. The baseline is the
   reference for every delta in the run, and the first run is the only one
   measured on a cold machine — cold page cache, cold autotune, possibly
   different clocks. A baseline of one draw gives every later delta its error,
   and more trials never shrink it (P29).

   Without `--canonical-cmd` no promotion can be adjudicated, and the run will
   tell you so.

3. **Calibrate the metric.**

   ```bash
   ratchet canary
   ```

   Three possible outcomes:

   | Output | Meaning | Next |
   |---|---|---|
   | all PASS | the metric separates every declared failure mode | run |
   | BLIND (holdout) | the metric is blind, an adjudicator covers it | run, every promotion needs a read |
   | BLIND (UNCOVERED) | the metric is blind and nothing checks it | **halt.** Fix the contract or the metric. |

   Exit code 2 means halted. Do not clear the halt by editing the canary out.

## Measuring the noise floor

Do this before the first trial, and again whenever the machine changes. It is
the one measurement that makes every other number in the run mean something.

```bash
ratchet control --cmd "python evaluate.py --model incumbent" --runs 8
```

Nothing is changed, so everything it reports is noise. It prints σ and the
**relative uncertainty of that σ**, then `min_delta` at 1.0×, 2.0× and 2.8× σ.

How to read it:

| ratio `min_delta`/σ | meaning |
|---|---|
| < 1.0 | the run refuses to start. A threshold under one sd cannot separate anything. |
| 1.0 – 2.0 | accepting noise records; the log will be fiction |
| 2.0 | ~92% one-sided on a single comparison — the usual choice |
| 2.8 | ~97.5%; buys a cleaner history at the cost of terminal bias (see PITFALLS appendix) |

Then leave it alone. Every trial pools its own within-arm variance into the same
estimate, so σ gets more accurate as the run proceeds for free.

**Do not** run three no-ops and call it measured. At n=3 the relative uncertainty
of σ is ±50% — the same error as trusting a two-seed floor, one order of
magnitude smaller. Eight runs of four seeds gives ±13%.

**Do not** pool across configurations. The variance between two different models
contains the effect you are measuring, and the floor would appear to shrink as
the run succeeded.

## Checking for drift

Machines warm up. Shared GPUs get noisy neighbours. A drift of 0.002 across a
night is indistinguishable from a real win by any test in this tool.

```bash
ratchet drift --cmd "python evaluate.py --model incumbent"
```

Every ~10 trials, and before you believe a marginal result. If the reference has
moved, the run halts: nothing measured before and after that point is comparable.

The better arrangement is not to need this. If your evaluator can interleave the
reference configuration with the candidate and report `meta.reference_seeds`,
the gate compares paired and the drift cancels instead of halting the run. See
`docs/ADAPTATIONS.md`, M13.

## During the run

- One writer per branch. Before any push: `git fetch` and
  `git rev-list --left-right --count origin/<branch>...HEAD`. `0 N` is ahead and
  safe; anything else is diverged and needs integration, never a force push.
- `ratchet status` after every ~10 trials. Watch `unadjudged` and the family
  distribution.
- `ratchet verify` occasionally. It is cheap and it is the only check that the
  record has not been rewritten.

## Promoting

Promotion is a human or adjudicator role. The agent may not call it.

```bash
ratchet promote --trial t007
```

- `PROMOTED` — the canonical baseline moved. The holdout read is spent.
- `PROMOTION DENIED` — the surrogate gain did not survive. Run the printed
  `git reset --hard <canonical>` and continue from there.

Each promotion costs one read from `holdout_budget`. When it hits zero the run
halts. That is by design: after N reads the sealed split is no longer sealed,
and continuing would be P5 one level up.

**Minting a new holdout** is a human decision, and it requires new data. Do not
re-split the old data — the search has already seen it through the surrogate.

## When the run halts

| Reason | What it means | What to do |
|---|---|---|
| `metric is blind to X with no adjudicator` | the metric cannot see a failure mode and nothing checks it | add an adjudicator, or replace the metric |
| `holdout budget exhausted` | the sealed split has been read too many times | mint a new split from new data |
| `N consecutive crashes` | the editable surface is broken, not the models | `git reset` to the last accepted commit and inspect |
| `queue: N unadjudicated` | accepts are outrunning verification | promote or roll back, then continue |
| `reference configuration drifted` | the machine changed under the run | stop. `control` again on a quiet machine, then restart the session |
| `min_delta is below the noise floor` | the contract cannot distinguish signal from noise | raise `min_delta` to ≥2σ, or cut the noise. Do not lower the floor. |

A halt is not a failure of the run. It is the run refusing to produce a number
it cannot justify. Clearing a halt by editing the contract is the one action
that makes every other guarantee worthless.

## Reading the output

```bash
ratchet report
```

- `canonical` and `working` are on **different instruments**. They are printed
  side by side and are never subtracted. A run whose canonical is below its
  working number is not "ahead"; it is unverified.
- The promotion log is the honest record. Every entry is a candidate that was
  measured against the goal.
- The Pareto archive lists the trade-offs the run discovered among promoted
  changes. It is where you look if you later want a different operating point
  without re-running anything.

## Cost planning

Trials ≈ budget / (eval time × seeds). The gate needs **four** seeds, because at
three per arm the best any rank test can do is p=0.10 and no comparison is
reportable at α=0.05. So a five-minute training run costs twenty minutes per
trial, not five. A hundred-trial night becomes a twenty-five-trial night.

That is the real price of the statistics, and it is up 33% from the three-seed
version. Budget for it up front — or lower the seed count and accept that the run
cannot distinguish a real win from a lucky one, and say so in the report. What
you must not do is lower it and then quote the deltas anyway.

`baseline --runs 3` and `control --runs 8` are outside this budget and cost
roughly 11 evaluations once. That is about 4% of a 25-trial night, and it is what
makes the other 96% interpretable.
