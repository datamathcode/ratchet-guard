"""ratchet-guard: a metric-aware autoresearch ratchet."""

from .gate import Decision, GateState, decide, effect, pareto_frontier
from .ledger import Ledger, State
from .model import (
    CanarySpec,
    Contract,
    Direction,
    GuardSpec,
    Measurement,
    MetricSpec,
    Trial,
    Verdict,
)

__version__ = "0.1.0"

__all__ = [
    "CanarySpec",
    "Contract",
    "Decision",
    "Direction",
    "GateState",
    "GuardSpec",
    "Ledger",
    "Measurement",
    "MetricSpec",
    "State",
    "Trial",
    "Verdict",
    "decide",
    "effect",
    "pareto_frontier",
]
