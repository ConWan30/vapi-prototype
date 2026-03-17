"""
Phase 56 tests — ZK Tournament Passport.

Tests:
1. test_tournament_passport_circuit_public_signals  — .circom file parses; public signals check
2. test_pitl_tournament_passport_sol_interface      — .sol file contains required functions
3. test_generate_passport_tool_ioid_not_registered  — no ioid_devices row → status: ioid_not_registered
4. test_generate_passport_tool_insufficient_sessions — 3 eligible sessions → pending_sessions
5. test_tournament_passports_table_schema           — Store idempotent schema
"""

import os
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_bridge_dir = os.path.join(os.path.dirname(__file__), "..")
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

# Project root (for contract/circuit files)
_project_root = os.path.join(os.path.dirname(__file__), "..", "..")

# Stub web3 / eth_account before any bridge import
import types

for _mod_name in ["web3", "web3.exceptions", "eth_account"]:
    if _mod_name not in sys.modules:
        _m = types.ModuleType(_mod_name)
        if _mod_name == "web3":
            _m.AsyncWeb3         = MagicMock()
            _m.AsyncHTTPProvider = MagicMock()
        elif _mod_name == "web3.exceptions":
            _m.ContractLogicError  = Exception
            _m.TransactionNotFound = Exception
        elif _mod_name == "eth_account":
            _m.Account = MagicMock()
        sys.modules[_mod_name] = _m


class TestTournamentPassportCircuitPublicSignals(unittest.TestCase):
    """test_tournament_passport_circuit_public_signals"""

    def test_tournament_passport_circuit_public_signals(self):
        circom_path = os.path.join(
            _project_root, "contracts", "circuits", "TournamentPassport.circom"
        )
        self.assertTrue(
            os.path.exists(circom_path),
            f"TournamentPassport.circom not found at {circom_path}"
        )
        with open(circom_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check public signals declaration
        required_public = [
            "deviceIdHash",
            "ioidTokenId",
            "passportHash",
            "minHumanityInt",
            "epoch",
        ]
        for sig in required_public:
            self.assertIn(
                sig, content,
                f"Public signal '{sig}' not found in TournamentPassport.circom"
            )

        # Check template instantiation
        self.assertIn("TournamentPassport(5)", content)
        self.assertIn("component main", content)

        # Verify private inputs are listed
        required_private = [
            "sessionNullifiers",
            "sessionHumanities",
            "deviceSecret",
        ]
        for sig in required_private:
            self.assertIn(sig, content, f"Private input '{sig}' not found")

        # Check constraint count hint in comment
        self.assertIn("MIN_HUMANITY", content)


class TestPITLTournamentPassportSolInterface(unittest.TestCase):
    """test_pitl_tournament_passport_sol_interface"""

    def test_pitl_tournament_passport_sol_interface(self):
        sol_path = os.path.join(
            _project_root, "contracts", "contracts", "PITLTournamentPassport.sol"
        )
        self.assertTrue(
            os.path.exists(sol_path),
            f"PITLTournamentPassport.sol not found at {sol_path}"
        )
        with open(sol_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Required functions
        required_fns = [
            "function submitPassport",
            "function getPassport",
            "function hasPassport",
            "function setPassportVerifier",
        ]
        for fn in required_fns:
            self.assertIn(fn, content, f"Function '{fn}' not found in PITLTournamentPassport.sol")

        # Required interfaces
        self.assertIn("IPITLSessionRegistry", content)
        self.assertIn("IVAPIioIDRegistry",    content)
        self.assertIn("ITournamentPassportVerifier", content)

        # Required events
        self.assertIn("event PassportIssued", content)
        self.assertIn("event PassportVerifierSet", content)

        # Required errors
        self.assertIn("error DeviceNotInioID", content)
        self.assertIn("error SessionNotProven", content)
        self.assertIn("error ProofFailed", content)

        # SESSION_COUNT = 5
        self.assertIn("SESSION_COUNT = 5", content)

        # Mock mode comment
        self.assertIn("mock mode", content.lower())


class TestGeneratePassportToolIoIDNotRegistered(unittest.TestCase):
    """test_generate_passport_tool_ioid_not_registered"""

    def test_generate_passport_tool_ioid_not_registered(self):
        from vapi_bridge.bridge_agent import BridgeAgent

        store_mock = MagicMock()
        store_mock.get_ioid_device.return_value = None  # not registered

        cfg_mock = MagicMock()
        agent = BridgeAgent(cfg_mock, store_mock)

        device_id = "e" * 64
        result = agent._execute_tool(
            "generate_tournament_passport",
            {"device_id": device_id}
        )

        self.assertEqual(result.get("status"), "ioid_not_registered",
                         f"Expected status=ioid_not_registered, got {result}")
        self.assertIn("device_id", result)


class TestGeneratePassportToolInsufficientSessions(unittest.TestCase):
    """test_generate_passport_tool_insufficient_sessions — 3 sessions < 5 required"""

    def test_generate_passport_tool_insufficient_sessions(self):
        from vapi_bridge.bridge_agent import BridgeAgent

        device_id = "f" * 64
        dev_bytes  = bytes.fromhex(device_id)[:32]
        device_address = "0x" + dev_bytes[-20:].hex()
        did = f"did:io:{device_address}"

        store_mock = MagicMock()
        store_mock.get_ioid_device.return_value = {
            "device_id":      device_id,
            "device_address": device_address,
            "did":            did,
            "tx_hash":        "0xabc",
            "registered_at":  time.time(),
        }
        store_mock.get_tournament_passport.return_value = None  # no existing passport
        # Only 3 eligible sessions (< 5 required)
        store_mock.get_passport_eligible_sessions.return_value = [
            {"record_hash": "aa" * 16, "pitl_humanity_prob": 0.75, "pitl_proof_nullifier": "0x1"},
            {"record_hash": "bb" * 16, "pitl_humanity_prob": 0.80, "pitl_proof_nullifier": "0x2"},
            {"record_hash": "cc" * 16, "pitl_humanity_prob": 0.70, "pitl_proof_nullifier": "0x3"},
        ]

        cfg_mock = MagicMock()
        agent = BridgeAgent(cfg_mock, store_mock)

        result = agent._execute_tool(
            "generate_tournament_passport",
            {"device_id": device_id, "min_humanity": 0.60}
        )

        self.assertEqual(result.get("status"), "pending_sessions",
                         f"Expected status=pending_sessions, got {result}")
        self.assertEqual(result.get("eligible_sessions"), 3)
        self.assertEqual(result.get("required"), 5)
        self.assertEqual(result.get("did"), did)


class TestTournamentPassportsTableSchema(unittest.TestCase):
    """test_tournament_passports_table_schema — Store idempotent"""

    def test_tournament_passports_table_schema(self):
        from vapi_bridge.store import Store

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tp.db")
            # First init
            s1 = Store(db_path)
            # Second init — idempotent
            s2 = Store(db_path)

            # Insert and retrieve tournament passport
            s1.store_tournament_passport(
                device_id="g" * 64,
                passport_hash="0x" + "1" * 64,
                ioid_token_id=42,
                min_humanity_int=650,
                tx_hash="0xtx_passport",
                on_chain=True,
            )
            row = s2.get_tournament_passport("g" * 64)
            self.assertIsNotNone(row, "Expected tournament passport row in DB")
            self.assertEqual(row["min_humanity_int"], 650)
            self.assertEqual(row["ioid_token_id"], 42)
            self.assertTrue(bool(row["on_chain"]))

            # Verify get_passport_eligible_sessions returns empty list for unknown device
            eligible = s1.get_passport_eligible_sessions("z" * 64, 0.60, limit=10)
            self.assertEqual(eligible, [])


if __name__ == "__main__":
    unittest.main()
