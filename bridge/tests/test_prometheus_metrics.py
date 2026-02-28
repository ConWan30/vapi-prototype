"""
Tests for Prometheus-compatible /metrics endpoint — Phase 36

4 tests covering:
1. GET /metrics returns 200 with Content-Type text/plain
2. Response body contains required gauge names and # HELP lines
3. vapi_critical_devices gauge present in output
4. vapi_active_detection_policies gauge present in output
"""
import sys
import os
import unittest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.monitoring import create_monitoring_app, MonitoringState


def _make_client(store=None):
    if store is None:
        store = MagicMock()
        store.get_devices_by_risk_label.return_value = []
        store.get_all_latest_digests.return_value = []
        store.get_all_active_policies.return_value = []

    _state = MonitoringState()
    app = create_monitoring_app(state=_state, store=store)
    return TestClient(app), _state


class TestPrometheusMetrics(unittest.TestCase):

    def test_1_metrics_returns_200_with_text_plain_content_type(self):
        """GET /metrics returns HTTP 200 with text/plain content-type."""
        client, _ = _make_client()
        resp = client.get("/metrics")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        ct = resp.headers.get("content-type", "")
        assert "text/plain" in ct, f"Expected text/plain, got {ct}"

    def test_2_response_contains_required_gauge_names_and_help_lines(self):
        """Response body contains # HELP lines for all 10 required metric names."""
        client, _ = _make_client()
        resp = client.get("/metrics")
        body = resp.text

        required_metrics = [
            "vapi_records_submitted_total",
            "vapi_records_failed_total",
            "vapi_records_per_minute",
            "vapi_active_devices",
            "vapi_rpc_errors_total",
            "vapi_uptime_seconds",
            "vapi_critical_devices",
            "vapi_warming_devices",
            "vapi_digests_synthesized",
            "vapi_active_detection_policies",
        ]
        for metric in required_metrics:
            assert metric in body, (
                f"Metric '{metric}' not found in /metrics output"
            )
            assert f"# HELP {metric}" in body, (
                f"# HELP line for '{metric}' not found in /metrics output"
            )

    def test_3_vapi_critical_devices_gauge_present(self):
        """vapi_critical_devices gauge is present with a numeric value."""
        store = MagicMock()
        store.get_devices_by_risk_label.side_effect = lambda label: (
            [{"device_id": "dev_crit"}] if label == "critical" else []
        )
        store.get_all_latest_digests.return_value = []
        store.get_all_active_policies.return_value = []

        client, _ = _make_client(store=store)
        resp = client.get("/metrics")
        body = resp.text

        assert "vapi_critical_devices" in body
        # Should show value 1 for the single critical device
        assert "vapi_critical_devices 1" in body, (
            f"Expected 'vapi_critical_devices 1' in output:\n{body}"
        )

    def test_4_vapi_active_detection_policies_gauge_present(self):
        """vapi_active_detection_policies gauge is present and reflects active count."""
        store = MagicMock()
        store.get_devices_by_risk_label.return_value = []
        store.get_all_latest_digests.return_value = []
        store.get_all_active_policies.return_value = [
            {"device_id": "dev_a", "multiplier": 0.70},
            {"device_id": "dev_b", "multiplier": 0.85},
            {"device_id": "dev_c", "multiplier": 0.70},
        ]

        client, _ = _make_client(store=store)
        resp = client.get("/metrics")
        body = resp.text

        assert "vapi_active_detection_policies" in body
        assert "vapi_active_detection_policies 3" in body, (
            f"Expected 'vapi_active_detection_policies 3' in output:\n{body}"
        )


if __name__ == "__main__":
    unittest.main()
