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
baseline   : surrogate=0.8885  goal=0.8625
noise sd   : 0.0098  ->  min_delta is 2.0x sd

NAIVE RATCHET   accept <=> the surrogate improved
  memorizer      surrogate=1.0000  goal=0.5800  accept
  quadratic      surrogate=0.9854  goal=0.9725  discard
  linear_l2      surrogate=0.8906  goal=0.8625  discard
  quadratic_l2   surrogate=0.9677  goal=0.9517  discard

GUARDED RATCHET   accept <=> the surrogate improved AND the gates agree
  memorizer      surrogate=1.0000  goal=0.5800  promotion_denied
  quadratic      surrogate=0.9854  goal=0.9725  promoted
  linear_l2      surrogate=0.8906  goal=0.8625  reject_noise
  quadratic_l2   surrogate=0.9677  goal=0.9517  reject_variance

  held at exit      naive: memorizer     guarded: quadratic
  goal at exit      naive: 0.5800        guarded: 0.9725
  goal delta        naive: -0.2825       guarded: +0.1100
```

The naive run reached a surrogate score of **1.0000** — a perfect score — and
finished 0.28 *below* where it started on the thing it was asked to improve.
Nothing malfunctioned. The ratchet did exactly what it was told.

The candidate order is deliberate: the memoriser is tried **first**, because an
agent reaching for a big win reaches for the cheap one. An agent that is honest
about its own uncertainty would do that. `min_delta` in that contract is 2.0× the
measured noise sd, which is why `linear_l2` is a noise rejection and not a
"close enough".

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
ratchet baseline --cmd "python evaluate.py --model baseline" --runs 3
ratchet control  --cmd "python evaluate.py --model baseline" --runs 8   # sigma
ratchet canary                              # is the metric blind to anything?
ratchet trial --commit $(git rev-parse --short HEAD) \
              --family architecture \
              --hypothesis "add interaction terms" \
              --predicted better \
              --cmd "python evaluate.py --model quadratic"
ratchet promote --trial t001                # sealed-holdout adjudication
ratchet drift --cmd "python evaluate.py --model baseline"   # every ~10 trials
ratchet report
```

`control` is the one most people skip. It runs the incumbent with nothing
changed, so everything it reports is noise, and it prints `min_delta` as a
multiple of a *measured* sd rather than one somebody guessed. Skip it and the
run still works; it just cannot tell you whether its own threshold means
anything.

## The gates

Evaluated in order; the first failure names the verdict, so every rejection has
exactly one cause.

| # | Gate | Rejects when |
|---|------|--------------|
| 0 | Halt | the run has stopped: metric blind, holdout spent, reference drifted, or a contract whose threshold is below the noise |
| 1 | Provenance | the measurement was produced under a different contract |
| 2 | Replicates | fewer than four seeds; no comparison is reportable at α=0.05 |
| 3 | Guards | a declared cost metric degraded past its tolerance |
| 3b | Overhead | work moved outside the scored budget, into import time or a warmup |
| 4 | Variance | the mean improved but run-to-run spread widened |
| 5 | Effect | the 95% CI lower bound does not clear `min_delta` after the complexity price |
| 6 | Queue | too many accepted changes are waiting for adjudication |
| 7 | Adjudication | the sealed holdout does not reproduce the gain |

Gate 7 runs at promotion, not at trial. The ratchet may move fast on the
working branch. Only the canonical baseline requires the goal's consent.

## If you only do five things

From `docs/PITFALLS.md`, in the order that makes the others interpretable:

1. **No-op trials → σ.** Then `min_delta = 2σ`. Write down that it is a ratchet
   damper, not a significance test.
2. **Baseline = mean of 3 runs, not one.**
3. **Assert the unscored share of the wall clock is under a cap.** One line.
4. **Interleave a reference config every ~10 trials.** Drift is not noise.
5. **Re-measure the top 3 kept changes at 3× budget and on a sealed split**
   before believing any of it.

Items 1–4 are a day's work and retrofit onto a running loop. Item 5 tells you
whether the night was real.

## The honest caveat

Building all of this means **you are no longer running autoresearch — you are
running a lab with an agent in it.** That is a different activity with a
different cost. Every gate rejects some true positives; the bet is that a
rejected true positive costs one trial and an accepted false positive costs the
rest of the run.

Run the naive loop when the metric is cheap and trustworthy and you want
throughput. Run this when the output is going to be believed by someone else.

## What it does not do

It does not make a greedy search non-greedy. A ratchet still cannot find a
paradigm shift, and no amount of guarding changes that. What it can do is stop
the ratchet from confidently installing the wrong thing, and make the
exploration it *does* do deliberate rather than accidental.

## Reading

- [`docs/EVALUATION.md`](docs/EVALUATION.md) — what the methodology gets right, and its structural limits
- [`docs/PITFALLS.md`](docs/PITFALLS.md) — 32 implementation pitfalls, each labelled detector or mitigation
- [`docs/SPEC.md`](docs/SPEC.md) — the specification of this implementation
- [`docs/ADAPTATIONS.md`](docs/ADAPTATIONS.md) — pitfall to mechanism, one row each
- [`program.md`](program.md) — the agent-facing instruction file
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — operating a run, and what to do when it halts

## License

MIT
