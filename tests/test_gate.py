import unittest

from ratchet_guard.gate import GateState, decide, pareto_frontier
from ratchet_guard.model import (
    Contract,
    Direction,
    GuardSpec,
    Measurement,
    MetricSpec,
    Verdict,
)


def contract(**kw) -> Contract:
    base = dict(
        goal="maximise quality on unseen data",
        metric=MetricSpec("q", Direction.MAX),
        min_delta=0.005,
        complexity_price=0.0,
        seed_replicates=4,
        families=("a", "b"),
        holdout_cmd="true",
        max_unadjudicated=5,
    )
    base.update(kw)
    return Contract(**base)


def m(value, seeds=None, guards=None, loc=0, meta=None):
    if seeds is None:
        seeds = [value - 0.015, value - 0.005, value + 0.005, value + 0.015]
    return Measurement(
        value=value,
        seeds=tuple(seeds),
        guards=guards or {},
        loc_delta=loc,
        meta=meta or {},
    )


BASE = m(0.80, [0.79, 0.795, 0.805, 0.81])


class TestAccept(unittest.TestCase):
    def test_clear_improvement_is_accepted(self):
        d = decide(contract(), BASE, m(0.90, [0.885, 0.895, 0.905, 0.915]), GateState())
        self.assertIs(d.verdict, Verdict.ACCEPT)
        self.assertTrue(d.accepted)

    def test_reason_quotes_the_interval(self):
        d = decide(contract(), BASE, m(0.90, [0.885, 0.895, 0.905, 0.915]), GateState())
        self.assertIn("CI", d.reason)


class TestReplicateFloor(unittest.TestCase):
    def test_improvement_below_min_delta_is_rejected(self):
        d = decide(contract(min_delta=0.05), BASE, m(0.82), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)

    def test_three_seeds_cannot_support_a_rank_test(self):
        """The smallest two-sided Mann-Whitney p at n=3 per arm is 0.10, so no
        n=3 comparison can clear alpha=0.05 whatever the effect size."""
        d = decide(contract(), BASE, m(0.95, [0.94, 0.95, 0.96]), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)
        self.assertIn("4", d.reason)

    def test_two_seeds_are_refused(self):
        d = decide(contract(), BASE, m(0.95, [0.94, 0.96]), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)

    def test_regression_is_rejected(self):
        d = decide(contract(), BASE, m(0.60), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)


class TestGuardGate(unittest.TestCase):
    def test_degraded_guard_rejects_a_big_metric_win(self):
        c = contract(guards=(GuardSpec("mem", Direction.MIN, 0.25),))
        base = m(0.80, guards={"mem": 200.0})
        cand = m(0.95, [0.935, 0.945, 0.955, 0.965], guards={"mem": 400.0})
        d = decide(c, base, cand, GateState())
        self.assertIs(d.verdict, Verdict.REJECT_GUARD)
        self.assertIn("mem", d.reason)

    def test_guard_within_tolerance_passes(self):
        c = contract(guards=(GuardSpec("mem", Direction.MIN, 0.25),))
        base = m(0.80, guards={"mem": 200.0})
        cand = m(0.95, [0.935, 0.945, 0.955, 0.965], guards={"mem": 220.0})
        d = decide(c, base, cand, GateState())
        self.assertIs(d.verdict, Verdict.ACCEPT)

    def test_unreported_guard_is_a_reject(self):
        c = contract(guards=(GuardSpec("mem", Direction.MIN, 0.25),))
        base = m(0.80, guards={"mem": 200.0})
        cand = m(0.95, [0.935, 0.945, 0.955, 0.965], guards={})
        d = decide(c, base, cand, GateState())
        self.assertIs(d.verdict, Verdict.REJECT_GUARD)


class TestOverheadGate(unittest.TestCase):
    def test_work_moved_outside_the_budget_is_rejected(self):
        cand = m(0.95, [0.935, 0.945, 0.955, 0.965])
        cand.wall_s = 600.0
        cand.meta["work_s"] = 300.0          # half the wall clock unscored
        d = decide(contract(wall_overhead_cap=0.10), BASE, cand, GateState())
        self.assertIs(d.verdict, Verdict.REJECT_OVERHEAD)
        self.assertAlmostEqual(d.checks["overhead"]["fraction"], 0.5)

    def test_overhead_within_cap_passes(self):
        cand = m(0.95, [0.935, 0.945, 0.955, 0.965])
        cand.wall_s = 320.0
        cand.meta["work_s"] = 300.0
        d = decide(contract(wall_overhead_cap=0.10), BASE, cand, GateState())
        self.assertIs(d.verdict, Verdict.ACCEPT)

    def test_absent_work_time_is_skipped_not_failed(self):
        cand = m(0.95, [0.935, 0.945, 0.955, 0.965])
        cand.wall_s = 600.0
        d = decide(contract(wall_overhead_cap=0.10), BASE, cand, GateState())
        self.assertIs(d.verdict, Verdict.ACCEPT)
        self.assertNotIn("overhead", d.checks)


class TestVarianceGate(unittest.TestCase):
    def test_fragile_win_is_rejected(self):
        d = decide(contract(variance_inflation_tolerance=2.0), BASE,
                   m(0.95, [0.88, 0.94, 0.96, 0.99]), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_VARIANCE)


class TestComplexityPrice(unittest.TestCase):
    def test_price_converts_a_marginal_win_into_a_loss(self):
        c = contract(min_delta=0.001, complexity_price=0.001)
        d = decide(c, BASE, m(0.82, [0.805, 0.815, 0.825, 0.835], loc=20), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)
        self.assertAlmostEqual(d.checks["effect"]["complexity_cost"], 0.02)

    def test_deleting_code_is_a_discount(self):
        c = contract(min_delta=0.001, complexity_price=0.001)
        d = decide(c, BASE, m(0.80, [0.785, 0.795, 0.805, 0.815], loc=-30), GateState())
        self.assertLess(d.checks["effect"]["complexity_cost"], 0)


class TestProvenanceGate(unittest.TestCase):
    def test_measurement_from_another_contract_is_refused(self):
        cand = m(0.90, [0.885, 0.895, 0.905, 0.915])
        cand.meta["contract_digest"] = "deadbeefdeadbeef"
        d = decide(contract(), BASE, cand, GateState())
        self.assertIs(d.verdict, Verdict.REJECT_PROVENANCE)


class TestHalts(unittest.TestCase):
    def test_halt_short_circuits_everything(self):
        st = GateState(halted=True, halt_reason="metric blind to memorisation")
        d = decide(contract(), BASE, m(0.99), st)
        self.assertIs(d.verdict, Verdict.HALT_METRIC)
        self.assertIn("blind", d.reason)

    def test_crash_is_not_a_zero(self):
        d = decide(contract(), BASE, Measurement(value=0.0, seeds=()), GateState(), crashes=True)
        self.assertIs(d.verdict, Verdict.CRASH)

    def test_drift_halts_the_run(self):
        st = GateState(drift_detected=True)
        d = decide(contract(), BASE, m(0.99), st)
        self.assertIs(d.verdict, Verdict.HALT_DRIFT)

    def test_min_delta_below_the_measured_noise_halts(self):
        st = GateState(noise_var=0.04, noise_df=12)   # sd 0.2
        d = decide(contract(min_delta=0.005), BASE, m(0.99), st)
        self.assertIs(d.verdict, Verdict.HALT_CONFIG)
        self.assertIn("noise floor", d.reason)

    def test_min_delta_above_the_noise_floor_proceeds(self):
        st = GateState(noise_var=0.0001, noise_df=12)  # sd 0.01
        d = decide(contract(min_delta=0.02), BASE,
                   m(0.90, [0.885, 0.895, 0.905, 0.915]), st)
        self.assertIs(d.verdict, Verdict.ACCEPT)


class TestPairedComparison(unittest.TestCase):
    def test_interleaved_reference_is_compared_paired(self):
        # The candidate's absolute level includes whatever the machine was
        # doing, but it still beats a reference measured in the same session.
        cand = m(0.86, [0.855, 0.858, 0.862, 0.865],
                 meta={"reference_seeds": [0.845, 0.848, 0.852, 0.855]})
        d = decide(contract(), BASE, cand, GateState())
        self.assertTrue(d.checks["effect"]["paired"])
        self.assertIs(d.verdict, Verdict.ACCEPT)

    def test_mismatched_reference_length_falls_back_to_unpaired(self):
        cand = m(0.90, [0.885, 0.895, 0.905, 0.915],
                 meta={"reference_seeds": [0.88, 0.89]})
        d = decide(contract(), BASE, cand, GateState())
        self.assertFalse(d.checks["effect"]["paired"])


class TestQueueGate(unittest.TestCase):
    def test_too_many_unadjudicated_accepts_is_refused(self):
        d = decide(contract(max_unadjudicated=1), BASE,
                   m(0.90, [0.885, 0.895, 0.905, 0.915]),
                   GateState(unadjudicated=1))
        self.assertIs(d.verdict, Verdict.REJECT_BUDGET)


class TestDebt(unittest.TestCase):
    def test_worse_first_is_allowed_in_an_under_explored_family(self):
        cand = m(0.78, [0.765, 0.775, 0.785, 0.795])
        cand.meta["predicted_direction"] = "worse_first"
        d = decide(contract(), BASE, cand, GateState(under_explored=True))
        self.assertIs(d.verdict, Verdict.ACCEPT_DEBT)

    def test_worse_first_is_refused_in_a_saturated_family(self):
        cand = m(0.78, [0.765, 0.775, 0.785, 0.795])
        cand.meta["predicted_direction"] = "worse_first"
        d = decide(contract(), BASE, cand, GateState(under_explored=False))
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)

    def test_debt_budget_is_finite(self):
        cand = m(0.78, [0.765, 0.775, 0.785, 0.795])
        cand.meta["predicted_direction"] = "worse_first"
        st = GateState(under_explored=True)
        st.open_debt = [object()] * 10
        d = decide(contract(exploration_fraction=0.1, max_trials=10), BASE, cand, st)
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)

    def test_holdout_is_required_when_the_metric_is_blind(self):
        d = decide(contract(), BASE, m(0.90, [0.885, 0.895, 0.905, 0.915]),
                   GateState(blind_spots=["memorizer"]))
        self.assertTrue(d.requires_holdout)


class TestPareto(unittest.TestCase):
    def test_dominated_rows_are_dropped(self):
        rows = [
            {"value": 0.90, "peak_mem_mb": 100, "wall_s": 10, "loc_delta": 5},
            {"value": 0.95, "peak_mem_mb": 100, "wall_s": 10, "loc_delta": 5},
            {"value": 0.95, "peak_mem_mb": 300, "wall_s": 10, "loc_delta": 5},
        ]
        front = pareto_frontier(rows, Direction.MAX)
        self.assertEqual(len(front), 1)
        self.assertEqual(front[0]["value"], 0.95)
        self.assertEqual(front[0]["peak_mem_mb"], 100)

    def test_a_trade_keeps_both_points(self):
        rows = [
            {"value": 0.95, "peak_mem_mb": 100, "wall_s": 10, "loc_delta": 5},
            {"value": 0.90, "peak_mem_mb": 50, "wall_s": 10, "loc_delta": 5},
        ]
        self.assertEqual(len(pareto_frontier(rows, Direction.MAX)), 2)

    def test_direction_min_inverts_the_primary_axis(self):
        rows = [
            {"value": 0.20, "peak_mem_mb": 100, "wall_s": 10, "loc_delta": 5},
            {"value": 0.30, "peak_mem_mb": 100, "wall_s": 10, "loc_delta": 5},
        ]
        front = pareto_frontier(rows, Direction.MIN)
        self.assertEqual(len(front), 1)
        self.assertEqual(front[0]["value"], 0.20)


if __name__ == "__main__":
    unittest.main()
