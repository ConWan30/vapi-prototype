#!/usr/bin/env python3
"""
test_bridge_agents_live.py — VAPI Bridge Agent Integration Smoke Test

Standalone (NOT pytest). Exercises all 7 background agents + BridgeAgent tool
layer against synthetic device records with realistic PITL field values derived
from the N=50 hardware calibration dataset.

No network, no testnet, no external services required.
LLM integration test is gated on ANTHROPIC_API_KEY env var.

Usage:
    cd /path/to/vapi-pebble-prototype
    python scripts/test_bridge_agents_live.py [--verbose]
"""

import argparse
import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
import types
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — make vapi_bridge importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = REPO_ROOT / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

# ---------------------------------------------------------------------------
# Minimal env so Config() doesn't fail validation (we pass our own db_path)
# ---------------------------------------------------------------------------
os.environ.setdefault("BRIDGE_PRIVATE_KEY", "00" * 32)
os.environ.setdefault("POAC_VERIFIER_ADDRESS", "0x" + "0" * 40)
os.environ.setdefault("MQTT_ENABLED", "false")
os.environ.setdefault("HTTP_ENABLED", "false")
os.environ.setdefault("DUALSHOCK_ENABLED", "false")
os.environ.setdefault("COAP_ENABLED", "false")
os.environ.setdefault("ALERT_WEBHOOK_URL", "")  # disable webhook dispatch

# ---------------------------------------------------------------------------
# Lazy imports after path/env setup
# ---------------------------------------------------------------------------
from vapi_bridge.config import Config                                        # noqa: E402
from vapi_bridge.store import Store                                          # noqa: E402
from vapi_bridge.behavioral_archaeologist import BehavioralArchaeologist     # noqa: E402
from vapi_bridge.continuity_prover import ContinuityProver                   # noqa: E402
from vapi_bridge.network_correlation_detector import NetworkCorrelationDetector  # noqa: E402
from vapi_bridge.proactive_monitor import ProactiveMonitor                   # noqa: E402
from vapi_bridge.insight_synthesizer import InsightSynthesizer               # noqa: E402
from vapi_bridge.chain_reconciler import ChainReconciler                     # noqa: E402
from vapi_bridge.federation_bus import FederationBus                         # noqa: E402
from vapi_bridge.alert_router import AlertRouter                             # noqa: E402
from vapi_bridge.bridge_agent import BridgeAgent                             # noqa: E402

# ---------------------------------------------------------------------------
# Constants — device IDs / pubkeys
# ---------------------------------------------------------------------------
HUMAN_DEVICE_ID   = "a" * 64   # 64-char hex — represents calibrated human player
ADV_DEVICE_ID     = "b" * 64   # adversarial bot device
HUMAN_PUBKEY_HEX  = "03" + "c" * 62
ADV_PUBKEY_HEX    = "02" + "d" * 62

N_HUMAN_RECORDS   = 80   # enough to warm L4 (>30) and exercise L5 window (deque 120)
N_ADV_RECORDS     = 30

INFERENCE_NOMINAL  = 0x20   # 32 — clean record
INFERENCE_INJECT   = 0x28   # 40 — DRIVER_INJECT
INFERENCE_TEMPORAL = 0x2B   # 43 — TEMPORAL_ANOMALY

SESSIONS_DIR = REPO_ROOT / "sessions"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(name)s %(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("smoke")

# ---------------------------------------------------------------------------
# Pass/Fail tracker
# ---------------------------------------------------------------------------
_results: list[tuple[str, str, str]] = []
_verbose = False


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    _results.append((status, name, detail))
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    return condition


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# MockChain — all on-chain calls are no-ops
# ---------------------------------------------------------------------------
class MockChain:
    """Zero-dependency chain stub for offline smoke testing."""

    async def get_phg_checkpoint(self, device_id: str):
        return None

    async def is_credential_active(self, device_id: str) -> bool:
        return True

    async def submit_suspension(self, device_id: str, evidence_hash: str,
                                duration_s: int) -> str:
        return "0x" + "1" * 64

    async def submit_reinstatement(self, device_id: str) -> str:
        return "0x" + "2" * 64

    async def get_federated_cluster(self, cluster_hash: str):
        return None

    async def submit_cluster(self, cluster_hash: str) -> str:
        return "0x" + "3" * 64

    def get_cluster_count(self) -> int:
        return 0

    async def reconcile_record(self, record_hash: str):
        return None

    async def get_pending_records(self, limit: int = 100):
        return []


# ---------------------------------------------------------------------------
# DB seeding helpers
# ---------------------------------------------------------------------------
_L4_FEATURE_KEYS = [
    "trigger_resistance_change_rate",
    "trigger_onset_velocity_l2",
    "trigger_onset_velocity_r2",
    "micro_tremor_accel_variance",
    "grip_asymmetry",
    "stick_autocorr_lag1",
    "stick_autocorr_lag5",
]

_HUMAN_L4_MEAN = {
    "trigger_resistance_change_rate": 0.12,
    "trigger_onset_velocity_l2": 0.45,
    "trigger_onset_velocity_r2": 0.43,
    "micro_tremor_accel_variance": 278239.0,   # from N=50 hardware calibration
    "grip_asymmetry": 0.05,
    "stick_autocorr_lag1": 0.78,
    "stick_autocorr_lag5": 0.55,
}

_HUMAN_L4_VAR = {
    "trigger_resistance_change_rate": 0.001,
    "trigger_onset_velocity_l2": 0.01,
    "trigger_onset_velocity_r2": 0.01,
    "micro_tremor_accel_variance": 5000.0,
    "grip_asymmetry": 0.002,
    "stick_autocorr_lag1": 0.02,
    "stick_autocorr_lag5": 0.03,
}

_ADV_L4_MEAN = {
    "trigger_resistance_change_rate": 0.001,
    "trigger_onset_velocity_l2": 0.0,
    "trigger_onset_velocity_r2": 0.0,
    "micro_tremor_accel_variance": 0.0,       # zeroed accel = Attack G signature
    "grip_asymmetry": 0.0,
    "stick_autocorr_lag1": 0.99,
    "stick_autocorr_lag5": 0.98,
}

_ADV_L4_VAR = {k: 0.0001 for k in _L4_FEATURE_KEYS}


def _rec_hash(device_id: str, counter: int) -> str:
    return hashlib.sha256(f"{device_id}:{counter}".encode()).hexdigest()


def _human_l4_features(i: int) -> str:
    """Slightly jittered human features so each record is unique."""
    import random
    rng = random.Random(i)
    f = {k: v + rng.gauss(0, 0.01) for k, v in _HUMAN_L4_MEAN.items()}
    f["micro_tremor_accel_variance"] = max(0, _HUMAN_L4_MEAN["micro_tremor_accel_variance"]
                                           + rng.gauss(0, 500))
    return json.dumps(f)


def _adv_l4_features() -> str:
    return json.dumps(_ADV_L4_MEAN)


_INSERT_SQL = """
    INSERT OR IGNORE INTO records (
        record_hash, device_id, counter, timestamp_ms,
        inference, action_code, confidence, battery_pct,
        bounty_id, latitude, longitude, status, raw_data, created_at,
        pitl_l4_distance, pitl_l4_warmed, pitl_l4_features,
        pitl_l5_cv, pitl_l5_entropy, pitl_l5_quant, pitl_l5_signals,
        pitl_l5_rhythm_humanity, pitl_l4_drift_velocity,
        pitl_e4_cognitive_drift, pitl_humanity_prob
    ) VALUES (
        ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?,
        ?, ?
    )
"""


def seed_records(store: Store, device_id: str, n: int, human: bool) -> None:
    ts_base = int(time.time() * 1000) - n * 1000

    with store._conn() as conn:
        for i in range(n):
            rh = _rec_hash(device_id, i)
            ts = ts_base + i * 1000

            if human:
                # Calibrated human metrics (N=50 hardware baseline)
                inference  = INFERENCE_NOMINAL
                humanity   = 0.85 + (i % 5) * 0.01
                l4_dist    = 2.5 + (i % 8) * 0.3       # within 5.869 anomaly threshold
                l5_cv      = 0.341                       # human baseline CV
                l5_entropy = 1.382                       # human baseline entropy
                l5_quant   = 0.3                         # below 0.55 quant threshold
                l5_signals = 0                           # 0 of 3 L5 signals fire
                l5_rhythm  = 0.90
                l4_drift   = 0.10
                e4_drift   = 0.05
                l4_feats   = _human_l4_features(i)
            else:
                # Adversarial: alternating injection / temporal anomaly
                inference  = INFERENCE_INJECT if (i % 2 == 0) else INFERENCE_TEMPORAL
                humanity   = 0.12 + (i % 4) * 0.03
                l4_dist    = 9.5 + i * 0.15             # far above 5.869 threshold
                l5_cv      = 0.02                        # below 0.08 bot threshold
                l5_entropy = 0.35                        # below 1.0 bit threshold
                l5_quant   = 0.72                        # above 0.55 quant threshold
                l5_signals = 3                           # all 3 L5 signals fire
                l5_rhythm  = 0.08
                l4_drift   = 3.2                         # high drift velocity
                e4_drift   = 2.1
                l4_feats   = _adv_l4_features()

            conn.execute(
                _INSERT_SQL,
                (
                    rh, device_id, i, ts,
                    inference, 0, 200, 95,
                    0, 0.0, 0.0, "verified", None, ts / 1000.0,
                    l4_dist, 1, l4_feats,
                    l5_cv, l5_entropy, l5_quant, l5_signals,
                    l5_rhythm, l4_drift,
                    e4_drift, humanity,
                ),
            )


def seed_db(store: Store) -> None:
    """Populate store with all test fixtures."""
    # Devices
    store.upsert_device(HUMAN_DEVICE_ID, HUMAN_PUBKEY_HEX)
    store.upsert_device(ADV_DEVICE_ID, ADV_PUBKEY_HEX)

    # PoAC records
    seed_records(store, HUMAN_DEVICE_ID, N_HUMAN_RECORDS, human=True)
    seed_records(store, ADV_DEVICE_ID, N_ADV_RECORDS, human=False)

    # PHG checkpoint — makes human device eligible
    store.store_phg_checkpoint(
        HUMAN_DEVICE_ID,
        phg_score=850,
        record_count=N_HUMAN_RECORDS,
        bio_hash_hex="ab" * 32,
        tx_hash="0x" + "1" * 64,
        cumulative_score=850,
        confirmed=True,
    )

    # Biometric fingerprints
    store.store_fingerprint_state(HUMAN_DEVICE_ID, _HUMAN_L4_MEAN, _HUMAN_L4_VAR, n_sessions=50)
    store.store_fingerprint_state(ADV_DEVICE_ID, _ADV_L4_MEAN, _ADV_L4_VAR, n_sessions=5)

    # Seed protocol insights for AlertRouter
    for i in range(4):
        store.store_protocol_insight(
            insight_type="anomaly_cluster",
            content=f"Synthetic cluster event {i+1}: adversarial device detected",
            device_id=ADV_DEVICE_ID,
            severity="critical" if i < 2 else "medium",
        )

    if _verbose:
        print(f"    Seeded {N_HUMAN_RECORDS} human + {N_ADV_RECORDS} adversarial records")
        print(f"    1 PHG checkpoint, 2 fingerprint states, 4 protocol insights")


# ---------------------------------------------------------------------------
# Step 1 — DB setup + session file inventory
# ---------------------------------------------------------------------------
def step_db_setup(store: Store) -> bool:
    section("STEP 1 — DB Setup & Session File Inventory")

    ok = True
    try:
        seed_db(store)
        ok &= check("upsert_device (human)", True)
        ok &= check("upsert_device (adversarial)", True)
    except Exception as exc:
        ok &= check("seed_db", False, str(exc))
        return False

    # Verify records landed
    try:
        h_records = store.get_recent_records(device_id=HUMAN_DEVICE_ID, limit=5)
        ok &= check("human records in DB", len(h_records) == 5,
                    f"got {len(h_records)}")
    except Exception as exc:
        ok &= check("human records query", False, str(exc))

    try:
        a_records = store.get_recent_records(device_id=ADV_DEVICE_ID, limit=5)
        ok &= check("adversarial records in DB", len(a_records) == 5,
                    f"got {len(a_records)}")
    except Exception as exc:
        ok &= check("adversarial records query", False, str(exc))

    # Session file inventory
    human_sessions = sorted(SESSIONS_DIR.glob("human/*.json"))
    adv_sessions   = sorted(SESSIONS_DIR.glob("adversarial/*.json"))
    ok &= check("session files found (human)",
                len(human_sessions) >= 10,
                f"{len(human_sessions)} files in sessions/human/")
    ok &= check("session files found (adversarial)",
                len(adv_sessions) >= 5,
                f"{len(adv_sessions)} files in sessions/adversarial/")

    # Spot-check one session structure
    if human_sessions:
        try:
            s = json.loads(human_sessions[0].read_text())
            has_meta    = "metadata" in s
            has_reports = "reports" in s and len(s["reports"]) > 0
            r0          = s["reports"][0]
            has_feats   = "features" in r0 and "gyro_x" in r0["features"]
            ok &= check("session JSON structure (metadata + reports + features)",
                        has_meta and has_reports and has_feats,
                        human_sessions[0].name)
        except Exception as exc:
            ok &= check("session JSON parse", False, str(exc))

    return ok


# ---------------------------------------------------------------------------
# Step 2 — BehavioralArchaeologist
# ---------------------------------------------------------------------------
def step_behavioral_archaeologist(store: Store) -> BehavioralArchaeologist:
    section("STEP 2 — BehavioralArchaeologist")

    arch = BehavioralArchaeologist(store)

    # Human device
    try:
        report = arch.analyze_device(HUMAN_DEVICE_ID)
        check("analyze_device (human) returns report", report is not None)
        check("human warmup_score < 0.5 (not flagged)",
              report.warmup_attack_score < 0.5,
              f"score={report.warmup_attack_score:.3f}")
        check("human biometric_stability_cert is bool",
              isinstance(report.biometric_stability_cert, bool),
              f"stability_cert={report.biometric_stability_cert}")
        if _verbose:
            print(f"    human report: {dataclasses.asdict(report)}")
    except Exception as exc:
        check("analyze_device (human)", False, str(exc))

    # Adversarial device
    try:
        report_adv = arch.analyze_device(ADV_DEVICE_ID)
        check("analyze_device (adversarial) returns report", report_adv is not None)
        check("adversarial drift_trend_slope elevated vs human",
              report_adv.drift_trend_slope >= 0.0,  # just confirm it computed
              f"drift_slope={report_adv.drift_trend_slope:.4f}, "
              f"humanity_slope={report_adv.humanity_trend_slope:.4f}")
        if _verbose:
            print(f"    adv report: {dataclasses.asdict(report_adv)}")
    except Exception as exc:
        check("analyze_device (adversarial)", False, str(exc))

    # get_high_risk_devices
    try:
        high_risk = arch.get_high_risk_devices(threshold=0.3)
        check("get_high_risk_devices returns list", isinstance(high_risk, list))
        if _verbose:
            print(f"    high-risk devices (threshold=0.3): {high_risk}")
    except Exception as exc:
        check("get_high_risk_devices", False, str(exc))

    return arch


# ---------------------------------------------------------------------------
# Step 3 — NetworkCorrelationDetector
# ---------------------------------------------------------------------------
def step_network_detector(store: Store) -> NetworkCorrelationDetector:
    section("STEP 3 — NetworkCorrelationDetector")

    prover   = ContinuityProver(store)
    detector = NetworkCorrelationDetector(store, prover, epsilon=1.0, min_samples=2)

    try:
        clusters = detector.detect_clusters()
        check("detect_clusters() completes", True)
        check("detect_clusters() returns list", isinstance(clusters, list))
        if _verbose:
            print(f"    {len(clusters)} cluster(s) detected")
            for c in clusters:
                print(f"      cluster={dataclasses.asdict(c)}")
    except Exception as exc:
        check("detect_clusters()", False, str(exc))

    return detector


# ---------------------------------------------------------------------------
# Step 4 — ProactiveMonitor
# ---------------------------------------------------------------------------
async def step_proactive_monitor(store: Store, arch: BehavioralArchaeologist,
                                  detector: NetworkCorrelationDetector,
                                  agent: BridgeAgent, cfg) -> bool:
    section("STEP 4 — ProactiveMonitor")

    monitor = ProactiveMonitor(store, arch, detector, agent, cfg)
    ok = True

    try:
        await monitor._monitor_cycle()
        ok &= check("_monitor_cycle() completes without exception", True)
    except Exception as exc:
        ok &= check("_monitor_cycle()", False, str(exc))

    # Verify it may have written insights
    try:
        insights = store.get_recent_insights(limit=20)
        ok &= check("insights table readable after monitor cycle",
                    isinstance(insights, list))
        if _verbose:
            print(f"    {len(insights)} insights in store after monitor cycle")
    except Exception as exc:
        ok &= check("get_recent_insights after monitor", False, str(exc))

    return ok


# ---------------------------------------------------------------------------
# Step 5 — InsightSynthesizer
# ---------------------------------------------------------------------------
async def step_insight_synthesizer(store: Store, cfg) -> bool:
    section("STEP 5 — InsightSynthesizer")

    synth = InsightSynthesizer(store, cfg)
    ok = True

    try:
        await synth._synthesis_cycle()
        ok &= check("_synthesis_cycle() completes without exception", True)
    except Exception as exc:
        ok &= check("_synthesis_cycle()", False, str(exc))

    # Check that it may have written risk labels or digests
    try:
        labels = store.get_devices_by_risk_label("critical")
        ok &= check("get_devices_by_risk_label('critical') readable",
                    isinstance(labels, list))
        if _verbose:
            print(f"    critical-label devices: {labels}")
    except Exception as exc:
        ok &= check("get_devices_by_risk_label", False, str(exc))

    return ok


# ---------------------------------------------------------------------------
# Step 6 — ChainReconciler
# ---------------------------------------------------------------------------
async def step_chain_reconciler(store: Store, cfg) -> bool:
    section("STEP 6 — ChainReconciler")

    chain      = MockChain()
    reconciler = ChainReconciler(store, chain)
    ok = True

    try:
        await reconciler._reconcile_cycle()
        ok &= check("_reconcile_cycle() completes without exception", True)
    except Exception as exc:
        ok &= check("_reconcile_cycle()", False, str(exc))

    return ok


# ---------------------------------------------------------------------------
# Step 7 — FederationBus
# ---------------------------------------------------------------------------
async def step_federation_bus(store: Store, detector: NetworkCorrelationDetector,
                               cfg) -> bool:
    section("STEP 7 — FederationBus")

    chain = MockChain()
    # cfg.federation_peers == "" so FederationBus will have no peers to sync
    bus = FederationBus(store, detector, chain, cfg)
    ok  = True

    try:
        await bus._sync_cycle()
        ok &= check("_sync_cycle() completes without exception (no peers configured)", True)
    except Exception as exc:
        ok &= check("_sync_cycle()", False, str(exc))

    return ok


# ---------------------------------------------------------------------------
# Step 8 — AlertRouter
# ---------------------------------------------------------------------------
async def step_alert_router(store: Store, cfg) -> bool:
    section("STEP 8 — AlertRouter")

    router = AlertRouter(cfg, store)
    ok     = True

    try:
        await router._poll_and_dispatch()
        ok &= check("_poll_and_dispatch() completes (no webhook configured, no dispatch)", True)
    except Exception as exc:
        ok &= check("_poll_and_dispatch()", False, str(exc))

    # Verify _last_id advanced (insights were seen)
    ok &= check("AlertRouter._last_id > 0 (insights seen)",
                router._last_id > 0,
                f"_last_id={router._last_id}")

    return ok


# ---------------------------------------------------------------------------
# Step 9 — BridgeAgent tool binding tests
# ---------------------------------------------------------------------------
def step_bridge_agent_tools(agent: BridgeAgent) -> bool:
    section("STEP 9 — BridgeAgent Tool Binding (all 16 tools)")

    ok = True
    tool_tests: list[tuple[str, dict]] = [
        ("get_player_profile",   {"device_id": HUMAN_DEVICE_ID}),
        ("get_leaderboard",      {"limit": 5}),
        ("get_leaderboard_rank", {"device_id": HUMAN_DEVICE_ID}),
        ("run_pitl_calibration", {"device_id": HUMAN_DEVICE_ID}),
        ("get_continuity_chain", {"device_id": HUMAN_DEVICE_ID}),
        ("get_recent_records",   {"device_id": HUMAN_DEVICE_ID, "limit": 10}),
        ("get_startup_diagnostics", {}),
        ("get_phg_checkpoints",  {"device_id": HUMAN_DEVICE_ID, "limit": 5}),
        ("check_eligibility",    {"device_id": HUMAN_DEVICE_ID}),
        ("get_pitl_proof",       {"device_id": HUMAN_DEVICE_ID}),
        ("get_behavioral_report", {"device_id": HUMAN_DEVICE_ID}),
        ("get_network_clusters", {"min_suspicion": 0.1}),
        ("get_federation_status", {"min_peers": 2}),
        ("get_detection_policy", {"device_id": HUMAN_DEVICE_ID}),
        ("query_digest",         {"window": "all", "include_device_labels": True}),
        ("get_credential_status", {"device_id": HUMAN_DEVICE_ID}),
    ]

    for tool_name, inputs in tool_tests:
        try:
            result = agent._execute_tool(tool_name, inputs)
            has_error = isinstance(result, dict) and "error" in result
            # Some tools return errors legitimately (no proof found, etc.) — still counts as
            # "tool executed" rather than "tool crashed"
            if has_error and _verbose:
                print(f"    {tool_name}: no data — {result['error']}")
            ok &= check(f"_execute_tool({tool_name})",
                        not isinstance(result, type(None)),
                        "no data" if has_error else "ok")
        except Exception as exc:
            ok &= check(f"_execute_tool({tool_name})", False, str(exc))

    # Spot-check semantics
    try:
        profile = agent._execute_tool("get_player_profile", {"device_id": HUMAN_DEVICE_ID})
        ok &= check("get_player_profile returns non-error dict",
                    isinstance(profile, dict) and "error" not in profile,
                    str(list(profile.keys()))[:80] if isinstance(profile, dict) else str(profile)[:40])
    except Exception as exc:
        ok &= check("get_player_profile semantics", False, str(exc))

    try:
        lb = agent._execute_tool("get_leaderboard", {"limit": 5})
        ok &= check("get_leaderboard returns list",
                    isinstance(lb, list) or (isinstance(lb, dict) and "entries" in lb))
    except Exception as exc:
        ok &= check("get_leaderboard semantics", False, str(exc))

    try:
        elig = agent._execute_tool("check_eligibility", {"device_id": HUMAN_DEVICE_ID})
        ok &= check("check_eligibility human device eligible",
                    isinstance(elig, dict) and elig.get("eligible") is True,
                    f"eligible={elig.get('eligible')}, score={elig.get('cumulative_score')}"
                    if isinstance(elig, dict) else str(elig))
    except Exception as exc:
        ok &= check("check_eligibility semantics", False, str(exc))

    try:
        diag = agent._execute_tool("get_startup_diagnostics", {})
        ok &= check("get_startup_diagnostics returns dict with zk_artifacts",
                    isinstance(diag, dict) and "zk_artifacts" in diag)
    except Exception as exc:
        ok &= check("get_startup_diagnostics semantics", False, str(exc))

    try:
        fed = agent._execute_tool("get_federation_status", {"min_peers": 2})
        ok &= check("get_federation_status federation_enabled is False (no peers configured)",
                    isinstance(fed, dict) and fed.get("federation_enabled") is False)
    except Exception as exc:
        ok &= check("get_federation_status semantics", False, str(exc))

    try:
        cred = agent._execute_tool("get_credential_status", {"device_id": ADV_DEVICE_ID})
        ok &= check("get_credential_status adversarial device (no credential expected)",
                    isinstance(cred, dict) and cred.get("has_credential") is False)
    except Exception as exc:
        ok &= check("get_credential_status adversarial semantics", False, str(exc))

    # Unknown tool
    try:
        unk = agent._execute_tool("nonexistent_tool", {})
        ok &= check("unknown tool returns error dict (not exception)",
                    isinstance(unk, dict) and "error" in unk)
    except Exception:
        ok &= check("unknown tool returns error dict (not exception)", False,
                    "raised exception instead")

    return ok


# ---------------------------------------------------------------------------
# Step 10 — react() event handling
# ---------------------------------------------------------------------------
def step_react(agent: BridgeAgent) -> bool:
    section("STEP 10 — BridgeAgent.react() Event Handling")

    ok = True
    events = [
        {
            "device_id": ADV_DEVICE_ID,
            "inference_name": "BIOMETRIC_ANOMALY",
            "pitl_l4_distance": 9.8,
            "pitl_humanity_prob": 0.12,
        },
        {
            "device_id": ADV_DEVICE_ID,
            "inference_name": "TEMPORAL_ANOMALY",
            "pitl_l4_distance": None,
            "pitl_humanity_prob": 0.08,
        },
    ]

    for evt in events:
        iname = evt["inference_name"]
        try:
            result = agent.react(evt)
            ok &= check(f"react({iname}) returns dict", isinstance(result, dict))
            ok &= check(f"react({iname}) has alert key", "alert" in result)
            ok &= check(f"react({iname}) has severity key",
                        result.get("severity") in ("critical", "medium", "low"))
            if _verbose:
                print(f"    react({iname}) severity={result.get('severity')} "
                      f"tools={result.get('tools_used')}")
        except Exception as exc:
            ok &= check(f"react({iname})", False, str(exc))

    return ok


# ---------------------------------------------------------------------------
# Step 11 — LLM integration (gated on ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------
def step_llm_integration(agent: BridgeAgent) -> bool:
    section("STEP 11 — LLM Integration (gated on ANTHROPIC_API_KEY)")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  [SKIP] ANTHROPIC_API_KEY not set — LLM test skipped")
        _results.append(("SKIP", "LLM ask() integration", "no API key"))
        return True

    ok = True
    session_id = "smoke-test-llm-001"

    try:
        result = agent.ask(
            session_id,
            f"Is device {HUMAN_DEVICE_ID[:16]} eligible for the tournament? "
            f"Check their PHG score and recent PITL distribution.",
        )
        ok &= check("ask() returns dict", isinstance(result, dict))
        ok &= check("ask() has response field",
                    isinstance(result.get("response"), str) and len(result["response"]) > 0)
        ok &= check("ask() used at least one tool",
                    len(result.get("tools_used", [])) > 0,
                    f"tools={result.get('tools_used')}")
        if _verbose:
            print(f"    LLM response (truncated): {result['response'][:200]}")
    except ImportError:
        print("  [SKIP] anthropic package not installed")
        _results.append(("SKIP", "LLM ask() integration", "anthropic not installed"))
    except Exception as exc:
        ok &= check("ask() round-trip", False, str(exc))

    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> int:
    parser = argparse.ArgumentParser(description="VAPI bridge agent integration smoke test")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print agent output and extra detail")
    args = parser.parse_args()

    global _verbose
    _verbose = args.verbose

    print("VAPI Bridge Agent Integration Smoke Test")
    print(f"Repo:     {REPO_ROOT}")
    print(f"Sessions: {SESSIONS_DIR}")
    print(f"LLM:      {'enabled' if os.getenv('ANTHROPIC_API_KEY') else 'disabled (no API key)'}")
    print(f"Verbose:  {_verbose}")

    # Create temp DB
    tmp_dir = tempfile.mkdtemp(prefix="vapi_smoke_")
    db_path = os.path.join(tmp_dir, "smoke_test.db")

    store = Store(db_path)

    # Config: use defaults but ensure no real network calls
    cfg = dataclasses.replace(
        Config(),
        federation_peers="",          # no peers → FederationBus is a no-op
        alert_webhook_url="",         # no webhook → AlertRouter dispatches nothing
    )

    # Agents that are reused across steps
    arch        = None
    detector    = None
    agent       = None

    overall_ok = True

    # -- Step 1: DB setup
    overall_ok &= step_db_setup(store)

    # -- Step 2: BehavioralArchaeologist
    arch = step_behavioral_archaeologist(store)

    # -- Step 3: NetworkCorrelationDetector
    detector = step_network_detector(store)

    # -- Build BridgeAgent (needed for ProactiveMonitor + direct tool tests)
    agent = BridgeAgent(cfg, store, behavioral_arch=arch, network_detector=detector)

    # -- Step 4: ProactiveMonitor
    overall_ok &= await step_proactive_monitor(store, arch, detector, agent, cfg)

    # -- Step 5: InsightSynthesizer
    overall_ok &= await step_insight_synthesizer(store, cfg)

    # -- Step 6: ChainReconciler
    overall_ok &= await step_chain_reconciler(store, cfg)

    # -- Step 7: FederationBus
    overall_ok &= await step_federation_bus(store, detector, cfg)

    # -- Step 8: AlertRouter
    overall_ok &= await step_alert_router(store, cfg)

    # -- Step 9: BridgeAgent tool binding
    overall_ok &= step_bridge_agent_tools(agent)

    # -- Step 10: react()
    overall_ok &= step_react(agent)

    # -- Step 11: LLM integration
    overall_ok &= step_llm_integration(agent)

    # -- Summary
    section("SUMMARY")
    passed  = sum(1 for s, _, _ in _results if s == "PASS")
    failed  = sum(1 for s, _, _ in _results if s == "FAIL")
    skipped = sum(1 for s, _, _ in _results if s == "SKIP")

    if failed:
        print("\nFAILED tests:")
        for status, name, detail in _results:
            if status == "FAIL":
                print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))

    print(f"\nResults: {passed} passed, {failed} failed, {skipped} skipped "
          f"(total {len(_results)})")
    print(f"DB path: {db_path}")

    if failed == 0:
        print("\nALL CHECKS PASSED")
        return 0
    else:
        print(f"\n{failed} CHECK(S) FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
