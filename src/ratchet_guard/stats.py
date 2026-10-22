"""Statistics that the decision gate refuses to do without.

Every function here exists because an unguarded ratchet got it wrong in the
original methodology: one seed, no floor, no interval, no correction.
"""

from __future__ import annotations

import math
import random
from statistics import mean, stdev
from typing import Sequence


def bootstrap_ci(
    values: Sequence[float],
    n_boot: int = 4000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for the mean."""
    vals = list(values)
    if not vals:
        raise ValueError("bootstrap_ci needs at least one value")
    if len(vals) == 1:
        return (vals[0], vals[0])
    rng = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += vals[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return (lo, hi)


def diff_ci(
    baseline: Sequence[float],
    candidate: Sequence[float],
    n_boot: int = 4000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Bootstrap CI for mean(candidate) - mean(baseline), unpaired.

    The two arms MUST be resampled with different random streams. Resampling
    both with the same seed pairs them index by index, which cancels the
    shared variance and produces an interval that is far too narrow -- the
    exact error that makes a noise-level difference look decisive.
    """
    base = list(baseline)
    cand = list(candidate)
    if not base or not cand:
        raise ValueError("diff_ci needs both arms")
    if len(base) == 1 or len(cand) == 1:
        return (mean(cand) - mean(base), mean(cand) - mean(base))
    rng_b = random.Random(seed)
    rng_c = random.Random(seed + 7919)
    nb, nc = len(base), len(cand)
    deltas = []
    for _ in range(n_boot):
        sb = sum(base[rng_b.randrange(nb)] for _ in range(nb)) / nb
        sc = sum(cand[rng_c.randrange(nc)] for _ in range(nc)) / nc
        deltas.append(sc - sb)
    deltas.sort()
    lo = deltas[int((alpha / 2) * n_boot)]
    hi = deltas[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return (lo, hi)


def pooled_sd(*groups: Sequence[float]) -> float:
    """Pooled within-group standard deviation."""
    groups = [list(g) for g in groups if len(g) >= 2]
    if not groups:
        return 0.0
    num = 0.0
    den = 0
    for g in groups:
        num += (len(g) - 1) * stdev(g) ** 2
        den += len(g) - 1
    if den == 0:
        return 0.0
    return math.sqrt(num / den)


def mann_whitney(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """Two-sided Mann-Whitney U with tie correction. Returns (U, p)."""
    x, y = list(a), list(b)
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        raise ValueError("mann_whitney needs both arms")
    combined = sorted([(v, 0) for v in x] + [(v, 1) for v in y])
    ranks: list[float] = [0.0] * len(combined)
    i = 0
    tie_sum = 0.0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        t = j - i + 1
        if t > 1:
            tie_sum += t**3 - t
        i = j + 1
    r1 = sum(ranks[i] for i, (_, g) in enumerate(combined) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    n = n1 + n2
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie_sum / (n * (n - 1)))
    if sigma2 <= 0:
        return (u1, 1.0)
    z = (u1 - mu) / math.sqrt(sigma2)
    p = 2 * (1 - _norm_cdf(abs(z)))
    return (u1, min(1.0, p))


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def bonferroni(p: float, comparisons: int) -> float:
    """Correct a p-value for the number of comparisons actually made."""
    return min(1.0, p * max(1, comparisons))


def required_seed_n(noise_sd: float, effect: float) -> int:
    """Rough seeds needed to resolve `effect` against `noise_sd` at 80% power.

    Reported to the human so "run more seeds" is a number, not a vibe.
    """
    if effect <= 0:
        return 10**6
    d = effect / max(noise_sd, 1e-12)
    if d < 0.2:
        return 200
    return max(3, int(math.ceil(16 / (d**2))))
