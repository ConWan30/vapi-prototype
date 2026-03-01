"""
test_stable_track_quarantine.py — Stable track quarantine invariant tests.

Verifies that BiometricFusionClassifier._stable_mean/_stable_var are NEVER
updated during ANOMALY or SKILLED sessions — only during clean NOMINAL sessions.

This is the poison-resistance guarantee documented in dualshock_integration.py:
    STABLE TRACK QUARANTINE INVARIANT:
    update_stable_fingerprint() is called IFF inference == INFER_NOMINAL (0x20).
    BIOMETRIC_ANOMALY (0x30), TEMPORAL_ANOMALY (0x2B), SKILLED (0x21), and
    adaptive policy overrides must NOT update the stable track.

An adversary who interleaves anomalous sessions with clean ones to gradually
shift the stable track toward their bot profile is the exact attack being
blocked here. If these tests fail, that poisoning attack becomes viable.
"""

import sys
import unittest

import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))

from tinyml_biometric_fusion import BiometricFusionClassifier, BiometricFeatureFrame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _human_frame(offset: float = 0.0) -> BiometricFeatureFrame:
    """A biometric frame that looks like a consistent human player."""
    return BiometricFeatureFrame(
        trigger_resistance_change_rate=0.5 + offset,
        trigger_onset_velocity_l2=0.3 + offset,
        trigger_onset_velocity_r2=0.3 + offset,
        micro_tremor_accel_variance=0.02 + offset,
        grip_asymmetry=1.1 + offset,
        stick_autocorr_lag1=0.6 + offset,
        stick_autocorr_lag5=0.4 + offset,
    )


def _bot_frame() -> BiometricFeatureFrame:
    """A biometric frame far from the human baseline — would trigger anomaly."""
    return BiometricFeatureFrame(
        trigger_resistance_change_rate=5.0,
        trigger_onset_velocity_l2=5.0,
        trigger_onset_velocity_r2=5.0,
        micro_tremor_accel_variance=0.0,
        grip_asymmetry=1.0,
        stick_autocorr_lag1=0.99,
        stick_autocorr_lag5=0.99,
    )


def _warm_up(clf: BiometricFusionClassifier, n: int = 5, frame_offset: float = 0.0) -> None:
    """Drive through N_WARMUP_SESSIONS using update_fingerprint (candidate track only)."""
    for i in range(n):
        clf.update_fingerprint(_human_frame(offset=frame_offset))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStableTrackQuarantine(unittest.TestCase):
    """
    Stable track must only advance on NOMINAL sessions.

    The quarantine invariant: update_stable_fingerprint() is the ONLY path that
    modifies _stable_mean/_stable_var, and it must only be called when the session
    inference code is INFER_NOMINAL (0x20) — never on 0x30, 0x2B, 0x21, or any
    other code.
    """

    def test_1_stable_track_unchanged_after_anomaly_sessions(self):
        """
        Stable track must not move when anomaly sessions are fed via update_fingerprint.

        Scenario: Warm up with human frames (candidate track), then inject many
        anomalous frames via update_fingerprint (simulating what dualshock_integration
        does for non-NOMINAL sessions). The stable track must remain at its
        initial state — not yet initialized, because update_stable_fingerprint
        was never called.

        This is the core invariant: the adversary feeds anomalous data and hopes
        the stable baseline drifts. It must not.
        """
        clf = BiometricFusionClassifier()
        stable_mean_before = clf._stable_mean.copy()
        stable_var_before  = clf._stable_var.copy()

        # Update candidate track many times with bot frames
        for _ in range(20):
            clf.update_fingerprint(_bot_frame())

        # Stable track must be completely unchanged (never initialized)
        np.testing.assert_array_equal(
            clf._stable_mean, stable_mean_before,
            err_msg="Stable mean changed after anomaly-only updates — quarantine violated."
        )
        np.testing.assert_array_equal(
            clf._stable_var, stable_var_before,
            err_msg="Stable var changed after anomaly-only updates — quarantine violated."
        )
        self.assertFalse(clf._stable_initialized,
                         "Stable track should not be initialized without explicit NOMINAL call.")

    def test_2_nominal_anomaly_nominal_cycle_stable_anchored_to_nominal(self):
        """
        NOMINAL → ANOMALY × N → NOMINAL cycle: stable track must only reflect the
        NOMINAL frames, not drift toward the anomalous ones.

        This is the three-phase poisoning-resistance test:
          Phase A: warm up + one NOMINAL update (stable track anchors here)
          Phase B: many anomaly frames via update_fingerprint only (simulating
                   dualshock_integration NOT calling update_stable_fingerprint)
          Phase C: one more NOMINAL update_stable call
          Assert: stable mean is close to the NOMINAL frame, far from the bot frame.
        """
        clf = BiometricFusionClassifier()
        nominal_f = _human_frame(offset=0.0)
        bot_f     = _bot_frame()

        # Phase A: warm up and anchor stable track to human frame
        _warm_up(clf, n=5)
        clf.update_stable_fingerprint(nominal_f)
        stable_after_nominal = clf._stable_mean.copy()

        # Phase B: simulate 50 anomalous sessions — candidate track updates,
        # stable track MUST NOT update (caller responsibility; we test the object
        # respects its own contract when update_stable_fingerprint is not called)
        for _ in range(50):
            clf.update_fingerprint(bot_f)

        # Stable track must not have moved (no one called update_stable_fingerprint)
        np.testing.assert_array_equal(
            clf._stable_mean, stable_after_nominal,
            err_msg="Stable track drifted during anomaly-only candidate updates."
        )

        # Phase C: one clean NOMINAL update
        clf.update_stable_fingerprint(nominal_f)

        # Stable mean should remain close to the nominal frame vector
        nominal_vec = nominal_f.to_vector().astype(np.float64)
        bot_vec     = bot_f.to_vector().astype(np.float64)

        dist_to_nominal = float(np.linalg.norm(clf._stable_mean - nominal_vec))
        dist_to_bot     = float(np.linalg.norm(clf._stable_mean - bot_vec))

        self.assertLess(
            dist_to_nominal, dist_to_bot,
            f"Stable mean drifted toward bot profile after anomaly injection. "
            f"dist_to_nominal={dist_to_nominal:.4f} dist_to_bot={dist_to_bot:.4f}. "
            f"Poisoning attack would succeed — quarantine is broken."
        )

    def test_3_drift_velocity_rises_during_anomaly_injection(self):
        """
        fingerprint_drift_velocity must increase when candidate track is fed
        anomalous frames while stable track stays clean.

        This is the contamination signal: high drift_velocity means the current
        sessions are systematically different from the anchored stable baseline.
        If the stable track were poisoned, drift_velocity would stay low even
        during bot injection — making the contamination invisible.
        """
        clf = BiometricFusionClassifier()
        nominal_f = _human_frame()

        # Warm up candidate track with human frames
        _warm_up(clf, n=5)
        # Anchor stable track to clean human baseline
        clf.update_stable_fingerprint(nominal_f)

        drift_after_nominal = clf.fingerprint_drift_velocity

        # Now inject bot frames into candidate track only
        for _ in range(30):
            clf.update_fingerprint(_bot_frame())

        drift_after_bot_injection = clf.fingerprint_drift_velocity

        self.assertGreater(
            drift_after_bot_injection, drift_after_nominal,
            f"Drift velocity did not increase during bot injection "
            f"(before={drift_after_nominal:.4f}, after={drift_after_bot_injection:.4f}). "
            f"Contamination would be invisible — stable track may have followed anomaly updates."
        )

    def test_4_classify_uses_stable_not_candidate_when_initialized(self):
        """
        classify() must use _stable_mean/_stable_var (not candidate) once the
        stable track is initialized.

        This ensures that even if the candidate track drifts far toward an anomalous
        profile, the Mahalanobis distance is still computed against the stable
        baseline — so bot sessions continue to trigger anomaly detection even after
        extensive candidate track contamination.
        """
        clf = BiometricFusionClassifier()
        nominal_f = _human_frame()
        bot_f     = _bot_frame()

        # Warm up: 5 sessions needed before classify() activates
        for _ in range(5):
            clf.update_fingerprint(nominal_f)
        clf.update_stable_fingerprint(nominal_f)

        # Contaminate candidate track with many bot frames
        for _ in range(40):
            clf.update_fingerprint(bot_f)

        # Now classify a bot frame — should still detect anomaly because
        # classify() compares against stable track, not the contaminated candidate
        result = clf.classify(bot_f)

        self.assertIsNotNone(
            result,
            "classify() returned None for a clear bot frame after candidate contamination. "
            "If the candidate track were used as reference, contamination would blind detection."
        )
        inference_code, confidence = result
        self.assertEqual(
            inference_code, 0x30,
            f"Expected 0x30 BIOMETRIC_ANOMALY, got {hex(inference_code)}. "
            "Stable track is not being used as the anomaly detection reference."
        )
        self.assertGreaterEqual(
            confidence, 180,
            f"Anomaly confidence={confidence} below threshold 180 for clear bot frame. "
            "Stable track reference may have drifted toward the contaminated candidate."
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
