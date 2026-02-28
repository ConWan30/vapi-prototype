"""
Phase 37 — AlertRouter tests.

4 tests covering:
1. Dispatch fires for insight severity >= threshold
2. Dispatch silenced below threshold
3. Webhook HTTP non-2xx logged as warning, does not raise
4. No-op when alert_webhook_url is empty
"""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.alert_router import AlertRouter


def _make_router(webhook_url="http://example.com/hook",
                 threshold="medium",
                 fmt="generic"):
    cfg = MagicMock()
    cfg.alert_webhook_url        = webhook_url
    cfg.alert_webhook_format     = fmt
    cfg.alert_severity_threshold = threshold
    store = MagicMock()
    return AlertRouter(cfg, store), store


class TestAlertRouter(unittest.IsolatedAsyncioTestCase):

    async def test_1_dispatch_fires_above_threshold(self):
        """Dispatch fires when severity meets threshold (critical >= medium)."""
        router, store = _make_router(threshold="medium")
        store.get_recent_insights.return_value = [
            {"id": 1, "severity": "critical", "insight_type": "credential_suspended",
             "content": "test", "device_id": "aa" * 32, "created_at": 0.0},
        ]
        dispatched = []

        async def _fake_dispatch(url, insight):
            dispatched.append(insight)

        router._dispatch = _fake_dispatch
        await router._poll_and_dispatch()
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(router._last_id, 1)

    async def test_2_dispatch_silenced_below_threshold(self):
        """Dispatch is silenced when severity is below threshold (low < medium)."""
        router, store = _make_router(threshold="medium")
        store.get_recent_insights.return_value = [
            {"id": 2, "severity": "low", "insight_type": "policy_adjustment",
             "content": "test", "device_id": "", "created_at": 0.0},
        ]
        dispatched = []

        async def _fake_dispatch(url, insight):
            dispatched.append(insight)

        router._dispatch = _fake_dispatch
        await router._poll_and_dispatch()
        self.assertEqual(len(dispatched), 0)
        self.assertEqual(router._last_id, 2)  # ID still tracked

    async def test_3_webhook_non_2xx_logs_warning_not_raises(self):
        """A non-2xx HTTP response from the webhook does not raise; just logs warning."""
        router, store = _make_router(threshold="low")
        insight = {"id": 3, "severity": "medium", "insight_type": "bot_farm",
                   "content": "test", "device_id": "bb" * 32, "created_at": 0.0}

        class _FakeResp:
            def getcode(self): return 500
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            # Should not raise despite 500
            await router._dispatch("http://example.com/hook", insight)

    async def test_4_noop_when_webhook_url_empty(self):
        """No dispatch calls when alert_webhook_url is empty string."""
        router, store = _make_router(webhook_url="", threshold="low")
        store.get_recent_insights.return_value = [
            {"id": 4, "severity": "critical", "insight_type": "credential_suspended",
             "content": "test", "device_id": "cc" * 32, "created_at": 0.0},
        ]
        dispatched = []

        async def _fake_dispatch(url, insight):
            dispatched.append(insight)

        router._dispatch = _fake_dispatch
        await router._poll_and_dispatch()
        self.assertEqual(len(dispatched), 0)  # webhook_url empty → no dispatch
        self.assertEqual(router._last_id, 4)   # ID still tracked


if __name__ == "__main__":
    unittest.main()
