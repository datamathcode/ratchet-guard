#!/usr/bin/env python3
"""A gamed surrogate and an honest goal, side by side.

Splits:
  TRAIN   -- what every model fits. 400 points.
  dev     -- the surrogate metric. One fixed 240-point set that every trial in
             the run is scored against. Never used for fitting.
  sealed  -- the goal. 300 fresh points, used only by the adjudicator.

Both eval splits come from the same distribution, so nothing here is unfair.
The gap between them is produced entirely by the search: after enough trials
against one fixed set, that set is training data, and a candidate that
memorises it is a *correct* answer to the question the metric asked.

Prints one JSON object. Contract: see docs/SPEC.md.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time

N_TRAIN, N_DEV, N_SEALED = 400, 240, 300
TRUE_W = (0.95, 0.60, 1.30)  # x0, x1, x0*x1 -- the interaction dominates


def make_data(n: int, seed: int) -> list[tuple[float, float, int]]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        x0 = rng.gauss(0.0, 1.0)
        x1 = rng.gauss(0.0, 1.0)
        z = TRUE_W[0] * x0 + TRUE_W[1] * x1 + TRUE_W[2] * x0 * x1
        rows.append((x0, x1, 1 if z > 0 else 0))
    return rows


TRAIN = make_data(N_TRAIN, seed=3)
DEV = make_data(N_DEV, seed=7)
SEALED = make_data(N_SEALED, seed=11)

LOC_DELTA = {
    "linear": 0,
    "linear_l2": 2,
    "quadratic": 9,
    "quadratic_l2": 11,
    "memorizer": 16,
    "shuffle": 1,
}


def phi(x0: float, x1: float, kind: str) -> list[float]:
    if kind == "linear":
        return [1.0, x0, x1]
    return [1.0, x0, x1, x0 * x0, x1 * x1, x0 * x1]


def _sigmoid(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def train(rows, kind: str, l2: float, seed: int, epochs: int = 220, lr: float = 0.5):
    """Logistic regression by gradient descent.

    `seed` varies the training subsample and the initialisation, so two runs
    of an identical config genuinely differ. Seed spread here is real
    run-to-run noise, not a cosmetic perturbation.
    """
    rng = random.Random(seed)
    sample = [rows[rng.randrange(len(rows))] for _ in range(len(rows))]
    feats = [phi(a, b, kind) for a, b, _ in sample]
    labels = [float(c) for _, _, c in sample]
    d = len(feats[0])
    w = [rng.gauss(0.0, 0.01) for _ in range(d)]
    n = len(feats)
    for _ in range(epochs):
        grad = [0.0] * d
        for f, y in zip(feats, labels):
            p = _sigmoid(sum(wi * fi for wi, fi in zip(w, f)))
            err = p - y
            for j in range(d):
                grad[j] += err * f[j]
        for j in range(d):
            w[j] -= lr * (grad[j] / n + l2 * w[j])
    return w


def build(model: str, seed: int):
    """Return a predictor for the requested model name."""
    if model == "shuffle":
        # A label-blind control. Any metric that cannot rank this below the
        # baseline is measuring noise.
        rng = random.Random(seed)
        return lambda x0, x1: rng.randrange(2)

    base_kind = "linear" if model.startswith("linear") else "quadratic"
    l2 = 0.02 if model.endswith("_l2") else 0.0
    w = train(TRAIN, base_kind, l2, seed)

    if model != "memorizer":
        return lambda x0, x1: 1 if sum(
            wi * fi for wi, fi in zip(w, phi(x0, x1, base_kind))) > 0 else 0

    # The cheat: a per-example table fitted on the evaluation split itself,
    # with a majority-class fallback for anything it has not seen. This is
    # what "calibrate the threshold on the validation set" becomes after
    # seven hundred trials of gradient descent on the same 240 rows.
    table = {(round(a, 6), round(b, 6)): c for a, b, c in DEV}
    majority = 1 if sum(c for _, _, c in DEV) >= len(DEV) / 2 else 0

    def lookup(x0: float, x1: float) -> int:
        return table.get((round(x0, 6), round(x1, 6)), majority)

    return lookup


def accuracy(pred, rows) -> float:
    return sum(1 for a, b, c in rows if pred(a, b) == c) / len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="linear", choices=sorted(LOC_DELTA))
    ap.add_argument("--split", default="dev", choices=["dev", "sealed"])
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args()

    rows = DEV if args.split == "dev" else SEALED
    t0 = time.perf_counter()
    seeds = [accuracy(build(args.model, s), rows) for s in range(args.seeds)]
    wall = time.perf_counter() - t0

    d = len(phi(0.0, 0.0, "linear"))
    print(json.dumps({
        "value": sum(seeds) / len(seeds),
        "seeds": seeds,
        "guards": {"peak_mem_mb": 120.0 + 8.0 * d},
        "wall_s": round(wall, 3),
        "peak_mem_mb": 120.0 + 8.0 * d,
        "loc_delta": LOC_DELTA[args.model],
        "meta": {"model": args.model, "split": args.split, "n": len(rows)},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
