"""
BridgeAgent — Phase 30: Conversational Protocol Intelligence

An LLM-powered autonomous agent that wraps VAPI bridge data sources as tools,
enabling natural-language interaction with the protocol for tournament operators
and developers.

Uses Claude (claude-haiku-4-5-20251001) with tool_use for:
  - Player profile and eligibility queries
  - PITL distribution calibration and interpretation
  - Leaderboard and ranking analysis
  - Identity continuity chain explanation
  - Startup diagnostics and ZK artifact status
  - Recent PoAC record inspection

Sessions are maintained in-memory (dict keyed by session_id). Each session
preserves full conversation history for coherent multi-turn dialogue.

Requires: pip install anthropic
Degrades gracefully to HTTP 503 if anthropic package is not installed.
"""

import dataclasses
import io
import json
import logging
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_AGENT_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """You are the VAPI Bridge Agent — an expert on the Verified Autonomous Physical Intelligence protocol.

You help tournament operators and developers understand player data, eligibility, and system health using real bridge data.

Key VAPI concepts:
- PoAC records: 228-byte cryptographic proofs of controller activity (inference + confidence + biometrics)
- PHG score: cumulative humanity score from NOMINAL (0x20) inference records, confidence-weighted
- L4 Mahalanobis distance: biometric fingerprint distance (lower = closer to the player's own baseline)
- Humanity probability: Bayesian fusion of L4 (biometric) + L5 (rhythm) + E4 (cognitive) signals ∈ [0,1]
- CONTINUITY_THRESHOLD: max L4 distance for biometric identity continuity attestation
- Credential: on-chain PHGCredential minted when a player's PHG score meets the threshold
- Eligibility: device has a committed PHG checkpoint with cumulative score > 0

When answering:
1. Use available tools to fetch real data before drawing conclusions
2. Interpret PITL distributions contextually (high variance = diverse play patterns is normal)
3. Flag anomalies clearly: >20% low humanity_prob, high L4 drift, missing ZK artifacts
4. Be concise and actionable — operators need decisions, not lectures"""

_REACT_SYSTEM = (
    "You are the VAPI Protocol Monitor. A real-time anomaly was detected. "
    "Provide exactly 2 sentences: (1) what this PITL signal means, "
    "(2) what the operator should do. Be specific."
)

# Phase 50: Phase 46 anchor thresholds for drift detection
_PHASE46_ANOMALY_ANCHOR    = 6.726
_PHASE46_CONTINUITY_ANCHOR = 5.097

_TOOLS = [
    {
        "name": "get_player_profile",
        "description": (
            "Get a player's full profile: PHG score, record count, average humanity probability, "
            "L4/L5 biometric signals, and credential status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_leaderboard",
        "description": "Get the top players ranked by confirmed PHG humanity score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of leaderboard entries to return (default 10, max 100)",
                }
            },
        },
    },
    {
        "name": "get_leaderboard_rank",
        "description": "Get the 1-based leaderboard rank of a specific device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "run_pitl_calibration",
        "description": (
            "Analyze the L4 Mahalanobis distance and humanity probability distributions "
            "for a device (or all devices). Returns percentile stats (p25/p50/p75/p95) "
            "and a suggested CONTINUITY_THRESHOLD adjustment range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID (optional — omit to analyze all devices)",
                }
            },
        },
    },
    {
        "name": "get_continuity_chain",
        "description": (
            "Get the biometric identity continuity chain for a device — lists all "
            "cross-device attestations (source/destination role, proof hash, timestamp)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_recent_records",
        "description": (
            "Get recent PoAC records for a specific device or all devices, "
            "showing inference results, confidence, and PITL signals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of records to return (default 20, max 100)",
                },
            },
        },
    },
    {
        "name": "get_startup_diagnostics",
        "description": (
            "Get system readiness status: ZK proving key artifacts, IoTeX chain RPC URL, "
            "PHG credential contract address, and operator API key configuration."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_phg_checkpoints",
        "description": (
            "Get the PHG checkpoint chain for a device — shows score progression, "
            "bio hash, tx hash, and confirmation status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of checkpoints (default 10, max 50)",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "check_eligibility",
        "description": (
            "Check tournament eligibility: device has committed PHG score > 0 "
            "and optionally a minted soulbound credential."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_pitl_proof",
        "description": (
            "Get the latest ZK PITL session proof — nullifier hash, feature commitment, "
            "humanity probability integer, on-chain tx hash."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_behavioral_report",
        "description": (
            "Get a behavioral archaeology report for a device — drift trend, humanity trend, "
            "warmup attack score, burst farming score, biometric stability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_network_clusters",
        "description": (
            "Detect cross-device correlation clusters that may indicate coordinated bot farms. "
            "Returns clusters with suspicion scores."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_suspicion": {
                    "type": "number",
                    "description": "Minimum farm_suspicion_score to include (default 0.3)",
                }
            },
        },
    },
    {
        "name": "get_federation_status",
        "description": (
            "Get cross-bridge federation status: configured peers, local/remote cluster counts, "
            "and cross-confirmed threat hashes seen on ≥2 independent bridge instances."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_peers": {
                    "type": "integer",
                    "description": "Minimum distinct bridges for cross-confirmation (default 2)",
                }
            },
        },
    },
    {
        "name": "get_detection_policy",
        "description": (
            "Query adaptive PITL threshold multipliers derived from device risk labels. "
            "Returns per-device L4 Mahalanobis threshold multipliers set by InsightSynthesizer "
            "Mode 4 — the feedback loop that makes retrospective memory drive forward detection. "
            "Critical devices get multiplier=0.70 (30% tighter threshold); warming=0.85; "
            "cleared/stable=1.00 (baseline). Each policy has a basis_label explaining why it "
            "was set and an expires_at showing when it auto-reverts. "
            "Use this to explain: 'Why is this device failing biometric checks it passed last week?' "
            "or 'Which devices have tightened detection policies active right now?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device ID to look up (optional; if omitted returns all active policies)",
                },
                "risk_filter": {
                    "type": "string",
                    "description": "Filter policies by basis_label (optional)",
                    "enum": ["critical", "warming", "cleared", "stable", "all"],
                },
            },
        },
    },
    {
        "name": "query_digest",
        "description": (
            "Query synthesized longitudinal insight digests — the protocol's long-term memory. "
            "Returns rolling temporal summaries (24h/7d/30d) of threat pattern counts, "
            "template-generated narrative summaries, and per-device risk trajectory labels "
            "(stable/warming/critical/cleared). "
            "Use this to distinguish persistent vs. transient threats, understand device risk "
            "histories across weeks, and identify whether the same bot farms keep reappearing "
            "across synthesis windows. Digests are synthesized every 6 hours by InsightSynthesizer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {
                    "type": "string",
                    "description": "Time window to query: '24h', '7d', '30d', or 'all' (default) for all windows",
                    "enum": ["24h", "7d", "30d", "all"],
                },
                "include_device_labels": {
                    "type": "boolean",
                    "description": "If true, include per-device risk trajectory labels in the response",
                },
                "risk_filter": {
                    "type": "string",
                    "description": "Filter device labels by risk level (requires include_device_labels=true)",
                    "enum": ["critical", "warming", "cleared", "stable"],
                },
            },
        },
    },
    {
        "name": "get_credential_status",
        "description": (
            "Query a device's PHGCredential enforcement status — the complete evidence chain "
            "from biometric anomaly to trajectory label to enforcement action. "
            "Returns: has_credential, is_active (credential exists and not suspended), "
            "suspended bool, suspended_since/until timestamps, evidence_hash (references "
            "the insight digest that triggered suspension), consecutive_critical_windows count, "
            "current risk label, active detection policy, and reinstatement conditions. "
            "Use to answer: 'Why is this player blocked from the tournament bracket?' "
            "or 'When will this suspension be lifted?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID (required)",
                },
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "get_calibration_status",
        "description": (
            "Returns the current living calibration state for the PITL L4 biometric layer: "
            "global thresholds (anomaly + continuity), per-player personal profiles (if any have "
            "accumulated >=30 NOMINAL records), recent threshold evolution history (last 5 "
            "calibration_update insights), and when the next Mode 6 cycle will run. "
            "Use to answer: 'Has the L4 threshold drifted?', 'Does player X have a personal "
            "calibration profile?', or 'When was the threshold last auto-updated?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # Phase 50: 3 new tools
    {
        "name": "get_session_narrative",
        "description": (
            "Generate a 3-sentence data-derived narrative summary of a device's most recent "
            "session. No LLM call — purely deterministic data extraction. "
            "sentence_1: PITL layers fired and humanity_prob. "
            "sentence_2: Anomaly vs device history with L4 drift velocity context. "
            "sentence_3: Trend across the last 5 sessions (mean humanity_prob)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "compare_device_fingerprints",
        "description": (
            "Compare two devices' L4 biometric fingerprints via Mahalanobis distance between "
            "their EMA mean vectors from player_calibration_profiles. Uses diagonal covariance "
            "(baseline_std). Verdict: DISTINCT (dist > 6.726) / INDETERMINATE (dist > 5.097) / "
            "SIMILAR (dist <= 5.097). plain_english ALWAYS contains separation ratio 0.362 caveat "
            "because L4 is an intra-player anomaly detector only, not an identity verifier."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id_a": {
                    "type": "string",
                    "description": "First 64-character hex device ID",
                },
                "device_id_b": {
                    "type": "string",
                    "description": "Second 64-character hex device ID",
                },
            },
            "required": ["device_id_a", "device_id_b"],
        },
    },
    {
        "name": "get_calibration_agent_status",
        "description": (
            "Get peer CalibrationIntelligenceAgent status: recent unconsumed events from the "
            "peer agent, current PITL L4 thresholds vs Phase 46 anchors (6.726/5.097), "
            "count of pending recalibration flags queued for the peer, and the last "
            "threshold_history entry. Use to understand the autonomous detection-calibration "
            "feedback loop state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # Phase 51: game-aware profiling
    {
        "name": "get_game_profile",
        "description": (
            "Get the active game profile context. Returns profile ID, display name, "
            "L5 button priority order for this game (e.g. R2=sprint is primary in football "
            "instead of Cross), L6-Passive config (passive sprint-button onset tracking, "
            "no controller writes — safe during PS5 play), and per-session L6-Passive "
            "statistics: total R2 press events scored, resistance events flagged (onset > "
            "1.5x personal baseline = PS5 adaptive trigger resistance likely), and current "
            "EMA baseline onset_ms. Use to answer: 'What game profile is active?', "
            "'Why is R2 the primary L5 signal?', 'Any resistance events this session?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # Tool #22 — Phase 55
    {
        "name": "get_ioid_status",
        "description": (
            "Get the ioID device identity status for a specific device. Returns whether "
            "the device is registered in the VAPIioIDRegistry, its W3C DID (did:io:0x...), "
            "derived device address (last 20 bytes of device_id), registration timestamp, "
            "and on-chain transaction hash. Use to answer: 'What is this device's DID?', "
            "'Is this device registered in the ioID registry?', 'When was it registered?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    # Tools #24–27 — Phase 58
    {
        "name": "analyze_threshold_impact",
        "description": (
            "Compute how many sessions would flip NOMINAL→ANOMALY or ANOMALY→NOMINAL "
            "if the L4 Mahalanobis threshold shifted by a given percentage. "
            "Uses pitl_l4_distance from the records table. Never modifies thresholds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "delta_pct": {"type": "number",
                    "description": "Shift % (negative = tighten, positive = loosen)"},
                "threshold_type": {"type": "string",
                    "description": "'anomaly' or 'continuity' (default: anomaly)"},
            },
            "required": ["delta_pct"],
        },
    },
    {
        "name": "predict_evasion_cost",
        "description": (
            "Given a known attack class (G, H, I, J, K), return structured analysis: "
            "PITL layers to evade, L4 detection rate from validation suite, validation N, "
            "and detection notes. Classes G/H/I are validated (N=5 sessions each)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "attack_class": {"type": "string",
                    "description": "G/H/I (validated) or J/K (macro, unvalidated)"},
            },
            "required": ["attack_class"],
        },
    },
    {
        "name": "get_anomaly_trend",
        "description": (
            "Rolling L4 anomaly and humanity statistics for a device over a time window. "
            "Returns session_count, mean/std L4 distance, mean humanity, trend direction "
            "(IMPROVING/STABLE/DEGRADING), and anomaly spike count above threshold."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "64-character hex device ID"},
                "days": {"type": "integer", "description": "Lookback window in days (default 7)"},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "generate_incident_report",
        "description": (
            "Full operator-facing audit dump for a device: record history, inference code breakdown, "
            "L4/humanity score timeline, biometric fingerprint, ioID status, tournament passport "
            "status, calibration profile, and recent protocol insights."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "64-character hex device ID"},
            },
            "required": ["device_id"],
        },
    },
    # Tool #23 — Phase 56
    {
        "name": "generate_tournament_passport",
        "description": (
            "Generate or check tournament passport eligibility for a device. "
            "Requires: device must be ioID-registered AND have >= 5 NOMINAL sessions "
            "with humanity_prob >= 0.60 (minHumanityInt >= 600). Returns passport details "
            "if issued (passport_hash, min_humanity_int, issued_at), or status: "
            "'ioid_not_registered', 'pending_sessions' (count/5 complete), "
            "or 'passport_ready' with the passport record."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                },
                "min_humanity": {
                    "type": "number",
                    "description": "Minimum humanity_prob threshold (default 0.60)",
                },
            },
            "required": ["device_id"],
        },
    },
    # Tool #28 — Phase 59
    {
        "name": "get_controller_twin_data",
        "description": (
            "Return the complete My Controller digital twin data for a device: "
            "calibration profile, 12-feature biometric fingerprint EMA means, "
            "ioID DID, tournament passport status, anomaly trend, operator audit log, "
            "and last 20 PoAC chain lock points. Powers the Phase 59 3D visualization."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "64-character hex device ID"},
            },
            "required": ["device_id"],
        },
    },
    # Tool #29 — Phase 61
    {
        "name": "get_session_replay",
        "description": (
            "Return the frame checkpoint window for a specific PoAC record — up to 60 "
            "downsampled InputSnapshot frames (20 Hz) captured around the PoAC commit. "
            "Used for forensic session replay visualization in the My Controller 3D page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id":   {"type": "string", "description": "64-hex device ID"},
                "record_hash": {"type": "string", "description": "64-hex PoAC record_hash"},
            },
            "required": ["device_id", "record_hash"],
        },
    },
    # Tool #30 — Phase 62
    {
        "name": "get_enrollment_status",
        "description": (
            "Return PHG credential enrollment progress for a device. Shows how many "
            "NOMINAL sessions have been accumulated, average humanity probability, "
            "current enrollment status (pending/eligible/minting/credentialed/failed), "
            "and how many more sessions are needed to qualify for credential minting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
    # Tool #31 — Phase 63
    {
        "name": "get_reflex_baseline",
        "description": (
            "Return L6b neuromuscular reflex baseline statistics for a device. "
            "Shows probe count, mean reflex latency (ms), std deviation, "
            "classification distribution (HUMAN/BOT/INCONCLUSIVE/NO_RESPONSE), "
            "and number of BOT-classified events. Requires L6B_ENABLED=true and "
            "at least one completed probe cycle to return meaningful data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "64-character hex device ID",
                }
            },
            "required": ["device_id"],
        },
    },
]


def _blocks_to_content(blocks) -> list[dict]:
    """Convert anthropic ContentBlock objects to plain dicts for history storage."""
    result = []
    for block in blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            result.append({"type": "text", "text": block.text})
        elif btype == "tool_use":
            result.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
    return result


class BridgeAgent:
    """LLM-powered VAPI protocol intelligence agent.

    Wraps bridge data sources as Claude tools, enabling natural-language
    operator queries over PHG scores, PITL distributions, eligibility,
    continuity chains, and system health.

    Session history is maintained in-memory; restarting the bridge
    clears all session context.
    """

    def __init__(self, cfg, store, behavioral_arch=None, network_detector=None):
        self._cfg = cfg
        self._store = store
        self._behavioral_arch = behavioral_arch
        self._network_detector = network_detector
        self._sessions: dict[str, list[dict]] = {}

    # ------------------------------------------------------------------
    # Session persistence helpers (Phase 31)
    # ------------------------------------------------------------------

    def _load_history(self, session_id: str) -> list[dict]:
        """Load from in-memory cache; fall back to store on cache miss."""
        if session_id not in self._sessions:
            self._sessions[session_id] = self._store.get_agent_session(session_id)
        return self._sessions[session_id]

    def _trim_history_if_long(self, history: list[dict], max_messages: int | None = None) -> list[dict]:
        """Keep history bounded with structured tool-inventory summary (Phase 37 enhanced).

        When history exceeds the threshold, compresses all-but-last-20 messages into a
        single summary entry that records which tools were called in the compressed portion.
        This preserves useful context (what the agent was investigating) without consuming
        the full context window.
        """
        threshold = max_messages or int(getattr(self._cfg, "agent_max_history_before_compress", 60))
        if len(history) <= threshold:
            return history
        to_trim = history[:-20]
        recent  = history[-20:]
        # Extract tool use inventory from trimmed portion
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
                f"Tools used in compressed portion: {tool_summary}. "
                f"Continue from the {len(recent)} most recent messages below.]"
            ),
        }
        return [summary_entry] + recent

    def _save_history(self, session_id: str, history: list[dict]) -> None:
        """Write to in-memory cache AND SQLite store (with history trimming, Phase 32)."""
        history = self._trim_history_if_long(history)
        self._sessions[session_id] = history
        try:
            self._store.store_agent_session(session_id, history)
        except Exception as exc:
            log.warning("BridgeAgent: failed to persist session %s: %s", session_id, exc)

    # ------------------------------------------------------------------
    # Tool execution (all deterministic — no LLM calls here)
    # ------------------------------------------------------------------

    def _execute_tool(self, name: str, inputs: dict) -> Any:
        """Execute a named tool and return a JSON-serializable result."""
        try:
            if name == "get_player_profile":
                result = self._store.get_player_profile(inputs["device_id"])
                return result or {"error": "Device not found", "device_id": inputs["device_id"]}

            if name == "get_leaderboard":
                limit = min(int(inputs.get("limit", 10)), 100)
                return self._store.get_leaderboard(limit=limit)

            if name == "get_leaderboard_rank":
                rank = self._store.get_leaderboard_rank(inputs["device_id"])
                return {
                    "device_id": inputs["device_id"],
                    "rank": rank,
                    "ranked": rank is not None,
                }

            if name == "run_pitl_calibration":
                from .pitl_calibration import calibrate  # local import avoids circular dep

                buf = io.StringIO()
                with redirect_stdout(buf):
                    calibrate(self._store, inputs.get("device_id"))
                return {"output": buf.getvalue()}

            if name == "get_continuity_chain":
                chain = self._store.get_continuity_chain(inputs["device_id"])
                return {
                    "device_id": inputs["device_id"],
                    "chain": chain,
                    "length": len(chain),
                }

            if name == "get_recent_records":
                limit = min(int(inputs.get("limit", 20)), 100)
                records = self._store.get_recent_records(
                    device_id=inputs.get("device_id"),
                    limit=limit,
                )
                return {"records": records, "count": len(records)}

            if name == "get_startup_diagnostics":
                circuits_dir = Path(__file__).parents[2] / "contracts" / "circuits"
                return {
                    "zk_artifacts": {
                        circuit: (circuits_dir / f"{circuit}_final.zkey").exists()
                        for circuit in ("TeamProof", "PitlSessionProof")
                    },
                    "chain_rpc": getattr(self._cfg, "iotex_rpc_url", "") or None,
                    "phg_credential_address": getattr(self._cfg, "phg_credential_address", "") or None,
                    "operator_key_configured": bool(getattr(self._cfg, "operator_api_key", "")),
                }

            if name == "get_phg_checkpoints":
                limit = min(int(inputs.get("limit", 10)), 50)
                cps = self._store.get_phg_checkpoints(inputs["device_id"], limit=limit)
                return {
                    "device_id": inputs["device_id"],
                    "checkpoints": cps,
                    "count": len(cps),
                }

            if name == "check_eligibility":
                cp = self._store.get_last_phg_checkpoint(inputs["device_id"])
                cred = self._store.get_credential_mint(inputs["device_id"])
                score = cp["last_committed_score"] if cp else 0
                return {
                    "device_id": inputs["device_id"],
                    "eligible": score > 0,
                    "cumulative_score": score,
                    "has_credential": cred is not None,
                    "credential_id": cred["credential_id"] if cred else None,
                }

            if name == "get_pitl_proof":
                proof = self._store.get_latest_pitl_proof(inputs["device_id"])
                return proof or {
                    "error": "No PITL proof found",
                    "device_id": inputs["device_id"],
                }

            if name == "get_behavioral_report":
                if not self._behavioral_arch:
                    return {"error": "BehavioralArchaeologist not available"}
                report = self._behavioral_arch.analyze_device(inputs["device_id"])
                return dataclasses.asdict(report)

            if name == "get_network_clusters":
                if not self._network_detector:
                    return {"error": "NetworkCorrelationDetector not available"}
                min_s = float(inputs.get("min_suspicion", 0.3))
                clusters = self._network_detector.detect_clusters()
                return {
                    "clusters": [dataclasses.asdict(c) for c in clusters
                                 if c.farm_suspicion_score >= min_s],
                    "flagged_count": sum(1 for c in clusters if c.is_flagged),
                    "total_clusters": len(clusters),
                }

            if name == "get_federation_status":
                peers_raw = getattr(self._cfg, "federation_peers", "")
                peers = [p.strip() for p in peers_raw.split(",") if p.strip()] if peers_raw else []
                min_peers = int(inputs.get("min_peers", 2))
                try:
                    cross_confirmed = self._store.get_cross_confirmed_hashes(min_peers=min_peers)
                except Exception:
                    cross_confirmed = []
                try:
                    all_fed = self._store.get_federation_clusters(limit=20)
                except Exception:
                    all_fed = []
                local_fed = [c for c in all_fed if c.get("is_local")]
                remote_fed = [c for c in all_fed if not c.get("is_local")]
                return {
                    "peer_count": len(peers),
                    "peers_configured": peers,
                    "local_clusters_detected": len(local_fed),
                    "remote_clusters_received": len(remote_fed),
                    "cross_confirmed_hashes": cross_confirmed,
                    "cross_confirmed_count": len(cross_confirmed),
                    "federation_enabled": bool(peers_raw),
                }

            if name == "get_detection_policy":
                device_id = inputs.get("device_id", "").strip()
                risk_filter = inputs.get("risk_filter", "all")
                if device_id:
                    policy = self._store.get_detection_policy(device_id)
                    policies = [policy] if policy else []
                else:
                    policies = self._store.get_all_active_policies()
                if risk_filter and risk_filter != "all":
                    policies = [p for p in policies if p.get("basis_label") == risk_filter]
                return {
                    "policies": policies,
                    "total_count": len(policies),
                    "adaptive_enabled": bool(
                        getattr(self._cfg, "adaptive_thresholds_enabled", True)
                    ),
                    "critical_policy_multiplier": 0.70,
                    "warming_policy_multiplier": 0.85,
                }

            if name == "query_digest":
                window = inputs.get("window", "all")
                include_labels = bool(inputs.get("include_device_labels", False))
                risk_filter = inputs.get("risk_filter", None)

                try:
                    if window == "all":
                        digests = self._store.get_all_latest_digests()
                    elif window in ("24h", "7d", "30d"):
                        d = self._store.get_latest_digest(window)
                        digests = [d] if d else []
                    else:
                        digests = self._store.get_all_latest_digests()
                except Exception as exc:
                    digests = []
                    log.warning("query_digest: store error: %s", exc)

                result: dict = {
                    "synthesis_available": len(digests) > 0,
                    "digests": digests,
                }

                if include_labels or risk_filter:
                    try:
                        if risk_filter:
                            labels = self._store.get_devices_by_risk_label(risk_filter)
                        else:
                            labels = (
                                self._store.get_devices_by_risk_label("critical")
                                + self._store.get_devices_by_risk_label("warming")
                                + self._store.get_devices_by_risk_label("cleared")
                                + self._store.get_devices_by_risk_label("stable")
                            )
                        result["device_labels"] = labels
                        result["critical_device_count"] = len(
                            self._store.get_devices_by_risk_label("critical")
                        )
                        result["warming_device_count"] = len(
                            self._store.get_devices_by_risk_label("warming")
                        )
                    except Exception as exc:
                        result["device_labels"] = []
                        log.warning("query_digest: label fetch error: %s", exc)

                return result

            if name == "get_credential_status":
                device_id = inputs.get("device_id", "").strip()
                if not device_id:
                    return {"error": "device_id is required"}
                credential  = self._store.get_credential_mint(device_id)
                enforcement = self._store.get_credential_enforcement(device_id)
                risk_label  = self._store.get_device_risk_label(device_id)
                policy      = self._store.get_detection_policy(device_id)
                is_suspended = bool((enforcement or {}).get("suspended", False))
                return {
                    "device_id":                    device_id,
                    "has_credential":               credential is not None,
                    "is_active":                    credential is not None and not is_suspended,
                    "suspended":                    is_suspended,
                    "suspended_since":              (enforcement or {}).get("suspended_since"),
                    "suspended_until":              (enforcement or {}).get("suspended_until"),
                    "evidence_hash":                (enforcement or {}).get("evidence_hash"),
                    "consecutive_critical_windows": (enforcement or {}).get("consecutive_critical", 0),
                    "current_risk_label":           (risk_label or {}).get("risk_label", "unknown"),
                    "active_detection_policy":      policy,
                    "credential_minted_at":         (credential or {}).get("minted_at"),
                    "reinstatement_conditions": (
                        "Suspension clears when InsightSynthesizer labels this device 'cleared' "
                        "(requires >= 1 7-day window with zero critical or warming signals)."
                    ),
                    "enforcement_enabled": bool(
                        getattr(self._cfg, "phg_credential_enforcement_enabled", True)
                    ),
                }

            if name == "get_calibration_status":
                profiles = self._store.get_all_player_calibration_profiles()
                live: dict = {}
                try:
                    import json as _json
                    with open("calibration_profile_live.json") as _f:
                        live = _json.load(_f)
                except (FileNotFoundError, ValueError):
                    pass
                # Retrieve recent calibration_update insights (last 5)
                all_updates = self._store.get_insights_since(
                    time.time() - 5 * 21600  # last 5 cycles (30h)
                )
                recent_evolution = [
                    {"timestamp": r.get("created_at"), "narrative": r.get("content")}
                    for r in all_updates
                    if r.get("insight_type") == "calibration_update"
                ][:5]
                return {
                    "global_thresholds": {
                        "l4_anomaly":      getattr(self._cfg, "l4_anomaly_threshold", 7.019),
                        "l4_continuity":   getattr(self._cfg, "l4_continuity_threshold", 5.369),
                        "last_calibration": live.get("generated_at", "never"),
                        "source_records":   live.get("total_records", 0),
                        "confidence":       live.get("confidence", "unknown"),
                    },
                    "player_profiles": profiles,
                    "recent_evolution": recent_evolution,
                    "next_cycle_in": "up to 6h (aligned with InsightSynthesizer cycle)",
                }

            # Phase 50: 3 new tools
            if name == "get_session_narrative":
                device_id = inputs.get("device_id", "")
                if not device_id:
                    return {"error": "device_id is required"}
                recent = self._store.get_recent_records(device_id=device_id, limit=5)
                profile = self._store.get_player_profile(device_id)
                if not recent:
                    return {"error": "No records found for device", "device_id": device_id}
                last = recent[0]
                inference_name = last.get("action_name", "UNKNOWN")
                humanity_prob  = last.get("pitl_humanity_prob")
                l4_dist  = last.get("pitl_l4_distance")
                l5_cv    = last.get("pitl_l5_cv")
                l4_drift = last.get("pitl_l4_drift_velocity")
                # sentence_1
                layers = []
                if l4_dist is not None:
                    layers.append(f"L4={l4_dist:.3f}")
                if l5_cv is not None:
                    layers.append(f"L5_cv={l5_cv:.3f}")
                sent1 = (
                    f"Session inference: {inference_name}; "
                    f"PITL signals: {', '.join(layers) or 'none'}; "
                    f"humanity_prob={humanity_prob}."
                )
                # sentence_2
                total = (profile or {}).get("total_records", 0)
                drift_ctx = (
                    f"L4 drift_velocity={l4_drift:.3f}" if l4_drift is not None
                    else "drift velocity unavailable"
                )
                sent2 = f"Device has {total} total records; {drift_ctx}."
                # sentence_3
                if len(recent) >= 2:
                    avg_h = sum(r.get("pitl_humanity_prob") or 0.5 for r in recent) / len(recent)
                    sent3 = f"Trend across last {len(recent)} sessions: mean humanity_prob={avg_h:.3f}."
                else:
                    sent3 = "Insufficient session history for trend analysis."
                return {
                    "device_id": device_id,
                    "sentence_1": sent1,
                    "sentence_2": sent2,
                    "sentence_3": sent3,
                }

            if name == "compare_device_fingerprints":
                device_a = inputs.get("device_id_a", "")
                device_b = inputs.get("device_id_b", "")
                if not device_a or not device_b:
                    return {"error": "device_id_a and device_id_b are required"}
                profiles = self._store.get_all_player_calibration_profiles()
                profile_a = next((p for p in profiles if p.get("device_id") == device_a), None)
                profile_b = next((p for p in profiles if p.get("device_id") == device_b), None)
                if profile_a is None or profile_b is None:
                    missing = []
                    if profile_a is None:
                        missing.append(device_a[:16])
                    if profile_b is None:
                        missing.append(device_b[:16])
                    return {
                        "error": f"Missing calibration profiles for: {', '.join(missing)} "
                                 f"(need >=30 NOMINAL records each)",
                        "plain_english": (
                            "Cannot compare — calibration data unavailable. "
                            "separation ratio 0.362 (L4 is intra-player anomaly detector only)."
                        ),
                    }
                mean_a = float(profile_a.get("baseline_mean") or 0.0)
                mean_b = float(profile_b.get("baseline_mean") or 0.0)
                std_a  = float(profile_a.get("baseline_std") or 1.0) or 1.0
                dist   = abs(mean_a - mean_b) / std_a
                if dist > _PHASE46_ANOMALY_ANCHOR:
                    verdict = "DISTINCT"
                elif dist > _PHASE46_CONTINUITY_ANCHOR:
                    verdict = "INDETERMINATE"
                else:
                    verdict = "SIMILAR"
                plain_english = (
                    f"Devices are {verdict} (Mahalanobis distance={dist:.3f}). "
                    f"separation ratio 0.362 — L4 is intra-player anomaly detector only; "
                    f"SIMILAR does not confirm same identity."
                )
                return {
                    "device_id_a": device_a,
                    "device_id_b": device_b,
                    "mahalanobis_distance": round(dist, 3),
                    "verdict": verdict,
                    "thresholds": {
                        "distinct_above": _PHASE46_ANOMALY_ANCHOR,
                        "indeterminate_above": _PHASE46_CONTINUITY_ANCHOR,
                    },
                    "plain_english": plain_english,
                }

            if name == "get_calibration_agent_status":
                try:
                    events = self._store.read_unconsumed_events("bridge_agent", limit=5)
                except Exception:
                    events = []
                try:
                    pending = self._store.read_unconsumed_events(
                        "calibration_intelligence_agent", limit=100
                    )
                    pending_count = len(pending)
                except Exception:
                    pending_count = 0
                try:
                    th_history = self._store.get_threshold_history(limit=1)
                    last_history = th_history[0] if th_history else None
                except Exception:
                    last_history = None
                return {
                    "current_thresholds": {
                        "l4_anomaly":    getattr(self._cfg, "l4_anomaly_threshold", 6.726),
                        "l4_continuity": getattr(self._cfg, "l4_continuity_threshold", 5.097),
                        "phase46_anchors": {
                            "anomaly":    _PHASE46_ANOMALY_ANCHOR,
                            "continuity": _PHASE46_CONTINUITY_ANCHOR,
                        },
                    },
                    "recent_events_from_calib_agent": events,
                    "pending_flags_count": pending_count,
                    "last_threshold_history": last_history,
                    "peer_status": (
                        "CalibrationIntelligenceAgent (event-driven peer, 30-min consumer)"
                    ),
                }

            elif name == "get_game_profile":
                _gp_id = getattr(self._cfg, "game_profile_id", "")
                if not _gp_id:
                    result = {
                        "active": False,
                        "message": "No game profile configured. Set GAME_PROFILE_ID in bridge/.env.",
                    }
                else:
                    try:
                        from vapi_bridge.game_profile import get_profile_or_none
                        _gp = get_profile_or_none(_gp_id)
                        if _gp is None:
                            result = {
                                "active": False,
                                "profile_id": _gp_id,
                                "error": f"Profile '{_gp_id}' not found in registry",
                            }
                        else:
                            result = {
                                "active":            True,
                                "profile_id":        _gp.profile_id,
                                "display_name":      _gp.display_name,
                                "platform":          _gp.platform,
                                "l5_button_priority": list(_gp.l5_button_priority),
                                "l6_passive_enabled": _gp.l6_passive_enabled,
                                "l6_passive_button":  _gp.l6_passive_button,
                                "l6_passive_flag_ratio": _gp.l6_passive_flag_ratio,
                                "button_map":        dict(_gp.button_map),
                            }
                    except Exception as _exc:
                        result = {"active": False, "error": str(_exc)}
                return result

            elif name == "get_ioid_status":
                device_id = inputs.get("device_id", "")
                if not device_id:
                    return {"error": "device_id required"}
                # Derive device address from last 20 bytes of device_id
                try:
                    dev_bytes = bytes.fromhex(device_id.ljust(64, "0"))[:32]
                    device_address = "0x" + dev_bytes[-20:].hex()
                    did = f"did:io:{device_address}"
                except Exception:
                    device_address = ""
                    did = ""
                # Check local store
                ioid_record = self._store.get_ioid_device(device_id)
                if ioid_record:
                    result = {
                        "registered": True,
                        "device_id":       device_id[:16],
                        "did":             ioid_record.get("did", did),
                        "device_address":  ioid_record.get("device_address", device_address),
                        "tx_hash":         ioid_record.get("tx_hash", ""),
                        "registered_at":   ioid_record.get("registered_at", 0),
                        "source":          "local_store",
                    }
                else:
                    result = {
                        "registered": False,
                        "device_id":      device_id[:16],
                        "derived_did":    did,
                        "device_address": device_address,
                        "message": (
                            "Device not yet registered in ioID registry. "
                            "Registration occurs automatically on first PITL proof submission."
                        ),
                    }
                return result

            elif name == "generate_tournament_passport":
                device_id  = inputs.get("device_id", "")
                min_humanity = float(inputs.get("min_humanity", 0.60))
                if not device_id:
                    return {"error": "device_id required"}
                # Check ioID registration
                ioid_record = self._store.get_ioid_device(device_id)
                if not ioid_record:
                    return {
                        "status":     "ioid_not_registered",
                        "device_id":  device_id[:16],
                        "message": (
                            "Device is not in the ioID registry. Cannot issue tournament passport. "
                            "Ensure at least one PITL proof has been submitted."
                        ),
                    }
                # Check for existing passport
                existing_passport = self._store.get_tournament_passport(device_id)
                if existing_passport and existing_passport.get("passport_hash"):
                    return {
                        "status":          "passport_ready",
                        "device_id":       device_id[:16],
                        "did":             ioid_record.get("did", ""),
                        "passport_hash":   existing_passport.get("passport_hash", ""),
                        "min_humanity_int": existing_passport.get("min_humanity_int", 0),
                        "issued_at":       existing_passport.get("issued_at", 0),
                        "on_chain":        bool(existing_passport.get("on_chain", 0)),
                    }
                # Check eligible sessions
                eligible = self._store.get_passport_eligible_sessions(
                    device_id, min_humanity, limit=10
                )
                n_eligible = len(eligible)
                if n_eligible < 5:
                    return {
                        "status":          "pending_sessions",
                        "device_id":       device_id[:16],
                        "did":             ioid_record.get("did", ""),
                        "eligible_sessions": n_eligible,
                        "required":        5,
                        "min_humanity":    min_humanity,
                        "message": (
                            f"Only {n_eligible}/5 eligible sessions (humanity >= {min_humanity:.0%}). "
                            "Continue playing to accumulate NOMINAL sessions."
                        ),
                    }
                # Eligible: return summary
                min_hp = min(s.get("pitl_humanity_prob", 0.0) or 0.0 for s in eligible[:5])
                return {
                    "status":            "eligible",
                    "device_id":         device_id[:16],
                    "did":               ioid_record.get("did", ""),
                    "eligible_sessions": n_eligible,
                    "min_humanity_prob": round(min_hp, 4),
                    "min_humanity_int":  int(min_hp * 1000),
                    "message": (
                        f"{n_eligible} eligible sessions found. "
                        "Passport can be issued on next PITL proof submission."
                    ),
                }

            # Phase 58: Tools #24–27
            if name == "analyze_threshold_impact":
                threshold_type = inputs.get("threshold_type", "anomaly")
                delta_pct = float(inputs.get("delta_pct", 0.0))
                current = (self._cfg.l4_anomaly_threshold if threshold_type == "anomaly"
                           else self._cfg.l4_continuity_threshold)
                proposed = current * (1 + delta_pct / 100.0)
                with self._store._conn() as conn:
                    rows = conn.execute(
                        "SELECT pitl_l4_distance, inference FROM records WHERE pitl_l4_distance IS NOT NULL"
                    ).fetchall()
                if not rows:
                    return {"threshold_type": threshold_type, "current_threshold": current,
                            "proposed_threshold": proposed, "total_sessions": 0,
                            "nominal_to_anomaly": 0, "anomaly_to_nominal": 0, "flip_pct": 0.0}
                nominal_to_anomaly = sum(1 for r in rows
                                         if r["pitl_l4_distance"] < current and r["pitl_l4_distance"] >= proposed)
                anomaly_to_nominal = sum(1 for r in rows
                                         if r["pitl_l4_distance"] >= current and r["pitl_l4_distance"] < proposed)
                return {
                    "threshold_type": threshold_type,
                    "current_threshold": current,
                    "proposed_threshold": round(proposed, 4),
                    "delta_pct": delta_pct,
                    "total_sessions": len(rows),
                    "nominal_to_anomaly": nominal_to_anomaly,
                    "anomaly_to_nominal": anomaly_to_nominal,
                    "flip_pct": round(100.0 * (nominal_to_anomaly + anomaly_to_nominal) / len(rows), 2),
                }

            if name == "predict_evasion_cost":
                _ATTACK_DB = {
                    "G": {"layers_to_evade": ["L4", "L2B"], "l4_detection": "0% (batch), live via grip_variance",
                          "validation_n": 5,
                          "detection_notes": "zeroed accel evades L4 batch; L2B catches IMU-button decoupling"},
                    "H": {"layers_to_evade": ["L4"], "l4_detection": "100%", "validation_n": 5,
                          "detection_notes": "threshold-aware replay still anomalous vs personal Mahalanobis mean"},
                    "I": {"layers_to_evade": ["L4", "L2B", "L5"], "l4_detection": "0% (batch), live L4+L2B",
                          "validation_n": 5,
                          "detection_notes": "spectral mimicry passes L4 batch; live L2B triggers on decoupled IMU"},
                    "J": {"layers_to_evade": ["L4"], "l4_detection": "predicted 100% (jitter_var < 0.00005 s²)",
                          "validation_n": 0,
                          "detection_notes": "AntiMicro constant-interval presses; UNVALIDATED — macro sessions not yet captured"},
                    "K": {"layers_to_evade": ["L4", "L5"], "l4_detection": "unknown", "validation_n": 0,
                          "detection_notes": "reWASD Gaussian-jittered IBIs; UNVALIDATED"},
                }
                cls = inputs.get("attack_class", "").upper()
                rec = _ATTACK_DB.get(cls)
                if not rec:
                    return {"error": f"Unknown attack class '{cls}'. Validated: G, H, I. Hypothesized: J, K."}
                return {
                    "attack_class": cls,
                    **rec,
                    "separation_gap_note": (
                        "Inter-person separation ratio=0.362. Biometric transplant attack "
                        "(P1 uses P2 device) has 0% detection at all layers. Tournament blocker."
                    ),
                }

            if name == "get_anomaly_trend":
                device_id = inputs.get("device_id", "")
                days = int(inputs.get("days", 7))
                cutoff = time.time() - days * 86400
                with self._store._conn() as conn:
                    rows = conn.execute(
                        "SELECT pitl_l4_distance, pitl_humanity_prob, inference, created_at "
                        "FROM records WHERE device_id = ? AND created_at >= ? "
                        "AND pitl_l4_distance IS NOT NULL ORDER BY created_at ASC",
                        (device_id, cutoff),
                    ).fetchall()
                if not rows:
                    return {"device_id": device_id[:16], "session_count": 0, "days": days,
                            "message": "No warmed L4 sessions in window"}
                dists = [r["pitl_l4_distance"] for r in rows]
                hums  = [r["pitl_humanity_prob"] for r in rows if r["pitl_humanity_prob"] is not None]
                thr   = self._cfg.l4_anomaly_threshold
                mean_d = sum(dists) / len(dists)
                mid    = max(1, len(dists) // 2)
                first_h = sum(dists[:mid]) / mid
                second_h = sum(dists[mid:]) / max(len(dists) - mid, 1)
                trend = ("DEGRADING" if second_h > first_h * 1.1
                         else "IMPROVING" if second_h < first_h * 0.9 else "STABLE")
                return {
                    "device_id": device_id[:16], "days": days, "session_count": len(rows),
                    "mean_l4_distance": round(mean_d, 4),
                    "std_l4_distance": round((sum((d-mean_d)**2 for d in dists)/len(dists))**0.5, 4),
                    "mean_humanity": round(sum(hums)/len(hums), 4) if hums else None,
                    "anomaly_threshold": thr,
                    "spike_count": sum(1 for d in dists if d >= thr),
                    "spike_pct": round(100.0 * sum(1 for d in dists if d >= thr) / len(rows), 1),
                    "trend": trend,
                }

            if name == "generate_incident_report":
                device_id = inputs.get("device_id", "")
                dev     = self._store.get_device(device_id) or {}
                profile = self._store.get_player_profile(device_id) or {}
                with self._store._conn() as conn:
                    breakdown = conn.execute(
                        "SELECT inference, COUNT(*) as cnt FROM records WHERE device_id=? GROUP BY inference",
                        (device_id,),
                    ).fetchall()
                    recent = conn.execute(
                        "SELECT pitl_l4_distance, pitl_humanity_prob, inference, created_at "
                        "FROM records WHERE device_id=? ORDER BY created_at DESC LIMIT 10",
                        (device_id,),
                    ).fetchall()
                ioid     = self._store.get_ioid_device(device_id) or {}
                passport = self._store.get_tournament_passport(device_id) or {}
                calib    = self._store.get_player_calibration_profile(device_id) or {}
                insights = self._store.get_recent_insights(limit=5) if hasattr(self._store, "get_recent_insights") else []
                return {
                    "device_id": device_id[:16],
                    "first_seen": dev.get("first_seen"),
                    "last_seen": dev.get("last_seen"),
                    "records_total": dev.get("records_total", 0),
                    "records_verified": dev.get("records_verified", 0),
                    "inference_breakdown": {str(r["inference"]): r["cnt"] for r in breakdown},
                    "humanity_prob": profile.get("humanity_prob"),
                    "phg_score": profile.get("phg_score"),
                    "recent_sessions": [dict(r) for r in recent],
                    "ioid": {
                        "registered": bool(ioid),
                        "did": ioid.get("did"),
                        "tx_hash": ioid.get("tx_hash"),
                    },
                    "tournament_passport": {
                        "issued": bool(passport),
                        "passport_hash": passport.get("passport_hash"),
                        "on_chain": bool(passport.get("on_chain")),
                        "issued_at": passport.get("issued_at"),
                    },
                    "calibration": {
                        "has_profile": bool(calib),
                        "anomaly_threshold": calib.get("anomaly_threshold"),
                        "continuity_threshold": calib.get("continuity_threshold"),
                        "record_count": calib.get("session_count"),
                    },
                    "recent_insights": insights,
                }

            if name == "get_controller_twin_data":
                device_id = inputs.get("device_id", "")
                if not device_id:
                    return {"error": "device_id required"}
                return self._store.get_controller_twin_snapshot(device_id)

            if name == "get_session_replay":
                device_id   = inputs.get("device_id", "")
                record_hash = inputs.get("record_hash", "")
                if not device_id or not record_hash:
                    return {"error": "device_id and record_hash required"}
                result = self._store.get_frame_checkpoint(device_id, record_hash)
                return result if result is not None else {"frames": [], "frame_count": 0}

            if name == "get_enrollment_status":
                device_id = inputs.get("device_id", "")
                if not device_id:
                    return {"error": "device_id required"}
                row = self._store.get_enrollment(device_id)
                min_sessions = getattr(self._cfg, "enrollment_min_sessions", 10)
                if not row:
                    nominal, avg_h = self._store.count_nominal_sessions(device_id)
                    return {
                        "device_id":       device_id,
                        "status":          "pending",
                        "sessions_nominal": nominal,
                        "avg_humanity":    round(avg_h, 3),
                        "sessions_needed": max(0, min_sessions - nominal),
                    }
                needed = max(0, min_sessions - row["sessions_nominal"])
                return {**row, "sessions_needed": needed}

            if name == "get_reflex_baseline":
                device_id = inputs.get("device_id", "")
                if not device_id:
                    return {"error": "device_id required"}
                result = self._store.get_l6b_baseline(device_id)
                if result and result.get("probe_count", 0) == 0:
                    result["l6b_enabled"] = getattr(self._cfg, "l6b_enabled", False)
                    result["status"] = "no_probes_recorded"
                return result

            return {"error": f"Unknown tool: {name}"}

        except Exception as exc:
            log.warning("BridgeAgent tool %s failed: %s", name, exc)
            return {"error": str(exc), "tool": name}

    # ------------------------------------------------------------------
    # Phase 50: Proactive drift detection (called by InsightSynthesizer Mode 6 callback)
    # ------------------------------------------------------------------

    def check_threshold_drift(self, new_anomaly: float, new_continuity: float) -> None:
        """Called synchronously by InsightSynthesizer Mode 6 post-hook (Phase 50).

        Compares new thresholds against Phase 46 anchors (6.726/5.097).
        Always writes a threshold_history entry.
        Writes threshold_drift_alert insight + agent_events when drift > 10%.
        Writes threshold_stable insight when drift <= 10%.
        """
        drift_a = abs(new_anomaly - _PHASE46_ANOMALY_ANCHOR) / _PHASE46_ANOMALY_ANCHOR * 100
        drift_c = abs(new_continuity - _PHASE46_CONTINUITY_ANCHOR) / _PHASE46_CONTINUITY_ANCHOR * 100

        try:
            self._store.write_threshold_history(
                threshold_type="global_mode6",
                old_value=_PHASE46_ANOMALY_ANCHOR,
                new_value=new_anomaly,
                drift_pct=round(drift_a, 2),
                sessions_used=0,
                phase="mode6_living_calibration",
            )
        except Exception as exc:
            log.debug("check_threshold_drift: write_threshold_history failed: %s", exc)

        if drift_a > 10.0 or drift_c > 10.0:
            content = (
                f"Phase 50 threshold drift alert: "
                f"anomaly {_PHASE46_ANOMALY_ANCHOR:.3f}→{new_anomaly:.3f} ({drift_a:.1f}% drift), "
                f"continuity {_PHASE46_CONTINUITY_ANCHOR:.3f}→{new_continuity:.3f} "
                f"({drift_c:.1f}% drift). Exceeds 10% from Phase 46 anchors."
            )
            try:
                self._store.store_protocol_insight(
                    insight_type="threshold_drift_alert",
                    content=content,
                    device_id="__global__",
                    severity="medium",
                )
            except Exception as exc:
                log.debug("check_threshold_drift: store_protocol_insight failed: %s", exc)
            try:
                self._store.write_agent_event(
                    event_type="threshold_updated",
                    payload=json.dumps({
                        "new_anomaly":         new_anomaly,
                        "new_continuity":      new_continuity,
                        "drift_anomaly_pct":   round(drift_a, 2),
                        "drift_continuity_pct": round(drift_c, 2),
                    }),
                    source="bridge_agent",
                    target="calibration_intelligence_agent",
                )
            except Exception as exc:
                log.debug("check_threshold_drift: write_agent_event failed: %s", exc)
        else:
            content = (
                f"Phase 50 threshold stable: anomaly={new_anomaly:.3f} ({drift_a:.1f}% from anchor), "
                f"continuity={new_continuity:.3f} ({drift_c:.1f}% from anchor). Within 10% bounds."
            )
            try:
                self._store.store_protocol_insight(
                    insight_type="threshold_stable",
                    content=content,
                    device_id="__global__",
                    severity="low",
                )
            except Exception as exc:
                log.debug("check_threshold_drift: store_protocol_insight(stable) failed: %s", exc)

    # ------------------------------------------------------------------
    # Agentic reasoning loop
    # ------------------------------------------------------------------

    def ask(self, session_id: str, message: str) -> dict:
        """Process a natural-language operator query and return a response.

        Args:
            session_id: Conversation session identifier (caller-managed).
                        Re-use the same ID to maintain multi-turn context.
            message:    User's natural-language question or command.

        Returns:
            {"session_id": str, "response": str, "tools_used": list[str]}

        Raises:
            ImportError: if the anthropic package is not installed.
        """
        import anthropic  # Lazy — raises ImportError if package absent

        client = anthropic.Anthropic()
        history = self._load_history(session_id)
        history.append({"role": "user", "content": message})

        tools_used: list[str] = []

        for _ in range(5):  # cap at 5 tool-use rounds
            response = client.messages.create(
                model=_AGENT_MODEL,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                tools=_TOOLS,
                messages=history,
            )

            if response.stop_reason == "end_turn":
                text = "".join(
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                )
                history.append(
                    {"role": "assistant", "content": _blocks_to_content(response.content)}
                )
                self._save_history(session_id, history)
                return {
                    "session_id": session_id,
                    "response": text,
                    "tools_used": tools_used,
                }

            if response.stop_reason == "tool_use":
                history.append(
                    {"role": "assistant", "content": _blocks_to_content(response.content)}
                )
                tool_results = []
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use":
                        tools_used.append(block.name)
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result, default=str),
                            }
                        )
                history.append({"role": "user", "content": tool_results})
                continue

            break  # unexpected stop_reason

        self._save_history(session_id, history)
        return {
            "session_id": session_id,
            "response": "Agent loop ended without a final response.",
            "tools_used": tools_used,
        }

    def react(self, event: dict) -> dict:
        """Autonomously interpret a BIOMETRIC_ANOMALY or TEMPORAL_ANOMALY event (Phase 31).

        Uses an internal session keyed to the device so it never pollutes
        operator chat sessions. Never raises — always returns a dict.
        """
        device_id = event.get("device_id", "")
        inference_name = event.get("inference_name", "UNKNOWN")
        severity = (
            "critical"
            if any(s in inference_name for s in ("INJECT", "AIMBOT", "WALLHACK"))
            else "medium"
            if inference_name in ("BIOMETRIC_ANOMALY", "TEMPORAL_ANOMALY")
            else "low"
        )
        session_id = f"__react_{device_id[:8]}"
        msg = (
            f"Detected {inference_name} for device {device_id[:16]}. "
            f"L4_dist={event.get('pitl_l4_distance')}, "
            f"humanity_prob={event.get('pitl_humanity_prob')}. Explain and recommend."
        )
        try:
            result = self.ask(session_id, msg)
            try:
                self._store.store_protocol_insight(
                    insight_type="anomaly_reaction",
                    device_id=device_id,
                    content=result["response"],
                    severity=severity,
                )
            except Exception as _persist_exc:
                log.warning("react() insight persist failed: %s", _persist_exc)
            # Phase 50: systematic drift → recalibration flag
            if "BIOMETRIC_ANOMALY" in inference_name and self._behavioral_arch:
                try:
                    report = self._behavioral_arch.analyze_device(device_id)
                    drift_v = getattr(report, "drift_velocity", 0.0)
                    if drift_v > 0.6:
                        self._store.write_agent_event(
                            event_type="recalibration_needed",
                            device_id=device_id,
                            payload=json.dumps({
                                "drift_velocity": drift_v,
                                "trigger": "biometric_anomaly_systematic",
                                "session_count_since_last_calibration":
                                    self._store.count_records_since_last_calibration(device_id),
                                "recommendation": "focused_personal_recalibration",
                            }),
                            source="bridge_agent",
                            target="calibration_intelligence_agent",
                        )
                except Exception as _exc:
                    log.debug("Phase 50 recalibration flag failed: %s", _exc)
            return {
                "alert": result["response"],
                "severity": severity,
                "tools_used": result["tools_used"],
                "device_id": device_id,
                "inference": inference_name,
            }
        except (ImportError, Exception) as exc:
            return {
                "alert": f"{inference_name} detected. Agent unavailable: {exc}",
                "severity": severity,
                "tools_used": [],
                "device_id": device_id,
                "inference": inference_name,
            }

    async def stream_ask(self, session_id: str, message: str):
        """Async generator yielding SSE event dicts (Phase 31 streaming).

        Yields: {"type": "text_delta"|"tool_start"|"tool_result"|"done"|"error", ...}
        Raises ImportError if anthropic not installed (caller wraps in try/except).
        """
        import anthropic  # Lazy — raises ImportError if absent

        history = list(self._load_history(session_id))
        history.append({"role": "user", "content": message})
        tools_used: list[str] = []
        client = anthropic.AsyncAnthropic()

        for _ in range(5):
            async with client.messages.stream(
                model=_AGENT_MODEL,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                tools=_TOOLS,
                messages=history,
            ) as stream:
                async for text in stream.text_stream:
                    yield {"type": "text_delta", "text": text}
                final = await stream.get_final_message()

            if final.stop_reason == "end_turn":
                history.append(
                    {"role": "assistant", "content": _blocks_to_content(final.content)}
                )
                self._save_history(session_id, history)
                yield {"type": "done", "tools_used": tools_used}
                return

            if final.stop_reason == "tool_use":
                history.append(
                    {"role": "assistant", "content": _blocks_to_content(final.content)}
                )
                tool_results = []
                for block in final.content:
                    if getattr(block, "type", None) == "tool_use":
                        tools_used.append(block.name)
                        yield {"type": "tool_start", "tool": block.name}
                        result = self._execute_tool(block.name, block.input)
                        yield {
                            "type": "tool_result",
                            "tool": block.name,
                            "preview": str(result)[:120],
                        }
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result, default=str),
                            }
                        )
                history.append({"role": "user", "content": tool_results})
                continue

            break  # unexpected stop_reason

        self._save_history(session_id, history)
        yield {"type": "done", "tools_used": tools_used}
