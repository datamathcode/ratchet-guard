"""Run state and an append-only ledger.

Two rules, both learned from breaking them:

1. The ledger is written by the harness, never by the agent. If the thing
   that proposes the change also records the score, there is no measurement.
2. The ledger is append-only and hash-chained. No code path may regenerate it
   from another source: a rebuild silently destroys hand-written notes and
   mis-fills fields whose key is not unique.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .model import Contract, DebtEntry, Measurement, Trial

TSV_HEADER = (
    "trial\tcommit\tfamily\tverdict\tvalue\tgain\tci_low\tci_high\t"
    "seeds\tmemory_mb\twall_s\tloc_delta\thypothesis"
)


@dataclass
class State:
    contract_digest: str = ""
    trial_ordinal: int = 0
    trials: list[dict] = field(default_factory=list)
    baseline: dict | None = None
    baseline_commit: str = ""
    canonical: dict | None = None
    canonical_commit: str = ""
    holdout_reads_used: int = 0
    open_debt: list[dict] = field(default_factory=list)
    family_counts: dict[str, int] = field(default_factory=dict)
    family_gains: dict[str, list[float]] = field(default_factory=dict)
    canary_results: list[dict] = field(default_factory=list)
    promotions: list[dict] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)
    unadjudicated: int = 0
    consecutive_crashes: int = 0
    halted: bool = False
    halt_reason: str = ""

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls, state_dir: str | Path) -> "State":
        p = Path(state_dir) / "state.json"
        if not p.exists():
            return cls()
        return cls(**json.loads(p.read_text()))

    def save(self, state_dir: str | Path) -> None:
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, d / "state.json")

    # -- accessors ---------------------------------------------------------
    @property
    def baseline_measurement(self) -> Measurement | None:
        return Measurement.from_dict(self.baseline) if self.baseline else None

    @property
    def canonical_measurement(self) -> Measurement | None:
        return Measurement.from_dict(self.canonical) if self.canonical else None

    def record(self, trial: Trial, family: str, gain: float) -> None:
        self.trials.append(trial.to_dict())
        self.trial_ordinal += 1
        self.family_counts[family] = self.family_counts.get(family, 0) + 1
        self.family_gains.setdefault(family, []).append(gain)


class Ledger:
    """Append-only TSV for humans plus a hash-chained JSONL for audit."""

    def __init__(self, state_dir: str | Path) -> None:
        self.dir = Path(state_dir)
        self.tsv = self.dir / "results.tsv"
        self.audit = self.dir / "audit.jsonl"

    def ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.tsv.exists():
            self.tsv.write_text(TSV_HEADER + "\n")
        if not self.audit.exists():
            self.audit.write_text("")

    def _prev_hash(self) -> str:
        if not self.audit.exists():
            return "0" * 16
        last = ""
        for line in self.audit.read_text().splitlines():
            if line.strip():
                last = line
        if not last:
            return "0" * 16
        try:
            return json.loads(last)["hash"]
        except (json.JSONDecodeError, KeyError):
            return "0" * 16

    def append(self, trial: Trial, verdict: str, gain: float, ci: tuple[float, float], contract: Contract) -> None:
        self.ensure()
        m = trial.measurement
        if m is None:
            return
        row = "\t".join(
            [
                trial.id,
                trial.commit,
                trial.family,
                verdict,
                f"{m.value:.6f}",
                f"{gain:+.6f}",
                f"{ci[0]:+.6f}",
                f"{ci[1]:+.6f}",
                str(len(m.seeds)),
                f"{m.peak_mem_mb:.1f}",
                f"{m.wall_s:.1f}",
                str(m.loc_delta),
                trial.hypothesis.replace("\t", " ").replace("\n", " "),
            ]
        )
        with self.tsv.open("a") as f:
            f.write(row + "\n")

        prev = self._prev_hash()
        body = {
            "trial": trial.to_dict(),
            "verdict": verdict,
            "gain": gain,
            "ci": list(ci),
            "contract": contract.digest(),
            "prev": prev,
        }
        blob = json.dumps(body, sort_keys=True).encode()
        body["hash"] = hashlib.sha256(prev.encode() + blob).hexdigest()[:16]
        with self.audit.open("a") as f:
            f.write(json.dumps(body, sort_keys=True) + "\n")

    def verify_chain(self) -> tuple[bool, str]:
        """Recompute the hash chain. Any rewritten row breaks it."""
        prev = "0" * 16
        n = 0
        for line in self.audit.read_text().splitlines():
            if not line.strip():
                continue
            body = json.loads(line)
            claimed = body.pop("hash")
            recomputed = hashlib.sha256(prev.encode() + json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
            if recomputed != claimed or body["prev"] != prev:
                return False, f"chain broken at row {n}"
            prev = claimed
            n += 1
        return True, f"{n} rows verified"

    def rows(self) -> list[dict]:
        if not self.tsv.exists():
            return []
        lines = self.tsv.read_text().splitlines()
        if not lines:
            return []
        head = lines[0].split("\t")
        out = []
        for ln in lines[1:]:
            if not ln.strip():
                continue
            out.append(dict(zip(head, ln.split("\t"))))
        return out
