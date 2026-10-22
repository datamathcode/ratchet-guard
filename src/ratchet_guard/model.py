"""Core data model for ratchet-guard.

The contract is the machine-readable form of the research goal. It exists
because the original autoresearch loop encodes the goal in prose inside
`program.md`, where nothing can enforce it. Here the goal, the surrogate
metric, the guards, and the known blind spots are all declared, hashed, and
checked on every decision.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

SEED_FLOOR = 3
"""Minimum replicate seeds before any delta may be quoted.

Two seeds cannot estimate a noise floor: the range of two draws is the gap
between two arbitrary points, not a variance.
"""


class Direction(str, Enum):
    MIN = "min"
    MAX = "max"

    def sign(self) -> int:
        """Sign that turns (baseline, candidate) into a gain. Positive is better."""
        return 1.0 if self is Direction.MIN else -1.0


class Verdict(str, Enum):
    ACCEPT = "accept"
    ACCEPT_DEBT = "accept_debt"
    REJECT_NOISE = "reject_noise"
    REJECT_GUARD = "reject_guard"
    REJECT_VARIANCE = "reject_variance"
    REJECT_DOMINATED = "reject_dominated"
    REJECT_PROVENANCE = "reject_provenance"
    REJECT_BUDGET = "reject_budget"
    CRASH = "crash"
    HALT_METRIC = "halt_metric"
    HALT_HOLDOUT = "halt_holdout"


BLOCKING_HALTS = frozenset({Verdict.HALT_METRIC, Verdict.HALT_HOLDOUT})


@dataclass(frozen=True)
class MetricSpec:
    name: str
    direction: Direction
    unit: str = ""

    def better(self, a: float, b: float) -> bool:
        """True when `a` beats `b` on this metric."""
        return a < b if self.direction is Direction.MIN else a > b


@dataclass(frozen=True)
class GuardSpec:
    """A metric that must not degrade while the primary metric improves.

    `tolerance` is the largest acceptable relative degradation, e.g. 0.10
    means the guard metric may get 10% worse. This is the numeric form of
    Karpathy's prose line "VRAM is a soft constraint".
    """

    name: str
    direction: Direction
    tolerance: float


@dataclass(frozen=True)
class CanarySpec:
    """A reference mutant used to calibrate the surrogate metric.

    Each canary is a candidate whose true quality we already know. If the
    metric ranks it the wrong way, the metric is blind to that failure mode
    and must not be trusted alone.
    """

    name: str
    command: str
    expectation: str  # "worse" | "better"  -- relative to the live baseline
    separation: float
    covered_by: str = ""  # which adjudicator covers this blind spot
    failure_mode: str = ""


@dataclass(frozen=True)
class Contract:
    goal: str
    metric: MetricSpec
    guards: tuple[GuardSpec, ...] = ()
    min_delta: float = 0.0
    complexity_price: float = 0.0
    canaries: tuple[CanarySpec, ...] = ()
    families: tuple[str, ...] = ("default",)
    seed_replicates: int = SEED_FLOOR
    exploration_fraction: float = 0.15
    debt_repay_window: int = 12
    holdout_budget: int = 20
    holdout_cmd: str = ""
    max_trials: int = 400
    max_consecutive_crashes: int = 5
    canary_every: int = 25
    variance_inflation_tolerance: float = 2.0
    max_unadjudicated: int = 5
    raw: dict[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        """Stable hash of the decision-relevant fields.

        Recorded in every ledger row so a run can be audited against the
        contract that was in force when it produced that row.
        """
        payload = {
            "goal": self.goal,
            "metric": [self.metric.name, self.metric.direction.value],
            "guards": sorted([g.name, g.direction.value, g.tolerance] for g in self.guards),
            "min_delta": self.min_delta,
            "complexity_price": self.complexity_price,
            "canaries": sorted([c.name, c.expectation, c.separation] for c in self.canaries),
            "holdout_budget": self.holdout_budget,
        }
        blob = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    @classmethod
    def load(cls, path: str | Path) -> "Contract":
        raw = tomllib.loads(Path(path).read_text())
        if "goal" not in raw:
            raise ValueError("contract is missing the `goal` field; a metric without a goal is the bug this tool exists to prevent")
        m = raw["metric"]
        metric = MetricSpec(
            name=m["name"],
            direction=Direction(m.get("direction", "min")),
            unit=m.get("unit", ""),
        )
        guards = tuple(
            GuardSpec(
                name=g["name"],
                direction=Direction(g.get("direction", "min")),
                tolerance=float(g.get("tolerance", 0.0)),
            )
            for g in raw.get("guards", [])
        )
        canaries = tuple(
            CanarySpec(
                name=c["name"],
                command=c["command"],
                expectation=c.get("expectation", "worse"),
                separation=float(c.get("separation", 0.0)),
                covered_by=c.get("covered_by", ""),
                failure_mode=c.get("failure_mode", ""),
            )
            for c in raw.get("canaries", [])
        )
        adjudication = raw.get("adjudication", {})
        return cls(
            goal=raw["goal"],
            metric=metric,
            guards=guards,
            min_delta=float(raw.get("min_delta", 0.0)),
            complexity_price=float(raw.get("complexity_price", 0.0)),
            canaries=canaries,
            families=tuple(raw.get("families", ["default"])),
            seed_replicates=int(raw.get("seed_replicates", SEED_FLOOR)),
            exploration_fraction=float(raw.get("exploration_fraction", 0.15)),
            debt_repay_window=int(raw.get("debt_repay_window", 12)),
            holdout_budget=int(adjudication.get("holdout_budget", 20)),
            holdout_cmd=adjudication.get("holdout_cmd", ""),
            max_trials=int(raw.get("max_trials", 400)),
            max_consecutive_crashes=int(raw.get("max_consecutive_crashes", 5)),
            canary_every=int(raw.get("canary_every", 25)),
            variance_inflation_tolerance=float(raw.get("variance_inflation_tolerance", 2.0)),
            max_unadjudicated=int(raw.get("max_unadjudicated", 5)),
            raw=raw,
        )


@dataclass
class Measurement:
    """One evaluated candidate."""

    value: float
    seeds: tuple[float, ...] = ()
    guards: dict[str, float] = field(default_factory=dict)
    wall_s: float = 0.0
    peak_mem_mb: float = 0.0
    loc_delta: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Measurement":
        return cls(
            value=float(d["value"]),
            seeds=tuple(float(x) for x in d.get("seeds", [])),
            guards=dict(d.get("guards", {})),
            wall_s=float(d.get("wall_s", 0.0)),
            peak_mem_mb=float(d.get("peak_mem_mb", 0.0)),
            loc_delta=int(d.get("loc_delta", 0)),
            meta=dict(d.get("meta", {})),
        )


@dataclass
class Trial:
    id: str
    commit: str
    family: str
    hypothesis: str
    predicted_direction: str = "better"  # "better" | "worse_first"
    predicted_magnitude: float = 0.0
    measurement: Measurement | None = None
    verdict: str = ""
    reason: str = ""
    checks: dict[str, Any] = field(default_factory=dict)
    created: str = ""
    contract_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class DebtEntry:
    """A provisionally accepted change that has to pay for itself."""

    trial_id: str
    commit: str
    opened_at: int  # trial ordinal
    deadline: int
    magnitude: float
    repaid: bool = False


@dataclass
class CanaryResult:
    name: str
    expectation: str
    measured: float
    baseline: float
    separation: float
    passed: bool
    failure_mode: str = ""
    covered_by: str = ""
