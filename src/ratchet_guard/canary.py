"""The canary suite: a test of the instrument, not of the candidate.

Karpathy's loop spends every trial asking "is this better?" and never asks
"is this still the right question?". A canary answers the second question
by measuring candidates whose true quality is already known.

If the surrogate metric ranks a known-bad mutant above the live baseline,
the metric is blind to that failure mode. The suite does not fix the metric.
It records the blindness, and the gate then refuses to accept any change on
that metric's word alone.
"""

from __future__ import annotations

from .model import CanaryResult, CanarySpec, Contract
from .sandbox import EvalError, run_eval


def run_canaries(
    contract: Contract,
    baseline_value: float,
    cwd: str | None = None,
    timeout_s: float = 1800.0,
) -> list[CanaryResult]:
    """Measure every canary and record whether the metric ranked it correctly."""
    results: list[CanaryResult] = []
    for spec in contract.canaries:
        try:
            meas = run_eval(spec.command, timeout_s=timeout_s, cwd=cwd)
        except EvalError as exc:
            results.append(
                CanaryResult(
                    name=spec.name,
                    expectation=spec.expectation,
                    measured=float("nan"),
                    baseline=baseline_value,
                    separation=spec.separation,
                    passed=False,
                    failure_mode=f"canary did not run: {exc}",
                    covered_by=spec.covered_by,
                )
            )
            continue
        results.append(judge_canary(spec, meas.value, baseline_value))
    return results


def judge_canary(spec: CanarySpec, measured: float, baseline_value: float) -> CanaryResult:
    sign = 1.0 if spec.expectation == "worse" else -1.0
    # Positive margin means the canary landed where the metric should have put it.
    margin = sign * (baseline_value - measured)
    passed = margin >= spec.separation
    return CanaryResult(
        name=spec.name,
        expectation=spec.expectation,
        measured=measured,
        baseline=baseline_value,
        separation=spec.separation,
        passed=passed,
        failure_mode="" if passed else (spec.failure_mode or "metric ranked the canary incorrectly"),
        covered_by=spec.covered_by,
    )


def blind_spots(results: list[CanaryResult]) -> list[CanaryResult]:
    return [r for r in results if not r.passed]


def uncovered(results: list[CanaryResult], enabled_adjudicators: set[str]) -> list[CanaryResult]:
    """Blind spots with no declared, enabled adjudicator behind them.

    A blind spot nobody covers is not a caveat. It is a stop condition.
    """
    out = []
    for r in blind_spots(results):
        cov = (r.covered_by or "").strip()
        if not cov or cov not in enabled_adjudicators:
            out.append(r)
    return out
