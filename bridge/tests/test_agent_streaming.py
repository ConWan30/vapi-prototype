"""
Phase 31 — BridgeAgent Streaming (SSE) Tests

TestBridgeAgentStreaming (4):
1.  GET /agent/stream with wrong api_key → 403
2.  GET /agent/stream without api_key → 422
3.  GET /agent/stream with _MockStreamAgent → 200, content-type includes
    "text/event-stream", SSE lines contain text_delta and done events
4.  GET /agent/stream with _ErrorStreamAgent (raises ImportError in stream_ask)
    → 200, text/event-stream, first SSE event has type="error"
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub heavy deps before any bridge import
for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from fastapi.testclient import TestClient

from vapi_bridge.store import Store
from vapi_bridge.operator_api import create_operator_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_store() -> Store:
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


class _FakeConfig:
    operator_api_key = "testkey31stream"
    iotex_rpc_url = "http://127.0.0.1:8545"
    phg_credential_address = ""


class _MockStreamAgent:
    """Returns two SSE events: text_delta then done."""

    async def stream_ask(self, session_id: str, message: str):
        yield {"type": "text_delta", "text": "OK."}
        yield {"type": "done", "tools_used": []}

    def ask(self, session_id: str, message: str) -> dict:
        return {"session_id": session_id, "response": "OK.", "tools_used": []}


class _ErrorStreamAgent:
    """Raises ImportError on first stream_ask iteration."""

    async def stream_ask(self, session_id: str, message: str):
        raise ImportError("No module named 'anthropic'")
        yield  # marks function as async generator

    def ask(self, session_id: str, message: str) -> dict:
        raise ImportError("No module named 'anthropic'")


# ===========================================================================
# TestBridgeAgentStreaming
# ===========================================================================


class TestBridgeAgentStreaming(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        self.cfg = _FakeConfig()

    def _client(self, agent=None) -> TestClient:
        return TestClient(create_operator_app(self.cfg, self.store, _agent=agent))

    def test_1_wrong_api_key_returns_403(self):
        """GET /agent/stream with wrong api_key → 403."""
        client = self._client(_MockStreamAgent())
        resp = client.get(
            "/agent/stream",
            params={"session_id": "s1", "message": "hi", "api_key": "wrongkey"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_2_missing_api_key_returns_422(self):
        """GET /agent/stream without api_key → 422 (FastAPI validation error)."""
        client = self._client(_MockStreamAgent())
        resp = client.get(
            "/agent/stream",
            params={"session_id": "s1", "message": "hi"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_3_mock_agent_returns_sse_stream(self):
        """GET /agent/stream with mock → 200, text/event-stream, text_delta + done events."""
        client = self._client(_MockStreamAgent())
        with client.stream(
            "GET",
            "/agent/stream",
            params={
                "session_id": "s1",
                "message": "What is the leaderboard?",
                "api_key": "testkey31stream",
            },
        ) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
            lines = list(resp.iter_lines())

        # Extract non-empty data lines
        data_lines = [l for l in lines if l.startswith("data: ")]
        self.assertTrue(len(data_lines) >= 2, f"Expected >=2 data lines, got: {data_lines}")

        events = [json.loads(l[len("data: "):]) for l in data_lines]
        types_seen = {e["type"] for e in events}
        self.assertIn("text_delta", types_seen)
        self.assertIn("done", types_seen)

        # text_delta event has "text" field
        delta_events = [e for e in events if e["type"] == "text_delta"]
        self.assertTrue(len(delta_events) >= 1)
        self.assertIn("text", delta_events[0])

    def test_4_error_agent_returns_sse_error_event(self):
        """GET /agent/stream with error agent → 200, text/event-stream, type=error event."""
        client = self._client(_ErrorStreamAgent())
        with client.stream(
            "GET",
            "/agent/stream",
            params={
                "session_id": "s1",
                "message": "hello",
                "api_key": "testkey31stream",
            },
        ) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
            lines = list(resp.iter_lines())

        data_lines = [l for l in lines if l.startswith("data: ")]
        self.assertTrue(len(data_lines) >= 1, f"Expected >=1 data lines, got: {data_lines}")
        first_event = json.loads(data_lines[0][len("data: "):])
        self.assertEqual(first_event["type"], "error")
        self.assertIn("message", first_event)


if __name__ == "__main__":
    unittest.main()
