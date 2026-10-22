import unittest

from ratchet_guard.ladder import adjudicate, transfer
from ratchet_guard.model import Contract, Direction, Measurement, MetricSpec


def contract(**kw) -> Contract:
    base = dict(
        goal="g",
        metric=MetricSpec("q", Direction.MAX),
        min_delta=0.005,
        complexity_price=0.0,
    )
    base.update(kw)
    return Contract(**base)


def m(value, spread=0.01, loc=0, seeds=None):
    s = seeds if seeds is not None else [
        value - spread, value - spread / 3, value + spread / 3, value + spread
    ]
    return Measurement(value=value, seeds=tuple(s), loc_delta=loc)


class TestAdjudicate(unittest.TestCase):
    def test_clear_holdout_gain_is_promoted(self):
        ok, why = adjudicate(contract(), m(0.90), m(0.80))
        self.assertTrue(ok, why)

    def test_gain_that_does_not_survive_is_denied(self):
        ok, why = adjudicate(contract(), m(0.801), m(0.800))
        self.assertFalse(ok)
        self.assertIn("includes zero", why)

    def test_holdout_needs_four_seeds(self):
        """Three per arm bottoms out at p=0.10 on any rank test."""
        ok, why = adjudicate(contract(), m(0.90, seeds=[0.89, 0.90, 0.91]), m(0.80))
        self.assertFalse(ok)
        self.assertIn("4 are required", why)

    def test_complexity_price_can_block_promotion(self):
        ok, why = adjudicate(contract(complexity_price=0.01), m(0.85, loc=20), m(0.80))
        self.assertFalse(ok)
        self.assertIn("complexity price", why)

    def test_direction_min_is_respected(self):
        c = Contract(goal="g", metric=MetricSpec("loss", Direction.MIN), min_delta=0.001)
        ok, why = adjudicate(c, m(0.70), m(0.80))
        self.assertTrue(ok, why)


class TestTransfer(unittest.TestCase):
    def test_a_win_that_holds_at_scale_transfers(self):
        ok, _ = transfer(contract(), m(0.80), m(0.85))
        self.assertTrue(ok)

    def test_a_win_that_evaporates_does_not(self):
        ok, why = transfer(contract(), m(0.80), m(0.795))
        self.assertFalse(ok)
        self.assertIn("did not transfer", why)


if __name__ == "__main__":
    unittest.main()
