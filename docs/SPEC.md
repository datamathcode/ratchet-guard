# Specification

Normative. `MUST`, `MUST NOT`, `SHOULD`, `MAY` are used in the RFC 2119 sense.

Version 0.1.0.

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
| `seed_replicates` | `3` | minimum seeds per measurement (floor of 3 is enforced) |
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
 "seeds": [0.9298, 0.9310, 0.9328],
 "guards": {"peak_mem_mb": 812.4},
 "wall_s": 300.1,
 "peak_mem_mb": 812.4,
 "loc_delta": 41,
 "meta": {}}
```

Rules:

- `value` is REQUIRED. It is the mean of `seeds` if `seeds` is present; an
  implementation MUST reject a `value` that disagrees with the mean of `seeds`
  beyond floating-point tolerance.
- `seeds` SHOULD have at least three entries. Fewer than three is a rejection.
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

### Gate 1 — provenance

If the candidate's `meta.contract_digest` is present and differs from the current
contract digest, return `REJECT_PROVENANCE`.

Rationale: results produced under a different objective are not comparable, and
silently pooling them is P8.

### Gate 2 — replicate floor

Let `n = len(candidate.seeds)`. If `n < max(3, seed_replicates)`, return
`REJECT_NOISE`.

Rationale: two seeds cannot estimate a spread; the range of two draws is the gap
between two arbitrary points (P11).

### Gate 3 — guards

For each guard, let `b` and `c` be the baseline and candidate values. Define

```
degradation = (c - b) / max(|b|, eps)      if the guard is minimised
degradation = (b - c) / max(|b|, eps)      if the guard is maximised
```

If `degradation > tolerance`, return `REJECT_GUARD`. If either value is missing
and `tolerance < 1`, return `REJECT_GUARD`.

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

The two arms MUST be resampled with **independent** random streams. Resampling
both with one seed pairs them index by index, cancels shared variance, and
produces an interval that is far too narrow.

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

## 7. Promotion

`promote(trial, holdout_measurement) -> bool`

Requirements, all of which MUST hold:

1. The trial's verdict is `accept` or `accept_debt`.
2. `holdout_reads_used < holdout_budget`. Otherwise `HALT_HOLDOUT`.
3. The adjudicator command depends on the candidate (contains `{candidate}`).
4. The holdout measurement has at least three seeds.
5. The holdout does not include zero gain versus the canonical baseline:
   `min(s*d_lo, s*d_hi) > 0`.
6. The gain covers the complexity price, if one is set.

On success: the canonical baseline becomes the holdout measurement, and
`unadjudicated` resets to zero.

On failure: the working baseline MUST be restored to the surrogate measurement
of the canonical commit, found by looking up that commit in the ledger. If the
commit is not in the ledger, the implementation MUST refuse to guess and MUST
print the `git reset` command instead.

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
