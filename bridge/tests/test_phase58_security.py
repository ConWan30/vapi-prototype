"""
Phase 58A Security Hardening — Test Suite (15 tests)

Covers:
  - Operator endpoint authentication (wrong key → 401, no key configured → 503)
  - Sliding-window rate limiter (within limit → pass, over limit → 429, window reset)
  - Audit log store methods (insert/retrieve/filter/idempotent schema)
  - BridgeAgent tools #24–27 (analyze_threshold_impact, predict_evasion_cost,
    get_anomaly_trend, generate_incident_report)
"""

import os
import sqlite3
import sys
import tempfile
import time
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup (follows existing Phase 55/56/57 test pattern)
# ---------------------------------------------------------------------------
_bridge_dir = os.path.join(os.path.dirname(__file__), "..")
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

# Stub web3 and eth_account so chain.py imports without real dependencies
_web3_mod = types.ModuleType("web3")
_web3_exc = types.ModuleType("web3.exceptions")


class _AsyncWeb3Stub:
    def __init__(self, *a, **kw):
        pass
    @property
    def eth(self):
        return self
    def contract(self, address=None, abi=None):
        return MagicMock()
    def to_checksum_address(self, addr):
        return addr


class _AsyncHTTPProviderStub:
    def __init__(self, *a, **kw):
        pass


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
from vapi_bridge.transports.http import create_app, _check_rate_limit, _rate_buckets  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_store(tmp_path):
    db = os.path.join(str(tmp_path), "test_p58.db")
    return Store(db)


def _make_cfg_with_key():
    cfg = MagicMock()
    cfg.operator_api_key = "secret-test-key"
    cfg.rate_limit_per_minute = 60
    return cfg


def _make_cfg_no_key():
    cfg = MagicMock()
    cfg.operator_api_key = ""
    cfg.rate_limit_per_minute = 60
    return cfg


# ---------------------------------------------------------------------------
# Class 1: TestOperatorAuth
# ---------------------------------------------------------------------------

class TestOperatorAuth:
    def test_passport_endpoint_rejects_wrong_key(self):
        """POST /operator/passport with wrong x-api-key -> 401."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            cfg = _make_cfg_with_key()
            app = create_app(cfg, store, MagicMock())
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/operator/passport",
                json={"device_id": "ab" * 32},
                headers={"x-api-key": "wrong-key"},
            )
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"

    def test_passport_issue_rejects_wrong_key(self):
        """POST /operator/passport/issue with wrong x-api-key -> 401."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            cfg = _make_cfg_with_key()
            app = create_app(cfg, store, MagicMock())
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/operator/passport/issue",
                json={"device_id": "ab" * 32, "device_secret": "s"},
                headers={"x-api-key": "wrong-key"},
            )
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"

    def test_endpoint_503_when_no_key_configured(self):
        """operator_api_key='' -> 503 on passport endpoint."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            cfg = _make_cfg_no_key()
            app = create_app(cfg, store, MagicMock())
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/operator/passport",
                json={"device_id": "ab" * 32},
                headers={"x-api-key": "any"},
            )
        assert resp.status_code == 503
        assert "operator_api_key not configured" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Class 2: TestRateLimiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def setup_method(self, _):
        _rate_buckets.clear()

    def test_rate_limiter_allows_within_limit(self):
        """N requests < limit -> none blocked."""
        ip = "10.0.0.1"
        limit = 5
        for _ in range(limit):
            assert _check_rate_limit(ip, limit) is True

    def test_rate_limiter_blocks_over_limit(self):
        """N+1 requests > limit -> last returns False."""
        ip = "10.0.0.2"
        limit = 3
        for _ in range(limit):
            _check_rate_limit(ip, limit)
        assert _check_rate_limit(ip, limit) is False

    def test_rate_limiter_window_reset(self):
        """Backdating bucket entries 62s clears window -> next request passes."""
        ip = "10.0.0.3"
        limit = 2
        for _ in range(limit):
            _check_rate_limit(ip, limit)
        assert _check_rate_limit(ip, limit) is False
        # Backdate entries so they fall outside the 60s window
        _rate_buckets[ip] = [t - 62 for t in _rate_buckets[ip]]
        assert _check_rate_limit(ip, limit) is True


# ---------------------------------------------------------------------------
# Class 3: TestAuditLog
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_log_operator_action_stores_entry(self):
        """log_operator_action() writes a row retrievable via get_operator_audit_log()."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.log_operator_action(
                endpoint="/operator/passport",
                device_id="deadbeef",
                api_key_hash="abc123",
                source_ip="127.0.0.1",
                status_code=401,
                outcome="unauthorized",
            )
            rows = store.get_operator_audit_log(limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row["endpoint"] == "/operator/passport"
        assert row["device_id"] == "deadbeef"
        assert row["status_code"] == 401
        assert row["outcome"] == "unauthorized"

    def test_get_audit_log_filtered_by_device(self):
        """device_id filter returns only matching rows."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.log_operator_action("/ep", "device-A", "h1", "1.1.1.1", 200, "ok")
            store.log_operator_action("/ep", "device-B", "h2", "1.1.1.2", 401, "unauth")
            rows_a = store.get_operator_audit_log(device_id="device-A")
            rows_all = store.get_operator_audit_log()
        assert len(rows_a) == 1
        assert rows_a[0]["device_id"] == "device-A"
        assert len(rows_all) == 2

    def test_audit_log_table_idempotent(self):
        """Creating Store twice on same DB does not raise (idempotent schema)."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "idem.db")
            Store(db)
            s2 = Store(db)
            s2.log_operator_action("/test", "", "", "127.0.0.1", 200, "ok")
            rows = s2.get_operator_audit_log()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Class 4: TestAnalyzeThresholdImpact
# ---------------------------------------------------------------------------

def _seed_l4_records(db_path, distances):
    """Insert synthetic records with given L4 distances into an existing store DB."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    for i, dist in enumerate(distances):
        did = "dev{:02d}".format(i)
        conn.execute(
            "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?)",
            (did, "pub", time.time(), time.time()),
        )
        rh = "{:064x}".format(i + 1)
        conn.execute(
            "INSERT OR IGNORE INTO records "
            "(record_hash, device_id, counter, timestamp_ms, inference, action_code, "
            "confidence, battery_pct, status, created_at, pitl_l4_distance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rh, did, i, 0, 0x20, 0, 80, 80, "pending", time.time(), dist),
        )
    conn.commit()
    conn.close()


def _make_agent(db_path):
    store = Store(db_path)
    cfg = MagicMock()
    cfg.l4_anomaly_threshold = 7.009
    cfg.l4_continuity_threshold = 5.367
    cfg.agent_max_history_before_compress = 60
    cfg.game_profile_id = ""
    return BridgeAgent(cfg=cfg, store=store)


class TestAnalyzeThresholdImpact:
    def test_threshold_impact_tighten_flips_sessions(self):
        """delta_pct=-10 tightens threshold; sessions in 6.3-7.0 range flip nominal->anomaly."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            Store(db)  # init schema
            _seed_l4_records(db, [3.0, 4.0, 6.5, 6.5, 6.5])
            agent = _make_agent(db)
            result = agent._execute_tool("analyze_threshold_impact", {"delta_pct": -10.0})
        assert result["total_sessions"] == 5
        assert result["nominal_to_anomaly"] > 0
        assert result["flip_pct"] > 0

    def test_threshold_impact_zero_delta_no_flips(self):
        """delta_pct=0 -> both flip counts == 0."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            Store(db)
            _seed_l4_records(db, [3.0, 5.0, 8.0])
            agent = _make_agent(db)
            result = agent._execute_tool("analyze_threshold_impact", {"delta_pct": 0.0})
        assert result["nominal_to_anomaly"] == 0
        assert result["anomaly_to_nominal"] == 0


# ---------------------------------------------------------------------------
# Class 5: TestGetAnomalyTrend
# ---------------------------------------------------------------------------

class TestGetAnomalyTrend:
    def test_anomaly_trend_empty_device(self):
        """Device with no records -> session_count=0 without error."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            agent = _make_agent(db)
            result = agent._execute_tool("get_anomaly_trend", {"device_id": "ab" * 32})
        assert result["session_count"] == 0
        assert "message" in result

    def test_anomaly_trend_degrading_detected(self):
        """Rising L4 distances -> trend='DEGRADING'."""
        device_id = "ff" * 32
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            Store(db)  # init schema
            conn = sqlite3.connect(db)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?)",
                (device_id, "pub", time.time(), time.time()),
            )
            base_time = time.time() - 3600  # within 7-day window
            distances = [2.0, 2.5, 3.0, 5.0, 8.0, 10.0]
            for i, dist in enumerate(distances):
                rh = "ff{:062x}".format(i)
                conn.execute(
                    "INSERT OR IGNORE INTO records "
                    "(record_hash, device_id, counter, timestamp_ms, inference, action_code, "
                    "confidence, battery_pct, status, created_at, pitl_l4_distance) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rh, device_id, i, 0, 0x20, 0, 80, 80, "pending", base_time + i * 60, dist),
                )
            conn.commit()
            conn.close()
            agent = _make_agent(db)
            result = agent._execute_tool("get_anomaly_trend", {"device_id": device_id, "days": 7})
        assert result["session_count"] == 6
        assert result["trend"] == "DEGRADING"


# ---------------------------------------------------------------------------
# Class 6: TestPredictEvasionCost
# ---------------------------------------------------------------------------

class TestPredictEvasionCost:
    def test_evasion_cost_attack_h_100pct(self):
        """attack_class=H -> l4_detection='100%'."""
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent(os.path.join(tmp, "t.db"))
            result = agent._execute_tool("predict_evasion_cost", {"attack_class": "H"})
        assert result["attack_class"] == "H"
        assert result["l4_detection"] == "100%"
        assert result["validation_n"] == 5

    def test_evasion_cost_unknown_class(self):
        """Unknown attack class returns error dict."""
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent(os.path.join(tmp, "t.db"))
            result = agent._execute_tool("predict_evasion_cost", {"attack_class": "Z"})
        assert "error" in result


# ---------------------------------------------------------------------------
# Class 7: TestGenerateIncidentReport
# ---------------------------------------------------------------------------

class TestGenerateIncidentReport:
    def test_incident_report_structure(self):
        """All expected top-level keys present; no exception for unknown device."""
        with tempfile.TemporaryDirectory() as tmp:
            agent = _make_agent(os.path.join(tmp, "t.db"))
            result = agent._execute_tool(
                "generate_incident_report", {"device_id": "ab" * 32}
            )
        required_keys = {
            "device_id", "first_seen", "last_seen", "records_total", "records_verified",
            "inference_breakdown", "humanity_prob", "phg_score", "recent_sessions",
            "ioid", "tournament_passport", "calibration", "recent_insights",
        }
        assert required_keys.issubset(set(result.keys()))
        assert result["ioid"]["registered"] is False
        assert result["tournament_passport"]["issued"] is False
