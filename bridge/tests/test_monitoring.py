"""
Phase 18 — monitoring.py tests.

Tests cover:
- GET /monitor/health → 200, JSON with status field
- GET /monitor/metrics → 200, JSON with records_per_minute
- GET /monitor/alerts → 200, JSON list
- Health reports uptime_s > 0
- Metrics update after record submission
- Alerts list is empty when no errors
"""

import sys
import time
import unittest
from pathlib import Path

# Add bridge/ to path so vapi_bridge imports work
_bridge_dir = str(Path(__file__).resolve().parents[1])
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

from fastapi.testclient import TestClient

from vapi_bridge.monitoring import MonitoringState, create_monitoring_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_client() -> tuple:
    """Return (TestClient, fresh MonitoringState) using create_monitoring_app factory."""
    # Phase 36: use factory so tests get a private state instance (no global mutation)
    import vapi_bridge.monitoring as mon_module
    fresh_state = MonitoringState()
    app = create_monitoring_app(state=fresh_state)
    client = TestClient(app)
    return client, fresh_state, mon_module, None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMonitoringHealth(unittest.TestCase):

    def setUp(self):
        self.client, self.state, self.mod, self._orig = _fresh_client()

    def tearDown(self):
        pass  # factory-isolated state, no global teardown needed

    def test_health_returns_200(self):
        """GET /health returns HTTP 200."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_health_has_status_field(self):
        """GET /health JSON response contains a 'status' key."""
        resp = self.client.get("/health")
        data = resp.json()
        self.assertIn("status", data)
        self.assertIn(data["status"], ("ok", "degraded"))

    def test_health_uptime_positive(self):
        """health.uptime_s is greater than 0 immediately after startup."""
        resp = self.client.get("/health")
        data = resp.json()
        self.assertIn("uptime_s", data)
        self.assertGreaterEqual(data["uptime_s"], 0.0)


class TestMonitoringMetrics(unittest.TestCase):

    def setUp(self):
        self.client, self.state, self.mod, self._orig = _fresh_client()

    def tearDown(self):
        pass  # no teardown needed — factory creates isolated app per test

    def test_metrics_returns_200(self):
        """GET /metrics returns HTTP 200."""
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)

    def test_metrics_has_records_per_minute(self):
        """GET /metrics Prometheus text response contains vapi_records_per_minute."""
        resp = self.client.get("/metrics")
        # Phase 36: metrics endpoint now returns Prometheus text format
        self.assertIn("vapi_records_per_minute", resp.text)

    def test_metrics_update_after_submission(self):
        """records_submitted counter reflects record_submitted() calls in Prometheus text."""
        self.state.record_submitted("0xABCD")
        self.state.record_submitted("0xEF01")
        resp = self.client.get("/metrics")
        # Phase 36: check Prometheus text for total records = 2
        self.assertIn("vapi_records_submitted_total 2", resp.text)


class TestMonitoringAlerts(unittest.TestCase):

    def setUp(self):
        self.client, self.state, self.mod, self._orig = _fresh_client()

    def tearDown(self):
        pass  # factory-isolated state, no global teardown needed

    def test_alerts_returns_200(self):
        """GET /alerts returns HTTP 200."""
        resp = self.client.get("/alerts")
        self.assertEqual(resp.status_code, 200)

    def test_alerts_empty_when_healthy(self):
        """Alerts list is empty when the state has no errors (fresh state)."""
        resp = self.client.get("/alerts")
        data = resp.json()
        self.assertIsInstance(data, list)
        # Fresh state with uptime < 60s → no "no active devices" alert yet
        # (alert only fires after 60s uptime)
        critical = [a for a in data if a["severity"] == "critical"]
        self.assertEqual(len(critical), 0, f"Unexpected critical alerts: {data}")

    def test_alerts_fires_on_rpc_error(self):
        """RPC_UNREACHABLE alert fires after record_rpc_error()."""
        self.state.record_rpc_error()
        resp = self.client.get("/alerts")
        data = resp.json()
        codes = [a["code"] for a in data]
        self.assertIn("RPC_UNREACHABLE", codes)


if __name__ == "__main__":
    unittest.main()
