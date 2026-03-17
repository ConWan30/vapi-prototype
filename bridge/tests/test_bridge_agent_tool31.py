"""Tests for BridgeAgent tool #31 get_reflex_baseline — Phase 63."""

import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Stub heavy optional deps
for _mod in [
    "web3", "web3.exceptions", "eth_account",
    "pydualsense", "pydualsense.enums",
    "hidapi", "hid", "anthropic",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
_anthropic = sys.modules["anthropic"]
_anthropic.Anthropic = MagicMock()
_types_mod = types.ModuleType("anthropic.types")
_anthropic.types = _types_mod
sys.modules["anthropic.types"] = _types_mod

sys.path.insert(0, str(Path(__file__).parents[1]))

from vapi_bridge.store import Store
from vapi_bridge.bridge_agent import BridgeAgent


class _FakeConfig:
    operator_api_key = "testkey31"
    iotex_rpc_url = "http://127.0.0.1:8545"
    phg_credential_address = ""
    enrollment_min_sessions = 10
    l6b_enabled = False


def _fresh_store() -> Store:
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


class TestTool31:
    def test_get_reflex_baseline_returns_stats(self):
        """store has probe rows → tool returns mean/std/distribution."""
        store = _fresh_store()
        store.insert_l6b_probe("cc" * 32, 1000000, 130.0, "HUMAN", 700.0)
        store.insert_l6b_probe("cc" * 32, 1067000, 200.0, "HUMAN", 850.0)
        agent = BridgeAgent(_FakeConfig(), store)
        result = agent._execute_tool("get_reflex_baseline", {"device_id": "cc" * 32})
        assert result["probe_count"] == 2
        assert result["mean_latency_ms"] == pytest.approx(165.0)
        assert result["bot_events"] == 0
        assert result["classification_distribution"]["HUMAN"] == 2

    def test_get_reflex_baseline_no_data(self):
        """store empty → returns probe_count=0 with status=no_probes_recorded."""
        store = _fresh_store()
        agent = BridgeAgent(_FakeConfig(), store)
        result = agent._execute_tool("get_reflex_baseline", {"device_id": "dd" * 32})
        assert result["probe_count"] == 0
        assert result.get("status") == "no_probes_recorded"
