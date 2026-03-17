"""
Phase 61 — Session Replay + Feature History Scatter (12 tests)

Tests: frame_checkpoints table, store methods, /replay + /checkpoints + /features
endpoints, BridgeAgent tool #29 get_session_replay.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup + web3/eth_account stubs (matches phase 59 pattern)
# ---------------------------------------------------------------------------

_bridge_dir = os.path.join(os.path.dirname(__file__), "..")
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

_web3_mod = types.ModuleType("web3")
_web3_exc = types.ModuleType("web3.exceptions")


class _AsyncWeb3Stub:
    def __init__(self, *a, **kw): pass
    @property
    def eth(self): return self
    def contract(self, address=None, abi=None): return MagicMock()
    def to_checksum_address(self, addr): return addr


class _AsyncHTTPProviderStub:
    def __init__(self, *a, **kw): pass


_web3_mod.AsyncWeb3 = _AsyncWeb3Stub
_web3_mod.AsyncHTTPProvider = _AsyncHTTPProviderStub
_web3_exc.ContractLogicError = Exception
_web3_exc.TransactionNotFound = Exception
sys.modules.setdefault("web3", _web3_mod)
sys.modules.setdefault("web3.exceptions", _web3_exc)

_eth_acc = types.ModuleType("eth_account")
_eth_acc.Account = MagicMock()
sys.modules.setdefault("eth_account", _eth_acc)

from starlette.testclient import TestClient  # noqa: E402
from vapi_bridge.store import Store  # noqa: E402
from vapi_bridge.bridge_agent import BridgeAgent  # noqa: E402
from vapi_bridge.transports.http import create_app  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp():
    return tempfile.mkdtemp()


def _make_store(d):
    return Store(os.path.join(d, "p61.db"))


def _make_cfg():
    cfg = MagicMock()
    cfg.operator_api_key = "key"
    cfg.rate_limit_per_minute = 60
    cfg.l4_anomaly_threshold = 7.009
    cfg.l4_continuity_threshold = 5.367
    cfg.agent_max_history_before_compress = 60
    cfg.game_profile_id = ""
    return cfg


def _make_frames(n=5):
    return [{"ts_ms": i * 50, "left_stick_x": i, "accel_x": float(i)} for i in range(n)]


def _rh(seed="abc"):
    import hashlib
    return hashlib.sha256(seed.encode()).hexdigest()


def _seed_record(db_path, device_id, record_hash):
    """Insert a minimal device + record row to satisfy frame_checkpoints FK."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) "
        "VALUES (?,?,?,?)",
        (device_id, "pub", time.time(), time.time()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO records "
        "(record_hash, device_id, counter, timestamp_ms, inference, action_code, "
        "confidence, battery_pct, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (record_hash, device_id, 1, int(time.time() * 1000),
         0x20, 0x01, 200, 80, "pending", time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# TestFrameCheckpoints
# ---------------------------------------------------------------------------


class TestFrameCheckpoints:
    def test_store_and_retrieve_checkpoint(self):
        d = _tmp()
        try:
            db_path = os.path.join(d, "p61.db")
            s = Store(db_path)
            device_id = "a" * 64
            rh = _rh("r1")
            _seed_record(db_path, device_id, rh)
            frames = _make_frames(10)
            s.store_frame_checkpoint(device_id, rh, frames)
            result = s.get_frame_checkpoint(device_id, rh)
            assert result is not None
            assert result["record_hash"] == rh
            assert result["frames"] == frames
            assert result["frame_count"] == 10
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_get_checkpoint_unknown_record(self):
        d = _tmp()
        try:
            s = _make_store(d)
            result = s.get_frame_checkpoint("b" * 64, "0" * 64)
            assert result is None
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_checkpoint_schema_idempotent(self):
        d = _tmp()
        try:
            db_path = os.path.join(d, "idempotent.db")
            Store(db_path)   # first init
            Store(db_path)   # second init -- no exception
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_checkpoint_json_round_trip(self):
        d = _tmp()
        try:
            db_path = os.path.join(d, "p61.db")
            s = Store(db_path)
            device_id = "c" * 64
            rh = _rh("r2")
            _seed_record(db_path, device_id, rh)
            frames = [
                {"ts_ms": 0, "left_stick_x": 127, "accel_x": 0.5, "buttons_cross": 1},
                {"ts_ms": 50, "right_stick_y": -32767, "gyro_z": 12.3},
            ]
            s.store_frame_checkpoint(device_id, rh, frames)
            result = s.get_frame_checkpoint(device_id, rh)
            assert result["frames"] == frames
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# TestListCheckpoints
# ---------------------------------------------------------------------------


class TestListCheckpoints:
    def test_list_checkpoints_empty(self):
        d = _tmp()
        try:
            s = _make_store(d)
            result = s.list_checkpoints_for_device("d" * 64)
            assert result == []
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_list_checkpoints_populated(self):
        d = _tmp()
        try:
            db_path = os.path.join(d, "p61.db")
            s = Store(db_path)
            device_id = "e" * 64
            hashes = []
            for i in range(3):
                rh = _rh(f"rp{i}")
                hashes.append(rh)
                _seed_record(db_path, device_id, rh)
                s.store_frame_checkpoint(device_id, rh, _make_frames(i + 1))
                time.sleep(0.002)   # ensure ordering
            result = s.list_checkpoints_for_device(device_id)
            assert len(result) == 3
            assert set(result) == set(hashes)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# TestReplayEndpoint
# ---------------------------------------------------------------------------


class TestReplayEndpoint:
    def test_replay_endpoint_with_data(self):
        d = _tmp()
        try:
            db_path = os.path.join(d, "p61.db")
            s = Store(db_path)
            device_id = "f" * 64
            rh = _rh("ep1")
            _seed_record(db_path, device_id, rh)
            s.store_frame_checkpoint(device_id, rh, _make_frames(20))
            app = create_app(_make_cfg(), s, MagicMock())
            resp = TestClient(app).get(
                f"/controller/twin/{device_id}/replay?record_hash={rh}"
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["record_hash"] == rh
            assert len(data["frames"]) == 20
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_replay_endpoint_unknown_hash(self):
        d = _tmp()
        try:
            s = _make_store(d)
            device_id = "g" * 64
            app = create_app(_make_cfg(), s, MagicMock())
            resp = TestClient(app).get(
                f"/controller/twin/{device_id}/replay?record_hash={'0' * 64}"
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["frames"] == []
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_checkpoints_endpoint(self):
        d = _tmp()
        try:
            db_path = os.path.join(d, "p61.db")
            s = Store(db_path)
            device_id = "h" * 64
            for i in range(3):
                rh = _rh(f"ce{i}")
                _seed_record(db_path, device_id, rh)
                s.store_frame_checkpoint(device_id, rh, _make_frames(5))
            app = create_app(_make_cfg(), s, MagicMock())
            resp = TestClient(app).get(f"/controller/twin/{device_id}/checkpoints")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 3
            assert len(data["checkpoints"]) == 3
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# TestFeatureHistoryEndpoint
# ---------------------------------------------------------------------------


class TestFeatureHistoryEndpoint:
    def _seed_record_with_features(self, db_path, device_id, rh, features, l4_dist):
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) "
            "VALUES (?,?,?,?)",
            (device_id, "pub", time.time(), time.time()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO records "
            "(record_hash, device_id, counter, timestamp_ms, inference, action_code, "
            "confidence, battery_pct, status, created_at, pitl_l4_features, pitl_l4_distance) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rh, device_id, 1, int(time.time() * 1000), 0x20, 0x01, 200, 80,
             "pending", time.time(), json.dumps(features), l4_dist),
        )
        conn.commit()
        conn.close()

    def test_features_endpoint_empty(self):
        d = _tmp()
        try:
            s = _make_store(d)
            device_id = "i" * 64
            app = create_app(_make_cfg(), s, MagicMock())
            resp = TestClient(app).get(f"/controller/twin/{device_id}/features")
            assert resp.status_code == 200
            assert resp.json() == []
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_features_endpoint_with_data(self):
        d = _tmp()
        try:
            db_path = os.path.join(d, "p61.db")
            Store(db_path)
            device_id = "j" * 64
            rh = _rh("feat1")
            features = [float(x) for x in range(12)]
            self._seed_record_with_features(db_path, device_id, rh, features, 2.5)
            s = Store(db_path)
            app = create_app(_make_cfg(), s, MagicMock())
            resp = TestClient(app).get(f"/controller/twin/{device_id}/features")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["record_hash"] == rh
            assert data[0]["features"] == features
            assert abs(data[0]["l4_distance"] - 2.5) < 1e-6
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# TestReplayTool
# ---------------------------------------------------------------------------


class TestReplayTool:
    def test_get_session_replay_tool(self):
        d = _tmp()
        try:
            db_path = os.path.join(d, "p61.db")
            s = Store(db_path)
            agent = BridgeAgent(cfg=_make_cfg(), store=s)
            device_id = "k" * 64
            rh = _rh("tool1")
            _seed_record(db_path, device_id, rh)
            frames = _make_frames(15)
            s.store_frame_checkpoint(device_id, rh, frames)
            result = agent._execute_tool("get_session_replay", {
                "device_id": device_id,
                "record_hash": rh,
            })
            assert result["record_hash"] == rh
            assert result["frames"] == frames
            assert result["frame_count"] == 15
        finally:
            shutil.rmtree(d, ignore_errors=True)
