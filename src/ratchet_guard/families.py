"""Exploration policy: a bandit over idea families.

Karpathy's `program.md` answers idea exhaustion with "think harder". That is
an instruction to a language model, not a policy. The practical result is
mode collapse: the agent keeps proposing variations of its last success,
because that is what the recent context rewards.

A UCB score over declared families gives the loop a *reason* to leave a
neighborhood, and the debt mechanism gives it *permission* to lose there.
"""

from __future__ import annotations

import math

from .model import Contract


def scores(
    contract: Contract,
    family_counts: dict[str, int],
    family_gains: dict[str, list[float]],
    c: float = 1.4,
) -> dict[str, float]:
    total = max(1, sum(family_counts.values()))
    out: dict[str, float] = {}
    for fam in contract.families:
        n = family_counts.get(fam, 0)
        if n == 0:
            out[fam] = math.inf
            continue
        gains = family_gains.get(fam, [])
        mu = sum(gains) / len(gains) if gains else 0.0
        out[fam] = mu + c * math.sqrt(math.log(total) / n)
    return out


def suggest(
    contract: Contract,
    family_counts: dict[str, int],
    family_gains: dict[str, list[float]],
) -> str:
    """Family the loop should try next. Untried families win first."""
    s = scores(contract, family_counts, family_gains)
    return max(s.items(), key=lambda kv: kv[1])[0]


def under_explored(
    contract: Contract,
    family_counts: dict[str, int],
    family: str,
    factor: float = 0.75,
) -> bool:
    """True when a family has had fewer trials than an even split would give it."""
    total = sum(family_counts.values())
    if total == 0:
        return True
    even = total / max(1, len(contract.families))
    return family_counts.get(family, 0) < factor * even


def exhausted(
    contract: Contract,
    family_counts: dict[str, int],
    threshold: int = 8,
) -> bool:
    """True when every family has been tried enough that UCB no longer helps."""
    return all(family_counts.get(f, 0) >= threshold for f in contract.families)
