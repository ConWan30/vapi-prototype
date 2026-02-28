"""
Phase 21 — PoHG Pulse Dashboard Tests

Tests cover:
- PITL sidecar fields persisted to SQLite (L4 distance, L5 temporal features)
- Null PITL fields for non-DualShock records (backward-compat)
- get_recent_records() returns PITL columns
- Player profile PHG Trust Score computation
- PHG score excludes cheat records
- PHG score formula: Σ (confidence / 255) × 10 over NOMINAL records
- Player profile 404 for unknown device
- PITL timeline: bucketed by minute, excludes NOMINAL, empty when no detections
- PITL timeline: counts by inference code
- Operator dashboard returns 200
- Player dashboard returns 200 for known device
- WebSocket endpoint accepts connection
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add bridge/ to path so vapi_bridge imports work
_bridge_dir = str(Path(__file__).resolve().parents[1])
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

from fastapi.testclient import TestClient

from vapi_bridge.codec import PoACRecord, parse_record
from vapi_bridge.config import Config
from vapi_bridge.store import Store
from vapi_bridge.dashboard_api import create_dashboard_app
from vapi_bridge.transports.http import create_app, ws_broadcast, _ws_clients


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    inference: int = 0x20,
    confidence: int = 220,
    device_id: str = "aabbcc",
    pitl_l4_distance: float | None = None,
    pitl_l4_warmed_up: bool | None = None,
    pitl_l4_features_json: str | None = None,
    pitl_l5_cv: float | None = None,
    pitl_l5_entropy_bits: float | None = None,
    pitl_l5_quant_score: float | None = None,
    pitl_l5_anomaly_signals: int | None = None,
) -> PoACRecord:
    """Build a minimal PoACRecord with specified fields (no real parsing)."""
    rec = PoACRecord(
        prev_poac_hash=b"\x00" * 32,
        sensor_commitment=b"\x01" * 32,
        model_manifest_hash=b"\x02" * 32,
        world_model_hash=b"\x03" * 32,
        inference_result=inference,
        action_code=0x01,
        confidence=confidence,
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
            inference, 0x01, confidence, 90, 1,
            int(time.time() * 1000), 0.0, 0.0, 0)
    )
    rec.record_hash = hashlib.sha256(body[:164]).digest()
    rec.raw_body = body[:164]
    rec.device_id = bytes.fromhex(device_id.zfill(64))

    # PITL sidecar
    rec.pitl_l4_distance        = pitl_l4_distance
    rec.pitl_l4_warmed_up       = pitl_l4_warmed_up
    rec.pitl_l4_features_json   = pitl_l4_features_json
    rec.pitl_l5_cv              = pitl_l5_cv
    rec.pitl_l5_entropy_bits    = pitl_l5_entropy_bits
    rec.pitl_l5_quant_score     = pitl_l5_quant_score
    rec.pitl_l5_anomaly_signals = pitl_l5_anomaly_signals

    return rec


def _fresh_store() -> Store:
    """Return a Store backed by a fresh temp SQLite file."""
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


# ---------------------------------------------------------------------------
# TestPITLPersistence — SQLite round-trip for PITL columns
# ---------------------------------------------------------------------------

class TestPITLPersistence(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        dev_id = "aabbcc".zfill(64)
        self.store.upsert_device(dev_id, "deadbeef" * 16)

    def test_upsert_record_stores_l4_distance(self):
        """insert_record writes pitl_l4_distance to SQLite."""
        rec = _make_record(
            inference=0x30, confidence=190,
            pitl_l4_distance=4.2,
            pitl_l4_warmed_up=True,
        )
        self.store.insert_record(rec, b"\x00" * 228)
        rows = self.store.get_recent_records(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["pitl_l4_distance"], 4.2, places=3)

    def test_upsert_record_stores_l5_temporal_features(self):
        """insert_record writes L5 temporal PITL fields to SQLite."""
        rec = _make_record(
            inference=0x2B, confidence=210,
            pitl_l5_cv=0.05,
            pitl_l5_entropy_bits=1.2,
            pitl_l5_quant_score=0.67,
            pitl_l5_anomaly_signals=3,
        )
        self.store.insert_record(rec, b"\x00" * 228)
        rows = self.store.get_recent_records(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["pitl_l5_cv"], 0.05, places=4)
        self.assertAlmostEqual(rows[0]["pitl_l5_entropy"], 1.2, places=3)
        self.assertAlmostEqual(rows[0]["pitl_l5_quant"], 0.67, places=3)
        self.assertEqual(rows[0]["pitl_l5_signals"], 3)

    def test_upsert_record_null_pitl_fields_for_non_dualshock(self):
        """Records with no PITL data store NULL values — no regression for non-DualShock."""
        rec = _make_record(inference=0x20, confidence=220)
        # All PITL fields default to None
        self.store.insert_record(rec, b"\x00" * 228)
        rows = self.store.get_recent_records(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["pitl_l4_distance"])
        self.assertIsNone(rows[0]["pitl_l5_cv"])

    def test_get_recent_records_returns_pitl_columns(self):
        """get_recent_records() includes PITL columns in returned dicts."""
        rec = _make_record(pitl_l4_distance=2.7, pitl_l5_cv=0.12)
        self.store.insert_record(rec, b"\x00" * 228)
        rows = self.store.get_recent_records(limit=5)
        self.assertIn("pitl_l4_distance", rows[0])
        self.assertIn("pitl_l5_cv", rows[0])
        self.assertIn("pitl_l5_entropy", rows[0])


# ---------------------------------------------------------------------------
# TestPlayerProfileAPI — PHG Trust Score via dashboard_api
# ---------------------------------------------------------------------------

class TestPlayerProfileAPI(unittest.TestCase):

    DEVICE_ID = "cc" * 32  # 64-char hex

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device(self.DEVICE_ID, "aa" * 32)
        self.client = TestClient(create_dashboard_app(self.store))

    def test_player_profile_returns_phg_score(self):
        """GET /api/v1/player/{id}/profile returns a dict with phg_score."""
        rec = _make_record(inference=0x20, confidence=255, device_id=self.DEVICE_ID)
        self.store.insert_record(rec, b"\x00" * 228)
        resp = self.client.get(f"/api/v1/player/{self.DEVICE_ID}/profile")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("phg_score", data)
        self.assertGreater(data["phg_score"], 0)

    def test_phg_score_excludes_cheat_records(self):
        """PHG score only counts NOMINAL (0x20) records, not cheat records."""
        # Insert one NOMINAL and one cheat record
        nom = _make_record(inference=0x20, confidence=200, device_id=self.DEVICE_ID)
        cheat = _make_record(inference=0x28, confidence=220, device_id=self.DEVICE_ID)
        cheat.record_hash = b"\xff" * 32  # ensure unique hash
        self.store.insert_record(nom, b"\x00" * 228)
        self.store.insert_record(cheat, b"\x00" * 228)
        resp = self.client.get(f"/api/v1/player/{self.DEVICE_ID}/profile")
        data = resp.json()
        # Score = floor(200/255 * 10) = 7; cheat doesn't contribute
        expected = int((200 / 255) * 10)
        self.assertEqual(data["phg_score"], expected)

    def test_phg_score_sums_confidence_weighted_nominals(self):
        """PHG score = Σ int(confidence_i / 255 * 10) over NOMINAL records."""
        confidences = [200, 220, 255]
        for i, conf in enumerate(confidences):
            rec = _make_record(inference=0x20, confidence=conf, device_id=self.DEVICE_ID)
            rec.record_hash = bytes([i]) * 32  # unique hash per record
            self.store.insert_record(rec, b"\x00" * 228)

        resp = self.client.get(f"/api/v1/player/{self.DEVICE_ID}/profile")
        data = resp.json()
        expected = sum(int(c / 255 * 10) for c in confidences)
        self.assertEqual(data["phg_score"], expected)

    def test_player_profile_404_for_unknown_device(self):
        """GET /api/v1/player/{unknown_id}/profile returns 404."""
        resp = self.client.get("/api/v1/player/0" * 64 + "0000")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# TestPITLTimeline — adversarial pressure map data
# ---------------------------------------------------------------------------

class TestPITLTimeline(unittest.TestCase):

    DEVICE_ID = "dd" * 32

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device(self.DEVICE_ID, "bb" * 32)
        self.client = TestClient(create_dashboard_app(self.store))

    def test_pitl_timeline_returns_empty_for_no_detections(self):
        """GET /api/v1/pitl/timeline returns empty list when no non-NOMINAL records."""
        nom = _make_record(inference=0x20, confidence=220, device_id=self.DEVICE_ID)
        self.store.insert_record(nom, b"\x00" * 228)
        resp = self.client.get("/api/v1/pitl/timeline?minutes=10")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_pitl_timeline_excludes_nominal_records(self):
        """PITL timeline never contains NOMINAL (0x20) records."""
        nom  = _make_record(inference=0x20, confidence=220, device_id=self.DEVICE_ID)
        cheat = _make_record(inference=0x28, confidence=200, device_id=self.DEVICE_ID)
        cheat.record_hash = b"\x01" * 32
        self.store.insert_record(nom, b"\x00" * 228)
        self.store.insert_record(cheat, b"\x00" * 228)
        resp = self.client.get("/api/v1/pitl/timeline?minutes=10")
        data = resp.json()
        inferences = {row["inference"] for row in data}
        self.assertNotIn(0x20, inferences)
        self.assertIn(0x28, inferences)

    def test_pitl_timeline_counts_by_inference_code(self):
        """Timeline groups by inference code with correct counts."""
        for i in range(3):
            rec = _make_record(inference=0x2B, confidence=180, device_id=self.DEVICE_ID)
            rec.record_hash = bytes([i + 10]) * 32
            self.store.insert_record(rec, b"\x00" * 228)
        resp = self.client.get("/api/v1/pitl/timeline?minutes=10")
        data = resp.json()
        temporal_rows = [r for r in data if r["inference"] == 0x2B]
        total = sum(r["cnt"] for r in temporal_rows)
        self.assertEqual(total, 3)

    def test_pitl_timeline_buckets_by_minute(self):
        """Timeline rows have bucket field (integer multiple of 60)."""
        cheat = _make_record(inference=0x28, confidence=210, device_id=self.DEVICE_ID)
        self.store.insert_record(cheat, b"\x00" * 228)
        resp = self.client.get("/api/v1/pitl/timeline?minutes=10")
        data = resp.json()
        if data:
            self.assertIn("bucket", data[0])
            self.assertEqual(data[0]["bucket"] % 60, 0)


# ---------------------------------------------------------------------------
# TestDashboardRoutes — HTTP + WebSocket endpoints
# ---------------------------------------------------------------------------

class TestDashboardRoutes(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device("ee" * 32, "ff" * 32)
        cfg = Config()

        async def _noop_on_record(raw, src):
            pass

        self.app = create_app(cfg, self.store, _noop_on_record)
        self.client = TestClient(self.app, raise_server_exceptions=True)

    def test_operator_dashboard_returns_200(self):
        """GET / returns 200 with HTML content for the Operator dashboard."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("PoHG Pulse", resp.text)

    def test_player_dashboard_returns_200_for_known_device(self):
        """GET /player/{device_id} returns 200 with the Player dashboard HTML."""
        dev_id = "ee" * 32
        resp = self.client.get(f"/player/{dev_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("Trust", resp.text)

    def test_ws_endpoint_connects_and_receives_broadcast(self):
        """WebSocket /ws/records accepts connection and receives broadcast messages."""
        import asyncio

        with self.client.websocket_connect("/ws/records") as ws:
            # The endpoint should have added this client to _ws_clients
            # Send a broadcast from within the test
            loop = asyncio.new_event_loop()
            loop.run_until_complete(ws_broadcast('{"inference": 32, "confidence": 220}'))
            loop.close()
            msg = ws.receive_text()
            data = json.loads(msg)
            self.assertIn("inference", data)
            self.assertEqual(data["inference"], 32)


if __name__ == "__main__":
    unittest.main()
