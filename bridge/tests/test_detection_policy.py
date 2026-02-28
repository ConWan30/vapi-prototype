"""
Tests for InsightSynthesizer Mode 4 (_synthesize_detection_policies) — Phase 36

4 tests covering:
1. Mode 4 writes policy with multiplier=0.70 for critical-labeled device
2. Mode 4 writes policy with multiplier=0.85 for warming-labeled device
3. cleared device gets multiplier=1.0 (baseline, policy still written)
4. policy expires_at is set_at + poll_interval + 3600s (within tolerance)
"""
import asyncio
import sys
import os
import time
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.insight_synthesizer import InsightSynthesizer, _POLICY_MULTIPLIERS


def _make_synth(label_rows_by_label=None, existing_policy=None, poll_interval=21600.0):
    store = MagicMock()
    label_rows_by_label = label_rows_by_label or {}

    def _get_devices_by_risk_label(label):
        return label_rows_by_label.get(label, [])

    store.get_devices_by_risk_label.side_effect = _get_devices_by_risk_label
    store.get_detection_policy.return_value = existing_policy
    store.store_detection_policy = MagicMock()
    store.store_protocol_insight = MagicMock()
    store.get_insights_since.return_value = []
    store.get_federation_clusters.return_value = []
    store.store_insight_digest = MagicMock()
    store.prune_old_digests = MagicMock(return_value=0)
    store.prune_old_insights = MagicMock(return_value=0)

    cfg = MagicMock()
    cfg.adaptive_thresholds_enabled = True
    cfg.policy_multiplier_floor = 0.5
    cfg.digest_retention_days = 90.0

    return InsightSynthesizer(store, cfg, poll_interval=poll_interval), store


class TestDetectionPolicyMode4(unittest.TestCase):

    def test_1_critical_device_gets_multiplier_0_70(self):
        """Mode 4 stores policy with multiplier=0.70 for a critical-labeled device."""
        device_id = "dev_critical_a1b2"
        synth, store = _make_synth(
            label_rows_by_label={"critical": [{"device_id": device_id}]},
        )

        asyncio.run(synth._synthesize_detection_policies())

        store.store_detection_policy.assert_called()
        # Find the call for our device
        calls = {str(c): c for c in store.store_detection_policy.call_args_list}
        found = False
        for c in store.store_detection_policy.call_args_list:
            kwargs = c.kwargs
            args = c.args
            dev = kwargs.get("device_id") or (args[0] if args else None)
            mult = kwargs.get("multiplier") or (args[1] if len(args) > 1 else None)
            if dev == device_id:
                assert abs(mult - 0.70) < 0.001, f"Expected 0.70, got {mult}"
                found = True
        assert found, "store_detection_policy not called for critical device"

    def test_2_warming_device_gets_multiplier_0_85(self):
        """Mode 4 stores policy with multiplier=0.85 for a warming-labeled device."""
        device_id = "dev_warming_c3d4"
        synth, store = _make_synth(
            label_rows_by_label={"warming": [{"device_id": device_id}]},
        )

        asyncio.run(synth._synthesize_detection_policies())

        store.store_detection_policy.assert_called()
        found = False
        for c in store.store_detection_policy.call_args_list:
            kwargs = c.kwargs
            args = c.args
            dev = kwargs.get("device_id") or (args[0] if args else None)
            mult = kwargs.get("multiplier") or (args[1] if len(args) > 1 else None)
            if dev == device_id:
                assert abs(mult - 0.85) < 0.001, f"Expected 0.85, got {mult}"
                found = True
        assert found, "store_detection_policy not called for warming device"

    def test_3_cleared_device_gets_multiplier_1_00(self):
        """Mode 4 stores policy with multiplier=1.00 for a cleared-labeled device (baseline)."""
        device_id = "dev_cleared_e5f6"
        synth, store = _make_synth(
            label_rows_by_label={"cleared": [{"device_id": device_id}]},
        )

        asyncio.run(synth._synthesize_detection_policies())

        store.store_detection_policy.assert_called()
        found = False
        for c in store.store_detection_policy.call_args_list:
            kwargs = c.kwargs
            args = c.args
            dev = kwargs.get("device_id") or (args[0] if args else None)
            mult = kwargs.get("multiplier") or (args[1] if len(args) > 1 else None)
            if dev == device_id:
                assert abs(mult - 1.00) < 0.001, f"Expected 1.00, got {mult}"
                found = True
        assert found, "store_detection_policy not called for cleared device"

    def test_4_policy_expires_at_is_poll_plus_buffer(self):
        """Policy expires_at ≈ now + poll_interval + 3600s (within 10s tolerance)."""
        device_id = "dev_expires_g7h8"
        poll_interval = 21600.0
        synth, store = _make_synth(
            label_rows_by_label={"critical": [{"device_id": device_id}]},
            poll_interval=poll_interval,
        )

        t_before = time.time()
        asyncio.run(synth._synthesize_detection_policies())
        t_after = time.time()

        expected_min = t_before + poll_interval + 3600.0
        expected_max = t_after + poll_interval + 3600.0

        found = False
        for c in store.store_detection_policy.call_args_list:
            kwargs = c.kwargs
            args = c.args
            dev = kwargs.get("device_id") or (args[0] if args else None)
            expires = kwargs.get("expires_at") or (args[3] if len(args) > 3 else None)
            if dev == device_id and expires is not None:
                assert expected_min - 10 <= expires <= expected_max + 10, (
                    f"expires_at={expires} outside expected range "
                    f"[{expected_min:.0f}, {expected_max:.0f}]"
                )
                found = True
        assert found, "store_detection_policy not called with expires_at"


class TestPolicyMultipliersConstants(unittest.TestCase):
    """Verify _POLICY_MULTIPLIERS constants are correct."""

    def test_critical_is_0_70(self):
        assert _POLICY_MULTIPLIERS["critical"] == 0.70

    def test_warming_is_0_85(self):
        assert _POLICY_MULTIPLIERS["warming"] == 0.85

    def test_cleared_is_1_00(self):
        assert _POLICY_MULTIPLIERS["cleared"] == 1.00

    def test_stable_is_1_00(self):
        assert _POLICY_MULTIPLIERS["stable"] == 1.00


if __name__ == "__main__":
    unittest.main()
