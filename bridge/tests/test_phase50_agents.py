"""Phase 50 Agent Coordination Tests — 14 tests → bridge 888 → 902.

All use tempfile.mkdtemp() for SQLite isolation (Windows WAL rule).
No hardware markers needed — all pure Python.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Stub hardware/external modules before any bridge import
for _m in [
    "anthropic", "web3", "web3.exceptions", "eth_account",
    "pydualsense", "hidapi", "hid",
]:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

# Ensure we run from a temp directory to avoid SQLite WAL conflicts
os.chdir(tempfile.mkdtemp())

from bridge.vapi_bridge.store import Store
from bridge.vapi_bridge.bridge_agent import (
    BridgeAgent,
    _PHASE46_ANOMALY_ANCHOR,
    _PHASE46_CONTINUITY_ANCHOR,
)
from bridge.vapi_bridge.calibration_intelligence_agent import CalibrationIntelligenceAgent


def _make_store() -> Store:
    db_dir = tempfile.mkdtemp()
    return Store(os.path.join(db_dir, "test_phase50.db"))


def _make_cfg(**kwargs):
    cfg = MagicMock()
    cfg.l4_anomaly_threshold    = 6.726
    cfg.l4_continuity_threshold = 5.097
    cfg.operator_api_key        = "test_key"
    cfg.adaptive_thresholds_enabled    = True
    cfg.agent_max_history_before_compress = 60
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


# ============================================================
# TestAgentEvents (4 tests)
# ============================================================

class TestAgentEvents(unittest.TestCase):

    def test_write_agent_event_creates_record(self):
        store = _make_store()
        eid = store.write_agent_event(
            event_type="test_event",
            payload=json.dumps({"key": "value"}),
            source="bridge_agent",
            device_id="abc123",
            target="calibration_intelligence_agent",
        )
        self.assertIsInstance(eid, int)
        self.assertGreater(eid, 0)
        events = store.read_unconsumed_events("calibration_intelligence_agent")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "test_event")
        self.assertEqual(events[0]["source_agent"], "bridge_agent")

    def test_read_unconsumed_filters_by_target(self):
        store = _make_store()
        store.write_agent_event(
            "e1", "{}", "bridge_agent",
            target="calibration_intelligence_agent",
        )
        store.write_agent_event(
            "e2", "{}", "calibration_intelligence_agent",
            target="bridge_agent",
        )
        calib_events  = store.read_unconsumed_events("calibration_intelligence_agent")
        bridge_events = store.read_unconsumed_events("bridge_agent")
        self.assertEqual(len(calib_events), 1)
        self.assertEqual(calib_events[0]["event_type"], "e1")
        self.assertEqual(len(bridge_events), 1)
        self.assertEqual(bridge_events[0]["event_type"], "e2")

    def test_mark_event_consumed(self):
        store = _make_store()
        eid = store.write_agent_event(
            "ev", "{}", "bridge_agent", target="calib"
        )
        store.mark_event_consumed(eid, "calib_agent")
        events = store.read_unconsumed_events("calib")
        self.assertEqual(len(events), 0)

    def test_drift_velocity_triggers_recalibration_event(self):
        store  = _make_store()
        cfg    = _make_cfg()

        # Mock BehavioralArchaeologist to return drift_velocity = 0.7
        mock_arch   = MagicMock()
        mock_report = MagicMock()
        mock_report.drift_velocity = 0.7
        mock_arch.analyze_device.return_value = mock_report

        agent = BridgeAgent(cfg, store, behavioral_arch=mock_arch)

        event = {
            "device_id":         "abcd1234" + "0" * 56,
            "inference_name":    "BIOMETRIC_ANOMALY",
            "pitl_l4_distance":  8.5,
            "pitl_humanity_prob": 0.3,
        }

        with patch.object(
            agent, "ask", return_value={"response": "test", "tools_used": []}
        ):
            agent.react(event)

        events = store.read_unconsumed_events("calibration_intelligence_agent")
        self.assertTrue(
            any(e["event_type"] == "recalibration_needed" for e in events),
            f"Expected recalibration_needed in {[e['event_type'] for e in events]}",
        )


# ============================================================
# TestCalibrationAgentTools (6 tests)
# ============================================================

class TestCalibrationAgentTools(unittest.TestCase):

    def test_get_threshold_history_schema(self):
        store = _make_store()
        store.write_threshold_history(
            threshold_type="global_mode6",
            old_value=6.726,
            new_value=6.900,
            drift_pct=2.6,
            sessions_used=74,
            phase="mode6_living_calibration",
        )
        history = store.get_threshold_history(limit=5)
        self.assertEqual(len(history), 1)
        row = history[0]
        for field in ("threshold_type", "old_value", "new_value", "drift_pct",
                      "sessions_used", "phase"):
            self.assertIn(field, row, f"Missing field: {field}")
        self.assertEqual(row["threshold_type"], "global_mode6")
        self.assertAlmostEqual(row["old_value"], 6.726, places=3)

    def test_get_zero_variance_features(self):
        store = _make_store()
        cfg   = _make_cfg()
        agent = CalibrationIntelligenceAgent(cfg, store)
        result = agent._execute_tool("get_zero_variance_features", {})
        self.assertIn("features", result)
        names = [f["name"] for f in result["features"]]
        self.assertIn("trigger_resistance_change_rate", names)
        self.assertIn("touch_position_variance", names)
        # Index 0 must be trigger_resistance_change_rate
        idx0 = next(
            f for f in result["features"]
            if f["name"] == "trigger_resistance_change_rate"
        )
        self.assertEqual(idx0["feature_index"], 0)
        # Index 10 must be touch_position_variance
        idx10 = next(
            f for f in result["features"]
            if f["name"] == "touch_position_variance"
        )
        self.assertEqual(idx10["feature_index"], 10)

    def test_trigger_recalibration_personal_enforces_min(self):
        store     = _make_store()
        cfg       = _make_cfg()
        device_id = "aabbccdd" + "0" * 56
        agent     = CalibrationIntelligenceAgent(cfg, store)

        # Insert personal profile with anomaly_threshold = 5.0
        store.upsert_player_calibration_profile(device_id, 5.0, 3.5, 3.0, 0.5, 35)

        # High distances → new threshold would be > 5.0 → refused
        high_dist_records = [
            {"device_id": device_id, "pitl_l4_distance": 8.0} for _ in range(30)
        ]
        with patch.object(
            store, "get_nominal_records_for_calibration", return_value=high_dist_records
        ):
            result = agent._execute_tool(
                "trigger_recalibration", {"mode": "personal", "device_id": device_id}
            )
        self.assertIn("error", result)
        self.assertIn("refused", result["error"])
        self.assertIn("loosen", result["error"])

        # Low distances → new threshold would be < 5.0 → accepted
        low_dist_records = [
            {"device_id": device_id, "pitl_l4_distance": 2.0} for _ in range(30)
        ]
        with patch.object(
            store, "get_nominal_records_for_calibration", return_value=low_dist_records
        ):
            result = agent._execute_tool(
                "trigger_recalibration", {"mode": "personal", "device_id": device_id}
            )
        self.assertEqual(result.get("status"), "applied")
        self.assertLess(result["new_anomaly"], 5.0)
        self.assertIn("enforcement", result)

    def test_trigger_recalibration_global_blocked_7_days(self):
        store = _make_store()
        cfg   = _make_cfg()
        agent = CalibrationIntelligenceAgent(cfg, store)

        # Insert a recent global threshold_history entry (< 7 days ago)
        store.write_threshold_history(
            threshold_type="global_test",
            old_value=6.726,
            new_value=6.800,
            drift_pct=1.1,
            sessions_used=74,
            phase="agent_triggered",
        )

        result = agent._execute_tool("trigger_recalibration", {"mode": "global"})
        self.assertIn("error", result)
        self.assertIn("refused", result["error"])

    def test_compare_device_fingerprints_similar_for_identical(self):
        store     = _make_store()
        cfg       = _make_cfg()
        device_a  = "aaaa0000" + "0" * 56
        device_b  = "bbbb0000" + "0" * 56

        store.upsert_player_calibration_profile(device_a, 6.726, 5.097, 4.0, 1.0, 35)
        store.upsert_player_calibration_profile(device_b, 6.726, 5.097, 4.0, 1.0, 35)

        agent  = BridgeAgent(cfg, store)
        result = agent._execute_tool(
            "compare_device_fingerprints",
            {"device_id_a": device_a, "device_id_b": device_b},
        )
        self.assertEqual(result.get("verdict"), "SIMILAR")
        self.assertIn("plain_english", result)

    def test_compare_device_fingerprints_caveat_always_present(self):
        store    = _make_store()
        cfg      = _make_cfg()
        device_a = "cccc0000" + "0" * 56
        device_b = "dddd0000" + "0" * 56

        store.upsert_player_calibration_profile(device_a, 6.726, 5.097, 4.0, 1.0, 35)
        store.upsert_player_calibration_profile(device_b, 6.726, 5.097, 14.0, 1.0, 35)

        agent  = BridgeAgent(cfg, store)
        result = agent._execute_tool(
            "compare_device_fingerprints",
            {"device_id_a": device_a, "device_id_b": device_b},
        )
        self.assertIn("plain_english", result)
        self.assertIn("0.362", result["plain_english"])


# ============================================================
# TestBridgeAgentProactive (4 tests)
# ============================================================

class TestBridgeAgentProactive(unittest.TestCase):

    def test_session_narrative_returns_3_sentences(self):
        store     = _make_store()
        cfg       = _make_cfg()
        device_id = "eeee0000" + "0" * 56

        agent = BridgeAgent(cfg, store)

        mock_records = [
            {
                "action_name":          "NOMINAL",
                "pitl_humanity_prob":   0.85,
                "pitl_l4_distance":     4.2,
                "pitl_l5_cv":           0.9,
                "pitl_l4_drift_velocity": 0.1,
            }
        ]
        mock_profile = {"total_records": 45}

        with patch.object(store, "get_recent_records", return_value=mock_records), \
             patch.object(store, "get_player_profile", return_value=mock_profile):
            result = agent._execute_tool(
                "get_session_narrative", {"device_id": device_id}
            )

        self.assertIn("sentence_1", result)
        self.assertIn("sentence_2", result)
        self.assertIn("sentence_3", result)
        # Verify each sentence is a non-empty string
        self.assertIsInstance(result["sentence_1"], str)
        self.assertGreater(len(result["sentence_1"]), 0)

    def test_threshold_drift_over_10pct_writes_alert(self):
        store = _make_store()
        cfg   = _make_cfg()
        agent = BridgeAgent(cfg, store)

        # 21.5% drift on continuity
        new_continuity = round(_PHASE46_CONTINUITY_ANCHOR * 1.215, 3)

        agent.check_threshold_drift(_PHASE46_ANOMALY_ANCHOR, new_continuity)

        insights = store.get_recent_insights(limit=10)
        types = [i.get("insight_type") for i in insights]
        self.assertIn("threshold_drift_alert", types)

    def test_threshold_drift_under_10pct_writes_stable(self):
        store = _make_store()
        cfg   = _make_cfg()
        agent = BridgeAgent(cfg, store)

        # 0% drift — exact anchors
        agent.check_threshold_drift(_PHASE46_ANOMALY_ANCHOR, _PHASE46_CONTINUITY_ANCHOR)

        insights = store.get_recent_insights(limit=10)
        types = [i.get("insight_type") for i in insights]
        self.assertIn("threshold_stable", types)
        self.assertNotIn("threshold_drift_alert", types)

    def test_compare_device_fingerprints_distinct_verdict(self):
        store     = _make_store()
        cfg       = _make_cfg()
        device_a  = "ffff0000" + "0" * 56
        device_b  = "eeee1111" + "0" * 56

        # mean_b - mean_a = 18.0 - 3.0 = 15.0, std_a = 1.0 → dist = 15.0 >> 6.726
        store.upsert_player_calibration_profile(device_a, 7.0, 5.2, 3.0, 1.0, 35)
        store.upsert_player_calibration_profile(device_b, 7.0, 5.2, 18.0, 1.0, 35)

        agent  = BridgeAgent(cfg, store)
        result = agent._execute_tool(
            "compare_device_fingerprints",
            {"device_id_a": device_a, "device_id_b": device_b},
        )
        self.assertEqual(result.get("verdict"), "DISTINCT")
        self.assertIn("0.362", result["plain_english"])


if __name__ == "__main__":
    unittest.main()
