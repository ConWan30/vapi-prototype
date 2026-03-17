"""Phase 53 Serialization Safety Tests — 4 tests.

Covers the NaN/Inf guard added to _safe_val() and _record_to_ws_msg() in
bridge/vapi_bridge/transports/http.py. These fields can be NaN during L4
Mahalanobis classifier warmup, which caused json.dumps to raise ValueError
and silently drop all WS broadcast messages before Phase 53.
"""

import json
import sys
import time
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path adjustment — make vapi_bridge importable
# ---------------------------------------------------------------------------
_bridge_dir = str(Path(__file__).resolve().parents[1])
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

from vapi_bridge.transports.http import _safe_val, _record_to_ws_msg
from vapi_bridge.codec import PoACRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(**kwargs) -> PoACRecord:
    """Build a minimal PoACRecord suitable for _record_to_ws_msg()."""
    rec = PoACRecord(
        prev_poac_hash=b"\x00" * 32,
        sensor_commitment=b"\x01" * 32,
        model_manifest_hash=b"\x02" * 32,
        world_model_hash=b"\x03" * 32,
        inference_result=0x20,
        action_code=0x01,
        confidence=200,
        battery_pct=90,
        monotonic_ctr=1,
        timestamp_ms=int(time.time() * 1000),
        latitude=0.0,
        longitude=0.0,
        bounty_id=0,
        signature=b"\x00" * 64,
    )
    import hashlib, struct
    body = (
        rec.prev_poac_hash + rec.sensor_commitment +
        rec.model_manifest_hash + rec.world_model_hash +
        struct.pack(">BBBBIqddI",
            0x20, 0x01, 200, 90, 1,
            rec.timestamp_ms, 0.0, 0.0, 0)
    )
    rec.record_hash = hashlib.sha256(body[:164]).digest()
    rec.raw_body = body[:164]
    rec.device_id = bytes.fromhex("aabbcc".zfill(64))

    # PITL sidecar — apply caller overrides
    for k, v in kwargs.items():
        setattr(rec, k, v)

    return rec


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSafeVal(unittest.TestCase):

    def test_safe_val_nan_returns_none(self):
        """_safe_val(float('nan')) must return None — json.dumps cannot handle NaN."""
        result = _safe_val(float('nan'))
        self.assertIsNone(result)

    def test_safe_val_inf_returns_none(self):
        """_safe_val(float('inf')) must return None — json.dumps cannot handle Inf."""
        result = _safe_val(float('inf'))
        self.assertIsNone(result)

    def test_safe_val_normal_float_passes_through(self):
        """_safe_val(3.14) must return 3.14 unchanged — do not clobber valid data."""
        result = _safe_val(3.14)
        self.assertEqual(result, 3.14)


class TestRecordToWsMsg(unittest.TestCase):

    def test_record_to_ws_msg_with_nan_l4_distance(self):
        """_record_to_ws_msg must produce valid JSON even when pitl_l4_distance is NaN.

        Before Phase 53 this raised ValueError: Out of range float values are not
        JSON compliant, causing the entire WS broadcast to fail silently.
        """
        rec = _make_record(pitl_l4_distance=float('nan'))
        # Must not raise
        result = _record_to_ws_msg(rec)
        # Must be valid JSON
        parsed = json.loads(result)
        # pitl_l4_distance must be serialised as null (None), not NaN
        self.assertIsNone(parsed["pitl_l4_distance"])


if __name__ == "__main__":
    unittest.main()
