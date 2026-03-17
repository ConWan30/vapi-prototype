"""Phase 65 — SessionAdjudicator: autonomous ruling background agent."""

import asyncio
import hashlib
import json
import logging
import struct
import time

log = logging.getLogger(__name__)

_ADJUDICATOR_MODEL = "claude-opus-4-6"
_POLL_INTERVAL_S = 300  # 5 minutes

_SYSTEM_PROMPT = """You are the VAPI SessionAdjudicator — an autonomous anti-cheat ruling agent.
You receive structured PITL session evidence and produce a JSON ruling with:
  verdict: one of FLAG | HOLD | BLOCK | CERTIFY | CLEAR
  confidence: float 0.0-1.0
  reasoning: concise explanation (1-3 sentences)

Rules:
- Hard cheats {0x28, 0x29, 0x2A} in records -> BLOCK (confidence >= 0.9)
- Advisory codes {0x2B, 0x30, 0x31, 0x32} -> FLAG (confidence 0.4-0.7)
- Enrollment status 'eligible' + no hard cheats -> CERTIFY (confidence 0.85)
- risk_label 'critical' + no hard cheats -> HOLD (confidence 0.75)
- No signals -> FLAG (confidence 0.05) "No anomalies detected"
Respond with only valid JSON. No markdown. No explanations outside the JSON."""


class SessionAdjudicator:
    """Autonomous session adjudication background agent (Phase 65).

    Polls agent_events for 'ruling_request' events every 5 minutes.
    Synthesizes rulings via claude-opus-4-6 with PITL evidence context.
    Stores rulings in agent_rulings table. Writes reply events to bridge_agent.
    Fails gracefully — all exceptions caught, logged, never crash the bridge.
    """

    def __init__(self, cfg, store) -> None:
        self._cfg = cfg
        self._store = store

    async def run_event_consumer(self) -> None:
        """Background loop: poll every 5 minutes for ruling_request events."""
        log.info("SessionAdjudicator started (Phase 65) poll=%ds", _POLL_INTERVAL_S)
        _consecutive_failures = 0
        while True:
            try:
                await asyncio.sleep(_POLL_INTERVAL_S)
                await self._consume_pending_events()
                _consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _consecutive_failures += 1
                if _consecutive_failures >= 3:
                    log.error(
                        "SessionAdjudicator: %d consecutive failures: %s",
                        _consecutive_failures, exc,
                    )
                else:
                    log.warning("SessionAdjudicator: cycle error: %s", exc)

    async def _consume_pending_events(self) -> None:
        events = self._store.read_unconsumed_events("session_adjudicator", limit=20)
        if not events:
            return
        log.info("SessionAdjudicator: processing %d ruling_request(s)", len(events))
        for event in events:
            if event.get("event_type") != "ruling_request":
                continue
            try:
                await self._process_ruling_request(event)
            except Exception as exc:
                log.warning("SessionAdjudicator: ruling failed for event %s: %s",
                            event.get("id"), exc)

    async def _process_ruling_request(self, event: dict) -> None:
        payload = json.loads(event.get("payload_json", "{}"))
        device_id = payload.get("device_id", "")
        att_hash = payload.get("attestation_hash", "")
        if not device_id:
            return

        # Gather evidence (sync store calls — acceptable in async context here)
        enrollment = self._store.get_enrollment(device_id) or {}
        trajectory = self._store.get_device_risk_label(device_id) or {}
        records = self._store.get_recent_records(limit=20, device_id=device_id)
        l6b = self._store.get_l6b_baseline(device_id)

        # Build evidence summary
        inference_codes = [r.get("inference") for r in records
                           if r.get("inference") is not None]
        hard_cheats = [c for c in inference_codes if c in (0x28, 0x29, 0x2A)]
        advisories = [c for c in inference_codes if c in (0x2B, 0x30, 0x31, 0x32)]
        evidence_hashes = [r.get("record_hash", "") for r in records]

        evidence_summary = {
            "device_id": device_id,
            "hard_cheat_codes": hard_cheats,
            "advisory_codes": advisories,
            "record_count": len(records),
            "enrollment_status": enrollment.get("status", "unknown"),
            "avg_humanity": enrollment.get("avg_humanity", 0.0),
            "risk_label": trajectory.get("risk_label", "unknown"),
            "l6b_probes": l6b.get("probe_count", 0),
        }

        # LLM ruling
        verdict, confidence, reasoning = await self._llm_ruling(evidence_summary)

        # Commitment hash
        ts_ns = time.time_ns()
        blob = (
            verdict.encode()
            + json.dumps(sorted(evidence_hashes)).encode()
            + att_hash.encode()
            + struct.pack(">Q", ts_ns)
        )
        commitment_hash = hashlib.sha256(blob).hexdigest()

        ruling_id = self._store.insert_agent_ruling(
            device_id=device_id,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            evidence_json=json.dumps(evidence_summary),
            commitment_hash=commitment_hash,
            attestation_hash=att_hash,
            dry_run=False,
            source_agent="session_adjudicator",
        )
        self._store.mark_event_consumed(event["id"], "session_adjudicator")
        self._store.write_agent_event(
            event_type="ruling_completed",
            payload=json.dumps({"device_id": device_id, "verdict": verdict,
                                "ruling_id": ruling_id}),
            source="session_adjudicator",
            target="bridge_agent",
            device_id=device_id,
        )
        log.info("SessionAdjudicator: ruling %s -> %s (%.2f) for %s",
                 ruling_id, verdict, confidence, device_id[:12])

    async def _llm_ruling(self, evidence: dict) -> tuple:
        """Call claude-opus-4-6 to produce (verdict, confidence, reasoning)."""
        try:
            import anthropic
            client = anthropic.AsyncAnthropic()
            response = await client.messages.create(
                model=_ADJUDICATOR_MODEL,
                max_tokens=256,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user",
                           "content": json.dumps(evidence, default=str)}],
            )
            text = response.content[0].text.strip()
            parsed = json.loads(text)
            return (
                parsed.get("verdict", "FLAG"),
                float(parsed.get("confidence", 0.5)),
                parsed.get("reasoning", ""),
            )
        except Exception as exc:
            log.warning("SessionAdjudicator: LLM unavailable (%s), using rule fallback", exc)
            return self._rule_fallback(evidence)

    @staticmethod
    def _rule_fallback(evidence: dict) -> tuple:
        """Pure rule-based fallback when LLM is unavailable."""
        if evidence.get("hard_cheat_codes"):
            return "BLOCK", 0.9, "Hard cheat code detected (rule fallback - LLM unavailable)."
        if evidence.get("enrollment_status") == "eligible":
            return "CERTIFY", 0.8, "Enrollment threshold met (rule fallback)."
        if evidence.get("risk_label") == "critical":
            return "HOLD", 0.7, "Critical risk trajectory (rule fallback)."
        if evidence.get("advisory_codes"):
            return "FLAG", 0.5, "Advisory detection(s) (rule fallback)."
        return "FLAG", 0.05, "No anomalies detected (rule fallback)."
