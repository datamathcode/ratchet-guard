"""Out-of-process evaluation and provenance checks.

The original loop trusts the agent to run the evaluator and report the
number. This module makes the evaluator something the agent cannot reach:
its own process, a timeout, a hash of the harness, and a JSON contract on
stdout.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from pathlib import Path

from .model import Measurement


class EvalError(RuntimeError):
    pass


def hash_paths(paths: list[str | Path]) -> str:
    """Content hash of the evaluation surface, order-independent."""
    h = hashlib.sha256()
    for p in sorted(Path(x) for x in paths):
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    h.update(f.relative_to(p.parent).as_posix().encode())
                    h.update(f.read_bytes())
        elif p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def run_eval(
    command: str,
    timeout_s: float = 1800.0,
    cwd: str | Path | None = None,
) -> Measurement:
    """Run an evaluator command and parse its final JSON line.

    The evaluator must print one JSON object per invocation:

        {"value": 0.931, "seeds": [0.93, 0.932],
         "guards": {"peak_mem_mb": 812.4},
         "wall_s": 300.1, "peak_mem_mb": 812.4, "loc_delta": 41}

    A non-zero exit, a timeout, or unparseable output is a crash, never a
    zero. Substituting a sentinel value for a failed run is how a broken
    experiment becomes a permanent design decision.
    """
    argv = shlex.split(command)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvalError(f"timeout after {timeout_s:.0f}s") from exc
    except FileNotFoundError as exc:
        raise EvalError(f"evaluator not found: {argv[0]}") from exc

    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-6:])
        raise EvalError(f"exit {proc.returncode}: {tail}")

    payload = _last_json_object(proc.stdout)
    if payload is None:
        raise EvalError("evaluator printed no JSON object")
    if "value" not in payload:
        raise EvalError("evaluator JSON is missing `value`")
    payload.setdefault("seeds", [payload["value"]])
    return Measurement.from_dict(payload)


def _last_json_object(text: str) -> dict | None:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def git_head(repo: str | Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def dirty_paths(repo: str | Path, paths: list[str]) -> list[str]:
    """Tracked-file modifications inside the protected evaluation surface."""
    if not paths:
        return []
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--"] + paths,
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln[3:] for ln in out.stdout.splitlines() if ln.strip()]
