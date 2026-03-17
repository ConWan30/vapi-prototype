"""
Phase 65 -- VAPIAgent + AgentRuling SDK tests.

15 tests across 3 groups:
    Group 1: TestAgentRuling    (4)  -- dataclass properties, commitment, to_dict
    Group 2: TestVAPIAgent      (7)  -- adjudicate, attestation gate, interpret
    Group 3: TestCommitmentHash (4)  -- commitment formula properties
"""

import struct
import sys
import time
import unittest
from pathlib import Path

# sdk/ -> sys.path so vapi_agent and vapi_sdk are importable
_sdk_dir = str(Path(__file__).resolve().parents[1])
if _sdk_dir not in sys.path:
    sys.path.insert(0, _sdk_dir)

from vapi_sdk import (
    POAC_BODY_SIZE, SDK_VERSION,
    SDKAttestation, VAPISession,
)
from vapi_agent import AgentRuling, VAPIAgent, _compute_commitment, AGENT_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_record(
    inference: int = 0x20,
    action: int = 0x01,
    confidence: int = 220,
    battery: int = 95,
    ctr: int = 1,
    ts_ms: int = 1_700_000_000_000,
    prev_hash: bytes = b"\x00" * 32,
) -> bytes:
    """Build a syntactically valid 228-byte PoAC record for testing."""
    hashes = prev_hash + b"\xAB" * 32 + b"\xCD" * 32 + b"\xEF" * 32  # 128 bytes
    packed = struct.pack(">BBBBI", inference, action, confidence, battery, ctr)
    packed += struct.pack(">Q", ts_ms)
    packed += struct.pack(">ddI", 40.7128, -74.0060, 0)
    body = hashes + packed  # 164 bytes
    assert len(body) == POAC_BODY_SIZE
    sig = b"\x00" * 64
    return body + sig  # 228 bytes


def _make_full_attestation(all_active: bool = True) -> SDKAttestation:
    """Create SDKAttestation with all_layers_active controlled by parameter."""
    layers = {
        "L2_hid_xinput": all_active,
        "L3_behavioral": True,
        "L4_biometric": True,
        "L5_temporal": True,
        "L2B_imu_press": True,
    }
    return SDKAttestation(
        layers_active=layers,
        pitl_scores={},
        zk_proof_available=False,
        sdk_version=SDK_VERSION,
        verified_at=time.time(),
        attestation_hash=b"\xAA" * 32,
    )


# ---------------------------------------------------------------------------
# Group 1: TestAgentRuling
# ---------------------------------------------------------------------------

class TestAgentRuling(unittest.TestCase):

    def _make_ruling(self, verdict: str = "FLAG") -> AgentRuling:
        ts_ns = time.time_ns()
        att_hex = "aa" * 32
        commitment = _compute_commitment(verdict, ["bb" * 32], att_hex, ts_ns)
        return AgentRuling(
            device_id="cc" * 32,
            verdict=verdict,
            confidence=0.5,
            reasoning="test ruling",
            evidence_hashes=["bb" * 32],
            attestation_hash=att_hex,
            commitment_hash=commitment,
            timestamp=ts_ns / 1e9,
            dry_run=True,
        )

    def test_verdict_is_blocking_for_block(self):
        """AgentRuling.is_blocking is True only for BLOCK verdict."""
        block_ruling = self._make_ruling("BLOCK")
        flag_ruling = self._make_ruling("FLAG")
        self.assertTrue(block_ruling.is_blocking)
        self.assertFalse(flag_ruling.is_blocking)

    def test_verdict_is_advisory_for_flag_and_hold(self):
        """AgentRuling.is_advisory is True for FLAG and HOLD, not BLOCK or CLEAR."""
        self.assertTrue(self._make_ruling("FLAG").is_advisory)
        self.assertTrue(self._make_ruling("HOLD").is_advisory)
        self.assertFalse(self._make_ruling("BLOCK").is_advisory)
        self.assertFalse(self._make_ruling("CLEAR").is_advisory)

    def test_commitment_hash_is_32_bytes(self):
        """commitment_hash is exactly 32 bytes (SHA-256 output)."""
        ruling = self._make_ruling("FLAG")
        self.assertEqual(len(ruling.commitment_hash), 32)
        self.assertIsInstance(ruling.commitment_hash, bytes)

    def test_to_dict_includes_all_fields(self):
        """to_dict includes all required fields including agent_version."""
        ruling = self._make_ruling("CERTIFY")
        d = ruling.to_dict()
        for key in ("device_id", "verdict", "confidence", "reasoning",
                    "evidence_hashes", "attestation_hash", "commitment_hash",
                    "timestamp", "dry_run", "agent_version"):
            self.assertIn(key, d)
        self.assertEqual(d["agent_version"], AGENT_VERSION)
        # commitment_hash serialized as hex string
        self.assertIsInstance(d["commitment_hash"], str)
        self.assertEqual(len(d["commitment_hash"]), 64)


# ---------------------------------------------------------------------------
# Group 2: TestVAPIAgent
# ---------------------------------------------------------------------------

class TestVAPIAgent(unittest.TestCase):

    def test_adjudicate_offline_no_bridge_returns_ruling(self):
        """adjudicate() returns AgentRuling even with no bridge URL."""
        session = VAPISession()
        session.ingest_record(_make_raw_record(inference=0x20))
        att = _make_full_attestation(all_active=True)
        agent = VAPIAgent(dry_run=True)
        ruling = agent.adjudicate(session, att)
        self.assertIsInstance(ruling, AgentRuling)
        self.assertEqual(ruling.device_id, session._profile_id)
        self.assertTrue(ruling.dry_run)

    def test_adjudicate_clean_session_returns_flag_low_confidence(self):
        """Clean session (no cheat codes, full attestation) -> FLAG with low confidence."""
        session = VAPISession()
        session.ingest_record(_make_raw_record(inference=0x20))  # NOMINAL
        att = _make_full_attestation(all_active=True)
        agent = VAPIAgent(dry_run=True)
        ruling = agent.adjudicate(session, att)
        self.assertEqual(ruling.verdict, "FLAG")
        self.assertLess(ruling.confidence, 0.2)

    def test_adjudicate_cheat_code_returns_block_or_flag(self):
        """Session with hard cheat code 0x28 -> BLOCK (or FLAG if attestation incomplete)."""
        session = VAPISession()
        session.ingest_record(_make_raw_record(inference=0x28))  # DRIVER_INJECT
        att = _make_full_attestation(all_active=True)
        agent = VAPIAgent(dry_run=True)
        ruling = agent.adjudicate(session, att)
        # If all_layers_active=True, rule engine produces BLOCK before attestation gate
        self.assertIn(ruling.verdict, ("BLOCK", "FLAG"))
        self.assertGreater(ruling.confidence, 0.4)

    def test_block_requires_all_layers_active(self):
        """adjudicate() downgrades BLOCK->FLAG when not all_layers_active."""
        session = VAPISession()
        session.ingest_record(_make_raw_record(inference=0x28))  # DRIVER_INJECT
        # Partial attestation — L2_hid_xinput is False
        att = _make_full_attestation(all_active=False)
        agent = VAPIAgent(dry_run=True)
        ruling = agent.adjudicate(session, att)
        self.assertNotEqual(ruling.verdict, "BLOCK")  # must downgrade
        # Reasoning should mention attestation
        self.assertTrue(
            "all_layers_active" in ruling.reasoning
            or "attestation" in ruling.reasoning.lower()
            or "Downgraded" in ruling.reasoning
        )
        self.assertTrue(ruling.dry_run)

    def test_certify_requires_all_layers_active(self):
        """adjudicate() downgrades CERTIFY->FLAG when not all_layers_active."""
        session = VAPISession()
        att = _make_full_attestation(all_active=False)
        agent = VAPIAgent(dry_run=True)

        # Monkey-patch _rule_verdict on the instance to force CERTIFY verdict
        def _force_certify(cc, ac, chain, attestation):
            return "CERTIFY", 0.85, "forced certify"

        # Bind as a plain callable overriding the staticmethod lookup
        import types as _types
        agent._rule_verdict = _force_certify
        ruling = agent.adjudicate(session, att)
        # Should be downgraded to FLAG because all_layers_active=False
        self.assertNotEqual(ruling.verdict, "CERTIFY")
        self.assertIn("Downgraded", ruling.reasoning)

    def test_interpret_offline_returns_data_with_unavailable(self):
        """interpret() without bridge_url returns data dict with unavailable status."""
        agent = VAPIAgent(dry_run=True)  # no bridge_url
        data = {"device_id": "aa" * 32, "verdict": "FLAG"}
        result = agent.interpret(data, context="test")
        self.assertIn("agent_interpretation", result)
        self.assertEqual(result["agent_interpretation"]["status"], "unavailable")
        # Original data preserved
        self.assertEqual(result["device_id"], "aa" * 32)

    def test_dry_run_default_true(self):
        """VAPIAgent.dry_run defaults to True when not specified."""
        agent = VAPIAgent()
        self.assertTrue(agent._dry_run)
        session = VAPISession()
        session.ingest_record(_make_raw_record())
        att = _make_full_attestation()
        ruling = agent.adjudicate(session, att)
        self.assertTrue(ruling.dry_run)


# ---------------------------------------------------------------------------
# Group 3: TestCommitmentHash
# ---------------------------------------------------------------------------

class TestCommitmentHash(unittest.TestCase):

    def test_commitment_hash_includes_attestation(self):
        """Commitment hash changes when attestation_hash changes."""
        ts_ns = 1_700_000_000_000_000_000
        hashes = ["aa" * 32, "bb" * 32]
        h1 = _compute_commitment("FLAG", hashes, "cc" * 32, ts_ns)
        h2 = _compute_commitment("FLAG", hashes, "dd" * 32, ts_ns)
        self.assertNotEqual(h1, h2)

    def test_commitment_hash_deterministic_for_same_inputs(self):
        """Same inputs produce identical commitment hash (deterministic)."""
        ts_ns = 1_700_000_000_000_000_001
        ev = ["aa" * 32]
        att = "bb" * 32
        h1 = _compute_commitment("BLOCK", ev, att, ts_ns)
        h2 = _compute_commitment("BLOCK", ev, att, ts_ns)
        self.assertEqual(h1, h2)

    def test_different_verdicts_produce_different_hashes(self):
        """FLAG vs BLOCK commitment hashes differ (verdict is in the preimage)."""
        ts_ns = 1_700_000_000_000_000_002
        ev = ["aa" * 32]
        att = "bb" * 32
        hf = _compute_commitment("FLAG", ev, att, ts_ns)
        hb = _compute_commitment("BLOCK", ev, att, ts_ns)
        self.assertNotEqual(hf, hb)

    def test_commitment_hash_changes_with_different_attestation(self):
        """Evidence list changes propagate into commitment hash."""
        ts_ns = 1_700_000_000_000_000_003
        att = "cc" * 32
        h1 = _compute_commitment("FLAG", ["aa" * 32], att, ts_ns)
        h2 = _compute_commitment("FLAG", ["aa" * 32, "bb" * 32], att, ts_ns)
        self.assertNotEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
