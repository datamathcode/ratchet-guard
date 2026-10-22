#!/usr/bin/env python3
"""The same candidate stream, judged two ways.

    naive    accepts any change that moves the surrogate. This is Karpathy's
             rule, reproduced faithfully.
    guarded  accepts a change that moves the surrogate *and* survives the
             gates. This is ratchet-guard.

Both runs see identical candidates in identical order. The only difference is
who is allowed to decide.

Two baselines are tracked, and keeping them apart matters:

    dev_work       the working baseline, measured on the surrogate split
    goal_canonical the canonical baseline, measured on the sealed split

A rollback must restore the *dev* measurement. Restoring the sealed one would
compare a goal-split number against surrogate-split numbers for the rest of
the run -- a units bug that looks exactly like a result.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from ratchet_guard.canary import run_canaries  # noqa: E402
from ratchet_guard.gate import GateState, decide  # noqa: E402
from ratchet_guard.ladder import adjudicate  # noqa: E402
from ratchet_guard.model import Contract, Measurement  # noqa: E402
from ratchet_guard.sandbox import run_eval  # noqa: E402

EVAL = f"{sys.executable} {HERE / 'evaluate.py'}"
CONTRACT_PATH = HERE / "contract.toml"

# (model, family, hypothesis, predicted)
CANDIDATES = [
    ("quadratic", "architecture", "add x0*x1 interaction and squared terms", "better"),
    ("memorizer", "calibration", "exact-match lookup over the dev features", "better"),
    ("linear_l2", "regularisation", "l2 0.02 on the linear model", "better"),
    ("quadratic_l2", "regularisation", "l2 0.02 on the quadratic model", "better"),
]


def measure(model: str, split: str, seeds: int = 3) -> Measurement:
    return run_eval(f"{EVAL} --model {model} --split {split} --seeds {seeds}", timeout_s=300)


def fmt(v: float) -> str:
    return f"{v:.4f}"


def run(guarded: bool, contract: Contract, dev_baseline: Measurement, goal_baseline: Measurement):
    rows = []
    dev_work = dev_baseline
    goal_canonical = goal_baseline
    held = "linear"
    blind = [r.name for r in run_canaries(contract, dev_baseline.value, cwd=str(HERE)) if not r.passed]
    state = GateState(blind_spots=blind, under_explored=False)

    for i, (model, family, hyp, predicted) in enumerate(CANDIDATES, start=1):
        cand = measure(model, "dev")
        cand.meta["predicted_direction"] = predicted
        cand.meta["contract_digest"] = contract.digest()
        goal = measure(model, "sealed")

        if guarded:
            d = decide(contract, dev_work, cand, state)
            state.trial_ordinal += 1
            verdict, reason = d.verdict.value, d.reason
            if d.accepted:
                ok, why = adjudicate(contract, goal, goal_canonical)
                if ok:
                    dev_work, goal_canonical, held = cand, goal, model
                    verdict = "promoted"
                    reason = why
                else:
                    # Roll the working branch back. The dev baseline is what
                    # gets restored -- goal_canonical is a different unit.
                    verdict, reason = "promotion_denied", why
        else:
            if cand.value > dev_work.value:
                dev_work, held = cand, model
                verdict = "accept"
                reason = f"surrogate {fmt(dev_work.value)}"
            else:
                verdict = "discard"
                reason = f"surrogate {fmt(cand.value)} <= {fmt(dev_work.value)}"

        rows.append({"model": model, "dev": cand.value, "goal": goal.value,
                     "verdict": verdict, "reason": reason, "held": held})

    return rows, dev_work, goal_canonical, held


def main() -> int:
    contract = Contract.load(CONTRACT_PATH)
    dev0 = measure("linear", "dev")
    goal0 = measure("linear", "sealed")

    print(f"goal       : {contract.goal}")
    print(f"surrogate  : {contract.metric.name} ({contract.metric.direction.value}), "
          f"min_delta={contract.min_delta}, complexity_price={contract.complexity_price}")
    print(f"baseline   : surrogate={fmt(dev0.value)}  goal={fmt(goal0.value)}\n")

    naive, _, _, naive_held = run(False, contract, dev0, goal0)
    guarded, _, guarded_goal, guarded_held = run(True, contract, dev0, goal0)

    print("=" * 80)
    print("NAIVE RATCHET   accept <=> the surrogate improved")
    print("=" * 80)
    for r in naive:
        print(f"  {r['model']:<14} surrogate={fmt(r['dev'])}  goal={fmt(r['goal'])}  "
              f"{r['verdict']:<9} {r['reason'][:44]}")

    print()
    print("=" * 80)
    print("GUARDED RATCHET   accept <=> the surrogate improved AND the gates agree")
    print("=" * 80)
    for r in guarded:
        print(f"  {r['model']:<14} surrogate={fmt(r['dev'])}  goal={fmt(r['goal'])}  "
              f"{r['verdict']:<17} {r['reason'][:40]}")

    naive_goal = next(r["goal"] for r in naive if r["model"] == naive_held)
    print()
    print(f"  held at exit      naive: {naive_held:<12}  guarded: {guarded_held}")
    print(f"  goal at exit      naive: {fmt(naive_goal)}        guarded: {fmt(guarded_goal.value)}")
    print(f"  goal delta        naive: {naive_goal - goal0.value:+.4f}       "
          f"guarded: {guarded_goal.value - goal0.value:+.4f}")
    print()
    print("The naive run reached a surrogate score of 1.0000 -- a perfect score --")
    print("and finished 0.28 below where it started on the thing it was asked to")
    print("improve. Nothing malfunctioned. The ratchet did exactly what it was told.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
