"""The promotion ladder.

The ratchet has exactly one tier: the number moved, so the commit stays. That
conflates three different claims:

  screen     -- "this looks promising on the cheap surrogate"
  replicated -- "this looks promising on the cheap surrogate, three times"
  adjudged   -- "this also wins on the metric the search cannot touch"

Only the third claim deserves to update the canonical baseline. Collapsing
them is how a run accumulates 20 changes that add up to an 11% win on one
benchmark and nothing anywhere else.
"""

from __future__ import annotations

from .model import SEED_FLOOR, Contract, Direction, Measurement
from .stats import diff_ci


def adjudicate(
    contract: Contract,
    holdout: Measurement,
    canonical: Measurement,
) -> tuple[bool, str]:
    """Decide whether a candidate may become the canonical baseline.

    The holdout is the goal metric, measured on data the search never saw.
    This is the check that makes the canary suite worth running: the canary
    says the surrogate is blind, and this is the adjudicator that covers it.
    """
    if len(holdout.seeds) < SEED_FLOOR:
        return False, (
            f"holdout produced {len(holdout.seeds)} seed(s); {SEED_FLOOR} are required "
            "before any delta is quotable"
        )

    s = contract.metric.direction.sign()
    lo, hi = diff_ci(canonical.seeds, holdout.seeds)
    gain_lo, gain_hi = sorted([-s * hi, -s * lo])

    if gain_lo <= 0:
        return False, (
            f"holdout gain CI [{gain_lo:+.6f}, {gain_hi:+.6f}] includes zero; "
            "the surrogate improvement did not survive adjudication"
        )
    if holdout.loc_delta > 0 and contract.complexity_price > 0:
        priced = gain_lo - contract.complexity_price * holdout.loc_delta
        if priced <= 0:
            return False, (
                f"holdout gain {gain_lo:+.6f} does not cover the complexity price "
                f"of {holdout.loc_delta} added lines"
            )
    return True, f"holdout gain {gain_lo:+.6f} (95% CI lower bound) clears zero"


def transfer(contract: Contract, small: Measurement, large: Measurement) -> tuple[bool, str]:
    """Check that a win found at the screen scale still holds at the goal scale.

    Karpathy verified this by hand once ("all additive; transferred to
    depth-24"). A loop that never checks it will happily stack wins that only
    exist at five minutes and depth twelve.
    """
    if large.value == 0.0 and not large.seeds:
        return False, "transfer run produced no measurement"
    # +1 when a bigger number is better. `direction.sign()` is the base-minus-
    # candidate form used by the gate; using it here would invert the result.
    d = 1.0 if contract.metric.direction is Direction.MAX else -1.0
    lo, hi = diff_ci(small.seeds, large.seeds)
    gain_lo = min(d * lo, d * hi)
    if gain_lo <= 0:
        return False, f"win did not transfer: gain CI lower bound {gain_lo:+.6f}"
    return True, f"transferred with {gain_lo:+.6f} at the larger budget"
