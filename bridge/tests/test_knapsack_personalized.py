"""
Phase 13 — knapsack_personalized.py tests.

Tests cover:
- BountyDescriptor and DeviceState construction
- PreferenceModel.compute_utility() basic shape
- PreferenceModel.compute_utility_with_dp() adds noise (DP active)
- DP budget tracking: budget_spent increments; budget exhaustion falls back to raw utility
- PreferenceModel.update() SGD moves weights toward better outcome
- PreferenceModel.serialize_weights() / from_bytes() round-trip
- preference_hash() changes after weight update
- PersonalizedKnapsack.optimize() returns subset within budget
- Knapsack respects max_active limit
- Knapsack energy budget constraint (no over-commitment)
- Preemption: high-utility unselected bounty displaces lowest-utility selected bounty
- compute_combined_world_model_hash() differs from EWC-only hash (E2+E4 synergy)
"""

import sys
import hashlib
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))

from knapsack_personalized import (
    BountyDescriptor,
    DeviceState,
    PersonalizedKnapsack,
    PreferenceModel,
    compute_combined_world_model_hash,
    PREF_DIM,
    DP_EPSILON,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bounty(
    bounty_id: int = 1,
    reward_micro: int = 500_000,
    sensor_req: int = 0,
    duration_s: int = 600,
    lat_min: float = 37.0,
    lat_max: float = 38.0,
    lon_min: float = -122.5,
    lon_max: float = -121.5,
) -> BountyDescriptor:
    return BountyDescriptor(
        bounty_id=bounty_id,
        reward_iotx_micro=reward_micro,
        sensor_requirements=sensor_req,
        min_samples=10,
        sample_interval_s=60,
        duration_s=duration_s,
        deadline_ms=int(1e12),
        zone_lat_min=lat_min,
        zone_lat_max=lat_max,
        zone_lon_min=lon_min,
        zone_lon_max=lon_max,
    )


def _device(
    battery: int = 80,
    lat: float = 37.5,
    lon: float = -122.0,
    sensors: int = 0xFF,
    tier: int = 2,
) -> DeviceState:
    return DeviceState(
        battery_pct=battery,
        latitude=lat,
        longitude=lon,
        active_sensor_flags=sensors,
        tier=tier,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPreferenceModelUtility(unittest.TestCase):

    def test_compute_utility_returns_float(self):
        pm = PreferenceModel(seed=0)
        u = pm.compute_utility(_bounty(), _device())
        self.assertIsInstance(u, float)

    def test_compute_utility_positive_for_good_bounty(self):
        """A well-matched bounty (inside zone, all sensors, high reward) should score > 0."""
        pm = PreferenceModel(seed=1)
        u = pm.compute_utility(_bounty(reward_micro=5_000_000), _device(battery=90))
        self.assertGreater(u, 0.0)

    def test_utility_higher_for_better_reward(self):
        """Higher reward bounty should score higher than low reward, ceteris paribus."""
        pm = PreferenceModel(seed=2)
        dev = _device()
        low = pm.compute_utility(_bounty(reward_micro=100_000), dev)
        high = pm.compute_utility(_bounty(reward_micro=5_000_000), dev)
        self.assertGreater(high, low)

    def test_sensor_mismatch_reduces_utility(self):
        """Bounty requiring sensor 0x01 unavailable on device should score lower."""
        pm = PreferenceModel(seed=3)
        dev_no_sensor = _device(sensors=0x00)
        dev_has_sensor = _device(sensors=0x01)
        bounty = _bounty(sensor_req=0x01)
        u_no = pm.compute_utility(bounty, dev_no_sensor)
        u_yes = pm.compute_utility(bounty, dev_has_sensor)
        self.assertLess(u_no, u_yes)


class TestDifferentialPrivacy(unittest.TestCase):

    def test_dp_utility_differs_from_raw_utility(self):
        """DP utility should differ from raw utility (noise added)."""
        pm = PreferenceModel(seed=42)
        bounty = _bounty()
        dev = _device()
        raw = pm.compute_utility(bounty, dev)
        # Run multiple DP queries; at least one should differ
        pm2 = PreferenceModel(seed=99)
        dp_results = [pm2.compute_utility_with_dp(bounty, dev) for _ in range(10)]
        raw2 = pm2.compute_utility(bounty, dev)
        # With epsilon=1.5 and scale=1/1.5, noise has nonzero variance — at least one differs
        self.assertTrue(any(abs(r - raw2) > 1e-10 for r in dp_results))

    def test_budget_increments_after_dp_query(self):
        pm = PreferenceModel(seed=5)
        initial = pm.budget_spent
        pm.compute_utility_with_dp(_bounty(), _device())
        self.assertAlmostEqual(pm.budget_spent, initial + DP_EPSILON)

    def test_budget_exhaustion_falls_back_to_raw(self):
        """When budget is exhausted, DP utility equals raw utility."""
        pm = PreferenceModel(seed=6)
        pm.budget_spent = pm.daily_budget  # exhaust budget
        bounty = _bounty()
        dev = _device()
        raw = pm.compute_utility(bounty, dev)
        dp_val = pm.compute_utility_with_dp(bounty, dev)
        self.assertAlmostEqual(dp_val, raw)

    def test_reset_daily_budget_zeroes_spent(self):
        pm = PreferenceModel(seed=7)
        pm.budget_spent = 20.0
        pm.reset_daily_budget()
        self.assertEqual(pm.budget_spent, 0.0)


class TestPreferenceModelLearning(unittest.TestCase):

    def test_update_changes_weights(self):
        pm = PreferenceModel(seed=8)
        original = pm.weights.copy()
        pm.update(_bounty(), _device(), outcome=1.0)
        self.assertFalse(np.allclose(pm.weights, original))

    def test_update_toward_perfect_outcome_reduces_loss(self):
        """Repeated updates toward outcome=1.0 should reduce prediction error."""
        pm = PreferenceModel(seed=9)
        bounty = _bounty(reward_micro=5_000_000)
        dev = _device()
        target = 1.0

        first_pred = pm.compute_utility(bounty, dev)
        for _ in range(100):
            pm.update(bounty, dev, outcome=target, lr=0.05)
        last_pred = pm.compute_utility(bounty, dev)

        # Loss = |pred - target|; should decrease with repeated training
        self.assertLess(abs(last_pred - target), abs(first_pred - target))

    def test_weights_stay_positive_after_updates(self):
        """Weights are clamped to [0.01, 10.0] — never degenerate."""
        pm = PreferenceModel(seed=10)
        for _ in range(200):
            pm.update(_bounty(), _device(), outcome=0.0, lr=0.5)
        self.assertTrue(np.all(pm.weights >= 0.01))


class TestSerializationRoundTrip(unittest.TestCase):

    def test_serialize_returns_correct_byte_length(self):
        pm = PreferenceModel(seed=11)
        b = pm.serialize_weights()
        self.assertEqual(len(b), PREF_DIM * 8)  # 5 float64 = 40 bytes

    def test_from_bytes_restores_weights(self):
        pm = PreferenceModel(seed=12)
        pm.weights = np.array([1.1, 2.2, 3.3, 4.4, 5.5])
        b = pm.serialize_weights()
        pm2 = PreferenceModel.from_bytes(b)
        np.testing.assert_array_almost_equal(pm.weights, pm2.weights)

    def test_preference_hash_is_32_bytes(self):
        pm = PreferenceModel(seed=13)
        h = pm.preference_hash()
        self.assertEqual(len(h), 32)

    def test_preference_hash_changes_after_update(self):
        pm = PreferenceModel(seed=14)
        h1 = pm.preference_hash()
        pm.update(_bounty(), _device(), outcome=0.5)
        h2 = pm.preference_hash()
        self.assertNotEqual(h1, h2)


class TestPersonalizedKnapsack(unittest.TestCase):

    def _make_bounties(self, n: int, base_reward: int = 500_000) -> list:
        return [_bounty(bounty_id=i + 1, reward_micro=base_reward * (i + 1)) for i in range(n)]

    def test_optimize_returns_list(self):
        knap = PersonalizedKnapsack(PreferenceModel(seed=20))
        result = knap.optimize(self._make_bounties(5), battery_budget_pct=50.0, device_state=_device())
        self.assertIsInstance(result, list)

    def test_optimize_empty_input_returns_empty(self):
        knap = PersonalizedKnapsack(PreferenceModel(seed=21))
        result = knap.optimize([], battery_budget_pct=50.0, device_state=_device())
        self.assertEqual(result, [])

    def test_optimize_respects_max_active(self):
        knap = PersonalizedKnapsack(PreferenceModel(seed=22))
        result = knap.optimize(
            self._make_bounties(10),
            battery_budget_pct=100.0,
            device_state=_device(),
            max_active=3,
        )
        self.assertLessEqual(len(result), 3)

    def test_optimize_respects_battery_budget(self):
        """Total energy cost of selected bounties must not exceed battery budget."""
        knap = PersonalizedKnapsack(PreferenceModel(seed=23))
        bounties = [_bounty(bounty_id=i, duration_s=3600) for i in range(5)]  # high cost
        budget = 10.0
        result = knap.optimize(bounties, battery_budget_pct=budget, device_state=_device())
        total_energy = sum(b.energy_cost_pct for b in result)
        self.assertLessEqual(total_energy, budget + 1e-9)

    def test_preemption_selects_high_utility_bounty(self):
        """A very high reward bounty should displace the worst selected bounty."""
        pm = PreferenceModel(seed=24)
        knap = PersonalizedKnapsack(pm)

        # Fill the queue with mediocre bounties
        mediocre = [_bounty(bounty_id=i, reward_micro=100_000, duration_s=60) for i in range(4)]
        # One extremely high-reward bounty (not in initial top-4 due to ordering)
        great = _bounty(bounty_id=99, reward_micro=9_000_000, duration_s=60)

        # Optimize with all 5; the great bounty should be selected
        all_bounties = mediocre + [great]
        result = knap.optimize(all_bounties, battery_budget_pct=100.0, device_state=_device())
        result_ids = {b.bounty_id for b in result}
        self.assertIn(99, result_ids)


class TestE2E4Synergy(unittest.TestCase):

    def test_combined_hash_differs_from_ewc_only(self):
        """Adding preference bytes to EWC hash must change the output."""
        ewc_bytes = b"\\x01" * 200   # Simulated EWC weight bytes
        pref_bytes = b"\\x02" * 40   # Simulated preference bytes (5 float64)
        h_ewc_only = hashlib.sha256(ewc_bytes).digest()
        h_combined = compute_combined_world_model_hash(ewc_bytes, pref_bytes)
        self.assertNotEqual(h_ewc_only, h_combined)

    def test_combined_hash_is_32_bytes(self):
        h = compute_combined_world_model_hash(b"\\xaa" * 100, b"\\xbb" * 40)
        self.assertEqual(len(h), 32)

    def test_combined_hash_changes_with_different_prefs(self):
        ewc_bytes = b"\\x01" * 200
        h1 = compute_combined_world_model_hash(ewc_bytes, b"\\x00" * 40)
        h2 = compute_combined_world_model_hash(ewc_bytes, b"\\xff" * 40)
        self.assertNotEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
