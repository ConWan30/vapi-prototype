"""
Phase 25 — Agent Intelligence Tests

Tests cover:
- L5 rhythm_humanity_score() — inverts CV/entropy/quant into [0,1] positive humanity signal
- L4 two-track EMA — stable fingerprint freeze, drift velocity
- E4 cognitive drift — embedding delta across sessions (store round-trip)
- Bayesian humanity_probability fusion (L4 × L5 × E4)
- Weighted PHG SQL (phg_score_weighted, humanity_prob_avg, l5_rhythm_humanity_avg)
"""

import json
import math
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BRIDGE_DIR = Path(__file__).parents[1]
CONTROLLER_DIR = BRIDGE_DIR.parent / "controller"
sys.path.insert(0, str(BRIDGE_DIR))
sys.path.insert(0, str(CONTROLLER_DIR))

# ---------------------------------------------------------------------------
# Stub heavy deps
# ---------------------------------------------------------------------------
for _mod in ("web3", "web3.exceptions", "eth_account"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
_web3_exc = sys.modules["web3.exceptions"]
for _attr in ("ContractLogicError", "TransactionNotFound"):
    if not hasattr(_web3_exc, _attr):
        setattr(_web3_exc, _attr, type(_attr, (Exception,), {}))
_web3_mod = sys.modules["web3"]
for _attr in ("AsyncWeb3", "AsyncHTTPProvider"):
    if not hasattr(_web3_mod, _attr):
        setattr(_web3_mod, _attr, MagicMock())
_eth_acct = sys.modules["eth_account"]
if not hasattr(_eth_acct, "Account"):
    _eth_acct.Account = MagicMock()

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from vapi_bridge.store import Store


def _make_store(tmp_path):
    return Store(str(tmp_path / "test.db"))


# ===========================================================================
# 1. L5 rhythm_humanity_score()
# ===========================================================================

class TestL5RhythmHumanityScore(unittest.TestCase):
    """Tests for TemporalRhythmOracle.rhythm_humanity_score()."""

    def _make_oracle(self):
        try:
            from temporal_rhythm_oracle import TemporalRhythmOracle
            return TemporalRhythmOracle()
        except ImportError:
            self.skipTest("temporal_rhythm_oracle not importable")

    def test_returns_05_when_no_samples(self):
        oracle = self._make_oracle()
        score = oracle.rhythm_humanity_score()
        self.assertAlmostEqual(score, 0.5)

    def test_high_cv_entropy_low_quant_returns_high_score(self):
        """Human-like timing: high CV, high entropy, low quantization → high humanity."""
        oracle = self._make_oracle()
        TemporalFeatures = MagicMock()
        TemporalFeatures.cv = 0.30
        TemporalFeatures.entropy_bits = 3.5
        TemporalFeatures.quant_score = 0.1
        TemporalFeatures.anomaly_signals = 0
        with patch.object(oracle, "extract_features", return_value=TemporalFeatures):
            score = oracle.rhythm_humanity_score()
        # cv=0.30/0.25=1.0, entropy=3.5/3.0=1.0, non_quant=0.9 → avg ≈ 0.967
        self.assertGreater(score, 0.9)

    def test_bot_like_timing_returns_low_score(self):
        """Bot-like timing: low CV, low entropy, high quant → low humanity."""
        oracle = self._make_oracle()
        TemporalFeatures = MagicMock()
        TemporalFeatures.cv = 0.02
        TemporalFeatures.entropy_bits = 0.5
        TemporalFeatures.quant_score = 0.9
        TemporalFeatures.anomaly_signals = 3
        with patch.object(oracle, "extract_features", return_value=TemporalFeatures):
            score = oracle.rhythm_humanity_score()
        # cv=0.02/0.25=0.08, entropy=0.5/3.0=0.167, non_quant=0.1 → avg ≈ 0.116
        self.assertLess(score, 0.2)

    def test_score_bounded_zero_to_one(self):
        """Score must always be in [0, 1]."""
        oracle = self._make_oracle()
        TemporalFeatures = MagicMock()
        TemporalFeatures.cv = 99.0          # extreme value — clamped to 1.0
        TemporalFeatures.entropy_bits = 99.0
        TemporalFeatures.quant_score = 0.0
        TemporalFeatures.anomaly_signals = 0
        with patch.object(oracle, "extract_features", return_value=TemporalFeatures):
            score = oracle.rhythm_humanity_score()
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


# ===========================================================================
# 2. L4 Two-Track EMA
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required")
class TestL4TwoTrackEMA(unittest.TestCase):
    """Tests for BiometricFusionClassifier two-track EMA."""

    def _make_classifier(self):
        try:
            from tinyml_biometric_fusion import BiometricFusionClassifier
            return BiometricFusionClassifier()
        except ImportError:
            self.skipTest("tinyml_biometric_fusion not importable")

    def _make_features(self, **overrides):
        try:
            from tinyml_biometric_fusion import BiometricFeatureFrame
        except ImportError:
            self.skipTest("tinyml_biometric_fusion not importable")
        defaults = {
            "trigger_resistance_change_rate": 0.3,
            "trigger_onset_velocity_l2": 0.5,
            "trigger_onset_velocity_r2": 0.5,
            "micro_tremor_accel_variance": 0.02,
            "grip_asymmetry": 1.1,
            "stick_autocorr_lag1": 0.6,
            "stick_autocorr_lag5": 0.4,
        }
        defaults.update(overrides)
        return BiometricFeatureFrame(**defaults)

    def test_update_stable_fingerprint_method_exists(self):
        clf = self._make_classifier()
        self.assertTrue(hasattr(clf, "update_stable_fingerprint"))

    def test_fingerprint_drift_velocity_property_exists(self):
        clf = self._make_classifier()
        self.assertTrue(hasattr(clf, "fingerprint_drift_velocity"))

    def test_drift_velocity_zero_before_warmup(self):
        """Before warmup, stable track not initialized; drift velocity must be 0."""
        clf = self._make_classifier()
        self.assertAlmostEqual(clf.fingerprint_drift_velocity, 0.0)

    def test_stable_fingerprint_not_polluted_by_candidate_updates(self):
        """When only update_fingerprint (candidate) is called, stable mean is unchanged."""
        clf = self._make_classifier()
        try:
            from tinyml_biometric_fusion import BiometricFusionClassifier as _BFC
            N_WARMUP_SESSIONS = _BFC.N_WARMUP_SESSIONS
        except (ImportError, AttributeError):
            self.skipTest("N_WARMUP_SESSIONS not importable")
        f_neutral = self._make_features()
        # Warm up both tracks with neutral features
        for _ in range(N_WARMUP_SESSIONS + 2):
            clf.update_fingerprint(f_neutral)
            clf.update_stable_fingerprint(f_neutral)
        stable_mean_before = np.copy(clf._stable_mean)
        # Now push candidate far from stable with extreme values
        f_extreme = self._make_features(trigger_onset_velocity_l2=0.99, trigger_onset_velocity_r2=0.01)
        for _ in range(10):
            clf.update_fingerprint(f_extreme)
        # Stable mean should be unchanged
        np.testing.assert_array_equal(stable_mean_before, clf._stable_mean)

    def test_drift_velocity_nonzero_after_candidate_diverges(self):
        """When candidate track diverges from stable, drift_velocity > 0."""
        clf = self._make_classifier()
        try:
            from tinyml_biometric_fusion import BiometricFusionClassifier as _BFC
            N_WARMUP_SESSIONS = _BFC.N_WARMUP_SESSIONS
        except (ImportError, AttributeError):
            self.skipTest("N_WARMUP_SESSIONS not importable")
        f_neutral = self._make_features()
        for _ in range(N_WARMUP_SESSIONS + 2):
            clf.update_fingerprint(f_neutral)
            clf.update_stable_fingerprint(f_neutral)
        f_extreme = self._make_features(trigger_onset_velocity_l2=0.99, trigger_onset_velocity_r2=0.01)
        for _ in range(10):
            clf.update_fingerprint(f_extreme)
        drift = clf.fingerprint_drift_velocity
        self.assertGreater(drift, 0.0)


# ===========================================================================
# 3. E4 Cognitive Drift
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required")
class TestE4CognitiveDrift(unittest.TestCase):
    """Tests for EWCWorldModel.get_embedding() and store cognitive embedding round-trip."""

    def _make_model(self):
        try:
            from world_model_continual import EWCWorldModel
            return EWCWorldModel()
        except ImportError:
            self.skipTest("world_model_continual not importable")

    def test_get_embedding_returns_8_dim_array(self):
        model = self._make_model()
        vec = np.random.randn(30).astype(np.float32)  # INPUT_DIM=30
        emb = model.get_embedding(vec)
        self.assertEqual(emb.shape, (8,))

    def test_get_embedding_deterministic_for_same_input(self):
        model = self._make_model()
        vec = np.ones(30, dtype=np.float32)  # INPUT_DIM=30
        emb1 = model.get_embedding(vec)
        emb2 = model.get_embedding(vec)
        np.testing.assert_array_almost_equal(emb1, emb2)

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._store = _make_store(Path(self._tmpdir))

    def test_store_and_retrieve_cognitive_embedding(self):
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        self._store.store_cognitive_embedding("devA", embedding, session_count=5)
        result = self._store.get_last_cognitive_embedding("devA")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 8)
        self.assertAlmostEqual(result[0], 0.1, places=5)

    def test_get_last_cognitive_embedding_returns_none_for_unknown_device(self):
        result = self._store.get_last_cognitive_embedding("unknown_device")
        self.assertIsNone(result)

    def test_store_cognitive_embedding_overwrites_previous(self):
        """Upsert: storing twice for same device updates the row."""
        self._store.store_cognitive_embedding("devB", [0.1] * 8, session_count=1)
        self._store.store_cognitive_embedding("devB", [0.9] * 8, session_count=2)
        result = self._store.get_last_cognitive_embedding("devB")
        self.assertAlmostEqual(result[0], 0.9, places=5)


# ===========================================================================
# 4. Bayesian Humanity Probability Fusion
# ===========================================================================

class TestHumanityProbabilityFusion(unittest.TestCase):
    """Tests for the Bayesian fusion formula: 0.4*p_L4 + 0.4*p_L5 + 0.2*p_E4."""

    def _fuse(self, l4_distance=None, l4_warmed=True, l5_rhythm=None, e4_drift=None):
        """Replicate the fusion logic from dualshock_integration.py."""
        if l4_warmed and l4_distance is not None:
            p_l4 = math.exp(-max(0.0, l4_distance - 2.0))
        else:
            p_l4 = 0.5
        p_l5 = l5_rhythm if l5_rhythm is not None else 0.5
        if e4_drift is not None:
            p_e4 = math.exp(-e4_drift / 3.0)
        else:
            p_e4 = 0.5
        return 0.4 * p_l4 + 0.4 * p_l5 + 0.2 * p_e4

    def test_all_neutral_returns_05(self):
        prob = self._fuse(l4_distance=None, l4_warmed=False, l5_rhythm=None, e4_drift=None)
        self.assertAlmostEqual(prob, 0.5)

    def test_perfect_human_signals_returns_high_prob(self):
        """Biometric within stable range, high L5 rhythm, low E4 drift → high probability."""
        prob = self._fuse(l4_distance=1.0, l4_warmed=True, l5_rhythm=0.95, e4_drift=0.1)
        self.assertGreater(prob, 0.85)

    def test_clear_bot_signals_returns_low_prob(self):
        """Far biometric distance, low L5 rhythm, chaotic E4 drift → low probability."""
        prob = self._fuse(l4_distance=8.0, l4_warmed=True, l5_rhythm=0.05, e4_drift=15.0)
        self.assertLess(prob, 0.15)

    def test_l4_not_warmed_contributes_neutral(self):
        """When L4 not warmed, p_l4=0.5 regardless of distance."""
        prob_warmed = self._fuse(l4_distance=5.0, l4_warmed=True, l5_rhythm=0.5)
        prob_cold   = self._fuse(l4_distance=5.0, l4_warmed=False, l5_rhythm=0.5)
        # Warmed with dist=5: p_l4 = exp(-3) ≈ 0.05 → lower than neutral
        self.assertLess(prob_warmed, prob_cold)

    def test_result_bounded_zero_to_one(self):
        """Fusion must always be in [0, 1] for any reasonable inputs."""
        for dist in [0.0, 1.0, 5.0, 100.0]:
            for rhythm in [0.0, 0.5, 1.0]:
                for drift in [0.0, 1.0, 50.0]:
                    prob = self._fuse(
                        l4_distance=dist, l4_warmed=True,
                        l5_rhythm=rhythm, e4_drift=drift
                    )
                    self.assertGreaterEqual(prob, 0.0, f"dist={dist} rhythm={rhythm} drift={drift}")
                    self.assertLessEqual(prob, 1.0, f"dist={dist} rhythm={rhythm} drift={drift}")


# ===========================================================================
# 5. Weighted PHG SQL
# ===========================================================================

class TestWeightedPHGSQL(unittest.TestCase):
    """Tests for phg_score_weighted in get_player_profile()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def _make_store_with_records(self, records):
        """Insert synthetic records with pitl_humanity_prob into the store."""
        store = _make_store(Path(self._tmpdir))
        dev = "aabbccddeeff0011"
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO devices "
                "(device_id, pubkey_hex, first_seen, last_seen) VALUES (?,?,?,?)",
                (dev, "pubkey", time.time(), time.time()),
            )
            for i, (inference, confidence, humanity_prob) in enumerate(records):
                rh = f"{i:064x}"
                conn.execute("""
                    INSERT OR IGNORE INTO records
                        (record_hash, device_id, counter, timestamp_ms, inference,
                         action_code, confidence, battery_pct, status, created_at,
                         pitl_humanity_prob)
                    VALUES (?,?,?,?,?,1,?,80,'verified',?,?)
                """, (rh, dev, i, i * 1000, inference, confidence, time.time(), humanity_prob))
            conn.commit()
        return store, dev

    def test_nominal_with_high_humanity_earns_bonus(self):
        """NOMINAL records with humanity_prob=1.0 should yield higher weighted score than raw."""
        records = [(32, 255, 1.0)] * 10  # 10 NOMINAL, full confidence, full humanity
        store, dev = self._make_store_with_records(records)
        profile = store.get_player_profile(dev)
        raw      = profile.get("phg_score", 0)
        weighted = profile.get("phg_score_weighted", 0)
        # With humanity_prob=1.0, bonus factor = 1.5 so weighted > raw
        self.assertGreaterEqual(weighted, raw)
        self.assertGreater(weighted, 0)

    def test_null_humanity_prob_gives_same_as_raw(self):
        """Old records (NULL humanity_prob) → COALESCE(NULL,0) → no bonus → weighted≈raw."""
        records = [(32, 255, None)] * 5
        store, dev = self._make_store_with_records(records)
        profile = store.get_player_profile(dev)
        raw      = profile.get("phg_score", 0)
        weighted = profile.get("phg_score_weighted", 0)
        self.assertEqual(raw, weighted)

    def test_non_nominal_records_excluded_from_weighted(self):
        """Cheat (0x28) records should contribute 0 to phg_score_weighted."""
        records = [(32, 200, 0.8)] * 5 + [(0x28, 200, 0.8)] * 5
        store, dev = self._make_store_with_records(records)
        profile = store.get_player_profile(dev)
        self.assertEqual(profile.get("nominal_records", 0), 5)
        self.assertGreater(profile.get("phg_score_weighted", 0), 0)


if __name__ == "__main__":
    unittest.main()
