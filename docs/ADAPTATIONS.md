# Adaptations

## The principle

> A ratchet is a one-way valve on a measurement. Therefore it must never be
> the sole judge of that measurement.

Every adaptation below is one way of giving the ratchet a second opinion, or of
making the first opinion cheaper to trust. Nothing here makes the search
smarter. All of it makes the search's conclusions cheaper to falsify.

## The mechanisms

| # | Mechanism | What it replaces |
|---|---|---|
| M1 | **Goal contract** — machine-readable objective, hashed per result | prose in `program.md` |
| M2 | **Canary suite** — known-good/known-bad mutants that calibrate the metric | the assumption that the metric measures the goal |
| M3 | **Sealed adjudication** — a split the search cannot reach, with a rationed read budget | the same split used for selection and reporting |
| M4 | **Noise floor and `min_delta`** — measured spread, not an assumed one | accepting any improvement |
| M5 | **Ordered gates** — seven checks, one cause per verdict | a single comparison |
| M6 | **Complexity price** — metric units charged per added line | "a 0.002 win that adds 20 hacky lines? probably not worth it" |
| M7 | **Speculative debt** — a bounded budget of accepted regressions with a repayment deadline | "it can never take a step backward" |
| M8 | **Family bandit** — UCB over declared idea families | "if you run out of ideas, think harder" |
| M9 | **Append-only hash-chained ledger**, written by the harness | an untracked TSV written by the agent |
| M10 | **Promotion ladder** — screen → replicate → adjudicate → transfer | one tier, one number |
| M11 | **Kill switches** — crash streak, holdout exhaustion, queue depth | `NEVER STOP` with no stop condition |
| M12 | **Calibration tier** — a no-op control run that measures σ, pooled within-arm as the run proceeds | a `min_delta` someone guessed |
| M13 | **Interleaved reference** — `reference_seeds` in the same session, compared paired | a baseline measured hours ago on a machine that has since warmed up |
| M14 | **Overhead gate** — rejects a candidate that moved work outside the scored budget | the unscored half of the wall clock |

## Mapping

| Pitfall | Mechanism |
|---|---|
| P1 metric-goal divergence | M1, M2, M3 |
| P2 entrenchment | M3 (the exploit cannot become canonical), M10 |
| P3 saturation freeze | M2 (identifies the ceiling as an instrument problem), M7 (allows escape) |
| P4 evaluator reachability | M9, out-of-process eval, harness hash |
| P5 eval-set overfitting | M3, M10 |
| P6 multi-objective collapse | M1 (`[[guards]]`), M6 |
| P7 short-horizon proxy | M10 (transfer tier) |
| P8 goal drift | M1 (contract digest stamped per result) |
| P9 n=1 | M4 |
| P10 unknown noise floor | M4 |
| P11 two-seed floor | M4 (`SEED_FLOOR = 4`) |
| P12 winner's curse | M3, M10 |
| P13 step-count confound | M4 (replicate floor; assert equal counts) |
| P14 variance inflation | M5 (gate 4) |
| P15 crash as zero | `EvalError` — a crash produces no measurement |
| P16 greedy optimum | M7, M8 |
| P17 no memory of why | structured trial record, hypothesis + prediction |
| P18 interaction blindness | M10 (leave-one-out at the adjudicated tier) |
| P19 complexity ratchet | M6 |
| P20 mode collapse | M8 |
| P21 self-grading | M9 |
| P22 incomplete revert | harness hash + provenance gate |
| P23 fragile record | M9 |
| P24 unbounded autonomy | M11 |
| P25 concurrent writers | runbook procedure |
| P26 unverified transfer | M10 (transfer tier) |
| P27 noise as progress | M4 (threshold), M12 (measured σ), M10 (replication), M3 (unbiased adjudicator) |
| P28 unscored wall clock | M14, M1 (`wall_overhead_cap`) |
| P29 single-draw baseline | M12, `baseline --runs 3` |
| P30 no negative control | M12 |
| P31 non-iid noise | M13 (paired), M12 (`drift` halt) |
| P32 evaluator read access | `program.md`: interface, not source |

## The five that carry the design

### M2 — the canary suite

This is the direct answer to metric blindness, and it is the piece that no
other autoresearch variant has.

A canary is a candidate whose true quality is already known. If the metric
ranks a known-bad mutant above the live baseline, the metric is blind to that
failure mode — and the suite **records the blindness instead of failing**.

Recording rather than failing is the important design choice. A metric that
fails a canary is not broken; it is *narrow*. Almost every useful metric is
blind to something, and a tool that refuses to run at the first gap is a tool
nobody uses. What the suite produces is a calibration: a list of failure modes
this metric cannot see.

The gate then uses that list. **If the metric is blind, it may not judge
alone.** Every accept becomes conditional on a sealed-holdout read. The cost of
the blindness is paid in adjudication budget, not in correctness.

Coverage is declared explicitly in the contract (`covered_by`), and a blind spot
with no enabled adjudicator behind it is a hard stop. "We know the metric is
blind to memorisation and we decided to do nothing about it" is not a caveat.
It is the exact configuration that produces the failure.

### M3 — sealed adjudication with a rationed budget

The holdout is not just "another split". It is a split with a *budget*: a total
number of reads, decremented on every promotion, never replenished. When it is
exhausted the run halts.

The budget is what makes it work. An unlimited holdout becomes a second
training set within a few hundred trials — the same P5 failure, one level up.
A rationed one forces the loop to spend its reads on candidates it actually
believes in, which is exactly the human behaviour that a greedy loop lacks.

The adjudicator must depend on the candidate under review. The contract's
`holdout_cmd` therefore requires a `{candidate}` placeholder, and promotion
refuses to run without it. An adjudicator that scores a fixed model would
approve anything, which is worse than no adjudicator, because it looks like one.

### M7 — speculative debt

The known limitation of the ratchet is that it cannot take a step back. The fix
is not to remove the ratchet — the monotone rule is what keeps the run from
accumulating damage. The fix is to give it a *bounded, accounted* exception.

A change may be accepted while worse, if and only if:

- the proposer pre-registered that it expects to be worse first, and
- the idea family is under-explored relative to an even split, and
- the debt budget (a fraction of total trials) is not exhausted, and
- the regression is not catastrophic relative to the noise floor.

The entry carries a deadline. If the cumulative gain has not turned positive
within the repayment window, the debt expires and the run rolls back. So the
loop can go backwards, but it has to *say so in advance* and it has to *pay it
back*. That is a materially different thing from an unbounded step-back, and it
recovers most of what P16 costs.

### M12 — the calibration tier

The threshold the ratchet compares against used to be a number a human guessed.
M4 made it a measured number, but it was still measured once, at the start,
from too few samples to be worth much.

The calibration tier fixes the sample size without spending trials. Three things
happen in sequence:

1. **`control`** runs the incumbent unchanged, several times. Nothing can
   improve, so everything it measures is noise. It reports σ and, importantly,
   the *relative uncertainty of σ* — a number the operator needs in order to know
   whether to trust the threshold they are about to set.
2. **Every trial pools its within-arm variance** into the same estimate. This is
   free: the seeds were already there. By trial 20 the floor rests on ~80 samples
   instead of 8, and no experiment was paid for it.
3. **`min_delta` below σ halts the run** rather than rejecting trials politely.
   A threshold under one standard deviation cannot distinguish anything, so every
   verdict it could produce is a coin flip that has been written down.

The one rule that matters: pooling never crosses arms. Variance *between*
configurations contains the effect under test, so a cross-arm pool would make the
noise floor appear to shrink as the run succeeded — the instrument would report
itself as becoming more precise exactly when it was being tuned to the incumbent.

### M13 — the interleaved reference

P31 says the noise is not iid, so every rank test in P11 is invalid, and the
document's remedy is "detect drift and abort". That is a stop condition, not a
measurement.

Interleaving is the measurement. If the evaluator runs candidate and reference
seeds alternately in one session and reports both, then drift is a *shared
offset* between two arms that were measured at the same moments. A paired
difference cancels it exactly, and the comparison becomes valid again without
waiting for a quiet machine.

This is why the protocol carries `reference_seeds`. An evaluator that reports it
gets the paired interval automatically; one that does not falls back to the
unpaired comparison against a baseline measured possibly hours earlier, which is
the situation P31 is describing. Abort-on-drift stays as the fallback for
evaluators that cannot interleave.

## What was deliberately not changed

**One change per experiment.** Bundling destroys attribution. Every mechanism
here depends on being able to say which change caused which delta.

**A single mutable surface.** One file to edit keeps the diff reviewable and the
sandbox definable. This is the constraint that makes everything else auditable.

**The git ratchet itself.** Accepted changes still become commits. The history
is still the state. There is still no daemon.

**Tokeniser-independent metrics.** `val_bpb` is right. `ratchet-guard` is
metric-agnostic, but the contract asks for the same care: pick a number that
survives the changes you intend to make.

**The human writes the objective.** The contract is richer than `program.md`,
but it is still the human's file, and the agent may not edit it. If the agent
could edit the goal, every other guarantee would be theatre.

## The cost

Every gate rejects some true positives. The bet is that a rejected true
positive costs one trial, and an accepted false positive costs the rest of the
run — because it becomes the baseline that everything else must beat.

That bet is wrong when trials are expensive and the metric is trustworthy. In
that case, drop the adjudication gate and keep the statistics. The contract is
layered so that this is a configuration change, not a rewrite: set
`holdout_budget = 0` and the canary suite will halt on the first blind spot,
which is the honest response to "I am not going to check".
