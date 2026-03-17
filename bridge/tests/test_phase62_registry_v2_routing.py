"""
Phase 62 — PITLSessionRegistryV2 Routing Tests (4 tests)

Verifies that submit_pitl_proof routes to the correct registry:
  test_1_submit_uses_v2_when_configured
  test_2_submit_falls_back_to_v1_when_v2_absent
  test_3_submit_skips_when_neither_configured
  test_4_config_reads_v2_address_from_env
"""

import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))


def _make_stub(pitl_registry=None, pitl_registry_v2=None):
    """Build a minimal ChainClient stub with only the attrs submit_pitl_proof needs."""
    from vapi_bridge.chain import ChainClient

    stub = object.__new__(ChainClient)
    stub._pitl_registry = pitl_registry
    stub._pitl_registry_v2 = pitl_registry_v2
    stub._send_tx = AsyncMock(return_value="0xdeadbeef")
    return stub


def _mock_registry():
    reg = MagicMock()
    reg.functions.submitPITLProof = MagicMock()
    return reg


class TestPITLRegistryV2Routing(unittest.TestCase):

    def test_1_submit_uses_v2_when_configured(self):
        """submit_pitl_proof routes to v2 when _pitl_registry_v2 is set."""
        v2 = _mock_registry()
        v1 = _mock_registry()
        stub = _make_stub(pitl_registry=v1, pitl_registry_v2=v2)

        asyncio.run(stub.submit_pitl_proof("aa" * 32, bytes(256), 12345, 800, 0x00, 99, 7))

        call_fn = stub._send_tx.call_args[0][0]
        self.assertIs(call_fn, v2.functions.submitPITLProof)

    def test_2_submit_falls_back_to_v1_when_v2_absent(self):
        """submit_pitl_proof falls back to v1 when _pitl_registry_v2 is None."""
        v1 = _mock_registry()
        stub = _make_stub(pitl_registry=v1, pitl_registry_v2=None)

        asyncio.run(stub.submit_pitl_proof("aa" * 32, bytes(256), 12345, 800, 0x00, 99, 7))

        call_fn = stub._send_tx.call_args[0][0]
        self.assertIs(call_fn, v1.functions.submitPITLProof)

    def test_3_submit_skips_when_neither_configured(self):
        """submit_pitl_proof returns '' when neither registry is configured."""
        stub = _make_stub(pitl_registry=None, pitl_registry_v2=None)

        result = asyncio.run(stub.submit_pitl_proof("aa" * 32, bytes(256), 0, 0, 0, 0, 0))

        self.assertEqual(result, "")
        stub._send_tx.assert_not_called()

    def test_4_config_reads_v2_address_from_env(self):
        """Config reads PITL_SESSION_REGISTRY_V2_ADDRESS env var into pitl_session_registry_v2_address."""
        from vapi_bridge.config import Config

        test_addr = "0x" + "62" * 20
        with patch.dict(os.environ, {"PITL_SESSION_REGISTRY_V2_ADDRESS": test_addr}):
            cfg = Config()
        self.assertEqual(cfg.pitl_session_registry_v2_address, test_addr)


if __name__ == "__main__":
    unittest.main()
