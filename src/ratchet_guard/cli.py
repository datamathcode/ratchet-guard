"""Command line interface.

The agent is allowed to call exactly two commands that change state:
`trial` (propose and measure) and nothing else. `promote` is a human or a
separately-authorized adjudicator role, because promotion is the step where
the goal, not the surrogate, decides.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import stdev

from . import families, ladder
from .canary import run_canaries, uncovered
from .gate import GateState, decide, pareto_frontier, settle_debt
from .ledger import Ledger, State
from .model import (
    BLOCKING_HALTS,
    SEED_FLOOR,
    Contract,
    DebtEntry,
    Measurement,
    Trial,
    Verdict,
)
from .sandbox import EvalError, hash_paths, run_eval
from .stats import rel_sd_of_sd

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
        noise_var=state.noise_var,
        noise_df=state.noise_df,
        reference=state.reference,
        trials_since_reference=state.trials_since_reference,
        drift_detected=state.drift_detected,
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
    # The baseline is the reference for every delta in the run, so it must be a
    # mean of independent runs, not one draw. One cold-start measurement means
    # every later comparison inherits its error, and that error does not shrink
    # as the run continues.
    runs = max(1, args.runs)
    pooled: list[float] = []
    per_run: list[float] = []
    for i in range(runs):
        try:
            m = run_eval(args.cmd, timeout_s=args.timeout, cwd=args.cwd)
        except EvalError as exc:
            print(f"baseline run {i + 1}/{runs} failed: {exc}", file=sys.stderr)
            return 1
        pooled.extend(m.seeds)
        per_run.append(m.value)
        meas = m
    meas = Measurement(
        value=sum(pooled) / len(pooled),
        seeds=tuple(pooled),
        guards=meas.guards,
        wall_s=meas.wall_s,
        peak_mem_mb=meas.peak_mem_mb,
        loc_delta=meas.loc_delta,
        meta={**meas.meta, "runs": runs, "per_run": per_run},
    )
    required = max(SEED_FLOOR, contract.seed_replicates)
    if len(meas.seeds) < required:
        print(
            f"refusing: baseline has {len(meas.seeds)} seed(s) from {runs} run(s); "
            f"{required} are required. Pass --runs {required}.",
            file=sys.stderr,
        )
        return 1
    state.baseline = meas.to_dict()
    state.baseline_commit = args.commit or "unknown"
    state.canonical_dev = meas.to_dict()   # surrogate reading of the canonical commit
    state.update_noise(meas.seeds)
    state.save(args.state)
    print(f"baseline  : {contract.metric.name} = {meas.value:.6f} "
          f"over {len(meas.seeds)} seeds from {runs} run(s)")

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
    if not crashed:
        state.update_noise(trial.measurement.seeds)

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
    floor = state.noise_sd
    if floor and decision.verdict in (Verdict.REJECT_NOISE, Verdict.ACCEPT, Verdict.ACCEPT_DEBT):
        ratio = contract.min_delta / floor if floor else float("inf")
        if ratio < 2.0:
            print(f"  note: min_delta is {ratio:.2f}x the measured noise sd "
                  f"({floor:.6f}); at this ratio the ratchet records noise.",
                  file=sys.stderr)
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
        state.canonical_dev = trial["measurement"]
        state.unadjudicated = 0
        print(f"PROMOTED {args.trial}: {reason}")
    else:
        print(f"PROMOTION DENIED {args.trial}: {reason}", file=sys.stderr)
        # Restore the working baseline to the SURROGATE reading of the canonical
        # commit. Restoring the adjudicated reading would compare a holdout
        # number against surrogate numbers for the rest of the run.
        restored = state.canonical_dev
        if restored is None:
            restored = next(
                (t["measurement"] for t in state.trials
                 if t["commit"] == state.canonical_commit),
                None,
            )
        if restored is not None:
            state.baseline = restored
            state.baseline_commit = state.canonical_commit
            state.unadjudicated = 0
        else:
            print(
                "working baseline could not be restored automatically; "
                f"run: git reset --hard {state.canonical_commit}",
                file=sys.stderr,
            )
            state.unadjudicated = 0

    print(f"holdout reads used: {state.holdout_reads_used}/{contract.holdout_budget}")
    state.save(args.state)
    return 0 if ok else 1


def cmd_control(args) -> int:
    """Measure an unchanged configuration N times. This is the noise floor.

    A no-op trial cannot possibly improve, which is why nobody runs one -- and
    why it is the cheapest and most informative trial in the run. Without it
    every other statistic here is advice about a number you do not have.
    """
    contract, state, ledger = _load(args)
    runs = max(2, args.runs)
    seeds: list[float] = []
    means: list[float] = []
    for i in range(runs):
        try:
            m = run_eval(args.cmd, timeout_s=args.timeout, cwd=args.cwd)
        except EvalError as exc:
            print(f"control run {i + 1}/{runs} failed: {exc}", file=sys.stderr)
            return 1
        seeds.extend(m.seeds)
        means.append(m.value)

    sd = stdev(seeds) if len(seeds) > 1 else 0.0
    state.noise_var = sd ** 2
    state.noise_df = len(seeds) - 1
    state.reference = {
        "value": sum(seeds) / len(seeds),
        "sd": sd, "n": len(seeds), "at_trial": state.trial_ordinal,
    }
    state.trials_since_reference = 0
    state.drift_detected = False
    # Re-baselining is the documented remedy for a drift halt, so clear it --
    # but only a drift halt. A metric or budget halt is not cleared by a
    # measurement.
    if state.halted and "drifted" in (state.halt_reason or ""):
        state.halted = False
        state.halt_reason = ""
    state.save(args.state)

    print(f"reference : {contract.metric.name} = {state.reference['value']:.6f}")
    print(f"runs      : {runs} ({len(seeds)} seeds)")
    print(f"noise sd  : {sd:.6f}  (relative uncertainty of this estimate: "
          f"+/-{rel_sd_of_sd(len(seeds)):.0%})")
    print()
    print(f"  min_delta 1.0x sd = {sd:.6f}   acceptances below this are noise records")
    print(f"  min_delta 2.0x sd = {2 * sd:.6f}  ~92% one-sided (single comparison)")
    print(f"  min_delta 2.8x sd = {2.8 * sd:.6f}  ~97.5% one-sided (ratchet damper)")

    if contract.min_delta < sd:
        print(f"\nWARNING: contract min_delta {contract.min_delta:.6f} is below 1 sd. "
              "The gate will halt the run at the first trial until this is raised.",
              file=sys.stderr)
    elif contract.min_delta < 2 * sd:
        print(f"\nWARNING: contract min_delta {contract.min_delta:.6f} is below 2 sd "
              f"({2 * sd:.6f}). Expect a high false-accept rate.", file=sys.stderr)
    if len(seeds) < 10:
        print(f"\nNOTE: {len(seeds)} seeds gives a sd estimate that is itself "
              f"+/-{rel_sd_of_sd(len(seeds)):.0%}. The floor tightens automatically as "
              "trials accumulate; re-run `control` after ~10 trials.", file=sys.stderr)
    return 0


def cmd_drift(args) -> int:
    """Re-measure the reference config and compare it to the stored one.

    A shared GPU drifts: thermal throttling, memory fragmentation, other
    tenants. If the reference moved, the trials either side of it were not
    drawn from the same distribution, and no rank test in the ledger is valid.
    """
    contract, state, ledger = _load(args)
    if not state.reference:
        print("no reference recorded; run `ratchet control` first", file=sys.stderr)
        return 1
    try:
        m = run_eval(args.cmd, timeout_s=args.timeout, cwd=args.cwd)
    except EvalError as exc:
        print(f"drift check failed: {exc}", file=sys.stderr)
        return 1

    ref = state.reference
    n_new = max(1, len(m.seeds))
    n_old = max(1, ref["n"])
    se = math.sqrt((ref["sd"] ** 2) / n_old + (stdev(m.seeds) ** 2 if n_new > 1 else 0) / n_new)
    delta = m.value - ref["value"]
    z = (delta / se) if se > 0 else 0.0

    print(f"reference was {ref['value']:.6f} (sd {ref['sd']:.6f}, n={n_old}, "
          f"at trial {ref['at_trial']})")
    print(f"reference now {m.value:.6f}   delta {delta:+.6f}   z = {z:+.2f}")
    print(f"trials since last check: {state.trials_since_reference}")

    if abs(z) > args.threshold:
        state.drift_detected = True
        state.halted = True
        state.halt_reason = (
            f"reference configuration drifted {delta:+.6f} (z={z:+.2f}); "
            "trials either side of this are not comparable"
        )
        state.save(args.state)
        print(f"\nHALT: {state.halt_reason}", file=sys.stderr)
        print("Fix the machine, then re-run `control` to re-baseline and clear the halt.",
              file=sys.stderr)
        return 2

    state.trials_since_reference = 0
    state.drift_detected = False
    state.save(args.state)
    print("no drift detected")
    return 0


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
    sd = state.noise_sd
    if sd:
        print(f"noise floor : {sd:.6f} (df={state.noise_df}, "
              f"min_delta = {contract.min_delta / sd:.2f} sd)")
    else:
        print("noise floor : UNKNOWN — run `ratchet control`. No delta is quotable without it.")
    if state.reference:
        stale = state.trials_since_reference
        flag = "  <-- OVERDUE" if stale > contract.canary_every else ""
        print(f"reference   : {state.reference['value']:.6f} "
              f"({stale} trials since check){flag}")
    if state.drift_detected:
        print("drift       : DETECTED — trials are not exchangeable")
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
    sp.add_argument("--runs", type=int, default=3,
                    help="independent runs pooled into the baseline (default 3)")
    sp.add_argument("--canonical-cmd", default=None,
                    help="measure the incumbent on the adjudicated split")
    sp.set_defaults(func=cmd_baseline)

    sp = sub.add_parser("control", help="measure an unchanged config to get the noise floor")
    common(sp)
    sp.add_argument("--cmd", required=True)
    sp.add_argument("--runs", type=int, default=8)
    sp.set_defaults(func=cmd_control)

    sp = sub.add_parser("drift", help="re-measure the reference config and compare")
    common(sp)
    sp.add_argument("--cmd", required=True)
    sp.add_argument("--threshold", type=float, default=3.0)
    sp.set_defaults(func=cmd_drift)

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
