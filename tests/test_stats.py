import math
import unittest

from ratchet_guard.stats import (
    bootstrap_ci,
    diff_ci,
    mann_whitney,
    paired_diff_ci,
    pooled_sd,
    rel_sd_of_sd,
    required_seed_n,
)


class TestDiffCI(unittest.TestCase):
    def test_identical_arms_include_zero(self):
        a = [0.90, 0.91, 0.89]
        lo, hi = diff_ci(a, a)
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)

    def test_clear_separation_excludes_zero(self):
        lo, hi = diff_ci([0.80, 0.81, 0.79], [0.90, 0.91, 0.89])
        self.assertGreater(lo, 0.0)
        self.assertGreater(hi, lo)

    def test_arms_must_not_share_a_random_stream(self):
        """Regression: resampling both arms with one seed pairs them index by
        index, cancels shared variance, and reports an interval far too
        narrow. An 0.005 difference on a 0.03 spread must straddle zero."""
        a = [0.900, 0.930, 0.960]
        b = [0.905, 0.935, 0.965]
        lo, hi = diff_ci(a, b)
        self.assertLess(lo, 0.0, "interval is too narrow; arms are correlated")
        self.assertGreater(hi, 0.0)
        width = hi - lo
        self.assertGreater(width, 0.02, f"implausibly narrow interval {width}")

    def test_interval_contains_the_observed_difference(self):
        a = [0.80, 0.82, 0.81, 0.79, 0.83, 0.80, 0.82, 0.81]
        b = [0.86, 0.88, 0.87, 0.85, 0.89, 0.86, 0.88, 0.87]
        lo, hi = diff_ci(a, b)
        observed = sum(b) / len(b) - sum(a) / len(a)
        self.assertLessEqual(lo, observed)
        self.assertGreaterEqual(hi, observed)


class TestBootstrap(unittest.TestCase):
    def test_ci_brackets_the_mean(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        lo, hi = bootstrap_ci(vals)
        self.assertLess(lo, 3.0)
        self.assertGreater(hi, 3.0)

    def test_single_value_degrades_quietly(self):
        self.assertEqual(bootstrap_ci([0.5]), (0.5, 0.5))


class TestMannWhitney(unittest.TestCase):
    def test_complete_separation_is_significant(self):
        _, p = mann_whitney([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
        self.assertLess(p, 0.05)

    def test_identical_samples_are_not_significant(self):
        _, p = mann_whitney([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
        self.assertGreater(p, 0.9)

    def test_ties_do_not_produce_nan(self):
        _, p = mann_whitney([1, 1, 1], [1, 1, 1])
        self.assertFalse(math.isnan(p))


class TestPooledSd(unittest.TestCase):
    def test_pooled_sd_between_group_sds(self):
        sd = pooled_sd([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        self.assertGreater(sd, 1.0)

    def test_singleton_groups_are_ignored(self):
        self.assertEqual(pooled_sd([1.0], [2.0]), 0.0)


class TestRequiredSeedN(unittest.TestCase):
    def test_larger_effect_needs_fewer_seeds(self):
        self.assertLess(required_seed_n(1.0, 2.0), required_seed_n(1.0, 0.1))

    def test_never_below_three(self):
        self.assertGreaterEqual(required_seed_n(0.01, 100.0), 3)


class TestPairedDiff(unittest.TestCase):
    def test_pairing_removes_a_shared_shift(self):
        """Both arms shifted by the same amount; the paired difference sees
        nothing, while an unpaired comparison would see a wide interval."""
        ref = [0.80, 0.81, 0.79, 0.80]
        cand = [0.90, 0.91, 0.89, 0.90]
        lo, hi = paired_diff_ci(cand, ref)
        self.assertAlmostEqual(lo, 0.10, places=6)
        self.assertAlmostEqual(hi, 0.10, places=6)

    def test_mismatched_lengths_are_refused(self):
        with self.assertRaises(ValueError):
            paired_diff_ci([1.0, 2.0], [1.0])

    def test_noise_cancels_when_the_shift_is_common_to_both_arms(self):
        cand = [0.90, 0.95, 0.85, 0.92]
        ref = [0.80, 0.85, 0.75, 0.82]
        lo, hi = paired_diff_ci(cand, ref)
        self.assertAlmostEqual(lo, 0.10, places=6)
        self.assertAlmostEqual(hi, 0.10, places=6)


class TestRelSdOfSd(unittest.TestCase):
    def test_three_samples_give_a_fifty_percent_estimate(self):
        self.assertAlmostEqual(rel_sd_of_sd(3), 0.5, places=6)

    def test_more_samples_are_better(self):
        self.assertLess(rel_sd_of_sd(30), rel_sd_of_sd(3))

    def test_impossible_sample_size(self):
        self.assertEqual(rel_sd_of_sd(1), float("inf"))


if __name__ == "__main__":
    unittest.main()
