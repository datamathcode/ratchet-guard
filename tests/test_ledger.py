import json
import tempfile
import unittest
from pathlib import Path

from ratchet_guard.ledger import Ledger, State
from ratchet_guard.model import Contract, Direction, Measurement, MetricSpec, Trial


def make_contract() -> Contract:
    return Contract(goal="g", metric=MetricSpec("q", Direction.MAX), min_delta=0.001)


def make_trial(tid: str, value: float) -> Trial:
    return Trial(
        id=tid,
        commit="abc1234",
        family="arch",
        hypothesis="try the thing",
        measurement=Measurement(value=value, seeds=(value, value, value)),
    )


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / ".ratchet"
        self.ledger = Ledger(self.dir)
        self.ledger.ensure()
        self.contract = make_contract()

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_creates_header_and_row(self):
        self.ledger.append(make_trial("t1", 0.9), "accept", 0.1, (0.05, 0.15), self.contract)
        rows = self.ledger.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trial"], "t1")
        self.assertEqual(rows[0]["verdict"], "accept")

    def test_tabs_in_a_hypothesis_cannot_break_the_columns(self):
        t = make_trial("t1", 0.9)
        t.hypothesis = "a\tb\tc"
        self.ledger.append(t, "accept", 0.1, (0.05, 0.15), self.contract)
        row = self.ledger.rows()[0]
        self.assertEqual(row["hypothesis"], "a b c")
        self.assertEqual(len(row), 13)

    def test_chain_verifies(self):
        for i in range(4):
            self.ledger.append(make_trial(f"t{i}", 0.9), "accept", 0.1, (0.0, 0.2), self.contract)
        ok, msg = self.ledger.verify_chain()
        self.assertTrue(ok, msg)
        self.assertIn("4 rows", msg)

    def test_rewriting_a_recorded_row_is_detected(self):
        self.ledger.append(make_trial("t1", 0.9), "accept", 0.1, (0.0, 0.2), self.contract)
        self.ledger.append(make_trial("t2", 0.9), "reject_noise", 0.0, (0.0, 0.0), self.contract)
        lines = self.ledger.audit.read_text().splitlines()
        body = json.loads(lines[0])
        body["gain"] = 999.0
        lines[0] = json.dumps(body, sort_keys=True)
        self.ledger.audit.write_text("\n".join(lines) + "\n")
        ok, msg = self.ledger.verify_chain()
        self.assertFalse(ok)
        self.assertIn("broken", msg)

    def test_appending_never_truncates(self):
        for i in range(3):
            self.ledger.append(make_trial(f"t{i}", 0.9), "accept", 0.1, (0.0, 0.2), self.contract)
        self.assertEqual(len(self.ledger.rows()), 3)


class TestState(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = State(trial_ordinal=7, blind_spots=["memoriser"])
            s.baseline = Measurement(value=0.5, seeds=(0.5, 0.5, 0.5)).to_dict()
            s.save(tmp)
            loaded = State.load(tmp)
            self.assertEqual(loaded.trial_ordinal, 7)
            self.assertEqual(loaded.blind_spots, ["memoriser"])
            self.assertEqual(loaded.baseline_measurement.value, 0.5)

    def test_missing_state_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(State.load(Path(tmp) / "nope").trial_ordinal, 0)

    def test_record_advances_counters(self):
        s = State()
        s.record(make_trial("t1", 0.9), "arch", 0.05)
        s.record(make_trial("t2", 0.9), "arch", 0.07)
        self.assertEqual(s.trial_ordinal, 2)
        self.assertEqual(s.family_counts["arch"], 2)
        self.assertEqual(s.family_gains["arch"], [0.05, 0.07])


class TestNoisePooling(unittest.TestCase):
    def test_within_arm_variance_is_pooled(self):
        s = State()
        s.update_noise((0.80, 0.82))
        first = s.noise_sd
        self.assertAlmostEqual(first, 0.014142, places=5)
        s.update_noise((0.80, 0.82))
        # Same spread twice: the estimate does not move, only its df.
        self.assertAlmostEqual(s.noise_sd, first, places=6)
        self.assertEqual(s.noise_df, 2)

    def test_a_separated_pair_does_not_enter_the_estimate(self):
        """The estimate is within-arm. Pooling means across configs would fold
        the effect being measured into the noise, and the floor would appear to
        shrink as the run succeeded."""
        s = State()
        for _ in range(5):
            s.update_noise((0.90, 0.91))
        before = s.noise_sd
        s.update_noise((0.90, 0.91))
        self.assertAlmostEqual(s.noise_sd, before, places=9)

    def test_single_seed_contributes_nothing(self):
        s = State()
        s.update_noise((0.9,))
        self.assertIsNone(s.noise_var)


if __name__ == "__main__":
    unittest.main()
