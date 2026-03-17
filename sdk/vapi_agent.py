"""
VAPIAgent — Phase 65: Autonomous Intelligence Layer (SDK side)

Provides:
  AgentRuling   — cryptographically committed autonomous PITL ruling dataclass
  VAPIAgent     — studio-side autonomous session adjudicator

Trust anchor: SDKAttestation.attestation_hash is included in every ruling's
commitment_hash, binding AI decisions to verified SDK integration state.

BLOCK/CERTIFY verdicts require attestation.all_layers_active == True.
FLAG/HOLD verdicts operate on any attestation (including partial).

dry_run=True (default): rulings computed locally, never submitted to bridge.
Works offline: produces evidence-only FLAG ruling when bridge unreachable.
No external dependencies beyond stdlib + vapi_sdk.

Phase 65 commitment formula:
  SHA-256(
      verdict.encode()
      + json.dumps(sorted(evidence_hashes)).encode()
      + attestation_hash_hex.encode()
      + struct.pack(">Q", timestamp_ns)
  )
"""

import hashlib
import json
import struct
import time
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vapi_sdk import VAPISession, SDKAttestation

AGENT_VERSION = "1.0.0-phase65"
_VERDICTS_REQUIRING_FULL_ATTESTATION = frozenset({"BLOCK", "CERTIFY"})


def _compute_commitment(
    verdict: str,
    evidence_hashes: list,
    attestation_hash: str,
    ts_ns: int,
) -> bytes:
    """SHA-256 commitment over verdict + sorted evidence + attestation + ts_ns."""
    blob = (
        verdict.encode()
        + json.dumps(sorted(evidence_hashes)).encode()
        + attestation_hash.encode()
        + struct.pack(">Q", ts_ns)
    )
    return hashlib.sha256(blob).digest()


@dataclass
class AgentRuling:
    """Cryptographically committed autonomous PITL ruling (Phase 65).

    Fields:
        device_id        — 64-hex VAPI device identifier
        verdict          — FLAG | HOLD | BLOCK | CERTIFY | CLEAR
        confidence       — float 0.0-1.0
        reasoning        — human-readable explanation
        evidence_hashes  — list of PoAC record_hash hex strings used as evidence
        attestation_hash — hex of SDKAttestation.attestation_hash (trust anchor)
        commitment_hash  — SHA-256(verdict+evidence+attestation+ts_ns) as bytes
        timestamp        — Unix float seconds
        dry_run          — True means ruling was computed locally, not submitted
    """
    device_id:        str
    verdict:          str
    confidence:       float
    reasoning:        str
    evidence_hashes:  list
    attestation_hash: str
    commitment_hash:  bytes
    timestamp:        float
    dry_run:          bool

    @property
    def is_blocking(self) -> bool:
        return self.verdict == "BLOCK"

    @property
    def is_advisory(self) -> bool:
        return self.verdict in ("FLAG", "HOLD")

    def to_dict(self) -> dict:
        return {
            "device_id":        self.device_id,
            "verdict":          self.verdict,
            "confidence":       self.confidence,
            "reasoning":        self.reasoning,
            "evidence_hashes":  self.evidence_hashes,
            "attestation_hash": self.attestation_hash,
            "commitment_hash":  self.commitment_hash.hex(),
            "timestamp":        self.timestamp,
            "dry_run":          self.dry_run,
            "agent_version":    AGENT_VERSION,
        }


class VAPIAgent:
    """
    Autonomous PITL session adjudicator (Phase 65).

    Trust anchor: SDKAttestation — BLOCK/CERTIFY require all_layers_active=True.
    FLAG/HOLD operate on any attestation (including partial).
    dry_run=True (default): ruling computed locally, never submitted to bridge.
    dry_run=False: ruling POSTed to bridge /agent/adjudicate (requires bridge_url).

    Works offline: produces evidence-only FLAG ruling when bridge unreachable.
    No external dependencies beyond stdlib + vapi_sdk.

    Example usage::

        agent = VAPIAgent(bridge_url="http://localhost:8765", dry_run=True)
        ruling = agent.adjudicate(session, attestation)
        if ruling.is_blocking:
            ban_player(ruling.device_id)
        enriched = agent.interpret(pitl_score_dict, context="final round")
    """

    def __init__(
        self,
        bridge_url: str = "",
        model: str = "claude-opus-4-6",
        dry_run: bool = True,
    ) -> None:
        self._bridge_url = bridge_url.rstrip("/")
        self._model = model
        self._dry_run = dry_run

    def adjudicate(
        self,
        session: "VAPISession",
        attestation: "SDKAttestation",
        timeout: float = 30.0,
    ) -> "AgentRuling":
        """
        Produce a commitment-bound ruling for a completed session.

        Evidence gathered locally from session records. When bridge_url is set
        and dry_run=False, also calls POST /agent/adjudicate on the bridge.

        BLOCK/CERTIFY upgrade: only when attestation.all_layers_active is True.
        Otherwise downgrades verdict to FLAG with explanation in reasoning.
        """
        # Build evidence from session records
        evidence_hashes = [r.record_hash.hex() for r in session._records]
        cheat_codes = [r.inference_result for r in session._records
                       if not r.is_clean]
        advisory_codes = [r.inference_result for r in session._records
                          if r.is_advisory]
        chain_ok = session.chain_integrity()

        # Determine base verdict via rule engine (no LLM required — offline safe)
        verdict, confidence, reasoning = self._rule_verdict(
            cheat_codes, advisory_codes, chain_ok, attestation
        )

        # Upgrade via bridge LLM if available and not dry_run
        if self._bridge_url and not self._dry_run:
            verdict, confidence, reasoning = self._bridge_adjudicate(
                session, attestation, verdict, confidence, reasoning, timeout
            )

        # Attestation gate: downgrade BLOCK/CERTIFY if layers incomplete
        if (verdict in _VERDICTS_REQUIRING_FULL_ATTESTATION
                and not attestation.all_layers_active):
            reasoning = (
                f"Downgraded from {verdict}: SDKAttestation.all_layers_active=False. "
                f"Active layers: {attestation.layers_active}. "
                "BLOCK/CERTIFY require all 5 PITL layers verified. " + reasoning
            )
            verdict = "FLAG"
            confidence = min(confidence, 0.5)

        ts_ns = time.time_ns()
        att_hex = attestation.attestation_hash.hex()
        return AgentRuling(
            device_id=session._profile_id,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            evidence_hashes=evidence_hashes,
            attestation_hash=att_hex,
            commitment_hash=_compute_commitment(verdict, evidence_hashes,
                                                att_hex, ts_ns),
            timestamp=ts_ns / 1_000_000_000.0,
            dry_run=self._dry_run,
        )

    def interpret(
        self,
        data: dict,
        context: str = "",
        timeout: float = 15.0,
    ) -> dict:
        """
        Agentic overlay: enrich any VAPI data dict with LLM interpretation.

        POSTs {data, context} to bridge POST /agent/interpret.
        Offline fallback: returns data with agent_interpretation={'status':'unavailable'}.
        """
        if not self._bridge_url:
            return {**data, "agent_interpretation": {"status": "unavailable"}}
        url = f"{self._bridge_url}/agent/interpret"
        payload = json.dumps({"data": data, "context": context}).encode()
        try:
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception:
            return {**data, "agent_interpretation": {"status": "unavailable"}}

    # --- Internal helpers ---

    @staticmethod
    def _rule_verdict(
        cheat_codes: list,
        advisory_codes: list,
        chain_ok: bool,
        attestation: "SDKAttestation",
    ) -> tuple:
        """Pure rule-based verdict (no LLM, always available offline)."""
        if not chain_ok:
            return ("HOLD", 0.9,
                    "PoAC chain integrity check failed - chain tampered or incomplete.")
        if cheat_codes:
            codes_str = ", ".join(f"0x{c:02X}" for c in cheat_codes)
            return "BLOCK", 0.95, f"Hard cheat code(s) detected: {codes_str}."
        if advisory_codes:
            codes_str = ", ".join(f"0x{c:02X}" for c in advisory_codes)
            return ("FLAG", 0.6,
                    f"Advisory detection(s): {codes_str}. Accumulating evidence.")
        if attestation.all_layers_active:
            return "FLAG", 0.05, "No anomalies detected. All layers active. Session clean."
        return "FLAG", 0.1, "No anomalies detected. Attestation incomplete."

    def _bridge_adjudicate(
        self,
        session: "VAPISession",
        attestation: "SDKAttestation",
        verdict: str,
        confidence: float,
        reasoning: str,
        timeout: float,
    ) -> tuple:
        """Call POST /agent/adjudicate and wait for ruling (best-effort)."""
        url = f"{self._bridge_url}/agent/adjudicate"
        payload = json.dumps({
            "device_id": session._profile_id,
            "attestation_hash": attestation.attestation_hash.hex(),
        }).encode()
        try:
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read())
            return (verdict, confidence,
                    reasoning + f" [bridge queued: event_id={result.get('event_id')}]")
        except Exception:
            return verdict, confidence, reasoning + " [bridge unreachable; rule-only verdict]"
