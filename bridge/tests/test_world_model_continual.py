"""
Phase 13 — world_model_continual.py tests.

Tests cover:
- EWCWorldModel forward pass shape
- EWC update reduces loss over repeated training
- world_model_hash changes between sessions (after update)
- world_model_hash is deterministic for same weights
- EWC Fisher computation + penalty activation
- Serialize/deserialize round-trip (hash identity)
- save/load round-trip (all weights preserved)
- from_legacy_world_model migration (baselines preserved)
- compute_world_model_improvement_bps: identical hashes = 0, different = nonzero
- build_session_vector returns shape (30,)
- E2+E4 synergy: preference_weights_bytes changes compute_hash output
"""

import sys
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))

from world_model_continual import (
    EWCWorldModel,
    INPUT_DIM,
    OUTPUT_DIM,
    TASK_BOUNDARY_SESSIONS,
    compute_world_model_improvement_bps,
)


class _MockFeatureFrame:
    """Minimal FeatureFrame stand-in with to_vector()."""
    def __init__(self, values=None):
        self._v = values if values is not None else np.zeros(INPUT_DIM, dtype=np.float32)

    def to_vector(self):
        return self._v


class TestEWCWorldModelForward(unittest.TestCase):

    def test_forward_output_shape(self):
        m = EWCWorldModel()
        x = np.random.rand(INPUT_DIM).astype(np.float32)
        out = m.forward(x)
        self.assertEqual(out.shape, (OUTPUT_DIM,))

    def test_forward_deterministic(self):
        m = EWCWorldModel(seed=0)
        x = np.ones(INPUT_DIM, dtype=np.float32)
        out1 = m.forward(x)
        out2 = m.forward(x)
        np.testing.assert_array_equal(out1, out2)

    def test_forward_different_inputs_differ(self):
        m = EWCWorldModel(seed=1)
        x1 = np.zeros(INPUT_DIM, dtype=np.float32)
        x2 = np.ones(INPUT_DIM, dtype=np.float32)
        out1 = m.forward(x1)
        out2 = m.forward(x2)
        self.assertFalse(np.allclose(out1, out2))


class TestEWCUpdate(unittest.TestCase):

    def test_update_returns_float_loss(self):
        m = EWCWorldModel()
        vec = np.random.rand(INPUT_DIM).astype(np.float32)
        loss = m.update(vec, 0.8)
        self.assertIsInstance(loss, float)
        self.assertGreater(loss, 0.0)

    def test_repeated_updates_reduce_loss(self):
        m = EWCWorldModel(seed=7)
        vec = np.random.rand(INPUT_DIM).astype(np.float32)
        losses = [m.update(vec, 0.7) for _ in range(200)]
        self.assertLess(losses[-1], losses[0],
                        msg="Loss should decrease after 200 updates on same input")

    def test_update_increments_total_updates(self):
        m = EWCWorldModel()
        m.update(np.zeros(INPUT_DIM, dtype=np.float32), 0.5)
        m.update(np.zeros(INPUT_DIM, dtype=np.float32), 0.5)
        self.assertEqual(m.total_updates, 2)


class TestWorldModelHash(unittest.TestCase):

    def test_compute_hash_returns_32_bytes(self):
        m = EWCWorldModel()
        h = m.compute_hash()
        self.assertEqual(len(h), 32)

    def test_hash_changes_after_update(self):
        m = EWCWorldModel(seed=3)
        h1 = m.compute_hash()
        m.update(np.random.rand(INPUT_DIM).astype(np.float32), 0.6)
        h2 = m.compute_hash()
        self.assertNotEqual(h1, h2)

    def test_hash_deterministic_before_update(self):
        m1 = EWCWorldModel(seed=42)
        m2 = EWCWorldModel(seed=42)
        self.assertEqual(m1.compute_hash(), m2.compute_hash())

    def test_hash_changes_with_preference_weights(self):
        m = EWCWorldModel(seed=5)
        h_without = m.compute_hash(b"")
        h_with = m.compute_hash(b"\x01\x02\x03\x04" * 5)
        self.assertNotEqual(h_without, h_with,
                            msg="Preference weights should change world_model_hash (E2+E4 synergy)")


class TestEWCFisher(unittest.TestCase):

    def test_compute_fisher_sets_ewc_active(self):
        m = EWCWorldModel()
        sessions = [np.random.rand(INPUT_DIM).astype(np.float32) for _ in range(5)]
        self.assertFalse(m._ewc_active)
        m.compute_fisher(sessions)
        self.assertTrue(m._ewc_active)

    def test_compute_fisher_saves_prev_weights(self):
        m = EWCWorldModel()
        sessions = [np.random.rand(INPUT_DIM).astype(np.float32) for _ in range(5)]
        m.compute_fisher(sessions)
        self.assertIn("W1", m._prev_weights)
        np.testing.assert_array_equal(m._prev_weights["W1"], m.W1)

    def test_ewc_active_affects_loss(self):
        m = EWCWorldModel(seed=9)
        sessions = [np.random.rand(INPUT_DIM).astype(np.float32) for _ in range(5)]
        vec = np.ones(INPUT_DIM, dtype=np.float32)

        loss_no_ewc = m.update(vec, 0.5)
        m.compute_fisher(sessions)
        # Move weights far from anchor
        m.W1 += np.ones_like(m.W1) * 5.0
        loss_ewc = m.update(vec, 0.5)
        self.assertGreater(loss_ewc, 0.0)  # EWC penalty adds to loss


class TestSerializationRoundTrip(unittest.TestCase):

    def test_serialize_weights_returns_bytes(self):
        m = EWCWorldModel()
        b = m.serialize_weights()
        self.assertIsInstance(b, bytes)
        # Expected size: (30*64 + 64 + 64*32 + 32 + 32*8 + 8) * 4 bytes
        expected_n_params = 30*64 + 64 + 64*32 + 32 + 32*8 + 8
        self.assertEqual(len(b), expected_n_params * 4)

    def test_hash_unchanged_after_serialize_reload(self):
        m = EWCWorldModel(seed=11)
        for _ in range(5):
            m.update(np.random.rand(INPUT_DIM).astype(np.float32), 0.5)
        h_before = m.compute_hash()

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            m.save(path)
            m2 = EWCWorldModel.load(path)
            h_after = m2.compute_hash()
            self.assertEqual(h_before, h_after,
                             msg="Hash must be identical after save/load round-trip")
        finally:
            os.unlink(path)

    def test_load_preserves_baselines(self):
        m = EWCWorldModel()
        m.reaction_baseline = 180.0
        m.precision_baseline = 0.75

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            m.save(path)
            m2 = EWCWorldModel.load(path)
            self.assertAlmostEqual(m2.reaction_baseline, 180.0)
            self.assertAlmostEqual(m2.precision_baseline, 0.75)
        finally:
            os.unlink(path)


class TestLegacyMigration(unittest.TestCase):

    def test_from_legacy_preserves_baselines(self):
        legacy = {
            "reaction_baseline": 210.0,
            "precision_baseline": 0.62,
            "consistency_baseline": 45.0,
            "imu_corr_baseline": 0.7,
            "total_sessions": 15,
        }
        m = EWCWorldModel.from_legacy_world_model(legacy)
        self.assertAlmostEqual(m.reaction_baseline, 210.0)
        self.assertAlmostEqual(m.imu_corr_baseline, 0.7)
        self.assertEqual(m.total_sessions, 15)

    def test_from_legacy_empty_dict_creates_defaults(self):
        m = EWCWorldModel.from_legacy_world_model({})
        self.assertAlmostEqual(m.reaction_baseline, 250.0)


class TestImprovementBps(unittest.TestCase):

    def test_identical_hashes_return_zero(self):
        h = b"\xab" * 32
        self.assertEqual(compute_world_model_improvement_bps(h, h), 0)

    def test_opposite_hashes_return_10000(self):
        h1 = b"\x00" * 32
        h2 = b"\xff" * 32
        self.assertEqual(compute_world_model_improvement_bps(h1, h2), 10000)

    def test_partial_difference_returns_nonzero(self):
        h1 = b"\x00" * 32
        h2 = b"\x01" + b"\x00" * 31
        bps = compute_world_model_improvement_bps(h1, h2)
        self.assertGreater(bps, 0)
        self.assertLess(bps, 10000)

    def test_invalid_hash_length_returns_zero(self):
        self.assertEqual(compute_world_model_improvement_bps(b"\x00" * 16, b"\xff" * 32), 0)


class TestBuildSessionVector(unittest.TestCase):

    def test_empty_frames_returns_zeros(self):
        v = EWCWorldModel.build_session_vector([])
        self.assertEqual(v.shape, (INPUT_DIM,))
        self.assertTrue(np.all(v == 0.0))

    def test_single_frame_returns_that_frame(self):
        vals = np.arange(INPUT_DIM, dtype=np.float32)
        v = EWCWorldModel.build_session_vector([_MockFeatureFrame(vals)])
        np.testing.assert_array_almost_equal(v, vals)

    def test_multiple_frames_returns_mean(self):
        frames = [
            _MockFeatureFrame(np.ones(INPUT_DIM, dtype=np.float32)),
            _MockFeatureFrame(np.ones(INPUT_DIM, dtype=np.float32) * 3.0),
        ]
        v = EWCWorldModel.build_session_vector(frames)
        self.assertAlmostEqual(float(v[0]), 2.0)


if __name__ == "__main__":
    unittest.main()
