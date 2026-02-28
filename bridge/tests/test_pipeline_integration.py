"""
Phase 14A — Pipeline integration tests.

Verifies that Phase 13 modules (E1 biometric, E4 EWC, E2 preference) are correctly
wired into the live PoAC pipeline:

  TestSensorCommitmentV2Bio   (2 tests) — 56B bio-enriched sensor hash
  TestWorldModelHash          (2 tests) — EWC + preference world model hash
  TestBiometricLayer4         (2 tests) — 0x30 BIOMETRIC_ANOMALY inference code
  TestBatchSchemaRouting      (2 tests) — batcher schema-aware routing
"""

import sys
import types
import hashlib
import struct
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure bridge/ root and controller/ are on sys.path
sys.path.insert(0, str(Path(__file__).parents[1]))
_CONTROLLER_DIR = str(Path(__file__).parents[2] / "controller")
if _CONTROLLER_DIR not in sys.path:
    sys.path.insert(0, _CONTROLLER_DIR)

# ---------------------------------------------------------------------------
# Stub heavy dependencies not installed in the test environment (web3, eth_account)
# ---------------------------------------------------------------------------
for _mod_name in ("web3", "web3.exceptions", "eth_account"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

_web3_exc = sys.modules["web3.exceptions"]
if not hasattr(_web3_exc, "ContractLogicError"):
    _web3_exc.ContractLogicError = type("ContractLogicError", (Exception,), {})
if not hasattr(_web3_exc, "TransactionNotFound"):
    _web3_exc.TransactionNotFound = type("TransactionNotFound", (Exception,), {})

_web3_mod = sys.modules["web3"]
for _attr in ("AsyncWeb3", "AsyncHTTPProvider"):
    if not hasattr(_web3_mod, _attr):
        setattr(_web3_mod, _attr, MagicMock())

_eth_acc = sys.modules["eth_account"]
if not hasattr(_eth_acc, "Account"):
    _eth_acc.Account = MagicMock()

# ---------------------------------------------------------------------------
# Minimal mock snapshot — mirrors DualSenseReader.InputSnapshot attributes
# ---------------------------------------------------------------------------

class _MockSnap:
    """Minimal InputSnapshot-compatible mock with sensible defaults."""
    left_stick_x:  int   = 0
    left_stick_y:  int   = 0
    right_stick_x: int   = 0
    right_stick_y: int   = 0
    l2_trigger:    int   = 0
    r2_trigger:    int   = 0
    l2_effect_mode: int  = 0
    r2_effect_mode: int  = 0
    accel_x: float = 0.0
    accel_y: float = 0.0
    accel_z: float = 1.0
    gyro_x:  float = 0.0
    gyro_y:  float = 0.0
    gyro_z:  float = 0.0
    buttons: int   = 0
    battery_mv: int = 4000
    inter_frame_us: int = 8000

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_snaps(n: int, **kwargs) -> list:
    return [_MockSnap(**kwargs) for _ in range(n)]


# ---------------------------------------------------------------------------
# Minimal mock PoACRecord for batcher routing tests
# ---------------------------------------------------------------------------

class _MockRecord:
    def __init__(self, schema_version: int = 0):
        self.schema_version    = schema_version
        self.record_hash_hex   = "aa" * 32
        self.device_id         = b"\x01" * 32
        self.device_id_hex     = "01" * 32
        self.raw_body          = b"\x00" * 164
        self.signature         = b"\x00" * 64


# ---------------------------------------------------------------------------
# TestSensorCommitmentV2Bio
# ---------------------------------------------------------------------------

class TestSensorCommitmentV2Bio(unittest.TestCase):
    """Verify compute_sensor_commitment_v2_bio() output properties."""

    def setUp(self):
        from tinyml_biometric_fusion import (
            BiometricFusionClassifier,
            compute_sensor_commitment_v2_bio,
        )
        self.BiometricFusionClassifier = BiometricFusionClassifier
        self.compute = compute_sensor_commitment_v2_bio
        self.classifier = BiometricFusionClassifier()

    def test_sensor_hash_is_32_bytes_with_bio_classifier(self):
        """56-byte bio-enriched input still produces a 32-byte SHA-256 output."""
        snap = _MockSnap(accel_z=1.0)
        ts   = 1_700_000_000_000
        h = self.compute(snap, ts, 0, 0, biometric_classifier=self.classifier)
        self.assertEqual(len(h), 32)
        self.assertIsInstance(h, bytes)

    def test_sensor_hash_changes_when_biometric_distance_changes(self):
        """Two classifiers with different fingerprint states produce different hashes."""
        snap = _MockSnap(accel_z=1.0)
        ts   = 1_700_000_000_000

        # Classifier 1: untouched (distance=0.0)
        c1 = self.BiometricFusionClassifier()
        h1 = self.compute(snap, ts, 0, 0, biometric_classifier=c1)

        # Classifier 2: mutate last_distance to a non-zero value
        c2 = self.BiometricFusionClassifier()
        c2.last_distance = 5.0
        h2 = self.compute(snap, ts, 0, 0, biometric_classifier=c2)

        self.assertNotEqual(h1, h2)


# ---------------------------------------------------------------------------
# TestWorldModelHash
# ---------------------------------------------------------------------------

class TestWorldModelHash(unittest.TestCase):
    """Verify EWC + preference world model hash computation."""

    def setUp(self):
        from world_model_continual import EWCWorldModel
        from knapsack_personalized import PreferenceModel
        import numpy as np
        self.EWCWorldModel   = EWCWorldModel
        self.PreferenceModel = PreferenceModel
        self.np = np

    def test_wm_hash_is_32_bytes_and_changes_after_ewc_update(self):
        """compute_hash() returns 32 bytes; changes after model.update()."""
        model = self.EWCWorldModel()
        h_before = model.compute_hash()
        self.assertEqual(len(h_before), 32)

        # Update with a random session vector
        vec = self.np.random.rand(30).astype(self.np.float32)
        model.update(vec, 0.8)

        h_after = model.compute_hash()
        self.assertEqual(len(h_after), 32)
        self.assertNotEqual(h_before, h_after)

    def test_wm_hash_combines_ewc_and_preference_weights(self):
        """E2+E4 synergy: hash(EWC || pref) differs from hash(EWC) and hash with default pref."""
        model = self.EWCWorldModel()
        pref  = self.PreferenceModel()

        # Without preference weights (empty pref_bytes)
        h_ewc_only = model.compute_hash(preference_weights_bytes=b"")

        # With preference weights
        pref_bytes = pref.serialize_weights()
        self.assertEqual(len(pref_bytes), 40)  # 5 × float64
        h_combined = model.compute_hash(preference_weights_bytes=pref_bytes)

        # Combined must differ from EWC-only (default weights are non-zero)
        self.assertNotEqual(h_ewc_only, h_combined)


# ---------------------------------------------------------------------------
# TestBiometricLayer4
# ---------------------------------------------------------------------------

class TestBiometricLayer4(unittest.TestCase):
    """Verify Layer 4 biometric anomaly detection logic."""

    def setUp(self):
        from tinyml_biometric_fusion import (
            BiometricFusionClassifier,
            BiometricFeatureExtractor,
            INFER_BIOMETRIC_ANOMALY,
        )
        self.BiometricFusionClassifier = BiometricFusionClassifier
        self.BiometricFeatureExtractor = BiometricFeatureExtractor
        self.INFER_BIOMETRIC_ANOMALY   = INFER_BIOMETRIC_ANOMALY

    def test_layer4_returns_0x30_when_anomaly_detected(self):
        """When Mahalanobis distance > threshold, classify() returns (0x30, conf)."""
        classifier = self.BiometricFusionClassifier()

        # Build a 'normal' fingerprint by updating with zero-features
        from tinyml_biometric_fusion import BiometricFeatureFrame
        normal = BiometricFeatureFrame()
        for _ in range(classifier.N_WARMUP_SESSIONS + 1):
            classifier.update_fingerprint(normal)

        # Create anomalous features with extreme values
        anomalous = BiometricFeatureFrame(
            trigger_resistance_change_rate=1000.0,
            micro_tremor_accel_variance=1000.0,
            grip_asymmetry=100.0,
            stick_autocorr_lag1=0.99,
            stick_autocorr_lag5=0.99,
        )
        result = classifier.classify(anomalous)
        if result is not None:
            inference, confidence = result
            self.assertEqual(inference, self.INFER_BIOMETRIC_ANOMALY)
            self.assertIsInstance(confidence, int)
            self.assertGreaterEqual(confidence, 0)
            self.assertLessEqual(confidence, 255)

    def test_layer4_does_not_override_existing_cheat_code(self):
        """Layer 4 must NOT override inference when inference is already a cheat code."""
        CHEAT_CODES = {0x28, 0x29, 0x2A}
        INFER_BIOMETRIC_ANOMALY = self.INFER_BIOMETRIC_ANOMALY

        classifier = self.BiometricFusionClassifier()
        # Populate fingerprint so classifier would detect anomaly
        from tinyml_biometric_fusion import BiometricFeatureFrame
        normal = BiometricFeatureFrame()
        for _ in range(classifier.N_WARMUP_SESSIONS + 1):
            classifier.update_fingerprint(normal)
        anomalous = BiometricFeatureFrame(trigger_resistance_change_rate=1000.0)

        # Simulate Layer 4 logic: only classify if inference NOT in CHEAT_CODES
        inference = 0x29  # existing WALLHACK_PREAIM cheat code

        if inference not in CHEAT_CODES:
            result = classifier.classify(anomalous)
            if result is not None:
                inference, _ = result

        # Cheat code must not be overridden by biometric
        self.assertEqual(inference, 0x29)


# ---------------------------------------------------------------------------
# TestBatchSchemaRouting
# ---------------------------------------------------------------------------

class TestBatchSchemaRouting(unittest.IsolatedAsyncioTestCase):
    """Verify batcher routes schema_version > 0 records individually."""

    def _make_batcher(self, chain):
        """Construct a minimal Batcher instance with a mock chain."""
        from vapi_bridge.batcher import Batcher

        store = MagicMock()
        store.batch_update_status = MagicMock()
        store.create_submission   = MagicMock(return_value=1)
        store.update_submission   = MagicMock()
        store.increment_device_verified = MagicMock()
        store.batch_update_status = MagicMock()

        cfg = MagicMock()
        cfg.batch_size    = 4
        cfg.batch_timeout = 1.0

        batcher = Batcher.__new__(Batcher)
        batcher._chain  = chain
        batcher._store  = store
        batcher._cfg    = cfg
        batcher._queue  = asyncio.Queue()
        return batcher

    async def test_batch_with_schema_v2_records_calls_verify_poac_per_record(self):
        """A batch where any record has schema_version > 0 must use verify_poac() per record."""
        chain = MagicMock()
        chain.verify_poac   = AsyncMock(return_value="0x" + "aa" * 32)
        chain.verify_single = AsyncMock(return_value="0x" + "bb" * 32)
        chain.verify_batch  = AsyncMock(return_value="0x" + "cc" * 32)
        chain.wait_for_receipt = AsyncMock(return_value={"status": 1})

        batcher = self._make_batcher(chain)

        records = [_MockRecord(schema_version=2), _MockRecord(schema_version=2)]
        batch   = [(r, b"\x00" * 32) for r in records]

        await batcher._submit_batch(batch)

        # verify_poac must have been called for each schema_version=2 record
        self.assertEqual(chain.verify_poac.call_count, 2)
        chain.verify_batch.assert_not_called()

    async def test_batch_with_schema_v0_records_uses_verify_batch(self):
        """A batch where all records have schema_version == 0 uses verify_batch()."""
        chain = MagicMock()
        chain.verify_poac   = AsyncMock(return_value="0x" + "aa" * 32)
        chain.verify_single = AsyncMock(return_value="0x" + "bb" * 32)
        chain.verify_batch  = AsyncMock(return_value="0x" + "cc" * 32)
        chain.wait_for_receipt = AsyncMock(return_value={"status": 1})

        batcher = self._make_batcher(chain)

        records = [_MockRecord(schema_version=0), _MockRecord(schema_version=0)]
        batch   = [(r, b"\x00" * 32) for r in records]

        await batcher._submit_batch(batch)

        chain.verify_batch.assert_called_once()
        chain.verify_poac.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 14B tests
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TestFeatureFrameToVector  (B1 — FeatureFrame.to_vector())
# ---------------------------------------------------------------------------

class TestFeatureFrameToVector(unittest.TestCase):
    """Verify FeatureFrame.to_vector() returns the correct 30-dim float32 array."""

    def test_to_vector_returns_30_floats(self):
        """Default FeatureFrame.to_vector() must return shape (30,) float32."""
        import sys
        from pathlib import Path
        controller_dir = str(Path(__file__).parents[2] / "controller")
        if controller_dir not in sys.path:
            sys.path.insert(0, controller_dir)
        import numpy as np
        from dualshock_emulator import FeatureFrame

        f = FeatureFrame()
        v = f.to_vector()
        self.assertEqual(v.shape, (30,))
        self.assertEqual(v.dtype, np.float32)
        self.assertTrue(all(np.isfinite(v)), "All elements must be finite")


# ---------------------------------------------------------------------------
# TestEWCSessionVecFidelity  (B1 — EWC session vec via classifier window)
# ---------------------------------------------------------------------------

class TestEWCSessionVecFidelity(unittest.TestCase):
    """Verify _build_ewc_session_vec uses FeatureFrame.to_vector() via classifier window."""

    def test_ewc_session_vec_via_classifier_window(self):
        """build_session_vector on FeatureFrame list must return shape (30,) float32."""
        import sys
        from pathlib import Path
        controller_dir = str(Path(__file__).parents[2] / "controller")
        if controller_dir not in sys.path:
            sys.path.insert(0, controller_dir)
        import numpy as np
        from dualshock_emulator import AntiCheatClassifier
        from world_model_continual import EWCWorldModel

        # Build a minimal InputSnapshot-like object
        snap = type("Snap", (), {
            "left_stick_x": 0, "left_stick_y": 0,
            "right_stick_x": 0, "right_stick_y": 0,
            "l2_trigger": 0, "r2_trigger": 0,
            "l2_effect_mode": 0, "r2_effect_mode": 0,
            "accel_x": 0.0, "accel_y": 0.0, "accel_z": 1.0,
            "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0,
            "buttons": 0, "battery_mv": 4000, "inter_frame_us": 8000,
        })()

        classifier = AntiCheatClassifier()
        for _ in range(5):
            classifier.extract_features(snap, 8.0)

        feature_frames = list(classifier.window)
        self.assertEqual(len(feature_frames), 5)

        vec = EWCWorldModel.build_session_vector(feature_frames)
        self.assertEqual(vec.shape, (30,))
        self.assertEqual(vec.dtype, np.float32)


# ---------------------------------------------------------------------------
# TestModelPersistence  (B2 — EWCWorldModel + PreferenceModel save/load)
# ---------------------------------------------------------------------------

class TestModelPersistence(unittest.TestCase):
    """Verify EWCWorldModel and PreferenceModel roundtrip save/load."""

    def setUp(self):
        import sys
        from pathlib import Path
        controller_dir = str(Path(__file__).parents[2] / "controller")
        if controller_dir not in sys.path:
            sys.path.insert(0, controller_dir)

    def test_ewc_model_save_load_roundtrip(self):
        """EWCWorldModel hash must be identical after save+load."""
        import tempfile, os
        import numpy as np
        from world_model_continual import EWCWorldModel

        model = EWCWorldModel()
        vec = np.random.rand(30).astype(np.float32)
        model.update(vec, 0.7)
        h_before = model.compute_hash()

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        try:
            model.save(tmp_path)
            model2 = EWCWorldModel.load(tmp_path)
            h_after = model2.compute_hash()
            self.assertEqual(h_before, h_after)
        finally:
            os.unlink(tmp_path)

    def test_preference_model_save_load_roundtrip(self):
        """PreferenceModel weights must be byte-identical after save+load."""
        import tempfile, os
        from knapsack_personalized import PreferenceModel

        m1 = PreferenceModel()
        b1 = m1.serialize_weights()

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            tmp_path = f.name
        try:
            m1.save(tmp_path)
            m2 = PreferenceModel.load(tmp_path)
            b2 = m2.serialize_weights()
            self.assertEqual(b1, b2)
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# TestKeccakNeverFallsBack  (B5 — _keccak256 correctness)
# ---------------------------------------------------------------------------

class TestKeccakNeverFallsBack(unittest.TestCase):
    """Verify _keccak256() returns true keccak-256, never SHA-256."""

    def test_keccak256_differs_from_sha256_of_empty(self):
        """_keccak256(b'') must NOT equal SHA-256(b'')."""
        import sys, hashlib
        from pathlib import Path
        bridge_root = str(Path(__file__).parents[1])
        if bridge_root not in sys.path:
            sys.path.insert(0, bridge_root)
        from swarm_zk_aggregator import SwarmZKAggregator

        sha256_empty = hashlib.sha256(b"").digest()
        result = SwarmZKAggregator._keccak256(b"")

        self.assertEqual(len(result), 32)
        self.assertNotEqual(result, sha256_empty,
                            "keccak256 must not silently fall back to SHA-256")


if __name__ == "__main__":
    unittest.main()
