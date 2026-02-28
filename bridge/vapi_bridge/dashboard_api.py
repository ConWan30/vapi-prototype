"""
PoHG Pulse Dashboard API — Phase 21 / Phase 26 / Phase 28

FastAPI sub-app mounted at /dash in main.py.

Endpoints:
  GET /api/v1/player/{device_id}/profile           — PHG Trust Score, Trust Ledger, biometric fingerprint
  GET /api/v1/pitl/timeline                        — PITL detection events bucketed by 1-minute intervals
  GET /api/v1/sdk/attestation                      — SDK self-verification status (60s TTL cache)
  GET /api/v1/player/{device_id}/behavioral-report — Warmup attack + burst farming scores (Phase 26)
  GET /api/v1/player/{device_id}/pitl-proof        — Latest PITL ZK proof record (Phase 26)
  GET /api/v1/network/farm-detection               — Bot farm cluster detection (Phase 26)
  GET /api/v1/player/{device_id}/credential        — Soulbound PHGCredential mint status (Phase 28)
  GET /api/v1/leaderboard                          — Top devices by confirmed PHG cumulative score (Phase 28)
  GET /api/v1/player/{device_id}/eligibility       — Tournament gate eligibility check (Phase 28)
  GET /proof/{device_id}                           — Shareable humanity proof page (Phase 28)
"""

import asyncio
import dataclasses
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sub-app factory
# ---------------------------------------------------------------------------

def create_dashboard_app(store, behavioral_arch=None, network_detector=None) -> FastAPI:
    """Create the PoHG Pulse dashboard API sub-app.

    Args:
        store:            Store instance (required).
        behavioral_arch:  BehavioralArchaeologist instance, or None (Phase 26).
        network_detector: NetworkCorrelationDetector instance, or None (Phase 26).

    New Phase 26 endpoints return HTTP 503 when behavioral_arch/network_detector are None.
    """

    app = FastAPI(title="PoHG Pulse Dashboard API", version="1.0.0-phase28")

    # ------------------------------------------------------------------
    # Player Profile — Trust Ledger + Identity Glyph data
    # ------------------------------------------------------------------

    @app.get("/api/v1/player/{device_id}/profile")
    async def player_profile(device_id: str) -> dict[str, Any]:
        """
        Return the PHG Trust Ledger for a device.

        PHG Trust Score = Σ (confidence_i / 255) × 10 over all verified NOMINAL records.
        Monotonically increasing with authentic play; cannot be inflated without on-chain PoAC.
        """
        profile = store.get_player_profile(device_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Device not found")

        # Biometric fingerprint (averaged feature vectors from recent NOMINAL records)
        fingerprint = store.get_biometric_fingerprint(device_id)

        return {
            **profile,
            "biometric_fingerprint": fingerprint,
            "fingerprint_available": fingerprint is not None,
            "phg_score_weighted":    profile.get("phg_score_weighted", profile.get("phg_score", 0)),
            "humanity_prob_avg":     profile.get("humanity_prob_avg", 0.0),
            "l5_rhythm_humanity_avg": profile.get("l5_rhythm_humanity_avg", 0.0),
        }

    # ------------------------------------------------------------------
    # PITL Timeline — Adversarial Pressure Map data
    # ------------------------------------------------------------------

    @app.get("/api/v1/pitl/timeline")
    async def pitl_timeline(minutes: int = 10) -> list[dict[str, Any]]:
        """
        PITL detection events bucketed by 1-minute intervals.

        Returns non-NOMINAL inference events grouped by (bucket_epoch, inference_code).
        Used by the Adversarial Pressure Map panel (stacked bar chart).
        """
        minutes = min(max(minutes, 1), 60)  # clamp 1-60
        return store.get_pitl_timeline(minutes)

    # ------------------------------------------------------------------
    # PHG Checkpoint Chain — Phase 22
    # ------------------------------------------------------------------

    @app.get("/api/v1/player/{device_id}/checkpoint-chain")
    async def checkpoint_chain(device_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        Return the most recent PHG checkpoints for a device (from SQLite cache).

        Checkpoints are committed on-chain every N verified NOMINAL records.
        Each entry includes the PHG score delta, record count, biometric hash, and tx hash.
        """
        limit = min(max(limit, 1), 100)
        return store.get_phg_checkpoints(device_id, limit)

    # ------------------------------------------------------------------
    # Continuity Chain — Phase 23
    # ------------------------------------------------------------------

    @app.get("/api/v1/player/{device_id}/continuity-chain")
    async def continuity_chain(device_id: str) -> list[dict[str, Any]]:
        """
        Return continuity claim records involving this device.

        Each entry: {device_id, claimed_by, claimed_at, direction}
        direction = "source" if this device was the old (migrated-from) device;
                   "destination" if it was the new (score-inheriting) device.
        """
        return store.get_continuity_chain(device_id)

    # ------------------------------------------------------------------
    # SDK Attestation — Heartbeat panel data
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Behavioral Report — Phase 26
    # ------------------------------------------------------------------

    @app.get("/api/v1/player/{device_id}/behavioral-report")
    async def behavioral_report(device_id: str) -> dict[str, Any]:
        """
        Longitudinal PITL analysis: warmup attack score, burst farming score,
        drift trend slope, biometric stability certificate.

        Returns HTTP 503 when BehavioralArchaeologist is not initialized.
        """
        if behavioral_arch is None:
            raise HTTPException(
                status_code=503,
                detail="BehavioralArchaeologist not initialized",
            )
        try:
            loop = asyncio.get_event_loop()
            report = await loop.run_in_executor(
                None, behavioral_arch.analyze_device, device_id
            )
            return dataclasses.asdict(report)
        except Exception as exc:
            log.warning("behavioral_report error for %s: %s", device_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    # ------------------------------------------------------------------
    # PITL Proof — Phase 26
    # ------------------------------------------------------------------

    @app.get("/api/v1/player/{device_id}/pitl-proof")
    async def pitl_proof(device_id: str) -> dict[str, Any]:
        """
        Return the most recent PITL ZK session proof record for a device.

        Returns HTTP 404 when no proof has been submitted for this device.
        """
        row = store.get_latest_pitl_proof(device_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No PITL proof found for device")
        return row

    # ------------------------------------------------------------------
    # Network Farm Detection — Phase 26
    # ------------------------------------------------------------------

    @app.get("/api/v1/network/farm-detection")
    async def farm_detection() -> list[dict[str, Any]]:
        """
        Return flagged device clusters (organized bot farms detected via DBSCAN).

        Returns HTTP 503 when NetworkCorrelationDetector is not initialized.
        Returns empty list when no suspicious clusters are detected.
        """
        if network_detector is None:
            raise HTTPException(
                status_code=503,
                detail="NetworkCorrelationDetector not initialized",
            )
        try:
            loop = asyncio.get_event_loop()
            clusters = await loop.run_in_executor(
                None, network_detector.get_flagged_clusters
            )
            return [dataclasses.asdict(c) for c in clusters]
        except Exception as exc:
            log.warning("farm_detection error: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    # ------------------------------------------------------------------
    # Phase 28: Credential, Leaderboard, Eligibility, Proof Page
    # ------------------------------------------------------------------

    @app.get("/api/v1/player/{device_id}/credential")
    async def player_credential(device_id: str) -> dict[str, Any]:
        """
        Return the PHGCredential mint record for a device.

        Returns HTTP 404 when the device has not yet had a credential minted.
        """
        row = store.get_credential_mint(device_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail="No PHGCredential minted for device"
            )
        return row

    @app.get("/api/v1/leaderboard")
    async def leaderboard(limit: int = 20) -> list[dict[str, Any]]:
        """
        Return top devices by confirmed cumulative PHG score.

        Uses confirmed=1 checkpoints from local SQLite (consistent with delta logic).
        """
        return store.get_leaderboard(limit=min(limit, 100))

    @app.get("/api/v1/player/{device_id}/eligibility")
    async def player_eligibility(device_id: str) -> dict[str, Any]:
        """
        Check tournament gate eligibility for a device.

        Uses local confirmed PHG checkpoints as eligibility proxy.
        Returns eligible=True when cumulative_score > 0 and device has confirmed checkpoints.
        """
        rows = store.get_leaderboard(limit=1000)
        entry = next((r for r in rows if r["device_id"] == device_id), None)
        if entry is None:
            return {
                "device_id": device_id,
                "eligible": False,
                "cumulative_score": 0,
                "details": "No confirmed PHG checkpoints found",
            }
        score = entry.get("cumulative_score") or 0
        return {
            "device_id": device_id,
            "eligible": score > 0,
            "cumulative_score": score,
            "details": "Eligible" if score > 0 else "Insufficient cumulative score",
        }

    @app.get("/proof/{device_id}", response_class=None)
    async def proof_page(device_id: str):
        """
        Shareable humanity proof page for a device (Phase 28).

        Returns an HTML page with the device's PHG credential details.
        Designed to be shared as a proof-of-humanity URL.
        """
        from fastapi.responses import HTMLResponse
        credential = store.get_credential_mint(device_id)
        proof = store.get_latest_pitl_proof(device_id)

        hp_int = proof["humanity_prob_int"] if proof else 0
        hp_pct = f"{hp_int / 10:.1f}%" if proof else "—"
        cred_id = credential["credential_id"] if credential else "—"
        null_prefix = (proof["nullifier_hash"][:18] + "...") if proof else "—"
        tx_display = (credential["tx_hash"][:18] + "...") if (credential and credential.get("tx_hash")) else "—"
        dev_display = device_id[:20] + "..." if len(device_id) > 20 else device_id

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VAPI Humanity Proof — {dev_display}</title>
  <style>
    body {{ font-family: monospace; background: #0a0a0a; color: #e0e0e0; padding: 2rem; max-width: 600px; margin: auto; }}
    h1 {{ color: #22c55e; font-size: 1.4rem; }}
    .badge {{ background: #14532d; border: 1px solid #22c55e; border-radius: 4px; padding: 1rem 1.5rem; margin: 1rem 0; }}
    .label {{ color: #6b7280; font-size: 0.8rem; text-transform: uppercase; margin-bottom: 0.25rem; }}
    .value {{ color: #f0fdf4; font-size: 1rem; word-break: break-all; }}
    .score {{ font-size: 2.5rem; color: #22c55e; font-weight: bold; }}
    .footer {{ color: #4b5563; font-size: 0.75rem; margin-top: 2rem; }}
  </style>
</head>
<body>
  <h1>&#x2705; Verified Human Gamer</h1>
  <p>This device has accumulated verifiable proof-of-human gameplay through the VAPI PITL stack.</p>
  <div class="badge">
    <div class="label">Device ID</div>
    <div class="value">{device_id}</div>
  </div>
  <div class="badge">
    <div class="label">Humanity Score</div>
    <div class="score">{hp_pct}</div>
    <div class="value" style="font-size:0.85rem; color:#6b7280">({hp_int} / 1000 raw)</div>
  </div>
  <div class="badge">
    <div class="label">PHG Credential ID</div>
    <div class="value">{cred_id}</div>
  </div>
  <div class="badge">
    <div class="label">Nullifier Hash (prefix)</div>
    <div class="value">{null_prefix}</div>
  </div>
  <div class="badge">
    <div class="label">On-Chain Tx (prefix)</div>
    <div class="value">{tx_display}</div>
  </div>
  <div class="footer">
    VAPI — Verified Autonomous Physical Intelligence &nbsp;|&nbsp;
    Phase 28: The Credential Becomes a Portal
  </div>
</body>
</html>"""
        return HTMLResponse(content=html, status_code=200)

    # Module-level attestation cache (shared across all requests)
    _attestation_cache: dict[str, Any] = {}
    _attestation_last_run: float = 0.0
    _ATTESTATION_TTL = 60.0  # seconds

    @app.get("/api/v1/sdk/attestation")
    async def sdk_attestation() -> dict[str, Any]:
        """
        Run VAPISession.self_verify() and return layer status.

        Result is cached for 60 seconds to avoid repeated work.
        self_verify() is sync so it runs in the default threadpool executor.
        """
        nonlocal _attestation_last_run

        now = time.time()
        if now - _attestation_last_run < _ATTESTATION_TTL and _attestation_cache:
            return {**_attestation_cache, "cached": True}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _run_self_verify)
            _attestation_cache.clear()
            _attestation_cache.update(result)
            _attestation_last_run = now
            return {**result, "cached": False}
        except Exception as exc:
            log.warning("SDK attestation failed: %s", exc)
            return {
                "error": str(exc),
                "all_layers_active": False,
                "layers_active": {},
                "pitl_scores": {},
                "cached": False,
            }

    return app


def _run_self_verify() -> dict:
    """Synchronous self-verification — run in executor."""
    # Add SDK to path if needed
    sdk_dir = str(Path(__file__).parents[3] / "sdk")
    if sdk_dir not in sys.path:
        sys.path.insert(0, sdk_dir)

    try:
        from vapi_sdk import VAPISession  # type: ignore
        session = VAPISession()
        att = session.self_verify()
        return {
            "all_layers_active": att.all_layers_active,
            "active_layer_count": att.active_layer_count,
            "layers_active": att.layers_active,
            "pitl_scores": att.pitl_scores,
            "zk_proof_available": att.zk_proof_available,
            "sdk_version": att.sdk_version,
            "verified_at": att.verified_at,
            "attestation_hash": att.attestation_hash.hex(),
        }
    except ImportError as exc:
        log.warning("vapi_sdk not importable: %s", exc)
        return {
            "all_layers_active": False,
            "active_layer_count": 0,
            "layers_active": {
                "L2_hid_xinput": False,
                "L3_behavioral": False,
                "L4_biometric": False,
                "L5_temporal": False,
            },
            "pitl_scores": {},
            "zk_proof_available": False,
            "sdk_version": "unavailable",
            "verified_at": time.time(),
            "attestation_hash": "0" * 64,
        }
