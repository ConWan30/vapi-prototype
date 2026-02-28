"""
Phase 37 — InsightSynthesizer Mode 5 tests.

4 tests covering:
1. Mode 5 stores suspension + protocol_insight for device with consecutive_critical=2
2. Mode 5 skips suspension when consecutive_critical < min_consecutive (1 < 2)
3. Mode 5 reinstates + logs when cleared-label device is suspended
4. Mode 5 is non-fatal: chain.suspend raising Exception does not abort
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.store import Store


def _fresh_store() -> Store:
    db_dir = tempfile.mkdtemp()
    return Store(os.path.join(db_dir, "test.db"))


def _make_synth(store, cfg=None, chain=None):
    from vapi_bridge.insight_synthesizer import InsightSynthesizer
    if cfg is None:
        cfg = MagicMock()
        cfg.synthesizer_poll_interval = 21600.0
        cfg.adaptive_thresholds_enabled = True
        cfg.policy_multiplier_floor = 0.5
        cfg.digest_retention_days = 90.0
        cfg.phg_credential_enforcement_enabled = True
        cfg.credential_enforcement_min_consecutive = 2
        cfg.credential_suspension_base_days = 7.0
        cfg.credential_suspension_max_days = 28.0
    if chain is None:
        chain = MagicMock()
        chain.suspend_phg_credential = AsyncMock(return_value="0xabc")
        chain.reinstate_phg_credential = AsyncMock(return_value="0xdef")
    return InsightSynthesizer(store, cfg, poll_interval=21600.0, chain=chain), chain


class TestCredentialSuspension(unittest.IsolatedAsyncioTestCase):

    async def test_1_mode5_suspends_at_consecutive_2(self):
        """Mode 5 writes DB suspension when consecutive_critical reaches 2."""
        store = _fresh_store()
        dev = "aa" * 32
        # Manually insert a credential mint and risk label
        with store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO phg_credential_mints"
                " (device_id, credential_id, minted_at)"
                " VALUES (?, 1, ?)",
                (dev, time.time()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO device_risk_labels"
                " (device_id, risk_label, label_evidence, label_set_at, prior_label)"
                " VALUES (?, 'critical', '{}', ?, 'stable')",
                (dev, time.time()),
            )
        # Pre-set consecutive to 1 so Mode 5 will bring it to 2 (>= min_consecutive)
        store.increment_consecutive_critical(dev)  # now =1
        synth, chain = _make_synth(store)
        await synth._synthesize_credential_enforcement()
        self.assertTrue(store.is_credential_suspended(dev))

    async def test_2_mode5_skips_below_min_consecutive(self):
        """Mode 5 skips suspension when consecutive_critical < 2 after first increment."""
        store = _fresh_store()
        dev = "bb" * 32
        # Seed credential + critical label; consecutive starts at 0 → Mode 5 increments to 1
        with store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO phg_credential_mints"
                " (device_id, credential_id, minted_at)"
                " VALUES (?, 1, ?)",
                (dev, time.time()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO device_risk_labels"
                " (device_id, risk_label, label_evidence, label_set_at, prior_label)"
                " VALUES (?, 'critical', '{}', ?, 'stable')",
                (dev, time.time()),
            )
        synth, chain = _make_synth(store)
        await synth._synthesize_credential_enforcement()
        # consecutive is 1 after first call — below min_consecutive=2, no suspension
        self.assertFalse(store.is_credential_suspended(dev))

    async def test_3_mode5_reinstates_cleared_device(self):
        """Mode 5 reinstates a device labeled 'cleared' that is currently suspended."""
        store = _fresh_store()
        dev = "cc" * 32
        import time as _t
        store.store_credential_suspension(dev, "aabbccdd" * 8, _t.time() + 86400)
        self.assertTrue(store.is_credential_suspended(dev))
        with store._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO device_risk_labels"
                " (device_id, risk_label, label_evidence, label_set_at, prior_label)"
                " VALUES (?, 'cleared', '{}', ?, 'critical')",
                (dev, _t.time()),
            )
        synth, chain = _make_synth(store)
        await synth._synthesize_credential_enforcement()
        self.assertFalse(store.is_credential_suspended(dev))

    async def test_4_mode5_nonfatal_on_chain_error(self):
        """chain.suspend_phg_credential raising Exception does not abort Mode 5."""
        store = _fresh_store()
        dev = "dd" * 32
        import time as _t
        with store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO phg_credential_mints"
                " (device_id, credential_id, minted_at)"
                " VALUES (?, 1, ?)",
                (dev, _t.time()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO device_risk_labels"
                " (device_id, risk_label, label_evidence, label_set_at, prior_label)"
                " VALUES (?, 'critical', '{}', ?, 'stable')",
                (dev, _t.time()),
            )
        # Pre-increment to 1 so Mode 5 will try to suspend (brings to 2)
        store.increment_consecutive_critical(dev)
        chain = MagicMock()
        chain.suspend_phg_credential = AsyncMock(side_effect=Exception("rpc timeout"))
        chain.reinstate_phg_credential = AsyncMock(return_value="")
        synth, _ = _make_synth(store, chain=chain)
        # Should not raise even with chain failure
        await synth._synthesize_credential_enforcement()
        # DB suspension should still be written despite on-chain failure
        self.assertTrue(store.is_credential_suspended(dev))


if __name__ == "__main__":
    unittest.main()
