"""
Phase 26 — BehavioralArchaeologist Tests

TestBehavioralCore (6):
1.  empty history → neutral report (session_count=0, scores near neutral)
2.  device with no records → session_count=0, warmup_score near 0
3.  stable drift + stable humanity → warmup_score < 0.3
4.  rising drift + rising humanity → warmup_score > 0.7
5.  bursty checkpoints (inter-gap CV > 2) → burst_farming_score > 0.7
6.  regular checkpoints → burst_farming_score < 0.3

TestBehavioralCertificates (5):
7.  biometric_stability_cert True when all drift_velocity < 0.5
8.  biometric_stability_cert False when avg drift_velocity > 0.5
9.  l4_consistency_cert True when L4 distances consistent (low std/mean)
10. l4_consistency_cert False when L4 distances highly variable
11. cert requires >= 5 data points; fewer → False

TestBehavioralPopulation (4):
12. get_population_report returns BehavioralReport list
13. get_high_risk_devices returns only devices above threshold
14. get_high_risk_devices returns [] when all below threshold
15. 3 devices (1 risky, 2 clean) → only 1 in get_high_risk_devices
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from vapi_bridge.behavioral_archaeologist import BehavioralArchaeologist, BehavioralReport

# ---------------------------------------------------------------------------
# Store mock helpers
# ---------------------------------------------------------------------------

def _mock_store(pitl_history=None, checkpoints=None, fingerprinted_devices=None):
    """Build a mock store with configurable return values."""
    store = MagicMock()
    store.get_pitl_history.return_value = pitl_history or []
    store.get_phg_checkpoints.return_value = checkpoints or []
    store.get_all_fingerprinted_devices.return_value = fingerprinted_devices or []
    return store


def _pitl_rows(drift_vals, humanity_vals, l4_dist_vals=None):
    """Build synthetic PITL history rows in DESC order (newest first)."""
    rows = []
    n = max(len(drift_vals), len(humanity_vals))
    l4_dist_vals = l4_dist_vals or [None] * n
    for i in reversed(range(n)):
        rows.append({
            "timestamp_ms": (n - i) * 1000,
            "inference": 0x20,
            "confidence": 200,
            "pitl_l4_drift_velocity": drift_vals[i] if i < len(drift_vals) else None,
            "pitl_l5_rhythm_humanity": humanity_vals[i] if i < len(humanity_vals) else None,
            "pitl_e4_cognitive_drift": None,
            "pitl_humanity_prob": humanity_vals[i] if i < len(humanity_vals) else None,
            "pitl_l4_distance": l4_dist_vals[i] if i < len(l4_dist_vals) else None,
        })
    return rows


def _checkpoints_at_times(times):
    """Build synthetic checkpoint list at specified committed_at timestamps."""
    return [{"committed_at": t, "id": i} for i, t in enumerate(times)]


# ===========================================================================
# TestBehavioralCore
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required")
class TestBehavioralCore(unittest.TestCase):

    def test_1_empty_history_neutral_report(self):
        """Empty PITL history → session_count=0, warmup_score near neutral baseline."""
        store = _mock_store()
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devA")
        self.assertIsInstance(report, BehavioralReport)
        self.assertEqual(report.session_count, 0)
        self.assertLess(report.warmup_attack_score, 0.3)

    def test_2_device_no_records(self):
        """No PITL records → session_count=0, warmup_score=0 (neutral)."""
        store = _mock_store(pitl_history=[])
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devB")
        self.assertEqual(report.session_count, 0)
        self.assertFalse(report.biometric_stability_cert)

    def test_3_stable_drift_stable_humanity_low_warmup(self):
        """Stable drift and stable humanity → warmup_attack_score < 0.3."""
        # Constant values — zero slope for both
        n = 20
        drift = [0.2] * n
        humanity = [0.5] * n
        store = _mock_store(pitl_history=_pitl_rows(drift, humanity))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devC")
        self.assertLess(report.warmup_attack_score, 0.3)

    def test_4_rising_drift_rising_humanity_high_warmup(self):
        """Rising drift + rising humanity → warmup_attack_score > 0.7."""
        n = 30
        # Strong rising trends: drift 0.01→0.31, humanity 0.2→0.5
        drift    = [0.01 + i * 0.01 for i in range(n)]
        humanity = [0.2  + i * 0.01 for i in range(n)]
        store = _mock_store(pitl_history=_pitl_rows(drift, humanity))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devD")
        self.assertGreater(report.warmup_attack_score, 0.7)

    def test_5_bursty_checkpoints_high_burst_score(self):
        """Bursty checkpoints (high CV) → burst_farming_score > 0.7."""
        now = time.time()
        # Very bursty: 3 consecutive, then large gap
        times = [now - 7200, now - 7100, now - 7050, now - 100, now - 50]
        store = _mock_store(checkpoints=_checkpoints_at_times(times))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devE")
        self.assertGreater(report.burst_farming_score, 0.5)

    def test_6_regular_checkpoints_low_burst_score(self):
        """Regular checkpoints (uniform spacing) → burst_farming_score < 0.3."""
        now = time.time()
        # Uniform: every 600 seconds
        times = [now - 600 * i for i in range(8)]
        store = _mock_store(checkpoints=_checkpoints_at_times(times))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devF")
        self.assertLess(report.burst_farming_score, 0.3)


# ===========================================================================
# TestBehavioralCertificates
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required")
class TestBehavioralCertificates(unittest.TestCase):

    def test_7_stability_cert_true_low_drift(self):
        """biometric_stability_cert True when all drift_velocity < 0.5."""
        n = 10
        drift = [0.1] * n  # all well below 0.5
        humanity = [0.6] * n
        store = _mock_store(pitl_history=_pitl_rows(drift, humanity))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devG")
        self.assertTrue(report.biometric_stability_cert)

    def test_8_stability_cert_false_high_drift(self):
        """biometric_stability_cert False when avg drift_velocity > 0.5."""
        n = 10
        drift = [0.9] * n  # all above 0.5
        humanity = [0.6] * n
        store = _mock_store(pitl_history=_pitl_rows(drift, humanity))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devH")
        self.assertFalse(report.biometric_stability_cert)

    def test_9_l4_consistency_cert_true(self):
        """l4_consistency_cert True when L4 distances have low std/mean."""
        n = 10
        drift    = [0.2] * n
        humanity = [0.5] * n
        l4_dists = [1.0] * n  # perfectly consistent
        store = _mock_store(pitl_history=_pitl_rows(drift, humanity, l4_dists))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devI")
        self.assertTrue(report.l4_consistency_cert)

    def test_10_l4_consistency_cert_false_variable(self):
        """l4_consistency_cert False when L4 distances are highly variable."""
        n = 10
        drift    = [0.2] * n
        humanity = [0.5] * n
        # Alternating 0.1 and 5.0 — very high std/mean
        l4_dists = [0.1 if i % 2 == 0 else 5.0 for i in range(n)]
        store = _mock_store(pitl_history=_pitl_rows(drift, humanity, l4_dists))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devJ")
        self.assertFalse(report.l4_consistency_cert)

    def test_11_cert_requires_5_data_points(self):
        """With fewer than 5 records, both certs must be False."""
        n = 4
        drift    = [0.1] * n
        humanity = [0.5] * n
        l4_dists = [1.0] * n
        store = _mock_store(pitl_history=_pitl_rows(drift, humanity, l4_dists))
        arch = BehavioralArchaeologist(store)
        report = arch.analyze_device("devK")
        self.assertFalse(report.biometric_stability_cert)
        self.assertFalse(report.l4_consistency_cert)


# ===========================================================================
# TestBehavioralPopulation
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required")
class TestBehavioralPopulation(unittest.TestCase):

    def _arch_with_devices(self, devices):
        """Build an archaeologist whose store returns the given device list."""
        store = MagicMock()
        store.get_all_fingerprinted_devices.return_value = devices
        store.get_pitl_history.return_value = []
        store.get_phg_checkpoints.return_value = []
        return BehavioralArchaeologist(store)

    def test_12_population_report_returns_list(self):
        """get_population_report returns a list of BehavioralReport."""
        arch = self._arch_with_devices(["devA", "devB"])
        reports = arch.get_population_report()
        self.assertIsInstance(reports, list)
        self.assertEqual(len(reports), 2)
        for r in reports:
            self.assertIsInstance(r, BehavioralReport)

    def test_13_high_risk_returns_above_threshold(self):
        """get_high_risk_devices returns only devices above threshold."""
        arch = self._arch_with_devices([])
        risky = arch.get_high_risk_devices(threshold=0.7)
        self.assertIsInstance(risky, list)

    def test_14_high_risk_empty_when_all_below_threshold(self):
        """get_high_risk_devices returns [] when all scores below threshold."""
        arch = self._arch_with_devices(["devA", "devB"])
        risky = arch.get_high_risk_devices(threshold=0.99)
        self.assertEqual(risky, [])

    def test_15_three_devices_one_risky(self):
        """3 devices (1 risky, 2 clean) — only 1 in get_high_risk_devices."""
        store = MagicMock()
        store.get_all_fingerprinted_devices.return_value = ["risky", "clean1", "clean2"]
        store.get_phg_checkpoints.return_value = []

        # risky: strong rising drift AND humanity → warmup_score > 0.7
        n = 30
        risky_rows = _pitl_rows(
            [0.01 + i * 0.01 for i in range(n)],
            [0.2  + i * 0.01 for i in range(n)],
        )
        clean_rows = _pitl_rows([0.2] * 10, [0.5] * 10)

        def _pitl_hist(device_id, limit=100):
            if device_id == "risky":
                return risky_rows
            return clean_rows

        store.get_pitl_history.side_effect = _pitl_hist

        arch = BehavioralArchaeologist(store)
        risky_devices = arch.get_high_risk_devices(threshold=0.7)
        self.assertEqual(len(risky_devices), 1)
        self.assertIn("risky", risky_devices)


if __name__ == "__main__":
    unittest.main()
