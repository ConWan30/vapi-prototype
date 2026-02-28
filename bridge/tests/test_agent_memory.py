"""
Tests for agent memory: protocol_insights table + history trimming — Phase 32

4 tests covering:
1. react() persists insight via store.store_protocol_insight
2. store_protocol_insight + get_recent_insights (DESC order)
3. prune_old_insights deletes all and returns count
4. _trim_history_if_long returns 21 entries (1 summary + 20 recent) for 85-entry history
"""
import tempfile
import time
import types
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub anthropic before importing bridge_agent
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = types.ModuleType("anthropic")


def _fresh_store():
    from vapi_bridge.store import Store
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Store(tf.name)


class TestAgentMemory(unittest.TestCase):

    def test_1_react_persists_insight_via_store(self):
        """react() calls store.store_protocol_insight with anomaly_reaction type."""
        from vapi_bridge.bridge_agent import BridgeAgent

        store = MagicMock()
        store.get_agent_session.return_value = []
        store.store_agent_session = MagicMock()
        store.store_protocol_insight = MagicMock()

        agent = BridgeAgent(MagicMock(), store)
        # Patch ask() so it returns a canned response without hitting the API
        agent.ask = MagicMock(
            return_value={"response": "Anomaly means X.", "tools_used": []}
        )

        event = {
            "device_id": "aabb1234" + "0" * 56,
            "inference_name": "BIOMETRIC_ANOMALY",
            "pitl_l4_distance": 3.1,
            "pitl_humanity_prob": 40,
        }
        agent.react(event)

        store.store_protocol_insight.assert_called_once()
        call_kwargs = store.store_protocol_insight.call_args
        assert "anomaly_reaction" in str(call_kwargs)

    def test_2_store_protocol_insight_and_get_recent_insights(self):
        """Insights are returned DESC by created_at (most recent first)."""
        store = _fresh_store()

        for i in range(3):
            store.store_protocol_insight(
                "type_a", f"content_{i}", device_id="dev1", severity="low"
            )
            time.sleep(0.01)  # ensure distinct timestamps

        results = store.get_recent_insights(limit=10)
        assert len(results) == 3
        # DESC order — most recently inserted first
        assert results[0]["content"] == "content_2"
        assert results[1]["content"] == "content_1"
        assert results[2]["content"] == "content_0"

    def test_3_prune_old_insights_deletes_all_returns_count(self):
        """prune_old_insights(age_days=0) deletes everything and returns row count."""
        store = _fresh_store()

        for i in range(5):
            store.store_protocol_insight("test_type", f"content_{i}")

        deleted = store.prune_old_insights(age_days=0)
        assert deleted == 5
        assert store.get_recent_insights() == []

    def test_4_trim_history_if_long_returns_21_entries(self):
        """_trim_history_if_long on 85-entry history returns [summary] + last 20 = 21 entries."""
        from vapi_bridge.bridge_agent import BridgeAgent

        agent = BridgeAgent(MagicMock(), MagicMock())
        history = [{"role": "user", "content": f"msg_{i}"} for i in range(85)]

        trimmed = agent._trim_history_if_long(history, max_messages=80)

        assert len(trimmed) == 21  # 1 summary + 20 most recent
        # Phase 37: summary message uses "compressed" (enhanced trim with tool inventory)
        assert "compressed" in trimmed[0]["content"] or "trimmed" in trimmed[0]["content"]
        assert trimmed[0]["role"] == "user"
        assert trimmed[-1]["content"] == "msg_84"  # last original message preserved


if __name__ == "__main__":
    unittest.main()
