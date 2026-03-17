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

            return {"error": f"Unknown tool: {name}"}

        except Exception as exc:
            log.warning("BridgeAgent tool %s failed: %s", name, exc)
            return {"error": str(exc), "tool": name}

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
        # Sanitize user-controlled fields before interpolating into LLM prompt
        safe_name = "".join(
            c for c in str(inference_name) if c.isprintable() and c not in "\n\r"
        )[:64]
        try:
            l4_dist = f"{float(event.get('pitl_l4_distance', 0)):.4f}"
        except (TypeError, ValueError):
            l4_dist = "N/A"
        try:
            humanity = f"{float(event.get('pitl_humanity_prob', 0)):.4f}"
        except (TypeError, ValueError):
            humanity = "N/A"
        msg = (
            f"Detected {safe_name!r} for device {device_id[:16]}. "
            f"L4_dist={l4_dist}, "
            f"humanity_prob={humanity}. Explain and recommend."
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
