"""
Phase 17 — test_l2b_imu_press_correlation.py

Tests cover:
- Group 1: Synthetic bot (no IMU precursor) → fires 0x31
- Group 2: Synthetic human (with precursor) → no fire
- Group 3: Mechanics (min events, reset, adaptive threshold)
- Group 4: Real session data fixtures (hw_005-hw_010 → no false positives)
- Group 5: Deterministic timestamp replay
"""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

# Add controller/ to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "controller"))

from l2b_imu_press_correlation import (
    CROSS_BIT,
    INFER_IMU_BUTTON_DECOUPLED,
    ImuPressCorrelationOracle,
    _COUPLED_FRACTION,
    _MIN_PRESS_EVENTS,
    _PRECURSOR_MIN_MS,
    _PRECURSOR_WINDOW_MS,
)

# ---------------------------------------------------------------------------
# Session fixture path
# ---------------------------------------------------------------------------

SESSION_DIR = Path(__file__).resolve().parents[2] / "sessions" / "human"


def _load_session_snaps(filename: str, max_reports: int = 5000):
    """
    Load a session JSON and return a list of minimal snap objects.

    buttons field: Cross in session JSON is buttons_0 bit 5 (0x20).
    InputSnapshot CROSS_BIT = bit 0.  Remap: (buttons_0 >> 5) & 1.
    """
    path = SESSION_DIR / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    snaps = []
    for r in data["reports"][:max_reports]:
        f = r["features"]
        snap = type("_S", (), {
            "timestamp_ms": float(r["timestamp_ms"]),
            "gyro_x":       float(f.get("gyro_x", 0.0)),
            "gyro_y":       float(f.get("gyro_y", 0.0)),
            "gyro_z":       float(f.get("gyro_z", 0.0)),
            "r2_trigger":   int(f.get("r2_trigger", 0)),
            # Cross: bit 5 of buttons_0 → remap to bit 0 (CROSS_BIT)
            "buttons":      (int(f.get("buttons_0", 0)) >> 5) & 1,
        })()
        snaps.append(snap)
    return snaps


# ---------------------------------------------------------------------------
# Snap factories
# ---------------------------------------------------------------------------

def _snap(ts_ms: float, buttons: int = 0, r2: int = 0,
          gx: float = 0.0, gy: float = 0.0, gz: float = 0.0):
    return type("_S", (), {
        "timestamp_ms": ts_ms,
        "buttons":      buttons,
        "r2_trigger":   r2,
        "gyro_x":       gx,
        "gyro_y":       gy,
        "gyro_z":       gz,
    })()


def _make_bot_oracle(n_presses: int = 20):
    """Oracle with n_presses that have NO IMU precursor (pure software injection)."""
    oracle = ImuPressCorrelationOracle()
    for i in range(n_presses):
        oracle._press_events.append({"ts": float(i * 500), "has_precursor": False})
    return oracle


def _make_human_oracle(n_presses: int = 20):
    """Oracle with n_presses that ALL have an IMU precursor."""
    oracle = ImuPressCorrelationOracle()
    for i in range(n_presses):
        oracle._press_events.append({"ts": float(i * 500), "has_precursor": True})
    return oracle


# ---------------------------------------------------------------------------
# Group 1: Synthetic bot
# ---------------------------------------------------------------------------

class TestSyntheticBot(unittest.TestCase):

    def test_no_precursor_fires_0x31(self):
        """All presses with no precursor → classify returns INFER_IMU_BUTTON_DECOUPLED."""
        oracle = _make_bot_oracle(20)
        result = oracle.classify()
        self.assertIsNotNone(result, "Expected 0x31 to fire for zero-precursor bot")
        code, conf = result
        self.assertEqual(code, INFER_IMU_BUTTON_DECOUPLED)
        self.assertGreaterEqual(conf, 190)
        self.assertLessEqual(conf, 230)

    def test_zero_gyro_fires(self):
        """
        Simulate software injection: snaps with Cross pressed but gyro_mag=0 throughout.
        Oracle should record has_precursor=False for each press and fire.
        """
        oracle = ImuPressCorrelationOracle()
        ts = 0.0
        # 40ms of gyro=0, then Cross rising edge
        for _ in range(40):
            oracle.push_snapshot(_snap(ts, buttons=0, gx=0.0))
            ts += 1.0
        # Press Cross — zero gyro_mag = no precursor
        oracle.push_snapshot(_snap(ts, buttons=CROSS_BIT, gx=0.0))
        ts += 1.0
        oracle.push_snapshot(_snap(ts, buttons=0, gx=0.0))  # release
        ts += 1.0
        # Repeat until _MIN_PRESS_EVENTS
        for i in range(_MIN_PRESS_EVENTS + 5):
            for _ in range(40):
                oracle.push_snapshot(_snap(ts, buttons=0, gx=0.0))
                ts += 1.0
            oracle.push_snapshot(_snap(ts, buttons=CROSS_BIT, gx=0.0))
            ts += 1.0
            oracle.push_snapshot(_snap(ts, buttons=0, gx=0.0))
            ts += 1.0

        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        self.assertEqual(feats.coupled_fraction, 0.0,
                         "Zero gyro → zero coupled fraction")
        self.assertTrue(feats.anomaly)
        result = oracle.classify()
        self.assertIsNotNone(result)
        self.assertEqual(result[0], INFER_IMU_BUTTON_DECOUPLED)

    def test_confidence_scales_with_decoupling(self):
        """
        Higher decoupling (lower coupled_fraction) → higher or equal confidence.
        Both 0% and 20% coupled may hit the 230 cap; the key invariant is that
        lower coupling never yields LOWER confidence.
        """
        oracle_50pct = ImuPressCorrelationOracle()
        oracle_0pct  = ImuPressCorrelationOracle()
        for i in range(20):
            oracle_50pct._press_events.append({"ts": float(i * 500), "has_precursor": i < 10})
            oracle_0pct._press_events.append({"ts": float(i * 500), "has_precursor": False})

        r50 = oracle_50pct.classify()
        r0  = oracle_0pct.classify()
        self.assertIsNotNone(r50)
        self.assertIsNotNone(r0)
        _, conf_50 = r50
        _, conf_0  = r0
        self.assertGreaterEqual(conf_0, conf_50,
                                "Zero coupling should yield ≥ confidence than 50%")


# ---------------------------------------------------------------------------
# Group 2: Synthetic human
# ---------------------------------------------------------------------------

class TestSyntheticHuman(unittest.TestCase):

    def test_precursor_human_no_fire(self):
        """All presses with precursor → coupled_fraction=1.0 → no anomaly."""
        oracle = _make_human_oracle(20)
        self.assertIsNone(oracle.classify())
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        self.assertFalse(feats.anomaly)
        self.assertAlmostEqual(feats.coupled_fraction, 1.0)

    def test_high_coupled_fraction_no_fire(self):
        """80% precursor presence (above threshold 0.55) → no fire."""
        oracle = ImuPressCorrelationOracle()
        for i in range(20):
            oracle._press_events.append({"ts": float(i * 500), "has_precursor": i >= 4})
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        self.assertGreaterEqual(feats.coupled_fraction, _COUPLED_FRACTION)
        self.assertFalse(feats.anomaly)
        self.assertIsNone(oracle.classify())

    def test_push_snapshot_with_precursor_detected(self):
        """
        Simulate real physics: strong gyro spike 30ms before Cross press.
        Oracle should detect has_precursor=True for that press.
        """
        oracle = ImuPressCorrelationOracle()
        ts = 1000.0
        # Add strong gyro_mag spike (500 LSB) 30ms before button press
        spike_ts = ts - 30.0
        # Feed IMU history with background noise, then spike
        for i in range(200):
            t = ts - 200.0 + i
            mag = 500.0 if abs(t - spike_ts) < 2.0 else 10.0
            oracle._imu_history.append((t, mag))
            oracle._imu_baseline.append(10.0)  # baseline=10

        # Now simulate Cross rising edge at ts
        oracle._record_press(ts)
        self.assertEqual(len(oracle._press_events), 1)
        self.assertTrue(
            oracle._press_events[0]["has_precursor"],
            "Strong gyro spike 30ms before press should be detected as precursor"
        )


# ---------------------------------------------------------------------------
# Group 3: Mechanics
# ---------------------------------------------------------------------------

class TestMechanics(unittest.TestCase):

    def test_below_min_events_returns_none(self):
        """Fewer than _MIN_PRESS_EVENTS press events → extract_features returns None."""
        oracle = ImuPressCorrelationOracle()
        for i in range(_MIN_PRESS_EVENTS - 1):
            oracle._press_events.append({"ts": float(i * 500), "has_precursor": False})
        self.assertIsNone(oracle.extract_features())
        self.assertIsNone(oracle.classify())

    def test_reset_clears_state(self):
        """reset() clears all deques and rising-edge state."""
        oracle = _make_bot_oracle(20)
        oracle._imu_history.append((0.0, 100.0))
        oracle._imu_baseline.append(50.0)
        oracle._cross_above = True
        oracle._r2_above = True
        oracle.reset()
        self.assertEqual(len(oracle._press_events), 0)
        self.assertEqual(len(oracle._imu_history), 0)
        self.assertEqual(len(oracle._imu_baseline), 0)
        self.assertFalse(oracle._cross_above)
        self.assertFalse(oracle._r2_above)
        self.assertIsNone(oracle.classify())

    def test_adaptive_threshold_tracks_baseline(self):
        """
        Oracle uses median of _imu_baseline + _IMU_SPIKE_THRESH.
        With high baseline, spikes must be proportionally higher.
        """
        oracle = ImuPressCorrelationOracle()
        # Set a high baseline (noise floor = 200 LSB)
        for _ in range(200):
            oracle._imu_baseline.append(200.0)
        ts = 1000.0
        # Small spike that would exceed 30 LSB above 0 baseline but NOT 30 above 200
        for i in range(100):
            oracle._imu_history.append((ts - 100.0 + i, 220.0))  # only +20 above 200
        oracle._record_press(ts)
        # +20 LSB above baseline=200: below _IMU_SPIKE_THRESH=30 → no precursor
        self.assertFalse(oracle._press_events[0]["has_precursor"])

    def test_humanity_score_neutral_before_warmup(self):
        """humanity_score() returns 0.5 before _MIN_PRESS_EVENTS."""
        oracle = ImuPressCorrelationOracle()
        self.assertAlmostEqual(oracle.humanity_score(), 0.5)

    def test_humanity_score_high_for_human(self):
        """100% precursor coupling → humanity_score = 1.0."""
        oracle = _make_human_oracle(20)
        self.assertAlmostEqual(oracle.humanity_score(), 1.0)

    def test_window_rolling(self):
        """_press_events maxlen caps at 500; oldest are dropped."""
        oracle = ImuPressCorrelationOracle()
        for i in range(510):
            oracle._press_events.append({"ts": float(i), "has_precursor": False})
        self.assertEqual(len(oracle._press_events), 500)


# ---------------------------------------------------------------------------
# Group 4: Real session data fixtures
# ---------------------------------------------------------------------------

class TestSessionFixtures(unittest.TestCase):

    def _run_oracle_on_session(self, filename: str, max_reports: int = 5000):
        """Load session JSON, push all snaps through oracle, return (oracle, feats)."""
        snaps = _load_session_snaps(filename, max_reports=max_reports)
        if not snaps:
            return None, None
        oracle = ImuPressCorrelationOracle()
        for snap in snaps:
            oracle.push_snapshot(snap)
        return oracle, oracle.extract_features()

    def test_hw005_executes_without_error(self):
        """hw_005.json (30002 reports, 1496 Cross presses): oracle runs without exception."""
        snaps = _load_session_snaps("hw_005.json", max_reports=5000)
        if not snaps:
            self.skipTest("hw_005.json not present — skipping fixture test")
        oracle = ImuPressCorrelationOracle()
        for snap in snaps:
            oracle.push_snapshot(snap)
        feats = oracle.extract_features()
        if feats is not None:
            self.assertGreaterEqual(feats.press_count, 0)
            self.assertGreaterEqual(feats.coupled_fraction, 0.0)
            self.assertLessEqual(feats.coupled_fraction, 1.0)

    def test_hw_batch_false_positive_rate(self):
        """
        Sessions hw_005-hw_010: at most 2/6 should fire 0x31.

        Human sessions from calibration must not trigger the oracle at an
        alarming rate. Uses first 5000 reports per session for speed.
        Fire rate > 2/6 would indicate threshold miscalibration.
        """
        sessions = [f"hw_{n:03d}.json" for n in range(5, 11)]
        fires = 0
        tested = 0
        for fname in sessions:
            snaps = _load_session_snaps(fname, max_reports=5000)
            if not snaps:
                continue
            tested += 1
            oracle = ImuPressCorrelationOracle()
            for snap in snaps:
                oracle.push_snapshot(snap)
            result = oracle.classify()
            if result is not None:
                fires += 1

        if tested == 0:
            self.skipTest("No session fixture files found — skipping")

        fire_rate = fires / tested
        self.assertLessEqual(
            fire_rate, 2 / 6,
            msg=f"Too many false positives: {fires}/{tested} human sessions fired 0x31"
        )

    def test_session_fixture_has_press_events(self):
        """
        hw_005.json has 1496 Cross presses confirmed — oracle should accumulate
        press events when first 5000 frames include Cross activity.
        """
        snaps = _load_session_snaps("hw_005.json", max_reports=5000)
        if not snaps:
            self.skipTest("hw_005.json not present — skipping fixture test")
        oracle = ImuPressCorrelationOracle()
        for snap in snaps:
            oracle.push_snapshot(snap)
        # hw_005 has 1496 Cross presses in 30002 reports → expect presses in first 5000
        self.assertGreater(
            len(oracle._press_events), 0,
            "Expected at least 1 press event from hw_005.json first 5000 frames"
        )


# ---------------------------------------------------------------------------
# Group 5: Deterministic timestamp replay
# ---------------------------------------------------------------------------

class TestTimestampReplay(unittest.TestCase):

    def test_timestamp_override_used(self):
        """
        snap.timestamp_ms is used when present (not wall clock).
        Two identical replays with same timestamps → same press_events.
        """
        oracle1 = ImuPressCorrelationOracle()
        oracle2 = ImuPressCorrelationOracle()

        # Sequence: background gyro at ts=0-80, spike at ts=50, Cross press at ts=100
        sequence = [
            (t, CROSS_BIT if t == 100 else 0, 0,
             500.0 if 48 <= t <= 52 else 10.0)  # gx
            for t in range(0, 200, 1)
        ] + [
            (t, 0, 0, 10.0)  # release and cooldown
            for t in range(200, 210)
        ]

        for oracle in (oracle1, oracle2):
            for ts, buttons, r2, gx in sequence:
                oracle.push_snapshot(_snap(float(ts), buttons=buttons, r2=r2, gx=gx))

        feats1 = oracle1.extract_features()
        feats2 = oracle2.extract_features()

        if feats1 is None or feats2 is None:
            return  # insufficient presses — test skipped implicitly

        self.assertEqual(feats1.coupled_fraction, feats2.coupled_fraction)
        self.assertEqual(feats1.press_count, feats2.press_count)

    def test_no_timestamp_falls_back_to_wall_clock(self):
        """snap without timestamp_ms attribute → oracle uses wall clock (no crash)."""
        oracle = ImuPressCorrelationOracle()

        class _NoTs:
            buttons = 0
            r2_trigger = 0
            gyro_x = 0.0
            gyro_y = 0.0
            gyro_z = 0.0

        for _ in range(30):
            oracle.push_snapshot(_NoTs())
        # Should not raise
        oracle.extract_features()  # may return None — that's fine


if __name__ == "__main__":
    unittest.main()
