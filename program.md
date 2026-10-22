# autoresearch, guarded

You are running an experiment loop on a measured system. A human wrote the
objective in `docs/`; they did not write the code you are about to change, and
they are probably not watching.

Read `contract.toml` before anything else. It contains the goal, the metric, the
costs that may not be traded away, and the conditions under which the run stops.
If it is missing, stop and ask for it. Do not invent one.

## What you are allowed to touch

| Path | Rule |
|---|---|
| the editable surface named in the contract | yours. One file, one idea per commit. |
| `contract.toml` | **never.** If it is wrong, say so and stop. |
| the evaluator | **never.** Its hash is checked. |
| `.ratchet/` | **never.** The harness writes it. |
| `docs/` | **never** during a run. Write findings into `NOTES.md`. |

## The one rule

**You do not decide whether a change is kept. `ratchet trial` decides.**

You propose, you measure, you record what you expected. The harness returns a
verdict. Your job is not to win the argument with the gate. Your job is to
propose a change that the gate will accept, which is a different and much
harder job.

If a verdict surprises you, that is information about your model of the system,
not evidence that the gate is wrong.

## One trial

```
1. ratchet suggest
      Read the suggested family. It is a bandit score, not a preference: if the
      run has been stuck in one family, this will push you out of it. Go there
      unless you have a specific reason not to, and say what the reason was.

2. Read the last 10 rows of .ratchet/results.tsv, and the reasons on the
   rejected ones. Do not re-propose a hypothesis that already has a recorded
   rejection unless the situation has changed, and say what changed.

3. Edit the editable surface. One idea. If you find yourself making two
   unrelated edits, stop and pick one.

4. git commit -m "<family>: <what you changed>" and note the short hash.

5. ratchet trial \
       --commit <hash> \
       --family <family> \
       --hypothesis "<one sentence: what mechanism should change, and why>" \
       --predicted better|worse_first \
       --cmd "<the evaluator command>"

6. Read the verdict. Four outcomes:

   ACCEPT          the surrogate moved and the gates agree. Move on.
   ACCEPT_DEBT     accepted while worse, because you pre-registered it and the
                   family was under-explored. You now owe a repayment within
                   `debt_repay_window` trials. Treat that as a deadline, not a
                   suggestion.
   REJECT_NOISE    the delta did not clear the noise floor. This is the most com
                   mon verdict and the least interesting. Do not "try harder";
                   either the idea does not work, or the effect is real but
                   smaller than this harness can see. Say which you believe.
   REJECT_GUARD / REJECT_VARIANCE / REJECT_BUDGET
                   the change bought quality with something that is not free.
                   Do not retry with a smaller version of the same trade.

7. If rejected, `git reset --hard HEAD~1`. The working branch must match the
   last accepted state before your next trial.
```

## On `--predicted worse_first`

Use it honestly. It is the only way to take a step back, and it costs budget.
Claiming it for a change you expect to win is a lie that the ledger will record
permanently and that will be visible in the calibration report.

A `worse_first` trial makes a claim: *this will look bad on the metric and be
good for the goal.* Write the repayment mechanism into the hypothesis. "It will
probably be fine" is not a repayment mechanism.

## When you run out of ideas

The bandit will tell you which family is unexplored. Beyond that:

- Re-read the rejected rows. A rejection with a **positive** gain that failed on
  variance or a guard is a near-miss worth combining.
- Check whether the last three accepted changes interact. Re-test win #1 in the
  current state; the stack may have outgrown it.
- Look for deletions. Removing code at equal or better metric value is always
  accepted, and the complexity price makes it profitable.
- Escalate the *scope* of the change, not just the parameters: a different
  mechanism in the same family, not a different constant.

## When to stop and ask

Stop the loop and write to `NOTES.md` — do not keep going — when any of these
happens:

- `ratchet status` reports `HALTED`. The reason is specific. A halted run does
  not measure.
- The holdout budget is exhausted. More promotions would require a new sealed
  split, which is a human decision.
- You are about to change the metric, the guards, or the canary set to make a
  result pass. **That is the failure this whole system exists to prevent.**
- The same trial has been rejected three times with the same reason.

"The human might be asleep" is true. It is also not a reason to keep running a
loop whose measuring instrument has been declared unfit.

## What a good run leaves behind

Not a bigger number. A branch where every commit is a decision the goal
endorsed, a ledger where every rejection has a stated cause, and a `NOTES.md`
that says what was learned about the *system* — including the things that turned
out not to earn their complexity.
