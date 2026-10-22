"""Command line interface.

The agent is allowed to call exactly two commands that change state:
`trial` (propose and measure) and nothing else. `promote` is a human or a
separately-authorized adjudicator role, because promotion is the step where
the goal, not the surrogate, decides.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import families, ladder
from .canary import run_canaries, uncovered
from .gate import GateState, decide, pareto_frontier, settle_debt
from .ledger import Ledger, State
from .model import (
    BLOCKING_HALTS,
    Contract,
    DebtEntry,
    Measurement,
    Trial,
    Verdict,
)
from .sandbox import EvalError, hash_paths, run_eval

DEFAULT_STATE = ".ratchet"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(args) -> tuple[Contract, State, Ledger]:
    contract_path = Path(args.contract)
    if not contract_path.exists():
        sys.exit(f"no contract at {contract_path}; the goal must be declared before anything is measured")
    contract = Contract.load(contract_path)
    state = State.load(args.state)
    ledger = Ledger(args.state)
    ledger.ensure()
    if not state.contract_digest:
        state.contract_digest = contract.digest()
    elif state.contract_digest != contract.digest():
        print(
            f"WARNING: contract changed ({state.contract_digest} -> {contract.digest()}). "
            "Results recorded before this point were produced under a different goal.",
            file=sys.stderr,
        )
    return contract, state, ledger


def _gate_state(contract: Contract, state: State, family: str) -> GateState:
    return GateState(
        trial_ordinal=state.trial_ordinal,
        holdout_reads_used=state.holdout_reads_used,
        open_debt=[DebtEntry(**d) for d in state.open_debt],
        family_counts=state.family_counts,
        blind_spots=state.blind_spots,
        halted=state.halted,
        halt_reason=state.halt_reason,
        unadjudicated=state.unadjudicated,
        under_explored=families.under_explored(contract, state.family_counts, family),
    )


# ---------------------------------------------------------------- commands


def cmd_init(args) -> int:
    contract, state, ledger = _load(args)
    print(f"goal      : {contract.goal}")
    print(f"metric    : {contract.metric.name} ({contract.metric.direction.value})")
    print(f"contract  : {contract.digest()}")
    print(f"guards    : {len(contract.guards)}  canaries: {len(contract.canaries)}")
    print(f"families  : {', '.join(contract.families)}")
    if contract.complexity_price == 0.0:
        print("WARNING: complexity_price is 0; gratuitous complexity is free. "
              "The ratchet will buy metric points with code.", file=sys.stderr)
    if not contract.canaries:
        print("WARNING: no canaries declared; the metric is uncalibrated. "
              "The gate cannot know whether it is blind.", file=sys.stderr)
    if contract.seed_replicates < 3:
        print("WARNING: seed_replicates < 3; no noise floor can be estimated.", file=sys.stderr)
    state.contract_digest = contract.digest()
    state.save(args.state)
    ledger.ensure()
    print(f"state     : {Path(args.state).resolve()}")
    return 0


def cmd_baseline(args) -> int:
    contract, state, ledger = _load(args)
    try:
        meas = run_eval(args.cmd, timeout_s=args.timeout, cwd=args.cwd)
    except EvalError as exc:
        print(f"baseline failed: {exc}", file=sys.stderr)
        return 1
    if len(meas.seeds) < 3:
        print(
            f"refusing: baseline has {len(meas.seeds)} seed(s). Re-run with >= 3 to "
            "establish a noise floor before any comparison is made.",
            file=sys.stderr,
        )
        return 1
    state.baseline = meas.to_dict()
    state.baseline_commit = args.commit or "unknown"
    state.save(args.state)
    print(f"baseline  : {contract.metric.name} = {meas.value:.6f} over {len(meas.seeds)} seeds")

    # The canonical baseline lives on a DIFFERENT split. Recording the
    # surrogate number here and later comparing a holdout number against it
    # would subtract one instrument's reading from another's.
    if args.canonical_cmd:
        try:
            goal = run_eval(args.canonical_cmd, timeout_s=args.timeout, cwd=args.cwd)
        except EvalError as exc:
            print(f"canonical baseline failed: {exc}", file=sys.stderr)
            return 1
        if len(goal.seeds) < 3:
            print("refusing: canonical baseline needs >= 3 seeds", file=sys.stderr)
            return 1
        state.canonical = goal.to_dict()
        state.canonical_commit = args.commit or "unknown"
        print(f"canonical : {goal.value:.6f} over {len(goal.seeds)} seeds (adjudicated split)")
    else:
        state.canonical = None
        state.canonical_commit = ""
        print("canonical : NOT SET. Pass --canonical-cmd; without it no promotion can be adjudicated.")
    state.save(args.state)
    return 0


def cmd_canary(args) -> int:
    contract, state, ledger = _load(args)
    base = state.baseline_measurement
    if base is None:
        print("no baseline yet; run `ratchet baseline` first", file=sys.stderr)
        return 1
    results = run_canaries(contract, base.value, cwd=args.cwd, timeout_s=args.timeout)
    state.canary_results = [
        {
            "name": r.name, "passed": r.passed, "measured": r.measured,
            "baseline": r.baseline, "separation": r.separation,
            "failure_mode": r.failure_mode, "covered_by": r.covered_by,
        }
        for r in results
    ]
    state.blind_spots = [r.name for r in results if not r.passed]

    enabled = {"holdout"} if contract.holdout_cmd else set()
    gaps = uncovered(results, enabled)

    print(f"{'canary':<22}{'expected':<10}{'measured':>12}{'baseline':>12}  verdict")
    for r in results:
        mark = "PASS" if r.passed else f"BLIND ({r.covered_by or 'UNCOVERED'})"
        print(f"{r.name:<22}{r.expectation:<10}{r.measured:>12.6f}{r.baseline:>12.6f}  {mark}")

    if gaps:
        state.halted = True
        state.halt_reason = "metric is blind to " + ", ".join(g.name for g in gaps) + " with no adjudicator"
        state.save(args.state)
        print(f"\nHALT: {state.halt_reason}", file=sys.stderr)
        return 2

    state.save(args.state)
    if state.blind_spots:
        print(
            f"\nMetric is blind to {len(state.blind_spots)} failure mode(s); "
            "every promotion now requires a sealed-holdout read."
        )
    else:
        print("\nMetric separates every canary. Surrogate may judge the screen tier alone.")
    return 0


def cmd_trial(args) -> int:
    contract, state, ledger = _load(args)
    if state.halted:
        print(f"HALTED: {state.halt_reason}", file=sys.stderr)
        return 2

    tid = args.trial_id or f"t{state.trial_ordinal + 1:03d}"
    trial = Trial(
        id=tid,
        commit=args.commit,
        family=args.family,
        hypothesis=args.hypothesis,
        predicted_direction=args.predicted,
        created=_now(),
        contract_digest=contract.digest(),
    )

    crashed = False
    try:
        meas = run_eval(args.cmd, timeout_s=args.timeout, cwd=args.cwd)
        if args.loc_delta is not None:
            meas.loc_delta = args.loc_delta
        meas.meta.setdefault("predicted_direction", args.predicted)
        meas.meta.setdefault("contract_digest", contract.digest())
        trial.measurement = meas
    except EvalError as exc:
        crashed = True
        trial.reason = str(exc)[:400]
        trial.measurement = Measurement(value=0.0, seeds=(), loc_delta=0)
        state.consecutive_crashes += 1

    base = state.baseline_measurement
    if base is None:
        print("no baseline yet; run `ratchet baseline` first", file=sys.stderr)
        return 1

    gs = _gate_state(contract, state, args.family)
    decision = decide(contract, base, trial.measurement, gs, crashes=crashed)

    if not crashed:
        state.consecutive_crashes = 0

    trial.verdict = decision.verdict.value
    trial.reason = decision.reason
    trial.checks = decision.checks
    ledger.append(trial, decision.verdict.value, decision.gain, decision.ci, contract)
    state.record(trial, args.family, decision.gain)

    if decision.verdict is Verdict.ACCEPT:
        state.baseline = trial.measurement.to_dict()
        state.baseline_commit = trial.commit
        if contract.holdout_cmd:
            state.unadjudicated += 1
    elif decision.verdict is Verdict.ACCEPT_DEBT:
        state.baseline = trial.measurement.to_dict()
        state.baseline_commit = trial.commit
        state.open_debt.append(
            DebtEntry(
                trial_id=tid,
                commit=trial.commit,
                opened_at=state.trial_ordinal,
                deadline=state.trial_ordinal + contract.debt_repay_window,
                magnitude=decision.gain,
            ).__dict__
        )

    expired = settle_debt(contract, gs, decision.gain, state.trial_ordinal)
    if expired:
        state.open_debt = [d for d in state.open_debt if d["trial_id"] not in expired]
        print(f"DEBT EXPIRED: {', '.join(expired)} -> roll back to {state.canonical_commit}", file=sys.stderr)

    if state.consecutive_crashes >= contract.max_consecutive_crashes:
        state.halted = True
        state.halt_reason = f"{state.consecutive_crashes} consecutive crashes"

    state.save(args.state)

    print(f"[{tid}] {decision.verdict.value.upper():<16} {decision.reason}")
    if args.json:
        print(json.dumps(decision.to_dict(), indent=2))
    if decision.verdict in BLOCKING_HALTS or state.halted:
        return 2
    return 0


def cmd_promote(args) -> int:
    contract, state, ledger = _load(args)
    if not contract.holdout_cmd:
        print("no adjudication.holdout_cmd in the contract; nothing can adjudicate", file=sys.stderr)
        return 1
    if state.holdout_reads_used >= contract.holdout_budget:
        state.halted = True
        state.halt_reason = f"holdout budget exhausted ({contract.holdout_budget} reads)"
        state.save(args.state)
        print(f"HALT: {state.halt_reason}. Mint a new holdout before continuing.", file=sys.stderr)
        return 2

    trial = next((t for t in state.trials if t["id"] == args.trial), None)
    if trial is None:
        print(f"unknown trial {args.trial}", file=sys.stderr)
        return 1
    if trial["verdict"] not in ("accept", "accept_debt"):
        print(f"trial {args.trial} was not accepted ({trial['verdict']}); nothing to promote", file=sys.stderr)
        return 1

    template = args.holdout_cmd or contract.holdout_cmd
    if args.holdout_cmd:
        holdout_cmd = args.holdout_cmd
    else:
        if "{candidate}" not in template:
            print(
                "refusing to adjudicate: the contract's holdout_cmd has no "
                "`{candidate}` placeholder, so it would score a fixed model and "
                "approve anything. Pass --holdout-cmd or fix the contract.",
                file=sys.stderr,
            )
            return 1
        holdout_cmd = template.replace("{candidate}", trial["commit"])

    try:
        holdout = run_eval(holdout_cmd, timeout_s=args.timeout, cwd=args.cwd)
    except EvalError as exc:
        print(f"holdout failed: {exc}", file=sys.stderr)
        return 1
    state.holdout_reads_used += 1

    canonical = state.canonical_measurement
    if canonical is None:
        print(
            "no adjudicated canonical baseline. Run `ratchet baseline --canonical-cmd ...` "
            "so there is something to compare the holdout against.",
            file=sys.stderr,
        )
        return 1
    ok, reason = ladder.adjudicate(contract, holdout, canonical)

    state.promotions.append({
        "trial": args.trial, "commit": trial["commit"],
        "holdout_value": holdout.value, "ok": ok, "reason": reason,
    })

    if ok:
        state.canonical = holdout.to_dict()
        state.canonical_commit = trial["commit"]
        state.unadjudicated = 0
        print(f"PROMOTED {args.trial}: {reason}")
    else:
        print(f"PROMOTION DENIED {args.trial}: {reason}", file=sys.stderr)
        # Restore the working baseline to the dev-split measurement of the
        # canonical commit. Leaving the rejected candidate in place would make
        # `status` report a baseline the run has already decided against.
        restored = next(
            (t for t in state.trials if t["commit"] == state.canonical_commit),
            None,
        )
        if restored is not None:
            state.baseline = restored["measurement"]
            state.baseline_commit = state.canonical_commit
        else:
            print(
                f"working baseline not restorable from the ledger; "
                f"run: git reset --hard {state.canonical_commit}",
                file=sys.stderr,
            )
        print(f"roll back the working branch: git reset --hard {state.canonical_commit}", file=sys.stderr)
        state.unadjudicated = 0

    print(f"holdout reads used: {state.holdout_reads_used}/{contract.holdout_budget}")
    state.save(args.state)
    return 0 if ok else 1


def cmd_status(args) -> int:
    contract, state, _ = _load(args)
    base = state.baseline_measurement
    canon = state.canonical_measurement
    print(f"goal        : {contract.goal}")
    print(f"metric      : {contract.metric.name} ({contract.metric.direction.value})")
    print(f"trials      : {state.trial_ordinal} / {contract.max_trials}")
    print(f"baseline    : {base.value:.6f} @ {state.baseline_commit}" if base else "baseline    : none")
    print(f"canonical   : {canon.value:.6f} @ {state.canonical_commit}" if canon else "canonical   : none")
    print(f"unadjudged  : {state.unadjudicated} / {contract.max_unadjudicated}")
    print(f"holdout     : {state.holdout_reads_used} / {contract.holdout_budget} reads")
    print(f"open debt   : {len(state.open_debt)}")
    print(f"blind spots : {', '.join(state.blind_spots) or 'none detected'}")
    if state.halted:
        print(f"HALTED      : {state.halt_reason}")
    else:
        print(f"next family : {families.suggest(contract, state.family_counts, state.family_gains)}")
    return 0


def cmd_report(args) -> int:
    contract, state, ledger = _load(args)
    rows = ledger.rows()
    if not rows:
        print("no trials recorded")
        return 0

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    kept = counts.get("accept", 0) + counts.get("accept_debt", 0)

    print(f"goal    : {contract.goal}")
    print(f"trials  : {len(rows)}   kept: {kept}   rejected: {len(rows) - kept}")
    print("outcomes: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    canon = state.canonical_measurement
    work = state.baseline_measurement
    if canon:
        print(f"canonical: {canon.value:.6f} @ {state.canonical_commit}  (adjudicated split)")
    else:
        print("canonical: not yet adjudicated")
    if work:
        print(f"working  : {work.value:.6f} @ {state.baseline_commit}  "
              f"(surrogate split, {state.unadjudicated} unadjudicated)")
    print("note     : the two lines above are different instruments and are "
          "not subtracted from each other")

    if state.promotions:
        print("\nPromotion log (adjudicated):")
        for p in state.promotions:
            mark = "promoted" if p["ok"] else "denied  "
            print(f"  {p['trial']}  {p['commit'][:12]:<12}  {mark}  "
                  f"holdout={p['holdout_value']:.6f}  {p['reason'][:44]}")

    promoted = {p["commit"] for p in state.promotions if p["ok"]}
    pool = [r for r in rows if r["commit"] in promoted]
    if pool:
        front = pareto_frontier(
            [
                {
                    "value": float(r["value"]),
                    "peak_mem_mb": float(r["memory_mb"]),
                    "wall_s": float(r["wall_s"]),
                    "loc_delta": int(r["loc_delta"]),
                    "trial": r["trial"],
                    "hypothesis": r["hypothesis"],
                }
                for r in pool
            ],
            contract.metric.direction,
        )
        print("\nPareto archive over promoted trials (surrogate space):")
        for f in front:
            print(f"  {f['trial']}  {f['value']:.6f}  {f['peak_mem_mb']:.0f}MB  "
                  f"{f['wall_s']:.0f}s  {f['loc_delta']:+d} loc  {f['hypothesis'][:44]}")

    if state.blind_spots:
        print(f"\nMetric blindness on record: {', '.join(state.blind_spots)}")
    ok, msg = ledger.verify_chain()
    print(f"audit chain: {'OK' if ok else 'BROKEN'} ({msg})")
    return 0


def cmd_verify(args) -> int:
    _, _, ledger = _load(args)
    ok, msg = ledger.verify_chain()
    print(("OK: " if ok else "BROKEN: ") + msg)
    return 0 if ok else 1


def cmd_suggest(args) -> int:
    contract, state, _ = _load(args)
    fam = families.suggest(contract, state.family_counts, state.family_gains)
    s = families.scores(contract, state.family_counts, state.family_gains)
    print(f"next family: {fam}")
    for k, v in sorted(s.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<20}{v:.4f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ratchet", description=__doc__)
    p.add_argument("--state", default=DEFAULT_STATE, help="state directory")
    p.add_argument("--contract", default="contract.toml", help="path to the goal contract")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--state", default=argparse.SUPPRESS)
        sp.add_argument("--contract", default=argparse.SUPPRESS)
        sp.add_argument("--cwd", default=None)
        sp.add_argument("--timeout", type=float, default=1800.0)

    sp = sub.add_parser("init", help="validate the contract and create state")
    common(sp)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("baseline", help="record the incumbent measurement")
    common(sp)
    sp.add_argument("--cmd", required=True)
    sp.add_argument("--commit", default="")
    sp.add_argument("--canonical-cmd", default=None,
                    help="measure the incumbent on the adjudicated split")
    sp.set_defaults(func=cmd_baseline)

    sp = sub.add_parser("canary", help="calibrate the metric against known mutants")
    common(sp)
    sp.set_defaults(func=cmd_canary)

    sp = sub.add_parser("trial", help="evaluate one candidate and record the verdict")
    common(sp)
    sp.add_argument("--cmd", required=True)
    sp.add_argument("--commit", required=True)
    sp.add_argument("--family", default="default")
    sp.add_argument("--hypothesis", required=True)
    sp.add_argument("--predicted", default="better", choices=["better", "worse_first"])
    sp.add_argument("--loc-delta", type=int, default=None)
    sp.add_argument("--trial-id", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_trial)

    sp = sub.add_parser("promote", help="adjudicate an accepted trial on the sealed holdout")
    common(sp)
    sp.add_argument("--trial", required=True)
    sp.add_argument("--holdout-cmd", default="")
    sp.set_defaults(func=cmd_promote)

    sp = sub.add_parser("status", help="show run state")
    common(sp)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("report", help="summary, Pareto archive, audit chain")
    common(sp)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("verify", help="recompute the ledger hash chain")
    common(sp)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("suggest", help="next idea family by UCB")
    common(sp)
    sp.set_defaults(func=cmd_suggest)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
