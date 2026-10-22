"""The decision gate.

This is the part the original ratchet does not have. Karpathy's loop accepts
a change when the scalar improves. That rule has no term for the goal, no
term for cost, no term for confidence, and no term for "the metric might be
lying". Each missing term is a separate gate here, and the gates run in a
fixed order so a rejection can be attributed to exactly one cause.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, stdev

from .model import (
    BLOCKING_HALTS,
    SEED_FLOOR,
    Contract,
    DebtEntry,
    Direction,
    Measurement,
    Verdict,
)
from .stats import diff_ci, mann_whitney, paired_diff_ci

EPS = 1e-12


@dataclass
class GateState:
    trial_ordinal: int = 0
    holdout_reads_used: int = 0
    open_debt: list[DebtEntry] = field(default_factory=list)
    family_counts: dict[str, int] = field(default_factory=dict)
    blind_spots: list[str] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""
    unadjudicated: int = 0
    under_explored: bool = True
    noise_var: float | None = None
    noise_df: int = 0
    reference: dict | None = None
    trials_since_reference: int = 0
    drift_detected: bool = False


@dataclass
class Decision:
    verdict: Verdict
    gain: float
    ci: tuple[float, float]
    reason: str
    checks: dict = field(default_factory=dict)
    requires_holdout: bool = False

    @property
    def accepted(self) -> bool:
        return self.verdict in (Verdict.ACCEPT, Verdict.ACCEPT_DEBT)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "gain": self.gain,
            "ci": list(self.ci),
            "reason": self.reason,
            "checks": self.checks,
            "requires_holdout": self.requires_holdout,
        }


def effect(contract: Contract, base: Measurement, cand: Measurement) -> float:
    """Signed gain in metric units, net of the complexity price.

    `loc_delta` is signed lines added. Adding complexity costs the candidate
    metric units; deleting it earns them. Karpathy encoded this as prose
    ("a 0.001 improvement that adds 20 hacky lines? probably not worth it").
    Prose is not a gate, so here it is a number the candidate must beat.
    """
    raw = contract.metric.direction.sign() * (base.value - cand.value)
    return raw - contract.complexity_price * cand.loc_delta


def decide(
    contract: Contract,
    baseline: Measurement,
    cand: Measurement,
    state: GateState,
    crashes: bool = False,
) -> Decision:
    checks: dict = {}

    if state.halted:
        return Decision(
            Verdict.HALT_METRIC, 0.0, (0.0, 0.0),
            f"run halted: {state.halt_reason}", checks,
        )

    if crashes:
        return Decision(Verdict.CRASH, 0.0, (0.0, 0.0), "evaluator failed; nothing to judge", checks)

    # --- gate 0b: drift ----------------------------------------------------
    if state.drift_detected:
        return Decision(
            Verdict.HALT_DRIFT, 0.0, (0.0, 0.0),
            "the reference configuration drifted; trials are not exchangeable and "
            "no delta from this session is quotable. Fix the machine, re-run `control`.",
            checks,
        )

    # --- gate 0c: the contract cannot see its own noise --------------------
    noise_sd = math.sqrt(state.noise_var) if state.noise_var else None
    checks["noise"] = {
        "sd": noise_sd, "df": state.noise_df, "min_delta": contract.min_delta,
        "ratio": (contract.min_delta / noise_sd) if noise_sd else None,
    }
    if noise_sd and contract.min_delta < noise_sd:
        return Decision(
            Verdict.HALT_CONFIG, 0.0, (0.0, 0.0),
            f"min_delta {contract.min_delta:.6f} is below the measured noise floor "
            f"{noise_sd:.6f}; acceptances below 1 sigma are noise records, not improvements. "
            "Raise min_delta and restart.",
            checks,
        )

    # --- gate 1: provenance -------------------------------------------------
    digest = cand.meta.get("contract_digest")
    if digest is not None and digest != contract.digest():
        return Decision(
            Verdict.REJECT_PROVENANCE, 0.0, (0.0, 0.0),
            f"measurement was produced under contract {digest}, current is {contract.digest()}",
            checks,
        )

    # --- gate 2: replicate floor -------------------------------------------
    n_seeds = len(cand.seeds)
    checks["seeds"] = {"n": n_seeds, "required": max(SEED_FLOOR, contract.seed_replicates)}
    if n_seeds < max(SEED_FLOOR, contract.seed_replicates):
        return Decision(
            Verdict.REJECT_NOISE, 0.0, (0.0, 0.0),
            f"{n_seeds} seed(s); a delta needs at least {max(SEED_FLOOR, contract.seed_replicates)}",
            checks,
        )

    # --- gate 3: guard metrics ---------------------------------------------
    guard_report = {}
    for g in contract.guards:
        bv = baseline.guards.get(g.name)
        cv = cand.guards.get(g.name)
        if bv is None or cv is None:
            guard_report[g.name] = {"status": "missing"}
            if g.tolerance < 1.0:
                checks["guards"] = guard_report
                return Decision(
                    Verdict.REJECT_GUARD, 0.0, (0.0, 0.0),
                    f"guard `{g.name}` not reported; an unreported cost is an unbounded cost",
                    checks,
                )
            continue
        degradation = (
            (cv - bv) / max(abs(bv), EPS) if g.direction.value == "min"
            else (bv - cv) / max(abs(bv), EPS)
        )
        ok = degradation <= g.tolerance + EPS
        guard_report[g.name] = {
            "baseline": bv, "candidate": cv,
            "degradation": round(degradation, 6), "tolerance": g.tolerance, "pass": ok,
        }
        if not ok:
            checks["guards"] = guard_report
            return Decision(
                Verdict.REJECT_GUARD, 0.0, (0.0, 0.0),
                f"guard `{g.name}` degraded {degradation:.1%} > {g.tolerance:.1%}",
                checks,
            )
    checks["guards"] = guard_report

    # --- gate 3b: the unscored half of the wall clock ----------------------
    # The budget is defined as training time, excluding startup and
    # compilation. Whatever an evaluator spends in import, preprocessing or a
    # warmup phase is compute no rule scores -- and it is free to move real
    # work there.
    work_s = cand.meta.get("work_s")
    if contract.wall_overhead_cap is not None and work_s is not None and cand.wall_s > 0:
        try:
            work = float(work_s)
        except (TypeError, ValueError):
            work = None
        if work is not None:
            overhead = max(0.0, (cand.wall_s - work) / cand.wall_s)
            checks["overhead"] = {
                "work_s": work, "wall_s": cand.wall_s,
                "fraction": round(overhead, 4), "cap": contract.wall_overhead_cap,
            }
            if overhead > contract.wall_overhead_cap:
                return Decision(
                    Verdict.REJECT_OVERHEAD, 0.0, (0.0, 0.0),
                    f"{overhead:.1%} of the wall clock is unscored (cap "
                    f"{contract.wall_overhead_cap:.1%}); work moved outside the budget "
                    "is work the comparison does not price",
                    checks,
                )

    # --- gate 4: variance inflation ---------------------------------------
    base_sd = stdev(baseline.seeds) if len(baseline.seeds) > 1 else 0.0
    cand_sd = stdev(cand.seeds) if len(cand.seeds) > 1 else 0.0
    infl = (cand_sd / base_sd) if base_sd > EPS else 1.0
    checks["variance"] = {"baseline_sd": base_sd, "candidate_sd": cand_sd, "inflation": infl}
    if base_sd > EPS and infl > contract.variance_inflation_tolerance:
        return Decision(
            Verdict.REJECT_VARIANCE, 0.0, (0.0, 0.0),
            f"fragile: run-to-run sd widened {infl:.1f}x; the mean is not reproducible",
            checks,
        )

    # --- gate 5: effect size against the noise floor ----------------------
    # If the evaluator measured the reference configuration in the SAME session
    # and reported it as `reference_seeds`, compare paired. Pairing removes
    # whatever the machine was doing that hour; unpaired arms measured hours
    # apart are comparing two different machines.
    cx = contract.complexity_price * cand.loc_delta
    s = contract.metric.direction.sign()
    ref_seeds = cand.meta.get("reference_seeds")
    paired = (
        isinstance(ref_seeds, (list, tuple))
        and len(ref_seeds) == len(cand.seeds) > 0
    )
    if paired:
        ref = tuple(float(x) for x in ref_seeds)
        d_lo, d_hi = paired_diff_ci(cand.seeds, ref)
        arm = Measurement(value=mean(ref), seeds=ref, guards=dict(baseline.guards))
    else:
        d_lo, d_hi = diff_ci(baseline.seeds, cand.seeds)
        arm = baseline
    ci_lo, ci_hi = min(-s * d_hi, -s * d_lo) - cx, max(-s * d_hi, -s * d_lo) - cx
    gain = effect(contract, arm, cand)
    threshold = max(contract.min_delta, 0.0)
    _, p = mann_whitney(arm.seeds, cand.seeds)
    checks["effect"] = {
        "raw_delta": s * (arm.value - cand.value),
        "complexity_cost": cx,
        "gain": gain, "ci": [ci_lo, ci_hi],
        "min_delta": contract.min_delta, "mann_whitney_p": p,
        "paired": paired,
        "mean_baseline": mean(arm.seeds), "mean_candidate": mean(cand.seeds),
    }

    if ci_lo > threshold:
        # --- gate 6: queue discipline -------------------------------------
        if contract.holdout_cmd and state.unadjudicated >= max(1, contract.max_unadjudicated):
            return Decision(
                Verdict.REJECT_BUDGET, gain, (ci_lo, ci_hi),
                f"{state.unadjudicated} accepted changes are unadjudicated (limit {contract.max_unadjudicated}); "
                "promote or roll back before stacking more",
                checks,
            )
        return Decision(
            Verdict.ACCEPT, gain, (ci_lo, ci_hi),
            f"gain {gain:+.6f} with 95% CI lower bound {ci_lo:+.6f} above {threshold:.6f}",
            checks,
            requires_holdout=bool(state.blind_spots) and bool(contract.holdout_cmd),
        )

    # --- gate 7: speculative debt -----------------------------------------
    budget = int(contract.exploration_fraction * contract.max_trials)
    if (
        cand.meta.get("predicted_direction") == "worse_first"
        and len(state.open_debt) < budget
        and state.under_explored
        and ci_lo > -abs(contract.min_delta) - 10 * max(base_sd, EPS)
    ):
        return Decision(
            Verdict.ACCEPT_DEBT, gain, (ci_lo, ci_hi),
            f"regression {gain:+.6f} taken as debt in an under-explored family; "
            f"must repay within {contract.debt_repay_window} trials",
            checks,
        )

    why = (
        f"indistinguishable from noise: CI [{ci_lo:+.6f}, {ci_hi:+.6f}] does not clear {threshold:.6f}"
        if ci_hi > threshold
        else f"gain {gain:+.6f} is at or below the acceptance threshold {threshold:.6f}"
    )
    return Decision(Verdict.REJECT_NOISE, gain, (ci_lo, ci_hi), why, checks)


def settle_debt(contract: Contract, state: GateState, current_gain: float, ordinal: int) -> list[str]:
    """Close or extend debt entries. Returns trial ids that must be rolled back."""
    expired: list[str] = []
    for d in state.open_debt:
        if current_gain > 0:
            d.repaid = True
            continue
        if ordinal >= d.deadline:
            expired.append(d.trial_id)
    state.open_debt = [d for d in state.open_debt if not d.repaid and d.trial_id not in expired]
    return expired


def pareto_frontier(rows: list[dict], direction: Direction = Direction.MIN) -> list[dict]:
    """Non-dominated set over (primary, memory, time, complexity).

    A single scalar line loses information the moment two candidates trade
    one cost for another. The archive keeps every trade the run discovered,
    so the human can pick a different point later without re-running anything.

    `direction` describes the primary metric. Every other axis is minimised,
    so a MAX metric is negated into the same comparison space.
    """
    s = direction.sign()

    def vec(r: dict) -> tuple[float, float, float, float]:
        return (
            s * r["value"],  # MAX -> negate, so smaller is always better here
            r.get("peak_mem_mb", 0.0),
            r.get("wall_s", 0.0),
            abs(r.get("loc_delta", 0)),
        )

    out = []
    for i, r in enumerate(rows):
        vi = vec(r)
        dominated = False
        for j, other in enumerate(rows):
            if i == j:
                continue
            vj = vec(other)
            if all(a <= b for a, b in zip(vj, vi)) and any(a < b for a, b in zip(vj, vi)):
                dominated = True
                break
        if not dominated:
            out.append(r)
    return out
