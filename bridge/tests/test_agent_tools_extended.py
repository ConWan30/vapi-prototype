"""
Phase 31 — BridgeAgent Extended Tool Tests

TestBridgeAgentToolsExtended (4):
1.  _execute_tool("get_phg_checkpoints", {...}) → dict with "checkpoints" list
2.  _execute_tool("check_eligibility", {...}) → dict with "eligible" bool
3.  _execute_tool("get_pitl_proof", {...}) → dict (error dict when no proof)
4.  react({anomaly_event}) when anthropic raises ImportError → dict with "alert" str
    and "severity"="medium"
"""

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub heavy deps before any bridge import
for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from vapi_bridge.store import Store
from vapi_bridge.bridge_agent import BridgeAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEVICE_HEX = "cd" * 32  # 64-char hex


def _fresh_store() -> Store:
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


class _FakeConfig:
    operator_api_key = "testkey31ext"
    iotex_rpc_url = "http://127.0.0.1:8545"
    phg_credential_address = ""


# ===========================================================================
# TestBridgeAgentToolsExtended
# ===========================================================================


class TestBridgeAgentToolsExtended(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        self.cfg = _FakeConfig()
        self.agent = BridgeAgent(self.cfg, self.store)

    def test_1_get_phg_checkpoints_returns_dict_with_list(self):
        """_execute_tool('get_phg_checkpoints') returns dict with 'checkpoints' list."""
        result = self.agent._execute_tool(
            "get_phg_checkpoints", {"device_id": _DEVICE_HEX}
        )
        self.assertIsInstance(result, dict)
        self.assertIn("checkpoints", result)
        self.assertIsInstance(result["checkpoints"], list)
        self.assertIn("count", result)
        self.assertEqual(result["device_id"], _DEVICE_HEX)

    def test_2_check_eligibility_returns_dict_with_eligible_bool(self):
        """_execute_tool('check_eligibility') returns dict with 'eligible' bool."""
        result = self.agent._execute_tool(
            "check_eligibility", {"device_id": _DEVICE_HEX}
        )
        self.assertIsInstance(result, dict)
        self.assertIn("eligible", result)
        self.assertIsInstance(result["eligible"], bool)
        self.assertIn("cumulative_score", result)
        self.assertIn("has_credential", result)
        # Unknown device → not eligible
        self.assertFalse(result["eligible"])

    def test_3_get_pitl_proof_returns_error_dict_when_no_proof(self):
        """_execute_tool('get_pitl_proof') returns error dict when no proof exists."""
        result = self.agent._execute_tool(
            "get_pitl_proof", {"device_id": _DEVICE_HEX}
        )
        self.assertIsInstance(result, dict)
        # No proof in empty DB → error dict
        self.assertIn("error", result)

    def test_4_react_import_error_returns_dict_with_alert_and_severity(self):
        """react() when anthropic raises ImportError returns dict with alert and severity."""
        event = {
            "device_id": _DEVICE_HEX,
            "inference_name": "BIOMETRIC_ANOMALY",
            "pitl_l4_distance": 3.5,
            "pitl_humanity_prob": 0.42,
        }

        # Patch ask() to raise ImportError (simulates missing anthropic package)
        def _raise_import(*args, **kwargs):
            raise ImportError("No module named 'anthropic'")

        self.agent.ask = _raise_import
        result = self.agent.react(event)

        self.assertIsInstance(result, dict)
        self.assertIn("alert", result)
        self.assertIsInstance(result["alert"], str)
        self.assertIn("severity", result)
        self.assertEqual(result["severity"], "medium")
        self.assertEqual(result["inference"], "BIOMETRIC_ANOMALY")
        self.assertIn("tools_used", result)


if __name__ == "__main__":
    unittest.main()
