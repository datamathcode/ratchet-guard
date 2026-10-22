# Specification

Normative. `MUST`, `MUST NOT`, `SHOULD`, `MAY` are used in the RFC 2119 sense.

Version 0.2.0.

**Changes in 0.2.0.** Replicate floor raised 3 → 4; `min_delta` may no longer sit below the measured noise floor; a wall-clock overhead gate added; a calibration tier added (`control`, `drift`); paired comparison supported via `reference_seeds`; three new verdicts.

---

## 1. Purpose

`ratchet-guard` decides whether a proposed change to a measured system becomes
the new baseline. It is a decision procedure, not a search procedure. The
search is unchanged from the original ratchet: some agent proposes a candidate,
something measures it, and a git branch either advances or resets.

The only thing this specification constrains is **who is allowed to say yes**.

---

## 2. Roles

The system MUST enforce three roles, and they MUST NOT be filled by the same
trust domain where a gate depends on the separation:

| Role | May | Must not |
|---|---|---|
| **Proposer** (an agent) | write the editable surface, call `trial` | write the ledger, edit the contract, call `promote` |
| **Evaluator** (a command) | read the editable surface, emit one JSON object | be edited between runs without invalidating provenance |
| **Adjudicator** (a command) | read the sealed split, emit one JSON object | be reachable from the proposer |

An implementation MUST refuse to run when the proposer can write the ledger or
the contract.

---

## 3. The contract

The objective is a TOML file. It MUST contain `goal`. A contract without a
prose goal is rejected: a metric without a goal is the bug this system exists to
prevent.

### 3.1 Required top-level keys

| Key | Type | Meaning |
|---|---|---|
| `goal` | string | prose statement of what is actually wanted |
| `[metric]` | table | the surrogate: `name`, `direction` (`min`\|`max`), optional `unit` |

### 3.2 Optional top-level keys

| Key | Default | Meaning |
|---|---|---|
| `min_delta` | `0.0` | smallest gain worth believing, in metric units |
| `complexity_price` | `0.0` | metric units charged per line added |
| `seed_replicates` | `4` | minimum seeds per measurement (floor of 4 is enforced) |
| `wall_overhead_cap` | `0.10` | max share of wall clock not covered by `meta.work_s`; `none` disables |
| `max_trials` | `400` | hard trial budget |
| `max_unadjudicated` | `5` | accepted-but-unadjudicated changes allowed |
| `exploration_fraction` | `0.15` | share of trials usable as debt |
| `debt_repay_window` | `12` | trials a debt entry survives before rollback |
| `canary_every` | `25` | trials between canary re-runs |
| `variance_inflation_tolerance` | `2.0` | max sd ratio versus the baseline |
| `families` | `["default"]` | declared idea families, for the bandit |

### 3.3 `[[guards]]`

Zero or more. Each is `name`, `direction`, `tolerance` (a relative degradation
above which the trial is rejected). A guard that is declared but not reported by
the evaluator is a rejection, not a skip — an unreported cost is an unbounded
cost.

### 3.4 `[adjudication]`

| Key | Meaning |
|---|---|
| `holdout_cmd` | command that scores a candidate on the sealed split. MUST contain `{candidate}`, replaced with the trial's commit. |
| `holdout_budget` | total reads for the whole run. Never replenished. |

If `holdout_cmd` is absent, the `holdout` adjudicator is **disabled**, and any
canary declaring `covered_by = "holdout"` becomes an uncovered blind spot, which
is a hard stop.

### 3.5 `[[canaries]]`

Each is `name`, `command`, `expectation` (`worse`\|`better`), `separation`,
`covered_by`, optional `failure_mode`.

A canary is a candidate whose true quality is known. `expectation = "worse"`
means the true quality is worse than the baseline, so a correct metric MUST
place it at least `separation` below the baseline.

---

## 4. The evaluator protocol

The evaluator MUST print exactly one JSON object as its last line on stdout:

```json
{"value": 0.9312,
 "seeds": [0.9298, 0.9310, 0.9328, 0.9301],
 "guards": {"peak_mem_mb": 812.4},
 "wall_s": 300.1,
 "peak_mem_mb": 812.4,
 "loc_delta": 41,
 "meta": {"work_s": 296.2,
          "reference_seeds": [0.9201, 0.9198, 0.9204, 0.9200]}}
```

Rules:

- `value` is REQUIRED. It is the mean of `seeds` if `seeds` is present; an
  implementation MUST reject a `value` that disagrees with the mean of `seeds`
  beyond floating-point tolerance.
- `seeds` MUST have at least four entries. Fewer than four is a rejection: with
  three per arm the smallest attainable two-sided Mann–Whitney p is 2/C(6,3) =
  0.10, so no three-seed comparison can clear α=0.05 whatever the effect size
  (P11).
- `meta.work_s` is OPTIONAL and SHOULD be reported. It is the part of `wall_s`
  the metric actually scores — training time, excluding startup and
  compilation. When present and `wall_overhead_cap` is set, the unscored share
  is a rejection condition (P28).
- `meta.reference_seeds` is OPTIONAL. When present, of the same length as
  `seeds`, it is the reference configuration measured **interleaved in the same
  session**. The comparison against the baseline then becomes paired by index,
  which cancels machine drift (P31). An implementation SHOULD prefer the paired
  interval whenever it is available.
- A non-zero exit, a timeout, or unparseable output is a **crash**. A crash MUST
  NOT produce a numeric value. It MUST produce a distinct status and a stored
  traceback tail.
- `loc_delta` is signed: positive means lines added, negative means removed.

---

## 5. Decision procedure

`decide(contract, baseline, candidate, state) -> Decision`

Gates MUST be evaluated in this order, and the first failing gate determines the
verdict. One failure, one cause: a verdict MUST be attributable to exactly one
gate so the ledger can be read as a record of *why*.

### Gate 0 — halt

If `state.halted`, return `HALT_METRIC` with the stored reason. A halted run
performs no measurements.

### Gate 0b — drift

If `state.drift_detected`, return `HALT_DRIFT`. A reference configuration whose
re-measured mean has moved beyond the drift threshold means the machine changed
under the run, and no comparison in this session is trustworthy (P31). The
remedy is `control`, not more trials.

### Gate 0c — contract viability

If `state.noise_var` is known, let `σ = sqrt(state.noise_var)`. If
`min_delta < σ`, return `HALT_CONFIG`.

Rationale: a threshold below one measured standard deviation cannot separate
signal from noise, so every acceptance the contract could produce is a noise
record (P10, P27). This is a configuration fault, not a trial outcome, and it is
worth stopping the run to say so once rather than rejecting a hundred trials
politely.

### Gate 1 — provenance

If the candidate's `meta.contract_digest` is present and differs from the current
contract digest, return `REJECT_PROVENANCE`.

Rationale: results produced under a different objective are not comparable, and
silently pooling them is P8.

### Gate 2 — replicate floor

Let `n = len(candidate.seeds)`. If `n < max(4, seed_replicates)`, return
`REJECT_NOISE`.

Rationale: two seeds cannot estimate a spread; the range of two draws is the gap
between two arbitrary points (P11). Three is not enough either — the smallest
attainable two-sided p at n=3 per arm is 0.10, so the comparison cannot be
reported at α=0.05 regardless of effect size.

### Gate 3 — guards

For each guard, let `b` and `c` be the baseline and candidate values. Define

```
degradation = (c - b) / max(|b|, eps)      if the guard is minimised
degradation = (b - c) / max(|b|, eps)      if the guard is maximised
```

If `degradation > tolerance`, return `REJECT_GUARD`. If either value is missing
and `tolerance < 1`, return `REJECT_GUARD`.

### Gate 3b — overhead

If `wall_overhead_cap` is set and `meta.work_s` is reported, let

```
overhead = (wall_s - work_s) / max(wall_s, eps)
```

If `overhead > wall_overhead_cap`, return `REJECT_OVERHEAD`.

Rationale: the budget is defined as scored work time, and everything outside it
is compute the metric never sees. Without this check an agent can move the work
into import time and it is free (P28). If `meta.work_s` is absent the check is
skipped, not failed — an unreported cost here is a missing signal, not evidence
of abuse.

### Gate 4 — variance

Let `s_b`, `s_c` be the sample standard deviations of the arms. If `s_b > eps`
and `s_c / s_b > variance_inflation_tolerance`, return `REJECT_VARIANCE`.

Rationale: a mechanism can raise the mean while making every individual run a
noisier sample (P14). When `s_b == 0` the check is skipped, because a zero
baseline spread carries no information about the acceptable ratio.

### Gate 5 — effect

Let `s = +1` if the metric is minimised, `-1` if maximised. Let

```
raw_gain = s * (baseline.value - candidate.value)
cost     = complexity_price * candidate.loc_delta
gain     = raw_gain - cost
```

Let `[d_lo, d_hi]` be the bootstrap interval of `mean(candidate) - mean(baseline)`,
and map it into gain space:

```
gain_ci = [-s*d_hi - cost, -s*d_lo - cost]     (then sorted)
```

If `gain_ci_lo > max(min_delta, 0)`, the candidate passes.

**Choice of comparison arm.** If the candidate reports `meta.reference_seeds`
with the same length as `seeds`, the interval MUST be computed as a paired
difference against those seeds, and the gain computed against their mean, not
against `state.baseline`. The reference was measured in the same session, so a
shared drift offset cancels and the interval is immune to P31. Otherwise the
interval MUST be computed as an unpaired difference against `state.baseline`.

The two arms MUST be resampled with **independent** random streams in the
unpaired case. Resampling both with one seed pairs them index by index, cancels
shared variance, and produces an interval that is far too narrow. In the paired
case the pairing is real and the resampling MUST be over paired differences.

### Gate 6 — queue discipline

If an adjudicator is configured and `state.unadjudicated >= max_unadjudicated`,
return `REJECT_BUDGET`. A run may not stack unlimited unverified changes; the
working branch may not drift arbitrarily far from the canonical one.

### Gate 7 — speculative debt

If the candidate did not pass gate 5, it MAY still be accepted as debt when all
of:

- `meta.predicted_direction == "worse_first"`,
- `len(open_debt) < exploration_fraction * max_trials`,
- the family is under-explored relative to an even split,
- the regression is not catastrophic: `gain_ci_lo > -min_delta - 10 * s_b`.

Returns `ACCEPT_DEBT`.

### Otherwise

`REJECT_NOISE`, with the interval quoted in the reason.

---

## 6. State transitions

| Verdict | Working baseline | `unadjudicated` | Debt |
|---|---|---|---|
| `ACCEPT` | ← candidate | +1 if an adjudicator exists | — |
| `ACCEPT_DEBT` | ← candidate | +1 if an adjudicator exists | +1 entry, deadline `t + debt_repay_window` |
| `REJECT_*` | unchanged | unchanged | unchanged |
| `CRASH` | unchanged | unchanged | unchanged |

Debt is settled after each trial: a positive gain repays every open entry; an
entry past its deadline expires and MUST trigger a rollback to the canonical
commit.

---

## 6b. Calibration

Calibration is what makes gates 0c and 5 mean anything. It happens before the
first trial and repeats during the run.

`control --cmd <incumbent, unchanged> --runs n` measures the reference
configuration with nothing changed. It MUST:

1. run the evaluator `n` times,
2. pool the seeds, compute `sd`, and store `noise_var` and `noise_df`,
3. store the mean as `reference`, with its trial index,
4. report `min_delta` as a multiple of the measured sd, and report the relative
   uncertainty of that sd.

A no-op can never improve, which is why nobody runs one, and why its absence is
P30. `n` SHOULD be at least 5; `n=3` yields ±50% on σ and is only enough to
detect an order-of-magnitude mistake.

After calibration, every trial MUST pool its **within-arm** variance into
`noise_var`. Pooling must never cross arms: variance between configurations
contains the effect being measured, and the floor would appear to shrink as the
run succeeded.

`drift --cmd <reference>` re-measures the reference and compares its mean to the
stored one. If the difference exceeds the declared threshold in standard errors,
the implementation MUST set `drift_detected`, which halts the run at gate 0b. A
successful `control` clears a drift halt, and only a drift halt.

---

## 7. Promotion

`promote(trial, holdout_measurement) -> bool`

Requirements, all of which MUST hold:

1. The trial's verdict is `accept` or `accept_debt`.
2. `holdout_reads_used < holdout_budget`. Otherwise `HALT_HOLDOUT`.
3. The adjudicator command depends on the candidate (contains `{candidate}`).
4. The holdout measurement has at least four seeds.
5. The holdout does not include zero gain versus the canonical baseline:
   `min(s*d_lo, s*d_hi) > 0`.
6. The gain covers the complexity price, if one is set.

On success: the canonical baseline becomes the holdout measurement, and
`unadjudicated` resets to zero.

On failure: the working baseline MUST be restored to the **surrogate** reading of
the canonical commit — never the adjudicated one, because the two are different
instruments and subtracting them compares numbers that do not share a scale. The
implementation MUST keep that reading (`canonical_dev`) alongside the
adjudicated `canonical`. It MAY fall back to looking the commit up in the ledger;
if neither is available it MUST refuse to guess and MUST print the `git reset`
command instead.

A refusal is not an error. It is the system working.

---

## 8. Invariants

These MUST hold at all times, and an implementation SHOULD assert them.

1. **A crash never yields a number.** `seeds` empty ⇒ no `value`.
2. **A delta always carries an interval.** No decision is recorded without one.
3. **Every result carries its contract digest.** No comparison crosses a digest
   change without an explicit refusal.
4. **The ledger is append-only.** No code path rewrites an existing row.
5. **The canary result precedes the first decision.** A run that has not been
   calibrated MUST NOT accept anything.
6. **A blind spot is never silent.** Every uncovered blind spot halts the run.
7. **Surrogate and adjudicated numbers are never subtracted.** They are
   different instruments.
8. **A delta is only quotable above the measured noise floor.** If `min_delta` is
   known to be below it, the run stops rather than producing verdicts.

---

## 9. Persistence

```
<state>/
  state.json    run state: baselines, counters, debt, promotions, halts
  results.tsv   append-only, human-readable, one row per trial
  audit.jsonl   append-only, hash-chained, one object per trial
```

`audit.jsonl` rows carry `prev` and `hash`, where
`hash = sha256(prev || json(body))[:16]`. `verify` recomputes the chain from the
first row. Any rewritten row breaks the chain at its index and every index
after it.

The TSV MUST NOT be the primary record. It exists for humans; the chain exists
for audit.

---

## 10. Non-goals

- **Making the search non-greedy.** The debt mechanism buys bounded exploration,
  not search. A ratchet will never find a paradigm shift.
- **Choosing the metric.** The system can tell you when a metric is blind to a
  declared failure mode. It cannot tell you what to measure.
- **Replacing human judgment about the question.** It constrains how the answer
  is believed, not which question is asked.
- **Distributed execution.** One run, one workspace, one writer.
