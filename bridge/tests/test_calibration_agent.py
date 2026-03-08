"""
Phase 17 — test_calibration_agent.py

Tests cover:
- Group 1: Throttling (min interval, session delta gate)
- Group 2: Threshold safety guard (large delta rejected, small delta applied)
- Group 3: Output parser
- Group 4: Session quality filter
- Group 5: ProactiveMonitor integration
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vapi_bridge.calibration_agent import CalibrationAgent, parse_calibrator_output

# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

SESSION_DIR = Path(__file__).resolve().parents[2] / "sessions" / "human"


def _make_cfg(anomaly: float = 6.905, continuity: float = 5.190):
    cfg = MagicMock()
    cfg.l4_anomaly_threshold    = str(anomaly)
    cfg.l4_continuity_threshold = str(continuity)
    return cfg


def _make_store():
    store = MagicMock()
    store.store_insight = MagicMock(return_value=None)
    return store


def _make_agent(sessions_dir=None, anomaly=6.905, continuity=5.190):
    cfg = _make_cfg(anomaly, continuity)
    store = _make_store()
    if sessions_dir is None:
        sessions_dir = str(SESSION_DIR)
    return CalibrationAgent(
        store=store,
        cfg=cfg,
        sessions_dir=sessions_dir,
        calibrator_script="scripts/threshold_calibrator.py",
    )


# ---------------------------------------------------------------------------
# Group 1: Throttling
# ---------------------------------------------------------------------------

class TestThrottling(unittest.TestCase):

    def test_min_interval_prevents_rerun(self):
        """If last run was < MIN_INTERVAL_SECS ago, returns None immediately."""
        agent = _make_agent()
        import time
        agent._last_run = time.time()  # set to NOW
        agent._last_count = 0

        result = asyncio.get_event_loop().run_until_complete(
            agent.check_and_recalibrate()
        )
        self.assertIsNone(result, "Should skip: last run was just now")

    def test_session_delta_gate(self):
        """If fewer than RECALIB_SESSION_DELTA new sessions, returns None."""
        agent = _make_agent()
        agent._last_run = 0.0  # no throttle
        # Set last count so that current file count - last_count < 5
        # Point to a directory that has 3 files (or use mock)
        import tempfile, os, json
        tmpdir = tempfile.mkdtemp()
        for i in range(3):
            p = Path(tmpdir) / f"hw_{i:03d}.json"
            p.write_text('{"reports":[{"timestamp_ms":0,"features":{"buttons_0":0,"r2_trigger":0,"gyro_x":0,"gyro_z":0,"right_stick_x":0,"right_stick_y":0,"left_stick_x":0,"left_stick_y":0}}]}')
        agent._sessions_dir = Path(tmpdir)
        agent._last_count = 0  # delta = 3, need 5

        result = asyncio.get_event_loop().run_until_complete(
            agent.check_and_recalibrate()
        )
        self.assertIsNone(result, "Delta=3 < RECALIB_SESSION_DELTA=5 → skip")


# ---------------------------------------------------------------------------
# Group 2: Threshold safety guard
# ---------------------------------------------------------------------------

class TestThresholdGuard(unittest.TestCase):

    def _run_with_mock_proc(self, stdout: str, cur_anomaly=6.905, cur_continuity=5.190):
        """Helper: run check_and_recalibrate with mock subprocess output."""
        import tempfile, time
        tmpdir = tempfile.mkdtemp()
        # Create 10 valid session stubs
        for i in range(10):
            p = Path(tmpdir) / f"hw_{i:03d}.json"
            p.write_text(json_stub())

        agent = _make_agent(sessions_dir=tmpdir, anomaly=cur_anomaly, continuity=cur_continuity)
        agent._last_run = 0.0
        agent._last_count = 0

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            result = asyncio.get_event_loop().run_until_complete(
                agent.check_and_recalibrate()
            )
        return result, agent

    def test_large_delta_rejected(self):
        """New threshold >10% different from current → REJECTED."""
        stdout = "L4 anomaly_threshold: 9.000\nL4 continuity_threshold: 7.000\n"
        # 9.000 vs 6.905 = delta 30.3% → rejected
        result, agent = self._run_with_mock_proc(stdout, cur_anomaly=6.905)
        self.assertIsNotNone(result)
        self.assertIn("REJECTED", result)
        # Cfg should NOT have been updated
        self.assertEqual(agent._cfg.l4_anomaly_threshold, "6.905")

    def test_small_delta_applied(self):
        """New threshold <10% different → accepted and applied."""
        stdout = "L4 anomaly_threshold: 7.000\nL4 continuity_threshold: 5.300\n"
        # 7.000 vs 6.905 = delta 1.4% → accepted
        result, agent = self._run_with_mock_proc(stdout, cur_anomaly=6.905)
        self.assertIsNotNone(result)
        self.assertNotIn("REJECTED", result)
        self.assertNotIn("Error", result)
        # Cfg should be updated
        self.assertEqual(agent._cfg.l4_anomaly_threshold, "7.0")

    def test_subprocess_failure_returns_error(self):
        """Non-zero returncode → returns error string."""
        import tempfile
        tmpdir = tempfile.mkdtemp()
        for i in range(10):
            p = Path(tmpdir) / f"hw_{i:03d}.json"
            p.write_text(json_stub())
        agent = _make_agent(sessions_dir=tmpdir)
        agent._last_run = 0.0
        agent._last_count = 0

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Traceback: something went wrong",
            )
            result = asyncio.get_event_loop().run_until_complete(
                agent.check_and_recalibrate()
            )
        self.assertIsNotNone(result)
        self.assertIn("failed", result.lower())


# ---------------------------------------------------------------------------
# Group 3: Output parser
# ---------------------------------------------------------------------------

class TestParseCalibrator(unittest.TestCase):

    def test_parse_calibrator_output_valid(self):
        """Standard calibrator output → (anomaly, continuity) floats."""
        stdout = (
            "Processing 69 sessions...\n"
            "L4 anomaly_threshold: 6.905\n"
            "L4 continuity_threshold: 5.190\n"
            "Done.\n"
        )
        a, c = parse_calibrator_output(stdout)
        self.assertAlmostEqual(a, 6.905)
        self.assertAlmostEqual(c, 5.190)

    def test_parse_calibrator_output_invalid(self):
        """Output with no threshold lines → (None, None)."""
        a, c = parse_calibrator_output("No thresholds here")
        self.assertIsNone(a)
        self.assertIsNone(c)

    def test_parse_calibrator_output_partial(self):
        """Only anomaly line → (None, None) because both required."""
        a, c = parse_calibrator_output("L4 anomaly_threshold: 6.905\n")
        self.assertIsNone(a)
        self.assertIsNone(c)


# ---------------------------------------------------------------------------
# Group 4: Session quality filtering
# ---------------------------------------------------------------------------

class TestSessionQuality(unittest.TestCase):

    def test_anomalous_polling_rate_excluded(self):
        """
        Session with 203 Hz polling rate (hw_043 pattern) should be flagged.
        Creates a synthetic session JSON with 1ms timestamps (1000 Hz) and
        another with 5ms timestamps (~200 Hz) and verifies the latter is excluded.
        """
        import tempfile, json
        tmpdir = tempfile.mkdtemp()

        # Normal session: 1ms intervals = 1000 Hz
        normal = {"reports": [{"timestamp_ms": float(i), "features": {}} for i in range(100)]}
        (Path(tmpdir) / "hw_001.json").write_text(json.dumps(normal))

        # Anomalous session: 5ms intervals = 200 Hz (below 800 Hz threshold)
        anomalous = {"reports": [{"timestamp_ms": float(i * 5), "features": {}} for i in range(100)]}
        (Path(tmpdir) / "hw_002.json").write_text(json.dumps(anomalous))

        agent = _make_agent(sessions_dir=tmpdir)
        all_files = sorted(Path(tmpdir).glob("hw_*.json"))
        valid, flags = agent._filter_sessions(all_files)

        self.assertEqual(len(flags), 1)
        self.assertIn("hw_002.json", flags[0]["session"])
        self.assertEqual(len(valid), 1)
        self.assertIn("hw_001.json", str(valid[0]))

    def test_quality_flags_persisted(self):
        """_persist_quality_flags() calls store.store_insight for each flag."""
        agent = _make_agent()
        flags = [
            {"session": "hw_043.json", "polling_rate_hz": 203.0,
             "reason": "polling_rate 203 Hz outside [800, 1100] Hz"},
        ]
        agent._persist_quality_flags(flags)
        agent._store.store_insight.assert_called_once()


# ---------------------------------------------------------------------------
# Group 5: ProactiveMonitor integration
# ---------------------------------------------------------------------------

class TestProactiveMonitorIntegration(unittest.TestCase):

    def test_monitor_calls_calibration_check(self):
        """
        ProactiveMonitor._monitor_cycle calls _check_auto_calibration
        when calibration_agent is provided.
        """
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from vapi_bridge.proactive_monitor import ProactiveMonitor

        mock_store       = MagicMock()
        mock_arch        = MagicMock()
        mock_net         = MagicMock()
        mock_agent       = MagicMock()
        mock_cfg         = MagicMock()
        mock_cal_agent   = AsyncMock()
        mock_cal_agent.check_and_recalibrate = AsyncMock(return_value=None)

        monitor = ProactiveMonitor(
            store=mock_store,
            behavioral_arch=mock_arch,
            network_detector=mock_net,
            agent=mock_agent,
            cfg=mock_cfg,
            calibration_agent=mock_cal_agent,
        )

        # Patch the 3 existing checks to no-ops so we only test _check_auto_calibration
        with patch.object(monitor, "_check_anomaly_clusters", AsyncMock()):
            with patch.object(monitor, "_check_high_risk_trajectories", AsyncMock()):
                with patch.object(monitor, "_check_eligibility_horizons", AsyncMock()):
                    asyncio.get_event_loop().run_until_complete(monitor._monitor_cycle())

        mock_cal_agent.check_and_recalibrate.assert_called_once()

    def test_calibration_result_dispatched_as_insight(self):
        """When calibration returns a result string, it is dispatched as an insight."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from vapi_bridge.proactive_monitor import ProactiveMonitor

        mock_store     = MagicMock()
        mock_store.store_protocol_insight = MagicMock()
        mock_cal_agent = AsyncMock()
        mock_cal_agent.check_and_recalibrate = AsyncMock(
            return_value="Applied: anomaly_threshold=7.000 (was 6.905)"
        )

        monitor = ProactiveMonitor(
            store=mock_store,
            behavioral_arch=MagicMock(),
            network_detector=MagicMock(),
            agent=MagicMock(),
            cfg=MagicMock(),
            calibration_agent=mock_cal_agent,
        )

        with patch("vapi_bridge.proactive_monitor.ws_broadcast", AsyncMock()):
            asyncio.get_event_loop().run_until_complete(monitor._check_auto_calibration())

        mock_store.store_protocol_insight.assert_called_once()
        call_kwargs = mock_store.store_protocol_insight.call_args[1]
        self.assertEqual(call_kwargs["insight_type"], "calibration_auto")
        self.assertEqual(call_kwargs["severity"], "info")

    def test_no_calibration_agent_no_crash(self):
        """ProactiveMonitor works without calibration_agent (None)."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from vapi_bridge.proactive_monitor import ProactiveMonitor
        monitor = ProactiveMonitor(
            store=MagicMock(), behavioral_arch=MagicMock(),
            network_detector=MagicMock(), agent=MagicMock(), cfg=MagicMock(),
        )
        asyncio.get_event_loop().run_until_complete(monitor._check_auto_calibration())
        # No exception = pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def json_stub():
    import json as _json
    return _json.dumps({
        "reports": [
            {"timestamp_ms": float(i), "features": {
                "buttons_0": 0, "r2_trigger": 0,
                "gyro_x": 0.0, "gyro_z": 0.0,
                "right_stick_x": 0, "right_stick_y": 0,
                "left_stick_x": 0, "left_stick_y": 0,
            }}
            for i in range(100)
        ]
    })


if __name__ == "__main__":
    unittest.main()
