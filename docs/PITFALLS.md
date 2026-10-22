# Implementation pitfalls

Each entry: what it looks like, why it happens, and the cheapest response.
Numbering is stable — `docs/ADAPTATIONS.md` refers to it. Numbers are identifiers, not reading order.

Severity: **A** = silently corrupts the result, nothing errors. **B** = wastes trials or money, visible eventually. **C** = annoying, recoverable.

Response type: **[D]** = detector. Computable from artifacts you already have; retrofit onto a running loop tonight. **[M]** = mitigation. Requires changing the harness, the ledger, or the permissions. Naming it is not fixing it.

**Provenance.** v1 was written from a reading of `karpathy/autoresearch`. v2 corrects three of v1's arithmetic claims, relabels every entry [D] or [M], and adds six. Blocks marked *Reviewer's note* are amendments made during implementation and are not part of the original text.

---

## Read this first

### P27. The ratchet converts noise into apparent progress — **A** [D]

This is the arithmetic the rest of the document assumes and never does. Assume **zero** true improvement and iid noise σ. The accept rule keeps any candidate that beats the incumbent, so the kept set is the running minimum of N draws.

- Number of new records in N iid draws ≈ H_N. At N=100, **≈5.2 accepted commits that are pure noise.**
- Terminal position ≈ μ − 2.4σ. The branch ends 2.4σ "better" than it started, having done nothing.
- The log shows a smooth monotone curve with five individually plausible wins. Nothing errors.

The second-order effect is worse. With the incumbent sitting at μ − 2.4σ, a *genuine* 1σ improvement is accepted with probability Φ(1 − 2.4) = **8%**. The loop has silently acquired a detection threshold of ~2.4σ that nobody chose, and it only rises. The ratchet manufactures progress and destroys sensitivity in the same motion.

*Why:* monotone accept on a noisy instrument. P2 is the qualitative version; this is the quantity.
*Detector:* the reference-config interleave (P31) plus sealed-split re-measurement (P5, P12). A threshold alone does not fix this — see the appendix.

### P28. The unmeasured half of the wall clock — **A** [D]

`program.md` defines the budget as wall-clock training time, **excluding startup and compilation**, with a 10-minute kill. That leaves up to ~5 minutes per experiment of compute no rule scores. An agent can move real work into import time — data preprocessing, tokenizer remapping, a warmup phase, a short pretrain — and it is free. `total_seconds` is printed (325.9 vs 300.1) so it is visible, but nothing rejects on it.

Verified in `train.py`: `training_seconds` and `total_seconds` are both printed, and `startup_time` is computed and never scored. The gap is real and it is on screen.

*Why:* the budget is part of the metric definition and only half of it is written down. Same family as P7.
*Detector:* assert `total_seconds − training_seconds` under a declared cap. One line. Its absence from a list that includes P4 is the largest gap in v1.

---

## Layer 1 — The objective

### P1. Metric-goal divergence — **A** [D]

The loop improves the surrogate while the goal stalls or degrades. Nothing errors. Every row in the log is a real measurement.

*Why:* the accept rule compares `M` to itself. `G` is not in the expression.
*Detector:* measure `G` on a split the search cannot reach, at least at promotion. If you cannot afford that, you do not have a goal — you have a metric, and you should stop claiming otherwise.

**Scope note:** for the shipped repo this is a category error. `val_bpb` is not a surrogate for a hidden goal; it *is* the declared goal. P1 goes live only when you port the method to something you cannot measure directly — which is the interesting case, but not this one. Do not let it license the belief that the shipped loop has a goal problem it does not have.

*Reviewer's note.* The scope note is right that `val_bpb` is the declared objective, and wrong to close the question there. The repo's own headline number is *time to GPT-2*, for which `val_bpb` on one shard in five minutes is a proxy. So the residual exists; it is just small enough that P27 dominates it. The correct reading: for the shipped repo the binding failure is **noise**; for any port it is **divergence**. Both entries stay live, in that order.

### P2. Entrenchment of the exploit — **A** [D]

A change that games the metric is accepted once; from then on it is the incumbent, and every future honest candidate loses to it on the same broken instrument.

*Why:* monotonicity. The ratchet has no undo, only a forward direction.
*Detector:* re-run the *previous* best candidate against the current best periodically. If the ranking flips, the metric has drifted. P27 is the noise-driven version of this; the exploit-driven version is worse because it is not random and will not average out.

### P3. Saturation freeze — **A** [D]

`M` reaches its ceiling. Honest work continues to exist; the loop cannot see it, and reports "no improvement found" for the rest of the run.

*Why:* the loop can only accept an increase. A plateau is indistinguishable from a solved problem.
*Detector:* track the trial-without-accept streak. A long streak next to a metric at its known maximum is saturation, not exhaustion.

### P4. Evaluator surface reachability — **A** [M]

The agent can reach the code that computes the number: `git checkout prepare.py`, a monkeypatch, an env var, a cached eval artifact, a write to the results file.

*Why:* "read-only" is an instruction to a language model, not a permission bit. The README tells you to disable all permissions.
*Mitigation:* hash the evaluation surface before every run and compare. Run the evaluator out-of-process with a timeout. Log the hash with the result. This is a harness change, not a check.

### P5. Eval-set overfitting by repeated selection — **A** [D]

Hundreds of trials against one fixed validation split. By trial 200 the split is a training set. Selection pressure is *maximal* exactly there, because that is the number being optimised.

*Why:* statistical, not code. Each trial is an independent look at the same sample; the maximum over 700 looks is heavily biased upward.
*Detector:* compare the winning delta on the selection split against the same delta on a sealed split. A gap that grows with trial count is this pitfall.

### P6. Scalar collapse of a multi-objective goal — **B** [D]

Quality, latency, memory, complexity and fragility are all real, and get compressed into one number. The rest survive only as prose ("VRAM is a soft constraint", the "simplicity criterion"), which the loop may ignore whenever the metric rewards ignoring it.

*Why:* the accept rule has arity one. This is the one place where prose is doing load-bearing work, and prose is not a constraint.
*Detector:* every time the metric improves, check every cost. If a cost moved and nothing rejected the change, that cost is not in the objective.

### P7. Short-horizon proxy — **A** [D]

The fixed budget rewards changes that pay off early. A change that needs three times the budget to show its value is scored as a loss and reverted.

*Why:* the budget is part of the metric definition, and nobody wrote that down.
*Detector:* take the top three kept changes and re-measure each at 3–5× the budget. If any of them reverses, the budget is selecting, not measuring.

### P8. Silent goal drift — **C** [M]

The human edits `program.md` mid-run. Nothing records that the objective changed, and results from before and after are pooled.

*Why:* `program.md` is a file, not a contract.
*Mitigation:* hash the human-owned objective file and stamp every result with it. Refuse to compare across a hash change.

**Scope note:** the human editing `program.md` is the benign version. The dangerous version is the *agent* editing it — it has write access, and the file is the only thing constraining it. Hash it and make a mismatch a hard stop, not a warning.

### P32. Read access to the evaluator is an information leak — **A** [M]

P4 covers the agent *modifying* the evaluator. But `program.md` step 3 instructs it to *read* `prepare.py` in full: the eval code, the val split construction, the tokenizer, the exact `EVAL_TOKENS`. Specialising to a distribution you can read is not a permission violation, and no hash detects it.

*Why:* the loop is optimised to exploit any information it is given, and the instructions hand it the instrument.
*Mitigation:* give the agent a documented interface to the metric, not the source. If it must read the file, treat every result as potentially split-aware and rely on P5's sealed split as the only adjudicator.

---

## Layer 2 — Statistics

### P9. One run is one sample — **A** [D]

`n=1`. The delta between two single runs contains run-to-run noise plus the effect. Quoting it as the effect is the most common error in the genre.

*Why:* the loop is built for throughput; replicates feel like waste.
*Detector:* rerun an identical config. If the two differ at all, every previous delta needs an interval.

### P10. Unknown noise floor, no minimum delta — **A** [D]

The ratchet accepts a `0.0003` improvement on a metric whose run-to-run spread is `0.03`. The commit stays, the history is fiction, and the next change is measured against a lucky sample.

*Why:* no threshold exists anywhere in the design.
*Detector:* measure the spread of one identical config, set `min_delta`, and refuse to accept below it. Re-measure the floor at the current operating point — early-training noise is much larger than late-training noise.

**Corrected guidance.** v1 said "roughly twice the spread," which is ambiguous and, read as 2σ, is weaker than it sounds. For two independent runs the SE of the difference is σ√2, so:

| `min_delta` | one-sided confidence | noise accepts / 100 | terminal drift |
|---|---|---|---|
| 1.4σ | 84% | many | ~1.5σ |
| 2.0σ | 92% | ~2 | ~2.4σ |
| 2.3σ | 95% | ~1 | ~2.6σ |
| 2.8σ | 97.5% | ~0.3 | ~3.1σ |

Two framings, two answers, and v1 never said which it was using. **For a single comparison**, 2.3σ is the 95% choice. **For the ratchet**, use 2.8σ: that is a deliberate choice of history integrity over terminal bias, not a significance level. See the appendix for what it costs.

### P11. Two seeds cannot estimate a noise floor — **A** [D]

A two-seed "floor" is the gap between two arbitrary draws. It will be too small about half the time, and a too-small floor makes noise look like signal.

*Why:* the range of two samples carries almost no information about spread.
*Detector:* require **four** seeds minimum before quoting any interval.

**Corrected numbers.** v1 claimed "with n=3 the exact non-parametric floor is p=0.05." It is not. For a two-sided Mann–Whitney test with n per arm, the smallest attainable p is 2/C(2n, n):

| n per arm | min two-sided p | usable at α=0.05 |
|---|---|---|
| 2 | 0.333 | no |
| 3 | **0.100** | no |
| 4 | **0.029** | yes |
| 5 | 0.008 | yes |

A sign test is worse — it bottoms out at 0.25 for n=3 and needs n=6 to reach 0.03. So the advice in v1 (n=2 is useless) was right and the number attached to it was wrong by a factor of two. Report mean, sd, and a rank test at n≥4; do not report a ratio.

### P12. Winner's curse across hundreds of trials — **A** [D]

The kept set is the maximum of many noisy draws. Re-measuring the winners on fresh data gives smaller effects. The cumulative "gain" of a run is partly the maximum of a noise distribution.

*Why:* selection on the same data used to report.
*Detector:* re-measure the canonical baseline's final value on a fresh split. The difference is the winner's curse, made visible. P27 is the same phenomenon stated as a rate rather than a bias.

### P13. Fixed wall-clock makes the step count a confound — **B** [D]

Two arms run for five minutes; the faster one takes more steps. The comparison is then "faster schedule with more steps" vs "slower schedule with fewer." That confound looks exactly like a result.

*Why:* the budget is time, the mechanism is steps, and nobody equated them.
*Detector:* log steps. Assert every arm recorded the same count before printing any delta.

**Scope note.** v1 called this a search bug. It is not. The repo's stated goal is "the most optimal model for your platform in that time budget," so trading steps for quality-per-step *is* the objective, and the loop is correct to accept it. The confound is real for **attribution** — "this architecture is better" is not supported — and for **transfer** (P26). It is a reporting bug, and it should be fixed in the write-up, not in the accept rule.

### P14. Variance inflation — **B** [D]

The mean improves and the spread widens. Every individual deployment is now a noisier sample, and the "win" is partly a lottery ticket.

*Why:* the accept rule compares means only.
*Detector:* compare the sd of candidate and baseline runs. Reject when the ratio exceeds a declared tolerance.

**Caveat:** an sd ratio is an F-test, and an F-test assumes both samples are iid normal. P31 says they are not. Treat a ratio above ~1.5 as a prompt to look, not as a verdict.

### P15. Crash recorded as a zero — **A** [D]

A failed run writes `0.0` into the metric column, or the parse returns `None` and becomes `0`. A crash now looks like a catastrophic regression, or a crash in a `min` metric looks like a perfect score.

*Why:* the sentinel is convenient — and in the shipped repo it is **mandated**. `program.md` instructs `0.000000` and `0.0` for crashes.
*Detector:* a crash must produce no measurement at all, a distinct status, and a stored traceback tail. Never a number.

**Understated in v1.** The `status` column partly rescues this, but only if every consumer reads it. Any naive `sort -k2 results.tsv` ranks crashes as the best experiments ever run on a min metric. The sentinel is not a convenience the agent invented; it is in the instructions.

---

## Layer 3 — The search

### P16. Greedy local optimum and mode collapse — **A** [D]

The loop cannot take a step back. A change that hurts now and enables a larger gain later is reverted, every time. The observed symptom is that the agent proposes minor variations of its last success, forever.

*Why:* monotone accept. This is the known limitation and it is structural — but it is not *only* structural: a bounded exploration budget recovers most of it without giving up the safety property. The two symptoms are one cause: proposals cluster near the last win because that is what recent context rewards, and there is no exploration policy. "Think harder" is an instruction, not a policy.
*Detector:* count distinct idea families in the last 20 trials. One family is mode collapse. A bandit score (UCB) over declared families makes the imbalance visible and correctable.

*(P20 merged here. Numbering retained as a stub — see below.)*

### P17. No memory of why — **B** [D]

Failures are recorded as free text ("switch to GeLU — discard"). The *reason* is lost, so the loop re-proposes the same dead ends every 50 trials, and cannot reason about the mechanism behind a win.

*Why:* the log is a scoreboard, not a knowledge base.
*Detector:* count duplicate hypotheses. If the same idea is retried more than twice, the failure record is not being read.

### P18. Additive bias and interaction blindness — **A** [D]

Each win is validated alone, at the moment it was found. Nobody re-tests win #3 after wins #4–#12 land. The stack accumulates components whose benefit was measured under conditions that no longer hold, and the total is less than the sum.

*Why:* one change per experiment is right for attribution and wrong for the final stack.
*Detector:* periodically ablate one accepted change at a time (leave-one-out) at the adjudicated tier. Components that no longer earn their place must come out.

### P19. The complexity ratchet is irreversible — **A** [D]

Twenty small hacks, each worth a `0.002`, each individually justified, add up to a codebase nobody can reason about. There is no mechanism to remove one, because removal is a regression and regressions are reverted.

*Why:* the accept rule has no term for the cost of the change itself. Note that `program.md`'s "simplicity criterion" is exactly the kind of prose P6 says the loop will ignore whenever the metric rewards ignoring it — the document argues against its own safeguard.
*Detector:* price complexity in metric units and require the gain to exceed the price. Give deletions a negative price so simplification is a win.

### P20. *(Merged into P16.)*

Retained so `docs/ADAPTATIONS.md` references do not break.

### P21. The agent grades its own homework — **A** [M]

The same process proposes the change, runs the evaluator, parses the number, and writes the row. There is no independent record.

*Why:* convenience — one agent, one loop.
*Mitigation:* the ledger must be written by the harness, append-only, hash-chained. The proposer may not write the record. This is a harness rebuild; there is no detector for it.

---

## Layer 4 — Operations

### P22. `git reset` does not revert everything — **A** [D]

Tracked files revert. Caches, generated data, installed packages, environment mutations and background processes do not. The "reverted" state is not the state that produced the baseline, and the next comparison is contaminated.

*Why:* git tracks source, not environment.
*Detector:* record the eval-harness hash and the environment fingerprint per trial. A mismatch is a provenance rejection, not a warning.

**Severity raised B→A.** v1 called this a waste. It is not: a contaminated revert means the incumbent and the candidate were measured in different worlds, which silently corrupts every subsequent comparison. That is the definition of A.

### P23. The only record is the least protected file — **A** [M]

`results.tsv` is deliberately untracked. It is also the entire memory of the run. A crash, a reset, or an over-eager `git clean` loses it.

*Why:* the file is treated as scratch output rather than as data.
*Mitigation:* append-only, hash-chained, outside the working tree, and written by the harness.

**Corrected scope.** v1 said "the only record." It is not — kept commits are in git, and the branch history survives. `results.tsv` is the only record of **discarded** trials. That is still damning, because the discarded trials are the ones you need to know the noise floor and the false-accept rate, but it changes the fix: you are not preserving the run, you are preserving the *negative* results.

### P24. Unbounded autonomy with no budget and no kill switch — **A** [M]

`NEVER STOP` is the instruction. There is no trial cap, no spend cap, no alarm on a rising crash rate, and no condition under which the loop should stop and ask.

*Why:* the design assumes a human is asleep and wants maximum throughput.
*Mitigation:* a trial budget, a crash-streak limit, a holdout budget, and a no-accept-in-N alarm that escalates instead of "thinking harder."

**Severity raised B→A.** An unbounded loop does not merely waste money. It runs long enough for P5 and P27 to saturate, so the output is *worse* than a short run, not just more expensive.

### P25. Concurrent writers on one branch — **B** [D]

Two sessions, or a human and a session, advance the same branch. A push is rejected or a commit is silently based on stale work. If it is force-pushed, someone's real bug fix disappears.

*Why:* git is the state, and the state has no lock.
*Detector:* `git fetch` and read `rev-list --left-right --count` before every push. Diverged means integrate, never force.

**Corrected scope.** v1 treats this as inherent. It is a misuse: the design is one branch per run (`autoresearch/<tag>`, with the README's `-gpu0` suffix implying parallel runs get separate branches). The real concurrency hazard is that untracked `results.tsv` is machine-local, so two agents on one box silently interleave rows into one file with no way to tell which trial came from which branch.

### P26. A five-minute win is assumed to transfer — **A** [D]

The kept change is validated at one scale and one budget. It may not hold at another. The ratchet does not care, because the only number it has ever seen is the small one.

*Why:* the ladder has one rung.
*Detector:* promote through tiers: screen, replicate, adjudicate, transfer. Only the last one may update the canonical baseline.

---

## Layer 2 (continued) — the entries v1 was missing

### P29. The baseline is n=1 and it is the reference for everything — **A** [D]

P9 says "rerun an identical config." It does not say that the baseline is special. The first run is the only one measured on a cold machine — cold page cache, cold autotune, possibly different clocks — and every subsequent delta is computed against it.

*Why:* the baseline is treated as a constant rather than as a measurement.
*Detector:* the baseline must be the **mean of ≥3 runs**, and that mean, not one draw, is what the accept rule compares against. If the baseline is one draw, every delta in the log inherits its error, and the error does not shrink with more trials.

### P30. No negative control — **A** [D]

There is no no-op trial. Nothing in the loop ever measures what "no change" scores.

*Why:* the loop is built for throughput, and a trial that cannot possibly improve looks like waste.
*Detector:* run no-op trials at the start — reorder a comment, change nothing, run it. They are the cheapest trials in the run, and this is what makes P9–P12, P27 and P29 actionable rather than descriptive; without them, every one of them is advice about a number you do not have.

*Reviewer's note.* The principle is right and v2's sample size is not. Three runs give σ with 2 degrees of freedom, and the relative uncertainty of an sd estimate is 1/√(2·df) — **±50% at n=3**. That is the same error P11 diagnoses one entry earlier: a spread estimated from too few draws. Three no-ops are enough to learn whether `min_delta` is off by an order of magnitude, which is the decision that matters. They are not enough to quote σ to two significant figures, and "give σ directly" overstates it. Two consequences, both implemented here:

1. Run **more** no-ops when the budget allows. `ratchet control --runs 8` gives ±13% at four seeds per run, still ~8% of a 100-trial night.
2. Better, keep **pooling** as the run proceeds. Every trial reports a within-arm sd for free, and pooling within-arm variances across trials drives the relative uncertainty down without spending a single extra trial. That is strictly better than a static preflight number, and it is what `state.noise_var` does. `ratchet control` *seeds* the estimate; it does not settle it.

The pooled estimate only works if it never pools *across* configs — that would fold the effect into the noise, and the floor would appear to fall as the run succeeded. Within-arm only.

### P31. The noise is not iid, so the rank tests are invalid — **A** [D]

P11 recommends a rank test; P14 compares sds. Both assume exchangeability. On a shared H100, "noise" includes thermal drift, memory fragmentation, and other tenants — it is a trend, not a distribution. A drift of `0.002` across a night is indistinguishable from a real win by any test in this document.

*Why:* every statistical remedy here treats trials as draws from one distribution. They are draws from a slowly moving one.
*Detector:* interleave a fixed reference config every ~10 trials and plot it against trial index. If the reference drifts, nothing else in the log means anything, and the correct response is to stop and fix the machine, not to run more trials.

*Reviewer's note.* This entry falsifies P11's remedy, and the document does not reconcile the two. "Detect drift and abort" is the weaker half of the answer; the stronger half is that **interleaving is itself the fix**. If the reference configuration is measured *inside the same session* as the candidate — not hours earlier — then drift is a shared offset that cancels in a paired difference. Exchangeability is not restored globally, but the comparison that matters becomes valid again. This is implemented as `reference_seeds`: an evaluator that interleaves candidate and reference seeds and reports both lets the gate compute a paired interval, and a paired interval is immune to the drift P31 describes. Abort-on-drift stays correct for the case where you cannot interleave. Prefer the paired design and you rarely need the abort.

---

## Appendix — why `min_delta` does not fix the ratchet

P27 gives the arithmetic for an unthresholded ratchet. The obvious response is a threshold. It does not work, and the reason is worth writing down because it is the single most common design error in this genre.

**The record-process argument.** With zero true effect and iid noise σ, the incumbent is the running minimum. The expected number of new records in N draws is H_N ≈ ln N + 0.577, so ~5.2 false accepts at N=100, and the terminal position is ≈ μ − 2.4σ. Both numbers are properties of *taking a running minimum*, not of the accept rule's details.

**What a threshold does.** With `min_delta = d`, a candidate is accepted only if it lands at least `d` below the incumbent. That reduces the *number* of false accepts roughly geometrically in `d`. But each accepted candidate is now at least `d` below, so the *size* of each false accept grows with `d`. The product — total drift — is roughly invariant:

| `min_delta` | accept prob at k=0 | expected false accepts / 100 | terminal drift |
|---|---|---|---|
| 0 | 0.50 | ~5.2 | ~2.4σ |
| 1.4σ | 0.081 | many | ~1.5σ |
| 2.0σ | 0.023 | ~2 | ~2.4σ |
| 2.3σ | 0.011 | ~1 | ~2.6σ |
| 2.8σ | 0.003 | ~0.3 | ~3.1σ |

These are order-of-magnitude, from the record-process approximation, and they should not be quoted as precise. The point they make is that **a threshold trades count for size, and the trade is not obviously favourable.**

*Reviewer's note.* The column does not support the sentence "roughly invariant." Terminal drift in this table is **U-shaped, with a minimum at 1.4σ** — it falls from 2.4σ to 1.5σ, then rises to 3.1σ. So if drift is the harm, 2.8σ is the **worst** row in the table and 1.4σ is the best — the opposite of the recommendation. The recommendation is still defensible, but it optimises something the document never names: **record integrity**, the number of commits in the history that are fiction, as distinct from **terminal bias**, how far the branch sits below the truth. These are two harms and one knob cannot serve both:

| you care about | threshold | cost you accept |
|---|---|---|
| terminal bias | 1.4σ | ~5 fictional commits per 100 |
| history honesty | 2.8σ | ~3σ of terminal bias |

Pick one deliberately and write down which. Do not say "2.8σ, it damps the drift" — on these numbers it does not. The lever that reduces *both* harms is not a threshold at all; it is the three below.

**Resolved, 2026-10-22.** The default recommendation in this repository is **2.8σ**, chosen for **history integrity over terminal bias**. The reasoning: terminal bias moves the branch to a worse place, but it is *measurable after the fact* by re-measuring the canonical baseline on a sealed split, and it is partly repaired by the same adjudication that catches a real regression. A fictional commit is different — it is permanent, it is indistinguishable from a real win in the artifact, and it corrupts the record that every future reader uses to decide what to believe. Between a bias you can detect and a history you cannot audit, take the bias.

The cost is stated, not hidden: roughly 3σ of terminal drift, and fewer accepted changes per hundred trials. `control` prints both options and marks 2.8σ as the default; an operator who prefers the other trade sets 2.0σ and writes down that they did.

**What actually moves the product.**

1. **Replication.** If each trial is the mean of *n* runs, σ_eff = σ/√n, and the terminal drift in absolute units falls as 1/√n. Three runs per trial cuts drift by 42% and costs 3× the wall clock. This is the only lever that attacks σ itself, and it is why P30 and P29 matter more than any threshold.
2. **Sealed adjudication.** A split the search cannot reach (P5) is not a better estimator of the same thing — it is an *independent* one, so the winner's curse does not apply to it. This is the only remedy that is unbiased rather than merely smaller.
3. **Don't ratchet.** Keep a population of candidates and re-test them against each other periodically instead of promoting one to incumbent. This costs memory and bookkeeping and gives up the "always improving" property, which is the property that was never real.

**And the assumption underneath all of it.** Every number above assumes exchangeability, which P31 says is false. If the machine drifts, the effective N is not 100 but the number of *independent* epochs between drift events, and the H_N arithmetic overstates the false-accept count while the sealed split understates the true one. Measure the drift before trusting any of this.

---

## The pattern behind all of them

v1 claimed every pitfall is one of three things. It is not: P16, P19, P22, P25 and P26 fit none of the three buckets. The honest taxonomy has five:

1. **A number with no interval** — P9–P14, P27, P29–P31. The loop cannot distinguish signal from noise, and it has no reason to want to.
2. **A proxy with no adjudicator** — P1–P8, P32. The loop cannot distinguish the goal from the metric, and it is optimised to exploit the difference.
3. **A decision with no record** — P21–P23. The loop cannot be audited after the fact, so the failure is discovered only by re-measuring, which nobody does.
4. **A search topology with no exit** — P16, P19, P26. The ratchet has no undo, so every locally-good decision is globally irreversible, and the cost of that is invisible to the accept rule.
5. **An environment with no provenance** — P22, P25, P28. The comparison is between two states, and nothing guarantees the states are what you think.

The layered conclusion survives, for a simpler reason than v1 gave: **the cheap fixes are all in category 1, and category 1 is the only one that tells you whether anything else worked.** Categories 2–5 are real, but you cannot evaluate a fix to any of them without a noise floor. That is the ordering.

## If you only do five things

1. **No-op trials → σ** (P30). Then `min_delta = 2.8σ`: the choice is for **history integrity over terminal bias** (P10, appendix). Write down that it is a ratchet damper, not a significance test.
2. **Baseline = mean of 3 runs, not one** (P29).
3. **Assert `total_seconds − training_seconds` under a cap** (P28).
4. **Interleave a fixed reference config every 10 trials; abort if it drifts** (P31).
5. **Re-measure the top 3 kept changes at 3× budget and on a sealed split** before believing any of it (P5, P7, P12).

Items 1–4 are a day's work and retrofit onto a running loop. Item 5 is the one that tells you whether the night was real.

## What changed from v1, and why it matters

- **Three factual errors fixed.** P11's p=0.05 (actually 0.10 at n=3; you need n=4), P10's "twice the spread" (ambiguous, and too lenient for a single comparison), P13's misdiagnosis (a reporting confound, not a search bug). These would have propagated — `docs/ADAPTATIONS.md` cites the numbering.
- **The arithmetic is now the headline** (P27, appendix) instead of being implied twelve times and computed zero times. The list previously told you noise is a problem without ever showing what noise does to a ratchet over 100 trials, which is the reason to care about all twelve entries.
- **Every entry is labelled [D] or [M].** v1 called everything a "detector," but roughly half are harness rebuilds. That distinction is the difference between a checklist you can run tonight and a lab you have to build.
- **Six entries added** (P27–P32), of which P28 and P30 are the ones that would have changed the outcome of a real run.
- **Three severities raised** (P22, P24 B→A) and five entries reframed with scope notes (P1, P8, P13, P23, P25) where v1 described a general ML hazard as if it were a property of this repo, or vice versa.

The document is now feasible as a plan rather than only as a taxonomy. The remaining honest caveat: P4, P21, P23, P24 and P32 are mitigations, and doing them means you are no longer running autoresearch — you are writing a lab with an agent in it. That is probably the right thing to do, but it should be a decision, not an accident.
