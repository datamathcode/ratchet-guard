# ratchet-guard

A metric-aware replacement for the greedy autoresearch ratchet.

Karpathy's `autoresearch` loop is one rule: **if the number improved, keep the
change.** It works, and it is the most useful research-automation pattern
anyone has published. It also has a structural blind spot that shows up in
every serious run: the ratchet optimizes the number it is given, and has no
way to notice when the number stops meaning the goal.

`ratchet-guard` keeps the loop and adds the missing judgment. The candidate
stream is unchanged. What changes is who is allowed to say yes.

```
naive     accept  <=>  metric moves the right way
guarded   accept  <=>  metric moves the right way
                       and the noise agrees
                       and the guards hold
                       and the cost is paid
                       and the adjudicator the search cannot reach agrees
```

## The failure it exists to prevent

> A ratchet is a one-way valve on a measurement. A wrong measurement, once
> accepted, is not a mistake that gets corrected later. It is a design
> commitment that every future candidate must now beat.

That sentence is the whole argument. In a naive loop, overfitting one fixed
validation split does not merely waste trials — it installs a candidate the
metric loves and the goal rejects, and then *defends* it, because every honest
candidate scores lower. The run reports progress while making things worse.

`examples/toy-metric-blindness` reproduces this in about four seconds on a CPU
with no dependencies. Same candidates, same order, two judges:

```
NAIVE RATCHET   accept <=> the surrogate improved
  quadratic      surrogate=0.9861  goal=0.9733  accept
  memorizer      surrogate=1.0000  goal=0.5800  accept
  linear_l2      surrogate=0.8917  goal=0.8644  discard
  quadratic_l2   surrogate=0.9653  goal=0.9522  discard

GUARDED RATCHET   accept <=> the surrogate improved AND the gates agree
  quadratic      surrogate=0.9861  goal=0.9733  promoted
  memorizer      surrogate=1.0000  goal=0.5800  promotion_denied
  linear_l2      surrogate=0.8917  goal=0.8644  reject_noise
  quadratic_l2   surrogate=0.9653  goal=0.9522  reject_variance

  held at exit      naive: memorizer     guarded: quadratic
  goal at exit      naive: 0.5800        guarded: 0.9733
  goal delta        naive: -0.2844       guarded: +0.1089
```

The naive run reached a surrogate score of **1.0000** — a perfect score — and
finished 0.28 *below* where it started on the thing it was asked to improve.
Nothing malfunctioned. The ratchet did exactly what it was told.

## Install

```bash
uv venv .venv && . .venv/bin/activate
uv pip install -e .
```

Zero runtime dependencies. Python 3.11+ (uses `tomllib`).

## Quickstart

```bash
cd examples/toy-metric-blindness
python run_demo.py          # naive vs guarded, side by side
```

Against a real project:

```bash
ratchet --contract contract.toml --state .ratchet init
ratchet baseline --cmd "python evaluate.py --model baseline"
ratchet canary                              # calibrate the metric
ratchet trial --commit $(git rev-parse --short HEAD) \
              --family architecture \
              --hypothesis "add interaction terms" \
              --predicted better \
              --cmd "python evaluate.py --model quadratic"
ratchet promote --trial t001                # sealed-holdout adjudication
ratchet report
```

## The seven gates

| # | Gate | Rejects when |
|---|------|--------------|
| 1 | Provenance | the measurement was produced under a different contract |
| 2 | Replicates | fewer than three seeds; no noise floor is estimable |
| 3 | Guards | a declared cost metric degraded past its tolerance |
| 4 | Variance | the mean improved but run-to-run spread widened |
| 5 | Effect | the 95% CI lower bound does not clear `min_delta` after the complexity price |
| 6 | Queue | too many accepted changes are waiting for adjudication |
| 7 | Adjudication | the sealed holdout does not reproduce the gain |

Gate 7 runs at promotion, not at trial. The ratchet may move fast on the
working branch. Only the canonical baseline requires the goal's consent.

## What it does not do

It does not make a greedy search non-greedy. A ratchet still cannot find a
paradigm shift, and no amount of guarding changes that. What it can do is stop
the ratchet from confidently installing the wrong thing, and make the
exploration it *does* do deliberate rather than accidental.

## Reading

- [`docs/EVALUATION.md`](docs/EVALUATION.md) — what the methodology gets right, and its structural limits
- [`docs/PITFALLS.md`](docs/PITFALLS.md) — 23 implementation pitfalls with a detector for each
- [`docs/SPEC.md`](docs/SPEC.md) — the specification of this implementation
- [`docs/ADAPTATIONS.md`](docs/ADAPTATIONS.md) — pitfall to mechanism, one row each
- [`program.md`](program.md) — the agent-facing instruction file
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — operating a run, and what to do when it halts

## License

MIT
