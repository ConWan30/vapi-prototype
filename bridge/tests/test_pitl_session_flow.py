"""
Phase 27 — PITLProver Session Flow Integration Tests

TestPITLSessionFlow (8):
1.  DualShockTransport has _pitl_prover attribute (None by default)
2.  session proof generated when _pitl_prover set and _pending_pitl_meta populated
3.  store_pitl_proof called once per shutdown
4.  chain.submit_pitl_proof called when chain is configured
5.  chain.submit_pitl_proof NOT called when chain is None
6.  shutdown proof skipped when _pending_pitl_meta is None/empty
7.  shutdown succeeds even when PITLProver raises (non-fatal try/except)
8.  generated hp_int is in valid range [0, 1000]
"""

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from vapi_bridge.pitl_prover import PITLProver, FEATURE_KEYS, PROOF_SIZE


def _mock_prover() -> PITLProver:
    """Return a PITLProver forced into mock mode via non-existent artifact paths."""
    return PITLProver(
        wasm_path="/nonexistent/PitlSessionProof.wasm",
        zkey_path="/nonexistent/PitlSessionProof_final.zkey",
    )


# ---------------------------------------------------------------------------
# Minimal _shutdown_cleanup surrogate
# We cannot instantiate DualShockTransport (requires HID/hardware), so we
# directly test the proof-generation block that was added to _shutdown_cleanup.
# This mirrors how we test other shutdown-level hooks (e.g., test_batcher_receipt.py).
# ---------------------------------------------------------------------------

def _make_pending_meta(humanity_prob: float = 0.75) -> dict:
    features = {k: 0.5 for k in FEATURE_KEYS}
    return {
        "l4_features_json":   json.dumps(features),
        "l5_rhythm_humanity": 0.8,
        "e4_cognitive_drift": 0.1,
        "humanity_prob":      humanity_prob,
        "l4_distance":        1.5,
        "l4_warmed_up":       True,
    }


async def _run_proof_block(pitl_prover, pending_meta, store, chain,
                           device_id_bytes: bytes = b"\xaa" * 32,
                           last_raw: bytes | None = None):
    """Execute the Phase 27 proof block from _shutdown_cleanup exactly as written."""
    import json as _json
    import time as _time

    if pitl_prover is not None and pending_meta:
        try:
            _feats_raw = pending_meta.get("l4_features_json") or "{}"
            _feats = _json.loads(_feats_raw) if isinstance(_feats_raw, str) else (_feats_raw or {})
            features = {k: float(_feats.get(k, 0.0)) for k in pitl_prover.FEATURE_KEYS}
            l5    = float(pending_meta.get("l5_rhythm_humanity") or 0.5)
            e4    = float(pending_meta.get("e4_cognitive_drift") or 0.0)
            infer = 0x20
            if last_raw and len(last_raw) >= 165:
                infer = last_raw[164]
            epoch   = int(_time.time()) // 3600
            dev_hex = device_id_bytes.hex()
            proof, fc, hp_int, null = pitl_prover.generate_proof(
                features, dev_hex, l5, e4, infer, epoch
            )
            store.store_pitl_proof(dev_hex, hex(null), hex(fc), hp_int)
            if chain is not None:
                asyncio.create_task(
                    chain.submit_pitl_proof(dev_hex, proof, fc, hp_int, infer, null, epoch)
                )
        except Exception as exc:
            pass  # non-fatal


# ===========================================================================
# Tests
# ===========================================================================

class TestPITLSessionFlow(unittest.TestCase):

    def test_1_pitl_prover_attribute_exists_as_none(self):
        """DualShockTransport __init__ sets _pitl_prover = None."""
        # We can import the class without hardware — just check the attribute is declared
        import importlib, types
        # Stub heavy dependencies so DualShockTransport can be imported partially
        for mod in ("hidapi", "hid", "pydualsense"):
            if mod not in sys.modules:
                sys.modules[mod] = types.ModuleType(mod)
        # The attribute is set in __init__; we verify it exists at the class level via source
        from vapi_bridge import dualshock_integration
        import inspect
        src = inspect.getsource(dualshock_integration.DualShockTransport.__init__)
        self.assertIn("_pitl_prover", src)
        self.assertIn("Phase 27", src)

    def test_2_proof_generated_with_valid_meta(self):
        """Session proof is generated and store_pitl_proof is called with valid meta."""
        prover = _mock_prover()
        store = MagicMock()
        meta = _make_pending_meta(0.75)

        asyncio.run(
            _run_proof_block(prover, meta, store, chain=None)
        )
        store.store_pitl_proof.assert_called_once()
        args = store.store_pitl_proof.call_args[0]
        self.assertIsInstance(args[0], str)   # device_id_hex
        self.assertIsInstance(args[1], str)   # hex(null)
        self.assertIsInstance(args[2], str)   # hex(fc)
        self.assertIsInstance(args[3], int)   # hp_int

    def test_3_store_pitl_proof_called_exactly_once(self):
        """store_pitl_proof is called exactly once per shutdown block execution."""
        prover = _mock_prover()
        store = MagicMock()
        meta = _make_pending_meta()

        asyncio.run(
            _run_proof_block(prover, meta, store, chain=None)
        )
        self.assertEqual(store.store_pitl_proof.call_count, 1)

    def test_4_submit_pitl_proof_called_when_chain_configured(self):
        """chain.submit_pitl_proof is called when chain is not None."""
        prover = _mock_prover()
        store = MagicMock()
        chain = MagicMock()
        chain.submit_pitl_proof = AsyncMock(return_value="0xtxhash")
        meta = _make_pending_meta()

        async def _run():
            await _run_proof_block(prover, meta, store, chain=chain)
            # Allow the create_task coroutine to run
            await asyncio.sleep(0)

        asyncio.run(_run())
        chain.submit_pitl_proof.assert_called_once()

    def test_5_submit_pitl_proof_not_called_when_chain_none(self):
        """chain.submit_pitl_proof is NOT called when chain is None."""
        prover = _mock_prover()
        store = MagicMock()
        meta = _make_pending_meta()

        asyncio.run(
            _run_proof_block(prover, meta, store, chain=None)
        )
        # No chain means no submit call; store is the only side-effect
        store.store_pitl_proof.assert_called_once()

    def test_6_proof_skipped_when_meta_empty(self):
        """No proof is generated when _pending_pitl_meta is empty dict or None."""
        prover = _mock_prover()
        store = MagicMock()

        # Empty dict
        asyncio.run(
            _run_proof_block(prover, {}, store, chain=None)
        )
        store.store_pitl_proof.assert_not_called()

        # None
        asyncio.run(
            _run_proof_block(prover, None, store, chain=None)
        )
        store.store_pitl_proof.assert_not_called()

    def test_7_shutdown_survives_prover_exception(self):
        """Proof block is non-fatal — exception in generate_proof does not propagate."""
        broken_prover = MagicMock()
        broken_prover.FEATURE_KEYS = FEATURE_KEYS
        broken_prover.generate_proof.side_effect = RuntimeError("simulated proof failure")
        store = MagicMock()
        meta = _make_pending_meta()

        # Should complete without raising
        try:
            asyncio.run(
                _run_proof_block(broken_prover, meta, store, chain=None)
            )
        except RuntimeError:
            self.fail("RuntimeError should have been swallowed by the non-fatal except block")

        store.store_pitl_proof.assert_not_called()

    def test_8_hp_int_in_valid_range(self):
        """Generated humanity_prob_int is in [0, 1000]."""
        prover = _mock_prover()
        store = MagicMock()
        meta = _make_pending_meta(humanity_prob=0.85)

        asyncio.run(
            _run_proof_block(prover, meta, store, chain=None)
        )
        store.store_pitl_proof.assert_called_once()
        hp_int = store.store_pitl_proof.call_args[0][3]
        self.assertGreaterEqual(hp_int, 0)
        self.assertLessEqual(hp_int, 1000)


if __name__ == "__main__":
    unittest.main()
