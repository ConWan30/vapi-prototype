"""
CalibrationIntelligenceAgent — Phase 50: Autonomous Calibration Peer

LLM-powered peer agent that works alongside BridgeAgent to maintain calibration
integrity. Uses claude-sonnet-4-6 with 6 specialist tools. Communicates with
BridgeAgent via the agent_events SQLite table.

Key invariant: trigger_recalibration ALWAYS enforces min() — personal thresholds
can only tighten, never loosen.

Filename: calibration_intelligence_agent.py (distinct from calibration_agent.py
which is the Phase 17 subprocess-based threshold recalibrator).
"""

import asyncio
import json
import logging
import statistics
import time
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

_CALIB_MODEL = "claude-sonnet-4-6"

_CALIB_SYSTEM_PROMPT = """You are the VAPI CalibrationIntelligenceAgent — an expert on \
maintaining the integrity of the PITL L4 biometric threshold calibration system.

Your role:
- Monitor L4 threshold drift against Phase 46 anchors (anomaly=6.726, continuity=5.097)
- Identify zero-variance features that inflate Mahalanobis distances
- Trigger personal per-device recalibration when drift signals accumulate
- Maintain separation analysis awareness (ratio=0.362 — L4 is intra-player only)
- Enforce: per-player thresholds can ONLY tighten, never loosen (min() rule)

When answering:
1. Use available tools to fetch real calibration data before drawing conclusions
2. Flag near-zero baseline_std features as suspicious (possible zero-variance contamination)
3. For trigger_recalibration: ALWAYS verify new_threshold <= current before applying
4. Reference Phase 46 anchors (6.726/5.097) when interpreting drift percentages
5. Be direct and actionable — calibration decisions affect tournament integrity"""

_CALIB_TOOLS = [
    {
        "name": "get_threshold_history",
        "description": (
            "Return recent L4 threshold change history with drift annotation vs "
            "Phase 46 anchors (anomaly=6.726, continuity=5.097). Each row shows "
            "threshold_type, old_value, new_value, drift_pct, sessions_used, phase."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 20)",
                }
            },
        },
    },
    {
        "name": "get_feature_variance_report",
        "description": (
            "Analyze per-feature variance across all player_calibration_profiles. "
            "Returns baseline_mean and baseline_std statistics across all devices, "
            "flags near-zero baseline_std devices (< 0.1) as potentially contaminated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_zero_variance_features",
        "description": (
            "Return the statically known zero-variance L4 features: "
            "trigger_resistance_change_rate (index 0) and touch_position_variance (index 10). "
            "These are structurally zero across all calibration sessions and are excluded "
            "from the active 9-feature Mahalanobis space."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_separation_analysis",
        "description": (
            "Return the Phase 49 interperson separation analysis: ratio=0.362, "
            "LOO=42.2%, indistinguishable=[P1,P2]. Use to contextualize any "
            "fingerprint comparison or cross-device query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_pending_recalibration_flags",
        "description": (
            "Return unconsumed agent_events targeting calibration_intelligence_agent. "
            "These are recalibration_needed events written by BridgeAgent when "
            "drift_velocity > 0.6 is detected on a BIOMETRIC_ANOMALY inference."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max events to return (default 50)",
                }
            },
        },
    },
    {
        "name": "trigger_recalibration",
        "description": (
            "Trigger personal or global L4 threshold recalibration. "
            "CRITICAL SAFETY RULE: personal recalibration ALWAYS enforces min() — "
            "if new_threshold > current_threshold, returns error 'refused: new threshold "
            "would loosen'. Global recalibration is blocked if last run < 7 days ago."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "'personal' (per-device) or 'global' (all devices)",
                    "enum": ["personal", "global"],
                },
                "device_id": {
                    "type": "string",
                    "description": "Device ID (required for mode=personal)",
                },
            },
            "required": ["mode"],
        },
    },
]

# Phase 46 anchors (never change — they are the calibrated ground truth)
_ANCHOR_ANOMALY    = 6.726
_ANCHOR_CONTINUITY = 5.097
_SEVEN_DAYS_S      = 7 * 24 * 3600
_MIN_PERSONAL_RECORDS = 30


def _blocks_to_content(blocks) -> list[dict]:
    """Convert anthropic ContentBlock objects to plain dicts."""
    result = []
    for block in blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            result.append({"type": "text", "text": block.text})
        elif btype == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return result


class CalibrationIntelligenceAgent:
    """LLM-powered VAPI calibration peer agent (Phase 50).

    Runs alongside BridgeAgent. Communicates via agent_events table.
    Enforces min() on all personal threshold updates.
    """

    def __init__(self, cfg, store):
        self._cfg   = cfg
        self._store = store
        self._sessions: dict[str, list[dict]] = {}

    # ------------------------------------------------------------------
    # Session persistence (mirrors BridgeAgent pattern)
    # ------------------------------------------------------------------

    def _load_history(self, session_id: str) -> list[dict]:
        if session_id not in self._sessions:
            self._sessions[session_id] = self._store.load_calib_agent_session(session_id)
        return self._sessions[session_id]

    def _save_history(self, session_id: str, history: list[dict]) -> None:
        history = self._trim_history_if_long(history)
        self._sessions[session_id] = history
        try:
            self._store.store_calib_agent_session(session_id, history)
        except Exception as exc:
            log.warning("CalibrationIntelligenceAgent: failed to persist session %s: %s",
                        session_id, exc)

    def _trim_history_if_long(self, history: list[dict], max_messages: int = 60) -> list[dict]:
        if len(history) <= max_messages:
            return history
        to_trim = history[:-20]
        recent  = history[-20:]
        tools_called: dict[str, int] = {}
        for msg in to_trim:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        t = block.get("name", "unknown")
                        tools_called[t] = tools_called.get(t, 0) + 1
        tool_summary = (
            ", ".join(f"{k}×{v}" for k, v in sorted(tools_called.items()))
            if tools_called else "none"
        )
        summary_entry = {
            "role": "user",
            "content": (
                f"[System: {len(to_trim)} prior messages compressed. "
                f"Tools used: {tool_summary}. "
                f"Continue from the {len(recent)} most recent messages.]"
            ),
        }
        return [summary_entry] + recent

    # ------------------------------------------------------------------
    # Tool execution (deterministic — no LLM calls)
    # ------------------------------------------------------------------

    def _execute_tool(self, name: str, inputs: dict) -> Any:
        try:
            if name == "get_threshold_history":
                limit = min(int(inputs.get("limit", 20)), 100)
                rows = self._store.get_threshold_history(limit=limit)
                # Annotate each row with drift from Phase 46 anchors
                for row in rows:
                    nv = row.get("new_value")
                    if nv is not None:
                        row["drift_from_anchor_pct"] = round(
                            abs(float(nv) - _ANCHOR_ANOMALY) / _ANCHOR_ANOMALY * 100, 2
                        )
                return {"history": rows, "count": len(rows),
                        "anchors": {"anomaly": _ANCHOR_ANOMALY, "continuity": _ANCHOR_CONTINUITY}}

            if name == "get_feature_variance_report":
                profiles = self._store.get_all_player_calibration_profiles()
                if not profiles:
                    return {"error": "No calibration profiles available (need >=30 NOMINAL records per device)"}
                stds  = [float(p.get("baseline_std")  or 0.0) for p in profiles]
                means = [float(p.get("baseline_mean") or 0.0) for p in profiles]
                near_zero = [
                    p.get("device_id", "")[:16]
                    for p in profiles
                    if float(p.get("baseline_std") or 0.0) < 0.1
                ]
                return {
                    "profiles_count": len(profiles),
                    "baseline_mean": {
                        "mean": round(statistics.mean(means), 4) if means else 0,
                        "min":  round(min(means), 4) if means else 0,
                        "max":  round(max(means), 4) if means else 0,
                    },
                    "baseline_std": {
                        "mean": round(statistics.mean(stds), 4) if stds else 0,
                        "min":  round(min(stds), 4) if stds else 0,
                        "max":  round(max(stds), 4) if stds else 0,
                    },
                    "near_zero_std_devices": near_zero,
                    "warning": (
                        f"{len(near_zero)} device(s) with baseline_std < 0.1 — possible "
                        "zero-variance contamination"
                    ) if near_zero else None,
                }

            if name == "get_zero_variance_features":
                return {
                    "features": [
                        {
                            "name": "trigger_resistance_change_rate",
                            "feature_index": 0,
                            "status": "structurally_zero",
                            "fix_path": "Requires adaptive trigger hardware + gameplay recapture",
                            "exclusion": "masked from active 9-feature Mahalanobis space",
                        },
                        {
                            "name": "touch_position_variance",
                            "feature_index": 10,
                            "status": "structurally_zero",
                            "fix_path": "Requires post-Phase-17 touchpad recapture (hardware + gameplay)",
                            "exclusion": "masked from active 9-feature Mahalanobis space",
                        },
                    ],
                    "note": (
                        "These 2 features are excluded by covariance mask. "
                        "Active feature count: 9 of 11. "
                        "accel_magnitude_spectral_entropy (index 9) replaced touchpad_active_fraction."
                    ),
                }

            if name == "get_separation_analysis":
                return {
                    "interperson_separation_ratio": 0.362,
                    "loo_classification_accuracy":  0.422,
                    "chance_level":                 0.333,
                    "indistinguishable_players":     ["P1", "P2"],
                    "best_discriminator":            "tremor_peak_hz",
                    "discriminator_detail": {
                        "P3_tremor_peak_hz": 7.8,
                        "P1_P2_tremor_peak_hz_range": [0.7, 1.0],
                        "note": "P1 and P2 are statistically INDISTINGUISHABLE by L4",
                    },
                    "implication": (
                        "L4 is an intra-player anomaly detector ONLY. "
                        "SIMILAR fingerprint comparison verdict does NOT confirm same identity. "
                        "True inter-player separation requires touchpad recapture + wider tremor FFT."
                    ),
                    "source": "docs/interperson-separation-analysis-v2.md",
                }

            if name == "get_pending_recalibration_flags":
                limit = min(int(inputs.get("limit", 50)), 200)
                events = self._store.read_unconsumed_events(
                    "calibration_intelligence_agent", limit=limit
                )
                return {
                    "pending_count": len(events),
                    "events": events,
                }

            if name == "trigger_recalibration":
                mode      = inputs.get("mode", "personal")
                device_id = inputs.get("device_id", "")

                if mode == "global":
                    last_time = self._store.get_last_global_recalibration_time()
                    elapsed   = time.time() - last_time
                    if elapsed < _SEVEN_DAYS_S:
                        remaining_h = (_SEVEN_DAYS_S - elapsed) / 3600
                        return {
                            "error": (
                                f"refused: global recalibration blocked "
                                f"(last run {elapsed/3600:.1f}h ago; "
                                f"cooldown=168h, {remaining_h:.1f}h remaining)"
                            )
                        }
                    return {
                        "status": "global_recalibration_deferred",
                        "note": (
                            "Global recalibration requires InsightSynthesizer Mode 6 "
                            "which runs on a 6h cycle. Use get_threshold_history to monitor."
                        ),
                    }

                # Personal recalibration
                if not device_id:
                    return {"error": "device_id required for mode=personal"}

                # Fetch NOMINAL records for this device
                all_records = self._store.get_nominal_records_for_calibration(limit=200)
                device_records = [
                    r for r in all_records if r.get("device_id") == device_id
                ]
                if len(device_records) < _MIN_PERSONAL_RECORDS:
                    return {
                        "error": (
                            f"refused: insufficient NOMINAL records for device "
                            f"{device_id[:16]} ({len(device_records)}/{_MIN_PERSONAL_RECORDS} required)"
                        )
                    }

                # Get current personal threshold
                profiles = self._store.get_all_player_calibration_profiles()
                current_profile = next(
                    (p for p in profiles if p.get("device_id") == device_id), None
                )
                current_anomaly = float(
                    (current_profile or {}).get("anomaly_threshold")
                    or getattr(self._cfg, "l4_anomaly_threshold", _ANCHOR_ANOMALY)
                )

                # Compute new threshold from device's records
                distances  = np.array([r["pitl_l4_distance"] for r in device_records], dtype=float)
                m = float(np.mean(distances))
                s = float(np.std(distances))
                new_anomaly    = round(m + 3.0 * s, 3)
                new_continuity = round(m + 2.0 * s, 3)

                # CRITICAL: enforce min() — NEVER loosen threshold
                if new_anomaly > current_anomaly:
                    return {
                        "error": (
                            f"refused: new threshold {new_anomaly:.3f} would loosen "
                            f"(new > current {current_anomaly:.3f})"
                        )
                    }

                # Apply tighter threshold
                self._store.upsert_player_calibration_profile(
                    device_id, new_anomaly, new_continuity,
                    round(m, 3), round(s, 3), len(device_records),
                )
                self._store.write_threshold_history(
                    threshold_type=f"personal_{device_id[:8]}",
                    old_value=current_anomaly,
                    new_value=new_anomaly,
                    drift_pct=round(
                        abs(new_anomaly - current_anomaly) / current_anomaly * 100, 2
                    ),
                    sessions_used=len(device_records),
                    phase="agent_triggered",
                    device_id=device_id,
                )
                return {
                    "status": "applied",
                    "device_id": device_id,
                    "old_anomaly": current_anomaly,
                    "new_anomaly": new_anomaly,
                    "delta": round(current_anomaly - new_anomaly, 3),
                    "records_used": len(device_records),
                    "enforcement": "min() enforced — threshold only tightened",
                }

            return {"error": f"Unknown tool: {name}"}

        except Exception as exc:
            log.warning("CalibrationIntelligenceAgent tool %s failed: %s", name, exc)
            return {"error": str(exc), "tool": name}

    # ------------------------------------------------------------------
    # Agentic reasoning loop (mirrors BridgeAgent.ask)
    # ------------------------------------------------------------------

    def ask(self, session_id: str, message: str) -> dict:
        """Process a calibration query (sync, 5-round tool loop)."""
        import anthropic
        client  = anthropic.Anthropic()
        history = self._load_history(session_id)
        history.append({"role": "user", "content": message})
        tools_used: list[str] = []

        for _ in range(5):
            response = client.messages.create(
                model=_CALIB_MODEL,
                max_tokens=1024,
                system=_CALIB_SYSTEM_PROMPT,
                tools=_CALIB_TOOLS,
                messages=history,
            )
            if response.stop_reason == "end_turn":
                text = "".join(
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                )
                history.append({"role": "assistant", "content": _blocks_to_content(response.content)})
                self._save_history(session_id, history)
                return {"session_id": session_id, "response": text, "tools_used": tools_used}

            if response.stop_reason == "tool_use":
                history.append({"role": "assistant", "content": _blocks_to_content(response.content)})
                tool_results = []
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use":
                        tools_used.append(block.name)
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                history.append({"role": "user", "content": tool_results})
                continue
            break

        self._save_history(session_id, history)
        return {
            "session_id": session_id,
            "response": "Agent loop ended without a final response.",
            "tools_used": tools_used,
        }

    async def stream_ask(self, session_id: str, message: str):
        """Async generator yielding SSE event dicts (mirrors BridgeAgent.stream_ask)."""
        import anthropic
        history = list(self._load_history(session_id))
        history.append({"role": "user", "content": message})
        tools_used: list[str] = []
        client = anthropic.AsyncAnthropic()

        for _ in range(5):
            async with client.messages.stream(
                model=_CALIB_MODEL,
                max_tokens=1024,
                system=_CALIB_SYSTEM_PROMPT,
                tools=_CALIB_TOOLS,
                messages=history,
            ) as stream:
                async for text in stream.text_stream:
                    yield {"type": "text_delta", "text": text}
                final = await stream.get_final_message()

            if final.stop_reason == "end_turn":
                history.append({"role": "assistant", "content": _blocks_to_content(final.content)})
                self._save_history(session_id, history)
                yield {"type": "done", "tools_used": tools_used}
                return

            if final.stop_reason == "tool_use":
                history.append({"role": "assistant", "content": _blocks_to_content(final.content)})
                tool_results = []
                for block in final.content:
                    if getattr(block, "type", None) == "tool_use":
                        tools_used.append(block.name)
                        yield {"type": "tool_start", "tool": block.name}
                        result = self._execute_tool(block.name, block.input)
                        yield {"type": "tool_result", "tool": block.name,
                               "preview": str(result)[:120]}
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                history.append({"role": "user", "content": tool_results})
                continue
            break

        self._save_history(session_id, history)
        yield {"type": "done", "tools_used": tools_used}

    # ------------------------------------------------------------------
    # Background event consumer (30-min poll)
    # ------------------------------------------------------------------

    async def run_event_consumer(self) -> None:
        """Autonomous background task: poll agent_events every 30 min (Phase 50).

        1. Reads recalibration_needed events from BridgeAgent
        2. Attempts trigger_recalibration(personal) for each device
        3. Marks events consumed; writes threshold_updated reply to BridgeAgent
        4. Runs get_separation_analysis and warns if ratio < 0.4
        """
        log.info("CalibrationIntelligenceAgent event consumer started (30-min poll)")
        _consecutive_failures = 0
        while True:
            try:
                await asyncio.sleep(1800)  # 30 minutes
                await self._consume_pending_events()
                _consecutive_failures = 0  # reset on success
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _consecutive_failures += 1
                if _consecutive_failures >= 3:
                    log.error(
                        "CalibrationIntelligenceAgent event consumer: %d consecutive failures"
                        " — store may be corrupted or ANTHROPIC_API_KEY invalid: %s",
                        _consecutive_failures, exc,
                    )
                else:
                    log.warning("CalibrationIntelligenceAgent event consumer error: %s", exc)

    async def _consume_pending_events(self) -> None:
        """Process one batch of pending recalibration events."""
        events = self._store.read_unconsumed_events(
            "calibration_intelligence_agent", limit=50
        )
        if not events:
            return

        log.info(
            "CalibrationIntelligenceAgent: processing %d pending event(s)", len(events)
        )
        for event in events:
            event_id  = event.get("id")
            device_id = event.get("device_id", "")
            try:
                result = self._execute_tool("trigger_recalibration", {
                    "mode": "personal",
                    "device_id": device_id,
                })
                self._store.mark_event_consumed(event_id, "calibration_intelligence_agent")
                # Write reply event back to BridgeAgent
                status = result.get("status", "error")
                self._store.write_agent_event(
                    event_type="threshold_updated",
                    payload=json.dumps({
                        "device_id": device_id,
                        "result_status": status,
                        "result": result,
                        "source_event_id": event_id,
                    }),
                    source="calibration_intelligence_agent",
                    target="bridge_agent",
                    device_id=device_id,
                )
                log.info(
                    "CalibrationIntelligenceAgent: device=%s recalibration %s",
                    device_id[:16] if device_id else "?", status,
                )
            except Exception as exc:
                log.warning(
                    "CalibrationIntelligenceAgent: failed to process event %s: %s",
                    event_id, exc,
                )

        # Separation analysis health check
        try:
            sep = self._execute_tool("get_separation_analysis", {})
            ratio = sep.get("interperson_separation_ratio", 1.0)
            if ratio < 0.4:
                self._store.store_protocol_insight(
                    insight_type="separation_alert",
                    content=(
                        f"CalibrationIntelligenceAgent: interperson separation ratio {ratio:.3f} "
                        f"< 0.4 threshold. L4 may not adequately distinguish bot from human. "
                        f"Consider touchpad recapture + tremor FFT widening."
                    ),
                    device_id="__global__",
                    severity="medium",
                )
        except Exception as exc:
            log.debug("CalibrationIntelligenceAgent: separation check failed: %s", exc)
