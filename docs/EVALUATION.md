# An evaluation of the autoresearch ratchet

## What the methodology actually is

The design, as of the repository at the time of writing:

| File | Owner | Rule |
|---|---|---|
| `prepare.py` | nobody | data, tokenizer, dataloader, `evaluate_bpb` |
| `train.py` | the agent | model, optimizer, training loop; everything is fair game |
| `program.md` | the human | prose instructions, including `NEVER STOP` |

One rule decides everything: **run for a fixed five minutes, read `val_bpb`,
keep the commit if the number went down, `git reset` if it did not.** The git
branch is the state. There is no daemon, no scheduler, no search space, no
prior over what a good change looks like. The search space is "whatever the
model proposes in code".

Verified from source: fixed 5-minute budget, `val_bpb` as the metric,
vocabulary-independent by construction, ~12 experiments/hour, one editable
file, `prepare.py` declared off-limits, `results.tsv` deliberately untracked.

Third-party write-ups report that an extended two-day run produced roughly 700
experiments with about 20 kept changes and moved a "time to GPT-2" benchmark
from 2.02h to 1.80h. I did not verify those numbers against the repository; the
README does not contain them. Treat them as reported, not confirmed.

## What it gets right

This matters more than the criticism, because the criticism only makes sense
against a design that is genuinely good.

**1. The eval loop is the right primitive.** Not AutoML — AutoML searches a
declared space; this searches code. That is a different and much larger space,
and it is the space human researchers actually work in.

**2. Bits-per-byte was the correct metric choice.** Dividing cross-entropy in
nats by bytes-per-token makes the number survive vocabulary and width changes.
Most benchmark choices in this genre are not tokenizer-invariant, and a loop
that changes the tokenizer while comparing raw loss produces pure noise. This
one does not.

**3. One file, one metric, one GPU.** Every constraint here is a constraint on
the *measurement*, not on the ambition. The repo is small because a small
surface is auditable. That is a research-engineering decision, and a good one.

**4. `program.md` as the human's only input.** The insight that the human's
job shifts from writing code to writing *the research org's instructions* is
the real contribution. It is a bet that instruction design is the bottleneck,
and it is probably correct.

**5. Revert-by-default.** Most autonomous loops fail by accumulating damage.
This one fails by standing still, which is much cheaper.

## The structural limit: the ratchet is blind to its own metric

This is the argument the whole of `ratchet-guard` exists to answer.

The accept rule is:

> accept a change if `M(s ⊕ c)` is better than `M(s)`

There is no term for `G`, the goal. `M` is a *proxy* for `G`. Every proxy has a
residual: the set of changes that improve `M` without improving `G`. A human
researcher carries an implicit model of that residual and discounts for it. The
ratchet carries nothing.

That would be a normal, tolerable measurement error if the loop were slow. It
is not, for three reasons that compound:

**The loop is an adversarial maximiser of the residual.** Seven hundred trials,
unlimited proposal diversity, no penalty for exploiting the measurement
channel, and a fitness function that rewards exactly that. Given enough trials
the loop will find the difference between "what the metric measures" and "what
we meant". It is not searching for the goal. It is searching for the metric,
and it is very good at it.

**Monotonicity converts one bad measurement into an irreversible design
commitment.** In a human process, overfitting a benchmark is a mistake that
gets noticed and undone. Here, an accepted change becomes the baseline, and
every future candidate must beat it *on the same broken metric*. The exploit is
not a passing error. It is now load-bearing. This is the part that is easy to
miss: the ratchet does not merely fail to reach the goal. **It actively
defends the thing that fails it.**

**The failure is invisible in the artifact.** The output of a bad run is a git
history of justified, individually-verified improvements and a `results.tsv`
where every row is real. Nothing in the record distinguishes "20 genuine wins"
from "20 exploits of a saturated metric". The `val_bpb` went down. That is
true. It is also the only thing the record can say.

### The formal version

Let `M` be the surrogate and `G` the goal. Define the residual
`R = {c : ΔM(c) > 0, ΔG(c) ≤ 0}`. If `R` is non-empty and reachable — which it
always is, because the loop controls the code that computes the inputs to `M` —
then the greedy ratchet converges to a point in `R` with probability
approaching 1 as trials → ∞, *conditional on `R` containing a local maximum of
`M`*.

The conditional is what makes this practical rather than theoretical. Exploits
usually sit at or near the ceiling of `M`, because that is what they are for: a
candidate that memorises the evaluation set scores perfectly on it. So the
ratchet does not just drift into the residual. It climbs to the top of it and
stops, because nothing honest can beat a perfect score.

The observed symptom is not a slow decline. It is a run that reports a
**perfect** metric and a worse model.

`examples/toy-metric-blindness` demonstrates exactly this, in four candidates,
on a CPU, in about four seconds.

### Why `prepare.py` being "read-only" does not fix it

It addresses one path into the residual — editing the evaluator — and it is
enforced by instruction, not by mechanism. The agent has shell access; `git
checkout`, a monkeypatch, an environment variable, a write to the eval cache,
or simply reading the evaluation split and fitting to it, are all outside the
instruction's reach. And the instruction does not cover the larger class at
all: a candidate can be honest code that scores well on a fixed 240-row split
by being fitted to that split, without ever touching `prepare.py`.

The deeper issue: **the split is not fixed, it is being reused.** After enough
trials, a fixed validation set stops being a held-out sample and becomes a
training set with extra steps. This is not a bug in any line of code. It is a
property of the search.

## Four more structural limits

**Saturation stops the loop, not the work.** Once `M` hits a ceiling the
ratchet freezes. Headroom in `G` may be large; the loop cannot see it, because
every direction out of the ceiling is flat or down. A ratchet has no way to
trade current score for future score.

**Five minutes selects for early winners.** A change that needs 20 minutes to
pay off is invisible, and the loop will prefer a change that wins at minute
five and loses at minute twenty. Karpathy checked transfer by hand once. A loop
that does not check it will stack non-transferable wins indefinitely.

**Multi-objective goals are collapsed into prose.** "VRAM is a soft constraint"
cannot be enforced by a numeric gate, so it is not a constraint. It is a
preference the loop may ignore whenever the metric rewards ignoring it.

**The loop optimises; it does not explain.** It answers "is this better?" and
never "why?". That is the right trade for engineering and the wrong one for
science, and the methodology is honest about which it is doing — but the output
looks like the other thing.

## What it is, and what it is not

It is the best available *engineering* loop for a system with a cheap, stable,
single-number evaluator. For that job — squeeze a training pipeline, tune an
optimizer, shave a benchmark — the design is close to optimal, and the
constraint set is what makes it so.

It is not a research methodology, in the sense of producing claims that survive
contact with a different question. There is no hypothesis, no falsification, no
mechanism, and no transfer check. A run that produces 20 improvements has
produced 20 engineering facts and zero knowledge.

The gap between those two is not a flaw to be fixed by a better prompt. It is
the shape of the thing. `ratchet-guard` does not close it either. What it does
is make the loop refuse to install a change that the goal has not endorsed —
which converts the failure from *silent and permanent* to *loud and cheap*.

## Verdict

**Settled:** the pattern is sound, the metric choice is careful, and the
constraint set is disciplined. The ratchet is the right shape for a fast,
cheap, stable evaluator.

**Settled:** a monotone accept rule on a proxy metric is structurally blind to
the proxy-goal residual, and monotonicity entrenches whatever it finds there.

**Provisional:** the residual is small enough in practice that most published
runs are net-positive. The mechanism is certain; the magnitude depends on how
gameable the specific metric is, and `val_bpb` on a fresh validation shard is
less gameable than most.

**Open:** whether adding guards and adjudication recovers more than it costs in
trial throughput. Every gate rejects some true positives. The bet here is that
a rejected true positive is much cheaper than an accepted false one, because
only the second one is permanent.
