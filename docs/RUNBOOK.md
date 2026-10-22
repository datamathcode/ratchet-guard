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
     --commit $(git rev-parse --short HEAD)
   ```

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

## Estimating the noise floor

Do this once, and again whenever the machine changes.

```bash
for i in 1 2 3; do python evaluate.py --model incumbent; done
```

The spread of those three numbers is the floor. Set `min_delta` to roughly
twice it. If the floor is larger than the effect you hope to find, the run
cannot see what you want to see, and no amount of trials fixes that.

Three seeds minimum. Two seeds is not a smaller estimate, it is a wrong one.

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

Trials ≈ budget / (eval time × seeds). The gate needs three seeds, so a five
minute training run costs fifteen minutes per trial, not five. A hundred-trial
night becomes a thirty-trial night.

That is the real price of the statistics. Budget for it up front, or lower the
seed count — and then accept that the run cannot distinguish a real win from a
lucky one, and say so in the report.
