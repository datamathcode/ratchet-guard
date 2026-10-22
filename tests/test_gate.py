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
        seed_replicates=3,
        families=("a", "b"),
        holdout_cmd="true",
        max_unadjudicated=5,
    )
    base.update(kw)
    return Contract(**base)


def m(value, seeds=None, guards=None, loc=0):
    return Measurement(
        value=value,
        seeds=tuple(seeds if seeds is not None else [value - 0.01, value, value + 0.01]),
        guards=guards or {},
        loc_delta=loc,
    )


BASE = m(0.80, [0.79, 0.80, 0.81])


class TestAccept(unittest.TestCase):
    def test_clear_improvement_is_accepted(self):
        d = decide(contract(), BASE, m(0.90, [0.89, 0.90, 0.91]), GateState())
        self.assertIs(d.verdict, Verdict.ACCEPT)
        self.assertTrue(d.accepted)

    def test_reason_quotes_the_interval(self):
        d = decide(contract(), BASE, m(0.90, [0.89, 0.90, 0.91]), GateState())
        self.assertIn("CI", d.reason)


class TestNoiseGate(unittest.TestCase):
    def test_improvement_below_min_delta_is_rejected(self):
        d = decide(contract(min_delta=0.05), BASE, m(0.82, [0.81, 0.82, 0.83]), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)

    def test_two_seeds_cannot_quote_a_delta(self):
        d = decide(contract(), BASE, m(0.95, [0.94, 0.96]), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)
        self.assertIn("seed", d.reason)

    def test_regression_is_rejected(self):
        d = decide(contract(), BASE, m(0.60, [0.59, 0.60, 0.61]), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)


class TestGuardGate(unittest.TestCase):
    def test_degraded_guard_rejects_a_big_metric_win(self):
        c = contract(guards=(GuardSpec("mem", Direction.MIN, 0.25),))
        base = m(0.80, [0.79, 0.80, 0.81], guards={"mem": 200.0})
        cand = m(0.95, [0.94, 0.95, 0.96], guards={"mem": 400.0})
        d = decide(c, base, cand, GateState())
        self.assertIs(d.verdict, Verdict.REJECT_GUARD)
        self.assertIn("mem", d.reason)

    def test_guard_within_tolerance_passes(self):
        c = contract(guards=(GuardSpec("mem", Direction.MIN, 0.25),))
        base = m(0.80, [0.79, 0.80, 0.81], guards={"mem": 200.0})
        cand = m(0.95, [0.94, 0.95, 0.96], guards={"mem": 220.0})
        d = decide(c, base, cand, GateState())
        self.assertIs(d.verdict, Verdict.ACCEPT)

    def test_unreported_guard_is_a_reject(self):
        c = contract(guards=(GuardSpec("mem", Direction.MIN, 0.25),))
        base = m(0.80, [0.79, 0.80, 0.81], guards={"mem": 200.0})
        cand = m(0.95, [0.94, 0.95, 0.96], guards={})
        d = decide(c, base, cand, GateState())
        self.assertIs(d.verdict, Verdict.REJECT_GUARD)


class TestVarianceGate(unittest.TestCase):
    def test_fragile_win_is_rejected(self):
        d = decide(contract(variance_inflation_tolerance=2.0), BASE,
                   m(0.95, [0.88, 0.95, 0.99]), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_VARIANCE)


class TestComplexityPrice(unittest.TestCase):
    def test_price_converts_a_marginal_win_into_a_loss(self):
        c = contract(min_delta=0.001, complexity_price=0.001)
        d = decide(c, BASE, m(0.82, [0.815, 0.82, 0.825], loc=20), GateState())
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)
        self.assertAlmostEqual(d.checks["effect"]["complexity_cost"], 0.02)

    def test_deleting_code_is_a_discount(self):
        c = contract(min_delta=0.001, complexity_price=0.001)
        d = decide(c, BASE, m(0.80, [0.795, 0.80, 0.805], loc=-30), GateState())
        self.assertLess(d.checks["effect"]["complexity_cost"], 0)


class TestProvenanceGate(unittest.TestCase):
    def test_measurement_from_another_contract_is_refused(self):
        cand = m(0.90, [0.89, 0.90, 0.91])
        cand.meta["contract_digest"] = "deadbeefdeadbeef"
        d = decide(contract(), BASE, cand, GateState())
        self.assertIs(d.verdict, Verdict.REJECT_PROVENANCE)


class TestHalts(unittest.TestCase):
    def test_halt_short_circuits_everything(self):
        st = GateState(halted=True, halt_reason="metric blind to memorisation")
        d = decide(contract(), BASE, m(0.99, [0.98, 0.99, 1.0]), st)
        self.assertIs(d.verdict, Verdict.HALT_METRIC)
        self.assertIn("blind", d.reason)

    def test_crash_is_not_a_zero(self):
        d = decide(contract(), BASE, Measurement(value=0.0, seeds=()), GateState(), crashes=True)
        self.assertIs(d.verdict, Verdict.CRASH)


class TestQueueGate(unittest.TestCase):
    def test_too_many_unadjudicated_accepts_is_refused(self):
        d = decide(contract(max_unadjudicated=1), BASE,
                   m(0.90, [0.89, 0.90, 0.91]),
                   GateState(unadjudicated=1))
        self.assertIs(d.verdict, Verdict.REJECT_BUDGET)


class TestDebt(unittest.TestCase):
    def test_worse_first_is_allowed_in_an_under_explored_family(self):
        cand = m(0.78, [0.775, 0.78, 0.785])
        cand.meta["predicted_direction"] = "worse_first"
        d = decide(contract(), BASE, cand, GateState(under_explored=True))
        self.assertIs(d.verdict, Verdict.ACCEPT_DEBT)

    def test_worse_first_is_refused_in_a_saturated_family(self):
        cand = m(0.78, [0.775, 0.78, 0.785])
        cand.meta["predicted_direction"] = "worse_first"
        d = decide(contract(), BASE, cand, GateState(under_explored=False))
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)

    def test_debt_budget_is_finite(self):
        cand = m(0.78, [0.775, 0.78, 0.785])
        cand.meta["predicted_direction"] = "worse_first"
        st = GateState(under_explored=True)
        st.open_debt = [object()] * 10
        d = decide(contract(exploration_fraction=0.1, max_trials=10), BASE, cand, st)
        self.assertIs(d.verdict, Verdict.REJECT_NOISE)

    def test_holdout_is_required_when_the_metric_is_blind(self):
        d = decide(contract(), BASE, m(0.90, [0.89, 0.90, 0.91]),
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
