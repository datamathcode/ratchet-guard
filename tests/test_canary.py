import unittest

from ratchet_guard.canary import blind_spots, judge_canary, uncovered
from ratchet_guard.model import CanarySpec


def spec(expectation="worse", separation=0.05, covered_by="holdout"):
    return CanarySpec(
        name="mem",
        command="true",
        expectation=expectation,
        separation=separation,
        covered_by=covered_by,
        failure_mode="rewards memorising the eval split",
    )


class TestJudge(unittest.TestCase):
    def test_correctly_ranked_bad_canary_passes(self):
        r = judge_canary(spec(), measured=0.50, baseline_value=0.80)
        self.assertTrue(r.passed)

    def test_metric_that_loves_the_bad_canary_is_blind(self):
        r = judge_canary(spec(), measured=1.00, baseline_value=0.80)
        self.assertFalse(r.passed)
        self.assertIn("memoris", r.failure_mode)

    def test_separation_threshold_is_respected(self):
        # 0.02 better is not enough when 0.05 is required.
        r = judge_canary(spec(separation=0.05), measured=0.78, baseline_value=0.80)
        self.assertFalse(r.passed)

    def test_good_canary_expectation_is_inverted(self):
        r = judge_canary(spec(expectation="better"), measured=0.90, baseline_value=0.80)
        self.assertTrue(r.passed)

    def test_good_canary_that_did_not_help_is_blind(self):
        r = judge_canary(spec(expectation="better"), measured=0.70, baseline_value=0.80)
        self.assertFalse(r.passed)


class TestCoverage(unittest.TestCase):
    def test_covered_blind_spot_is_not_a_stop(self):
        r = judge_canary(spec(covered_by="holdout"), measured=1.00, baseline_value=0.80)
        self.assertEqual(len(blind_spots([r])), 1)
        self.assertEqual(uncovered([r], {"holdout"}), [])

    def test_uncovered_blind_spot_is_a_stop(self):
        r = judge_canary(spec(covered_by=""), measured=1.00, baseline_value=0.80)
        self.assertEqual(len(uncovered([r], {"holdout"})), 1)

    def test_declared_but_disabled_adjudicator_is_not_coverage(self):
        r = judge_canary(spec(covered_by="holdout"), measured=1.00, baseline_value=0.80)
        self.assertEqual(len(uncovered([r], set())), 1)

    def test_passing_canary_never_needs_coverage(self):
        r = judge_canary(spec(), measured=0.50, baseline_value=0.80)
        self.assertEqual(uncovered([r], set()), [])


if __name__ == "__main__":
    unittest.main()
