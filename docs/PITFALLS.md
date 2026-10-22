# Implementation pitfalls

Each entry: what it looks like, why it happens, and the cheapest detector.
Numbering is stable — `docs/ADAPTATIONS.md` refers to it.

Severity: **A** = silently corrupts the result, nothing errors.
**B** = wastes trials or money, visible eventually.
**C** = annoying, recoverable.

---

## Layer 1 — The objective

### P1. Metric-goal divergence — **A**

The loop improves the surrogate while the goal stalls or degrades. Nothing
errors. Every row in the log is a real measurement.

*Why:* the accept rule compares `M` to itself. `G` is not in the expression.
*Detector:* measure `G` on a split the search cannot reach, at least at
promotion. If you cannot afford that, you do not have a goal — you have a
metric, and you should stop claiming otherwise.

### P2. Entrenchment of the exploit — **A**

The specific failure that distinguishes this loop from ordinary overfitting. A
change that games the metric is accepted once; from then on it is the
incumbent, and every future honest candidate loses to it on the same broken
instrument.

*Why:* monotonicity. The ratchet has no undo, only a forward direction.
*Detector:* re-run the *previous* best candidate against the current best
periodically. If the ranking flips, the metric has drifted.

### P3. Saturation freeze — **A**

`M` reaches its ceiling. Honest work continues to exist; the loop cannot see
it, and reports "no improvement found" for the rest of the run.

*Why:* the loop can only accept an increase. A plateau is indistinguishable
from a solved problem.
*Detector:* track the trial-without-accept streak. A long streak next to a
metric at its known maximum is saturation, not exhaustion.

### P4. Evaluator surface reachability — **A**

The agent can reach the code that computes the number: `git checkout
prepare.py`, a monkeypatch, an env var, a cached eval artifact, a write to the
results file.

*Why:* "read-only" is an instruction to a language model, not a permission bit.
*Detector:* hash the evaluation surface before every run and compare. Run the
evaluator out-of-process with a timeout. Log the hash with the result.

### P5. Eval-set overfitting by repeated selection — **A**

Hundreds of trials against one fixed validation split. By trial 200 the split
is a training set. Accuracy on it stops meaning anything, and the selection
pressure is *maximal* exactly there, because that is the number being optimised.

*Why:* statistical, not code. Each trial is an independent look at the same
sample; the maximum over 700 looks is heavily biased upward.
*Detector:* compare the winning delta on the selection split against the same
delta on a sealed split. A gap that grows with trial count is this pitfall.

### P6. Scalar collapse of a multi-objective goal — **B**

Quality, latency, memory, complexity and fragility are all real, and get
compressed into one number. The rest survive only as prose in `program.md`
("VRAM is a soft constraint"), which the loop may ignore whenever the metric
rewards ignoring it.

*Why:* the accept rule has arity one.
*Detector:* every time the metric improves, check every cost. If a cost moved
and nothing rejected the change, that cost is not in the objective.

### P7. Short-horizon proxy — **A**

The fixed budget rewards changes that pay off early. A change that needs three
times the budget to show its value is scored as a loss and reverted.

*Why:* the budget is part of the metric definition, and nobody wrote that down.
*Detector:* take the top three kept changes and re-measure each at 3–5x the
budget. If any of them reverses, the budget is selecting, not measuring.

### P8. Silent goal drift — **C**

The human edits `program.md` mid-run. Nothing records that the objective
changed, and results from before and after are pooled.

*Why:* `program.md` is a file, not a contract.
*Detector:* hash the human-owned objective file and stamp every result with it.
Refuse to compare across a hash change.

---

## Layer 2 — Statistics

### P9. One run is one sample — **A**

`n=1`. The delta between two single runs contains run-to-run noise plus the
effect. Quoting it as the effect is the most common error in the whole genre.

*Why:* the loop is built for throughput; replicates feel like waste.
*Detector:* rerun an identical config. If the two differ at all, every previous
delta needs an interval.

### P10. Unknown noise floor, no minimum delta — **A**

The ratchet accepts a `0.0003` improvement on a metric whose run-to-run spread
is `0.03`. The commit stays, the history is fiction, and the next change is
measured against a lucky sample.

*Why:* no threshold exists anywhere in the design.
*Detector:* measure the spread of one identical config, set `min_delta` to
roughly twice it, and refuse to accept anything below. Re-measure the floor at
the current operating point — early-training noise is much larger than
late-training noise.

### P11. Two seeds cannot estimate a noise floor — **A**

A two-seed "floor" is the gap between two arbitrary draws. It will be too small
about half the time, and a too-small floor makes noise look like signal.

*Why:* the range of two samples carries almost no information about spread.
*Detector:* require three seeds minimum before quoting any interval. With n=3
the exact non-parametric floor is p=0.05; report mean, sd and a rank test, not
a ratio.

### P12. Winner's curse across hundreds of trials — **A**

The kept set is the maximum of many noisy draws. Re-measuring the winners on
fresh data gives smaller effects. The cumulative "gain" of a run is partly the
maximum of a noise distribution.

*Why:* selection on the same data used to report.
*Detector:* re-measure the canonical baseline's final value on a fresh split.
The difference is the winner's curse, made visible.

### P13. Fixed wall-clock makes the step count a confound — **A**

Two arms run for five minutes; the faster one takes more steps. The comparison
is then "faster schedule with more steps" vs "slower schedule with fewer". That
confound looks exactly like a result.

*Why:* the budget is time, the mechanism is steps, and nobody equated them.
*Detector:* log steps. Assert every arm recorded the same count before printing
any delta.

### P14. Variance inflation — **B**

The mean improves and the spread widens. Every individual deployment is now a
noisier sample, and the "win" is partly a lottery ticket.

*Why:* the accept rule compares means only.
*Detector:* compare the sd of candidate and baseline runs. Reject when the ratio
exceeds a declared tolerance.

### P15. Crash recorded as a zero — **A**

A failed run writes `0.0` into the metric column, or the parse returns `None`
and becomes `0`. A crash now looks like a catastrophic regression, or a crash in
a `min` metric looks like a perfect score.

*Why:* the sentinel is convenient.
*Detector:* a crash must produce no measurement at all, a distinct status, and
a stored traceback tail. Never a number.

---

## Layer 3 — The search

### P16. Greedy local optimum — **A**

The loop cannot take a step back. A change that hurts now and enables a larger
gain later is reverted, every time. The observed symptom is that the agent
proposes minor variations of its last success, forever.

*Why:* monotone accept. This is the known limitation, and it is structural — but
it is not *only* structural: a bounded exploration budget recovers most of it
without giving up the safety property.
*Detector:* count distinct idea families in the last 20 trials. One family is
mode collapse.

### P17. No memory of why — **B**

Failures are recorded as free text ("switch to GeLU — discard"). The *reason*
is lost, so the loop re-proposes the same dead ends every 50 trials, and cannot
reason about the mechanism behind a win.

*Why:* the log is a scoreboard, not a knowledge base.
*Detector:* count duplicate hypotheses. If the same idea is retried more than
twice, the failure record is not being read.

### P18. Additive bias and interaction blindness — **A**

Each win is validated alone, at the moment it was found. Nobody re-tests win #3
after wins #4–#12 land. The stack accumulates components whose benefit was
measured under conditions that no longer hold, and the total is less than the
sum.

*Why:* one change per experiment is right for attribution and wrong for the
final stack.
*Detector:* periodically ablate one accepted change at a time (leave-one-out) at
the adjudicated tier. Components that no longer earn their place must come out.

### P19. The complexity ratchet is irreversible — **A**

Twenty small hacks, each worth a `0.002`, each individually justified, add up to
a codebase nobody can reason about. There is no mechanism to remove one, because
removal is a regression and regressions are reverted.

*Why:* the accept rule has no term for the cost of the change itself.
*Detector:* price complexity in metric units and require the gain to exceed the
price. Give deletions a negative price so simplification is a win.

### P20. Mode collapse onto one family — **B**

The agent keeps proposing in the neighbourhood of its last win, because that is
what the recent context rewards. Unexplored regions stay unexplored.

*Why:* no exploration policy. "Think harder" is an instruction, not a policy.
*Detector:* distribution of trials per declared family. A bandit score (UCB)
over families makes the imbalance visible and correctable.

### P21. The agent grades its own homework — **A**

The same process proposes the change, runs the evaluator, parses the number,
and writes the row. There is no independent record.

*Why:* convenience — one agent, one loop.
*Detector:* the ledger must be written by the harness, append-only, hash-chained.
The proposer may not write the record.

---

## Layer 4 — Operations

### P22. `git reset` does not revert everything — **B**

Tracked files revert. Caches, generated data, installed packages, environment
mutations and background processes do not. The "reverted" state is not the
state that produced the baseline, and the next comparison is contaminated.

*Why:* git tracks source, not environment.
*Detector:* record the eval-harness hash and the environment fingerprint per
trial. A mismatch is a provenance rejection, not a warning.

### P23. The only record is the least protected file — **A**

`results.tsv` is deliberately untracked. It is also the entire memory of the
run. A crash, a reset, or an over-eager `git clean` loses it.

*Why:* the file is treated as scratch output rather than as data.
*Detector:* append-only, hash-chained, outside the working tree, and written by
the harness.

### P24. Unbounded autonomy with no budget and no kill switch — **B**

`NEVER STOP` is the instruction. There is no trial cap, no spend cap, no alarm
on a rising crash rate, and no condition under which the loop should stop and
ask.

*Why:* the design assumes a human is asleep and wants maximum throughput.
*Detector:* a trial budget, a crash-streak limit, a holdout budget, and a
no-accept-in-N alarm that escalates instead of "thinking harder".

### P25. Concurrent writers on one branch — **B**

Two sessions, or a human and a session, advance the same branch. A push is
rejected or a commit is silently based on stale work. If it is force-pushed,
someone's real bug fix disappears.

*Why:* git is the state, and the state has no lock.
*Detector:* `git fetch` and read `rev-list --left-right --count` before every
push. Diverged means integrate, never force.

### P26. A five-minute win is assumed to transfer — **A**

The kept change is validated at one scale and one budget. It may not hold at
another. The ratchet does not care, because the only number it has ever seen is
the small one.

*Why:* the ladder has one rung.
*Detector:* promote through tiers: screen, replicate, adjudicate, transfer. Only
the last one may update the canonical baseline.

---

## The pattern behind all of them

Every pitfall above is an instance of one of three things:

1. **A number with no interval** (P9–P14) — the loop cannot distinguish signal
   from noise, and it has no reason to want to.
2. **A proxy with no adjudicator** (P1–P8) — the loop cannot distinguish the
   goal from the metric, and it is optimised to exploit the difference.
3. **A decision with no record** (P21–P23) — the loop cannot be audited after
   the fact, so the failure is discovered only by re-measuring, which nobody
   does.

Fix one and the others remain. That is why the adaptations are layered rather
than a single threshold.
