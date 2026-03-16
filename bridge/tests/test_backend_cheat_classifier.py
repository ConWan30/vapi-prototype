"""
Phase 61 — test_backend_cheat_classifier.py

Tests for controller/tinyml_backend_cheat.py (Layer 3 behavioral classifier).

Groups:
  1. TemporalFeatureWindow — dataclass construction and to_vector()
  2. BackendCheatClassifier mechanics — push_frame, reset, window minimum
  3. extract_temporal_features — velocity stop, jerk, latency analysis
  4. Heuristic classifier — clean / wallhack / aimbot rules
  5. classify_session — end-to-end with mock frames
  6. Inference code constants — hard codes 0x29 / 0x2A
  7. generate_training_data — deterministic output shape and class balance
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "controller"))

from tinyml_backend_cheat import (
    INFER_AIMBOT_BEHAVIORAL,
    INFER_NOMINAL,
    INFER_SKILLED,
    INFER_WALLHACK_PREAIM,
    BackendCheatClassifier,
    TemporalFeatureWindow,
    generate_training_data,
)


# ---------------------------------------------------------------------------
# Mock FeatureFrame
# ---------------------------------------------------------------------------

class _MockFrame:
    """Minimal FeatureFrame-compatible mock for classifier tests."""
    def __init__(
        self,
        stick_velocity_x=0.0,
        stick_velocity_y=0.0,
        jerk_magnitude=0.0,
        jerk_threshold=2.0,
        direction_change_anticipation=0.0,
    ):
        self.stick_velocity_x = stick_velocity_x
        self.stick_velocity_y = stick_velocity_y
        self.jerk_magnitude = jerk_magnitude
        self.jerk_threshold = jerk_threshold
        self.direction_change_anticipation = direction_change_anticipation


def _clean_frames(n=50):
    """Generate n frames with low-velocity natural-looking movement."""
    import random
    rng = random.Random(0)
    frames = []
    for _ in range(n):
        frames.append(_MockFrame(
            stick_velocity_x=rng.gauss(0, 0.01),
            stick_velocity_y=rng.gauss(0, 0.01),
            jerk_magnitude=abs(rng.gauss(0.3, 0.2)),
        ))
    return frames


def _wallhack_frames(n=60):
    """Frames with stop-start tracking pattern (wallhack signature)."""
    import random
    rng = random.Random(1)
    frames = []
    for i in range(n):
        # Alternating: 10 frames tracking (velocity > 0.05), then 2 frames stopped
        cycle = i % 12
        if cycle < 10:
            # Tracking — sustained velocity
            frames.append(_MockFrame(
                stick_velocity_x=rng.gauss(0.4, 0.02),
                stick_velocity_y=rng.gauss(0.0, 0.01),
                jerk_magnitude=abs(rng.gauss(0.3, 0.1)),
                direction_change_anticipation=0.1,
            ))
        else:
            # Abrupt stop
            frames.append(_MockFrame(
                stick_velocity_x=0.0,
                stick_velocity_y=0.0,
                jerk_magnitude=abs(rng.gauss(20.0, 2.0)),  # high dv/dt at stop
            ))
    return frames


def _aimbot_frames(n=60):
    """Frames with high-jerk snaps followed by micro-corrections (aimbot signature)."""
    import random
    rng = random.Random(2)
    frames = []
    for i in range(n):
        cycle = i % 10
        if cycle == 0:
            # Snap: high jerk, high velocity
            frames.append(_MockFrame(
                stick_velocity_x=0.8,
                stick_velocity_y=0.0,
                jerk_magnitude=3.5,
            ))
        elif cycle in (1, 2):
            # Micro-correction: small velocity after snap
            frames.append(_MockFrame(
                stick_velocity_x=0.025,
                stick_velocity_y=0.0,
                jerk_magnitude=0.05,
            ))
        else:
            frames.append(_MockFrame(
                stick_velocity_x=rng.gauss(0.0, 0.005),
                stick_velocity_y=rng.gauss(0.0, 0.005),
                jerk_magnitude=0.1,
            ))
    return frames


# ---------------------------------------------------------------------------
# Group 1: TemporalFeatureWindow
# ---------------------------------------------------------------------------

class TestTemporalFeatureWindow(unittest.TestCase):

    def _make_window(self, **kwargs) -> TemporalFeatureWindow:
        defaults = dict(
            velocity_stop_count=0.0,
            velocity_stop_sharpness=0.0,
            tracking_duration_avg=0.0,
            jerk_micro_tail_ratio=0.0,
            snap_correction_lag_ms=999.0,
            aim_settling_variance=1.0,
            direction_anticipation=0.0,
            window_frames=50,
            window_duration_ms=400.0,
        )
        defaults.update(kwargs)
        return TemporalFeatureWindow(**defaults)

    def test_to_vector_length_is_9(self):
        w = self._make_window()
        self.assertEqual(len(w.to_vector()), 9)

    def test_to_vector_field_order(self):
        w = self._make_window(
            velocity_stop_count=1.0,
            velocity_stop_sharpness=2.0,
            tracking_duration_avg=3.0,
            jerk_micro_tail_ratio=4.0,
            snap_correction_lag_ms=5.0,
            aim_settling_variance=6.0,
            direction_anticipation=7.0,
            window_frames=8,
            window_duration_ms=9.0,
        )
        vec = w.to_vector()
        self.assertEqual(vec, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])

    def test_window_frames_is_float_in_vector(self):
        w = self._make_window(window_frames=100)
        vec = w.to_vector()
        self.assertIsInstance(vec[7], float)


# ---------------------------------------------------------------------------
# Group 2: BackendCheatClassifier mechanics
# ---------------------------------------------------------------------------

class TestClassifierMechanics(unittest.TestCase):

    def test_fresh_classifier_returns_none_below_min_window(self):
        clf = BackendCheatClassifier()
        # Push fewer than WINDOW_MIN_FRAMES (37) — must not classify
        for _ in range(30):
            clf.push_frame(_MockFrame())
        w = clf.extract_temporal_features()
        self.assertIsNone(w)

    def test_at_min_window_returns_feature_window(self):
        clf = BackendCheatClassifier()
        for _ in range(BackendCheatClassifier.WINDOW_MIN_FRAMES):
            clf.push_frame(_MockFrame())
        w = clf.extract_temporal_features()
        self.assertIsNotNone(w)

    def test_reset_clears_buffer(self):
        clf = BackendCheatClassifier()
        for _ in range(50):
            clf.push_frame(_MockFrame())
        clf.reset()
        self.assertIsNone(clf.extract_temporal_features())

    def test_frame_buffer_max_enforced(self):
        clf = BackendCheatClassifier()
        n = BackendCheatClassifier.FRAME_BUFFER_MAX + 100
        for _ in range(n):
            clf.push_frame(_MockFrame())
        # deque maxlen ensures buffer doesn't grow unbounded
        self.assertLessEqual(len(clf._frame_buffer), BackendCheatClassifier.FRAME_BUFFER_MAX)

    def test_load_model_returns_false_without_tflite(self):
        clf = BackendCheatClassifier()
        result = clf.load_model("nonexistent.tflite")
        self.assertFalse(result)

    def test_load_model_empty_path_returns_false(self):
        clf = BackendCheatClassifier()
        self.assertFalse(clf.load_model(""))


# ---------------------------------------------------------------------------
# Group 3: extract_temporal_features
# ---------------------------------------------------------------------------

class TestExtractTemporalFeatures(unittest.TestCase):

    def test_window_frames_matches_buffer_size(self):
        clf = BackendCheatClassifier()
        frames = _clean_frames(50)
        for f in frames:
            clf.push_frame(f)
        w = clf.extract_temporal_features()
        self.assertEqual(w.window_frames, 50)

    def test_window_duration_ms_equals_frames_times_8(self):
        clf = BackendCheatClassifier()
        n = 60
        for _ in range(n):
            clf.push_frame(_MockFrame())
        w = clf.extract_temporal_features()
        self.assertAlmostEqual(w.window_duration_ms, n * 8.0)

    def test_clean_frames_low_stop_count(self):
        clf = BackendCheatClassifier()
        for f in _clean_frames(50):
            clf.push_frame(f)
        w = clf.extract_temporal_features()
        # Clean frames have low velocity — few stop-from-moving transitions
        self.assertLessEqual(w.velocity_stop_count, 3.0)

    def test_zero_velocity_frames_no_tracking_runs(self):
        clf = BackendCheatClassifier()
        for _ in range(50):
            clf.push_frame(_MockFrame(stick_velocity_x=0.0, stick_velocity_y=0.0))
        w = clf.extract_temporal_features()
        self.assertEqual(w.tracking_duration_avg, 0.0)

    def test_direction_anticipation_zero_for_zero_frames(self):
        clf = BackendCheatClassifier()
        for _ in range(40):
            clf.push_frame(_MockFrame(direction_change_anticipation=0.0))
        w = clf.extract_temporal_features()
        self.assertEqual(w.direction_anticipation, 0.0)

    def test_direction_anticipation_nonzero_when_set(self):
        clf = BackendCheatClassifier()
        for _ in range(40):
            clf.push_frame(_MockFrame(direction_change_anticipation=0.1))
        w = clf.extract_temporal_features()
        self.assertGreater(w.direction_anticipation, 0.0)


# ---------------------------------------------------------------------------
# Group 4: Heuristic classifier — rules
# ---------------------------------------------------------------------------

class TestHeuristicClassifier(unittest.TestCase):

    def _make_window(self, **kwargs):
        defaults = dict(
            velocity_stop_count=0.0,
            velocity_stop_sharpness=0.0,
            tracking_duration_avg=0.0,
            jerk_micro_tail_ratio=0.0,
            snap_correction_lag_ms=999.0,
            aim_settling_variance=1.0,
            direction_anticipation=0.0,
            window_frames=50,
            window_duration_ms=400.0,
        )
        defaults.update(kwargs)
        return TemporalFeatureWindow(**defaults)

    def test_all_clean_returns_none(self):
        clf = BackendCheatClassifier()
        w = self._make_window()
        result = clf._heuristic_classify(w)
        self.assertIsNone(result)

    def test_aimbot_pattern_returns_0x2a(self):
        clf = BackendCheatClassifier()
        w = self._make_window(
            jerk_micro_tail_ratio=0.7,
            snap_correction_lag_ms=30.0,
            aim_settling_variance=0.005,
        )
        result = clf._heuristic_classify(w)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], INFER_AIMBOT_BEHAVIORAL)
        self.assertGreaterEqual(result[1], BackendCheatClassifier.CONFIDENCE_THRESHOLD)

    def test_wallhack_pattern_returns_0x29(self):
        clf = BackendCheatClassifier()
        w = self._make_window(
            velocity_stop_count=5.0,
            velocity_stop_sharpness=20.0,
            tracking_duration_avg=12.0,
        )
        result = clf._heuristic_classify(w)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], INFER_WALLHACK_PREAIM)
        self.assertGreaterEqual(result[1], BackendCheatClassifier.CONFIDENCE_THRESHOLD)

    def test_aimbot_check_takes_priority_over_wallhack(self):
        # Both signals active — aimbot is checked first in the code
        clf = BackendCheatClassifier()
        w = self._make_window(
            jerk_micro_tail_ratio=0.7,
            snap_correction_lag_ms=20.0,
            aim_settling_variance=0.005,
            velocity_stop_count=5.0,
            velocity_stop_sharpness=20.0,
            tracking_duration_avg=12.0,
        )
        result = clf._heuristic_classify(w)
        self.assertEqual(result[0], INFER_AIMBOT_BEHAVIORAL)

    def test_below_threshold_returns_none(self):
        clf = BackendCheatClassifier()
        # All values just below threshold
        w = self._make_window(
            velocity_stop_count=3.0,   # not > 3
            velocity_stop_sharpness=15.0,  # not > 15
            jerk_micro_tail_ratio=0.5,     # not > 0.6
        )
        result = clf._heuristic_classify(w)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Group 5: classify_session end-to-end
# ---------------------------------------------------------------------------

class TestClassifySession(unittest.TestCase):

    def test_clean_session_returns_none(self):
        clf = BackendCheatClassifier()
        result = clf.classify_session(_clean_frames(50))
        self.assertIsNone(result)

    def test_insufficient_frames_returns_none(self):
        clf = BackendCheatClassifier()
        result = clf.classify_session(_clean_frames(10))
        self.assertIsNone(result)

    def test_reset_then_classify_starts_fresh(self):
        clf = BackendCheatClassifier()
        clf.classify_session(_clean_frames(50))
        clf.reset()
        # After reset, fewer than min window again
        result = clf.classify_session(_clean_frames(10))
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Group 6: Inference code constants
# ---------------------------------------------------------------------------

class TestInferenceCodes(unittest.TestCase):

    def test_wallhack_code_is_0x29(self):
        self.assertEqual(INFER_WALLHACK_PREAIM, 0x29)

    def test_aimbot_code_is_0x2a(self):
        self.assertEqual(INFER_AIMBOT_BEHAVIORAL, 0x2A)

    def test_nominal_code_is_0x20(self):
        self.assertEqual(INFER_NOMINAL, 0x20)

    def test_skilled_code_is_0x21(self):
        self.assertEqual(INFER_SKILLED, 0x21)

    def test_hard_codes_in_overridable_set(self):
        from tinyml_backend_cheat import _OVERRIDABLE_CODES
        self.assertIn(INFER_NOMINAL, _OVERRIDABLE_CODES)
        self.assertIn(INFER_SKILLED, _OVERRIDABLE_CODES)
        # Hard cheat codes are NOT overridable
        self.assertNotIn(INFER_WALLHACK_PREAIM, _OVERRIDABLE_CODES)
        self.assertNotIn(INFER_AIMBOT_BEHAVIORAL, _OVERRIDABLE_CODES)


# ---------------------------------------------------------------------------
# Group 7: generate_training_data
# ---------------------------------------------------------------------------

class TestGenerateTrainingData(unittest.TestCase):

    def test_output_shape(self):
        X, y = generate_training_data(n_per_class=10, seed=42)
        self.assertEqual(len(X), 30)  # 3 classes * 10
        self.assertEqual(len(y), 30)

    def test_feature_vector_length_is_9(self):
        X, y = generate_training_data(n_per_class=5, seed=0)
        for vec in X:
            self.assertEqual(len(vec), 9)

    def test_balanced_classes(self):
        X, y = generate_training_data(n_per_class=50, seed=1)
        from collections import Counter
        counts = Counter(y)
        self.assertEqual(counts[0], 50)
        self.assertEqual(counts[1], 50)
        self.assertEqual(counts[2], 50)

    def test_deterministic_with_same_seed(self):
        X1, y1 = generate_training_data(n_per_class=20, seed=99)
        X2, y2 = generate_training_data(n_per_class=20, seed=99)
        self.assertEqual(X1, X2)
        self.assertEqual(y1, y2)

    def test_different_seeds_produce_different_data(self):
        X1, _ = generate_training_data(n_per_class=20, seed=1)
        X2, _ = generate_training_data(n_per_class=20, seed=2)
        self.assertNotEqual(X1, X2)

    def test_all_labels_in_0_1_2(self):
        _, y = generate_training_data(n_per_class=30, seed=5)
        self.assertTrue(all(label in (0, 1, 2) for label in y))


if __name__ == "__main__":
    unittest.main()
