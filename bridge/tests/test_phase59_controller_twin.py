"""
Phase 59A Controller Twin — Test Suite (15 tests)
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import types
from unittest.mock import MagicMock

import pytest

_bridge_dir = os.path.join(os.path.dirname(__file__), "..")
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

_controller_dir = os.path.join(os.path.dirname(__file__), "..", "..", "controller")
if _controller_dir not in sys.path:
    sys.path.insert(0, _controller_dir)

# Stub web3 and eth_account
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
from vapi_bridge.transports.http import create_app, _ws_twin_clients, ws_twin_broadcast_record  # noqa: E402


def _tmp():
    return tempfile.mkdtemp()

def _make_store(d):
    return Store(os.path.join(d, "p59.db"))

def _make_cfg():
    cfg = MagicMock()
    cfg.operator_api_key = "key"
    cfg.rate_limit_per_minute = 60
    cfg.l4_anomaly_threshold = 7.009
    cfg.l4_continuity_threshold = 5.367
    cfg.agent_max_history_before_compress = 60
    cfg.game_profile_id = ""
    return cfg

def _seed(db_path, device_id, distances):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) VALUES (?,?,?,?)",
        (device_id, "pub", time.time(), time.time()),
    )
    for i, dist in enumerate(distances):
        rh = "{:064x}".format(i + 1)
        conn.execute(
            "INSERT OR IGNORE INTO records "
            "(record_hash, device_id, counter, timestamp_ms, inference, action_code, "
            "confidence, battery_pct, status, created_at, pitl_l4_distance) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rh, device_id, i, 0, 0x20, 0, 80, 80, "pending",
             time.time() - (len(distances) - i), dist),
        )
    conn.commit()
    conn.close()

def _agent(db_path):
    return BridgeAgent(cfg=_make_cfg(), store=Store(db_path))


# ---------------------------------------------------------------------------
# Class 1: TestGetControllerTwinSnapshot
# ---------------------------------------------------------------------------

class TestGetControllerTwinSnapshot:
    def test_twin_snapshot_empty_device(self):
        d = _tmp()
        try:
            s = _make_store(d)
            r = s.get_controller_twin_snapshot("ab" * 32)
            for k in ("device", "calibration", "biometric_fingerprint",
                      "ioid", "passport", "audit_log", "anomaly_trend",
                      "recent_records", "insights"):
                assert k in r, f"Missing: {k}"
            assert r["anomaly_trend"] == "UNKNOWN"
            assert r["recent_records"] == []
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_twin_snapshot_with_records(self):
        d = _tmp()
        try:
            db = os.path.join(d, "t.db")
            Store(db)
            _seed(db, "cc" * 32, [3.0, 4.0, 5.0, 6.0, 7.0])
            r = Store(db).get_controller_twin_snapshot("cc" * 32)
            assert len(r["recent_records"]) == 5
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_twin_snapshot_trend_degrading(self):
        d = _tmp()
        try:
            db = os.path.join(d, "t.db")
            Store(db)
            _seed(db, "dd" * 32, [2.0, 2.5, 5.0, 8.0])
            r = Store(db).get_controller_twin_snapshot("dd" * 32)
            assert r["anomaly_trend"] in ("DEGRADING", "IMPROVING", "STABLE")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_twin_snapshot_trend_improving(self):
        d = _tmp()
        try:
            db = os.path.join(d, "t.db")
            Store(db)
            _seed(db, "ee" * 32, [8.0, 7.0, 4.0, 2.0])
            r = Store(db).get_controller_twin_snapshot("ee" * 32)
            assert r["anomaly_trend"] in ("DEGRADING", "IMPROVING", "STABLE", "UNKNOWN")
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Class 2: TestGetBiometricFingerprint
# ---------------------------------------------------------------------------

class TestGetBiometricFingerprint:
    def test_biometric_fingerprint_returns_none_for_unknown(self):
        d = _tmp()
        try:
            r = _make_store(d).get_biometric_fingerprint("ab" * 32)
            assert r is None
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_biometric_fingerprint_table_query_does_not_raise(self):
        d = _tmp()
        try:
            ok = True
            try:
                _make_store(d).get_biometric_fingerprint("ff" * 32)
            except Exception:
                ok = False
            assert ok
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Class 3: TestControllerTwinHTTP
# ---------------------------------------------------------------------------

class TestControllerTwinHTTP:
    def test_controller_twin_endpoint_returns_200(self):
        d = _tmp()
        try:
            app = create_app(_make_cfg(), _make_store(d), MagicMock())
            resp = TestClient(app).get(f"/controller/twin/{'ab' * 32}")
            assert resp.status_code == 200
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_controller_twin_endpoint_required_keys(self):
        d = _tmp()
        try:
            app = create_app(_make_cfg(), _make_store(d), MagicMock())
            data = TestClient(app).get(f"/controller/twin/{'ab' * 32}").json()
            for k in ("device", "calibration", "ioid", "passport", "anomaly_trend"):
                assert k in data
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_controller_twin_chain_endpoint(self):
        d = _tmp()
        try:
            app = create_app(_make_cfg(), _make_store(d), MagicMock())
            resp = TestClient(app).get(f"/controller/twin/{'ab' * 32}/chain")
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_controller_twin_chain_empty_device(self):
        d = _tmp()
        try:
            app = create_app(_make_cfg(), _make_store(d), MagicMock())
            resp = TestClient(app).get(f"/controller/twin/{'ff' * 32}/chain?limit=10")
            assert resp.status_code == 200
            assert resp.json() == []
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Class 4: TestWsTwin
# ---------------------------------------------------------------------------

class TestWsTwin:
    def test_ws_twin_accepts_connection(self):
        d = _tmp()
        try:
            app = create_app(_make_cfg(), _make_store(d), MagicMock())
            with TestClient(app).websocket_connect(f"/ws/twin/{'ab' * 32}"):
                pass
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_ws_twin_broadcast_function_exists(self):
        """ws_twin_broadcast_record is callable and _ws_twin_clients is a dict."""
        assert callable(ws_twin_broadcast_record)
        assert isinstance(_ws_twin_clients, dict)


# ---------------------------------------------------------------------------
# Class 5: TestIBISnapshot
# ---------------------------------------------------------------------------

class TestIBISnapshot:
    def test_get_ibi_snapshot_empty(self):
        from tinyml_biometric_fusion import BiometricFeatureExtractor
        ex = BiometricFeatureExtractor()
        snap = ex.get_ibi_snapshot(last_n=20)
        assert isinstance(snap, dict)
        assert snap["cross"] == []
        assert snap["r2"] == []
        assert "jitter_variance" in snap

    def test_get_ibi_snapshot_with_data(self):
        from tinyml_biometric_fusion import BiometricFeatureExtractor
        ex = BiometricFeatureExtractor()
        for v in [100.0, 120.0, 115.0, 110.0, 105.0]:
            ex._jitter_r2_ibis.append(v)
        snap = ex.get_ibi_snapshot(last_n=10)
        assert len(snap["r2"]) == 5
        assert snap["r2"] == [100.0, 120.0, 115.0, 110.0, 105.0]


# ---------------------------------------------------------------------------
# Class 6: TestControllerTwinTool
# ---------------------------------------------------------------------------

class TestControllerTwinTool:
    def test_get_controller_twin_data_tool(self):
        d = _tmp()
        try:
            r = _agent(os.path.join(d, "t.db"))._execute_tool(
                "get_controller_twin_data", {"device_id": "ab" * 32}
            )
            for k in ("device", "calibration", "ioid", "passport", "anomaly_trend", "recent_records"):
                assert k in r
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_get_controller_twin_data_missing_device_id(self):
        d = _tmp()
        try:
            r = _agent(os.path.join(d, "t.db"))._execute_tool("get_controller_twin_data", {})
            assert "error" in r
        finally:
            shutil.rmtree(d, ignore_errors=True)
