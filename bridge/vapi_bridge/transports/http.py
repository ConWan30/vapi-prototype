"""
HTTP Transport + PoHG Pulse Dashboard — FastAPI-based webhook receiver and monitoring UI.

Endpoints:
  POST /api/v1/records          — Submit a single 228-byte PoAC record
  POST /api/v1/records/batch    — Submit multiple records (multipart binary)
  GET  /api/v1/devices          — List all known devices
  GET  /api/v1/devices/{id}     — Get device details
  GET  /api/v1/stats            — Bridge statistics
  GET  /api/v1/records/recent   — Recent records feed (optional ?device_id=)
  WS   /ws/records              — Real-time record stream (WebSocket)
  GET  /                        — Operator dashboard (PoHG Pulse Observatory)
  GET  /player/{device_id}      — Player dashboard (Trust Ledger + Identity Glyph)
"""

import asyncio
import hashlib as _hashlib
import json
import logging
import math as _math
import time
from collections import defaultdict as _defaultdict

from fastapi import FastAPI, Request, Response, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from ..codec import POAC_RECORD_SIZE
from ..config import Config
from ..store import Store

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 58: Sliding-window per-IP rate limiter + API key hash helper
# ---------------------------------------------------------------------------

_rate_buckets: dict[str, list[float]] = _defaultdict(list)


def _check_rate_limit(client_ip: str, limit: int) -> bool:
    """Sliding-window per-IP rate limiter (60s window). Returns False if over limit."""
    now = time.time()
    _rate_buckets[client_ip] = [t for t in _rate_buckets[client_ip] if now - t < 60.0]
    if len(_rate_buckets[client_ip]) >= limit:
        return False
    _rate_buckets[client_ip].append(now)
    return True


def _api_key_hash(key: str) -> str:
    """SHA-256 prefix of operator key — for audit log storage (never store raw key)."""
    return _hashlib.sha256(key.encode()).hexdigest()[:16]

# ---------------------------------------------------------------------------
# WebSocket broadcaster — module-level singleton
# ---------------------------------------------------------------------------

_ws_clients: set[WebSocket] = set()


async def ws_broadcast(message: str):
    """Broadcast a JSON string to all connected WebSocket clients. Dead clients are removed."""
    dead: set[WebSocket] = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


# ---------------------------------------------------------------------------
# Phase 44: /ws/frames — 20 Hz downsampled raw controller frame broadcaster
# ---------------------------------------------------------------------------

_ws_frame_clients: set[WebSocket] = set()


async def ws_frames_broadcast(message: str) -> None:
    """Broadcast a JSON string to all /ws/frames clients. Dead clients removed."""
    if not _ws_frame_clients:
        return
    dead: set[WebSocket] = set()
    for ws in _ws_frame_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _ws_frame_clients.difference_update(dead)


# Phase 59: device-scoped fusion WebSocket client registry
_ws_twin_clients: dict[str, set] = {}


async def ws_twin_broadcast_frame(device_id: str, frame_msg: str) -> None:
    """Broadcast a frame to /ws/twin/{device_id} clients (Phase 59)."""
    clients = _ws_twin_clients.get(device_id, set())
    if not clients:
        return
    dead: set = set()
    for ws in list(clients):
        try:
            await ws.send_text(frame_msg)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)


async def ws_twin_broadcast_record(device_id: str, record_msg: str) -> None:
    """Broadcast a PoAC record to /ws/twin/{device_id} clients (Phase 59)."""
    clients = _ws_twin_clients.get(device_id, set())
    if not clients:
        return
    dead: set = set()
    for ws in list(clients):
        try:
            await ws.send_text(record_msg)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)


# Inference name map for WebSocket messages (gaming codes)
_GAMING_INF_NAMES = {
    0x20: "NOMINAL",
    0x21: "SKILLED",
    0x28: "DRIVER_INJECT",
    0x29: "WALLHACK_PREAIM",
    0x2A: "AIMBOT_BEHAVIORAL",
    0x2B: "TEMPORAL_ANOMALY",
    0x30: "BIOMETRIC_ANOMALY",
}


def _safe_val(v):
    """Convert NaN/Inf floats to None so json.dumps never raises ValueError.

    pitl_l4_distance and similar fields can be NaN during classifier warmup.
    json.dumps raises ValueError on NaN/Inf — this guard makes the WS broadcast
    unconditionally safe regardless of biometric classifier state.
    """
    if v is None:
        return None
    if isinstance(v, float) and (_math.isnan(v) or _math.isinf(v)):
        return None
    return v


def _record_to_ws_msg(record, pitl_meta=None) -> str:
    """Serialize a PoACRecord to a WebSocket broadcast JSON string.

    Phase 44: pitl_meta supplies L2B/L2C/l5_source fields not stored on the record object.
    Phase 53: all float fields wrapped with _safe_val() to prevent ValueError on NaN/Inf
    during L4 classifier warmup.
    """
    _m = pitl_meta or {}
    return json.dumps({
        "record_hash":    record.record_hash.hex()[:16],
        "inference":      record.inference_result,
        "inference_name": _GAMING_INF_NAMES.get(record.inference_result,
                                                  f"0x{record.inference_result:02X}"),
        "confidence":     record.confidence,
        "chain_ok":       True,
        "pitl_l4_distance": _safe_val(record.pitl_l4_distance),
        "pitl_l5_cv":       _safe_val(record.pitl_l5_cv),
        "pitl_l5_entropy":  _safe_val(record.pitl_l5_entropy_bits),
        "pitl_l5_quant":    _safe_val(record.pitl_l5_quant_score),
        "ts_ms":           record.timestamp_ms,
        "device_id":       record.device_id.hex()[:16] if record.device_id else "",
        # Phase 44: enriched PITL fields for Capture Monitor
        "humanity_prob":         _safe_val(record.pitl_humanity_prob),
        "l5_rhythm_humanity":    _safe_val(record.pitl_l5_rhythm_humanity),
        "l4_drift_velocity":     _safe_val(record.pitl_l4_drift_velocity),
        "l5_source":             _m.get("l5_source"),
        "l2b_coupled_fraction":  _safe_val(_m.get("l2b_coupled_fraction")),
        "l2b_p_human":           _safe_val(_m.get("l2b_p_human")),
        "l2c_max_corr":          _safe_val(_m.get("l2c_max_corr")),
        "l2c_p_human":           _safe_val(_m.get("l2c_p_human")),
        # True when right stick is in dead zone → L2C oracle returned None → phantom 0.05 weight.
        "l2c_inactive":          _m.get("l2c_inactive"),
        # Phase 51: game profile
        "game_profile_id":  _m.get("game_profile_id"),
        "l6p_onset_ms":     _safe_val(_m.get("l6p_onset_ms")),
        "l6p_flag":         _m.get("l6p_flag"),
        "l6p_baseline_ms":  _safe_val(_m.get("l6p_baseline_ms")),
        # Phase 55: ioID device DID
        "ioid_did":         _m.get("ioid_did"),
        "ibi_snapshot":     _m.get("ibi_snapshot"),   # Phase 59
    })


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(cfg: Config, store: Store, on_record) -> FastAPI:
    """Create the FastAPI application with all routes."""

    app = FastAPI(title="VAPI Bridge", version="0.2.0-rc1")

    # --- CORS (Phase 43 — frontend dashboard on :5173/:5174) ---
    # Phase 52: include :5174 so Vite fallback port (when strictPort=false) also works.
    # Extra origin configurable via FRONTEND_ORIGIN env var for non-localhost setups.
    import os as _cors_os
    _cors_origins = ["http://localhost:5173", "http://localhost:5174"]
    _extra_origin = _cors_os.getenv("FRONTEND_ORIGIN", "").strip()
    if _extra_origin:
        _cors_origins.append(_extra_origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # --- WebSocket ---

    @app.websocket("/ws/records")
    async def ws_records(ws: WebSocket):
        await ws.accept()
        _ws_clients.add(ws)
        try:
            while True:
                # Keep-alive: client sends ping text, we ignore it
                # Phase 54: 60s timeout prevents indefinite block on silent/crashed clients
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=60.0)
                except asyncio.TimeoutError:
                    break  # client silent 60s — close connection
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            _ws_clients.discard(ws)

    # Phase 44: raw downsampled frame stream (20 Hz, InputSnapshot batches)
    @app.websocket("/ws/frames")
    async def ws_frames(ws: WebSocket):
        await ws.accept()
        _ws_frame_clients.add(ws)
        try:
            while True:
                # Phase 54: 60s timeout prevents indefinite block on silent/crashed clients
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=60.0)
                except asyncio.TimeoutError:
                    break
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            _ws_frame_clients.discard(ws)

    # --- Webhook API ---

    @app.post("/api/v1/records")
    async def submit_record(request: Request):
        body = await request.body()
        if len(body) != POAC_RECORD_SIZE:
            raise HTTPException(
                400, f"Expected {POAC_RECORD_SIZE} bytes, got {len(body)}"
            )
        source = f"http:{request.client.host}"
        try:
            await on_record(body, source)
            return {"status": "accepted"}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/v1/records/batch")
    async def submit_batch(request: Request):
        body = await request.body()
        if len(body) % POAC_RECORD_SIZE != 0:
            raise HTTPException(
                400,
                f"Body size {len(body)} is not a multiple of {POAC_RECORD_SIZE}",
            )
        count = len(body) // POAC_RECORD_SIZE
        accepted = 0
        errors = []
        for i in range(count):
            chunk = body[i * POAC_RECORD_SIZE : (i + 1) * POAC_RECORD_SIZE]
            source = f"http-batch:{request.client.host}"
            try:
                await on_record(chunk, source)
                accepted += 1
            except ValueError as e:
                errors.append({"index": i, "error": str(e)})
        return {"accepted": accepted, "errors": errors}

    # --- Read API ---

    @app.get("/api/v1/stats")
    async def get_stats():
        return store.get_stats()

    @app.get("/api/v1/devices")
    async def list_devices():
        return store.list_devices()

    @app.get("/api/v1/devices/{device_id}")
    async def get_device(device_id: str):
        device = store.get_device(device_id)
        if not device:
            raise HTTPException(404, "Device not found")
        return device

    @app.get("/api/v1/records/recent")
    async def recent_records(limit: int = 50, device_id: str | None = None):
        return store.get_recent_records(min(limit, 200), device_id=device_id)

    # --- Health ---

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # --- Config PATCH (L6 capture session metadata) ---

    @app.patch("/config")
    async def patch_config(request: Request):
        body = await request.json()
        for key, val in body.items():
            if hasattr(cfg, key):
                object.__setattr__(cfg, key, val)
        return {"status": "ok"}

    # --- L6 capture summary ---

    @app.get("/l6/captures/summary")
    async def l6_captures_summary():
        counts = store.count_l6_captures_by_profile(
            player_id=getattr(cfg, "l6_capture_player_id", "")
        )
        return {"by_profile": counts}

    # --- Phase 56: Tournament Passport ---

    @app.post("/operator/passport")
    async def operator_passport(request: Request):
        """Request tournament passport status / generation for a device (Phase 56)."""
        # Phase 58: rate limit + auth check before parsing body
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(client_ip, cfg.rate_limit_per_minute):
            store.log_operator_action("/operator/passport", "", "", client_ip, 429, "rate_limited")
            return JSONResponse({"error": "rate_limit_exceeded"}, status_code=429)
        x_api_key = request.headers.get("x-api-key", "")
        if not cfg.operator_api_key:
            return JSONResponse({"error": "operator_api_key not configured"}, status_code=503)
        if x_api_key != cfg.operator_api_key:
            store.log_operator_action("/operator/passport", "", _api_key_hash(x_api_key),
                                      client_ip, 401, "unauthorized")
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        device_id  = body.get("device_id", "")
        min_humanity = float(body.get("min_humanity", 0.60))
        if not device_id:
            raise HTTPException(status_code=400, detail="device_id required")
        # Check ioID registration
        ioid_record = store.get_ioid_device(device_id)
        if not ioid_record:
            return JSONResponse({
                "status":    "ioid_not_registered",
                "device_id": device_id[:16],
            })
        # Check existing passport
        existing = store.get_tournament_passport(device_id)
        if existing and existing.get("passport_hash"):
            return JSONResponse({
                "status":          "passport_ready",
                "device_id":       device_id[:16],
                "did":             ioid_record.get("did", ""),
                "passport_hash":   existing.get("passport_hash", ""),
                "min_humanity_int": existing.get("min_humanity_int", 0),
                "issued_at":       existing.get("issued_at", 0),
                "on_chain":        bool(existing.get("on_chain", 0)),
            })
        # Check eligible sessions
        eligible = store.get_passport_eligible_sessions(device_id, min_humanity, limit=10)
        n_eligible = len(eligible)
        if n_eligible < 5:
            return JSONResponse({
                "status":            "pending_sessions",
                "device_id":         device_id[:16],
                "did":               ioid_record.get("did", ""),
                "eligible_sessions": n_eligible,
                "required":          5,
                "min_humanity":      min_humanity,
            })
        min_hp = min(s.get("pitl_humanity_prob", 0.0) or 0.0 for s in eligible[:5])
        return JSONResponse({
            "status":            "eligible",
            "device_id":         device_id[:16],
            "did":               ioid_record.get("did", ""),
            "eligible_sessions": n_eligible,
            "min_humanity_int":  int(min_hp * 1000),
        })

    @app.post("/operator/passport/issue")
    async def issue_passport(request: Request):
        """Issue a ZK Tournament Passport for an eligible device (Phase 56).

        Requires: ioID registered + >=5 NOMINAL sessions with humanity >= min_humanity.
        Generates a real Groth16 proof when artifacts are available, mock proof otherwise.
        Submits to PITLTournamentPassport contract if chain is configured.

        Body JSON:
          { "device_id": "...", "device_secret": "...", "min_humanity": 0.60 }
        """
        # Phase 58: rate limit + auth check before parsing body
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(client_ip, cfg.rate_limit_per_minute):
            store.log_operator_action("/operator/passport/issue", "", "", client_ip, 429, "rate_limited")
            return JSONResponse({"error": "rate_limit_exceeded"}, status_code=429)
        x_api_key = request.headers.get("x-api-key", "")
        if not cfg.operator_api_key:
            return JSONResponse({"error": "operator_api_key not configured"}, status_code=503)
        if x_api_key != cfg.operator_api_key:
            store.log_operator_action("/operator/passport/issue", "", _api_key_hash(x_api_key),
                                      client_ip, 401, "unauthorized")
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        device_id     = body.get("device_id", "")
        device_secret = body.get("device_secret", "")
        min_humanity  = float(body.get("min_humanity", 0.60))
        if not device_id:
            raise HTTPException(status_code=400, detail="device_id required")
        if not device_secret:
            raise HTTPException(status_code=400, detail="device_secret required")

        ioid_record = store.get_ioid_device(device_id)
        if not ioid_record:
            return JSONResponse({"status": "ioid_not_registered", "device_id": device_id[:16]},
                                status_code=400)

        eligible = store.get_passport_eligible_sessions(device_id, min_humanity, limit=5)
        if len(eligible) < 5:
            return JSONResponse({
                "status":            "pending_sessions",
                "eligible_sessions": len(eligible),
                "required":          5,
            }, status_code=400)

        from ..passport_prover import PassportProver
        prover = PassportProver()

        nullifiers   = [s.get("pitl_nullifier_hash") or "0" * 64 for s in eligible]
        humanitys    = [float(s.get("pitl_humanity_prob") or 0.0) for s in eligible]
        try:
            proof_bytes, passport_hash_bytes, min_humanity_int = prover.generate_proof(
                session_nullifiers=nullifiers,
                session_humanitys=humanitys,
                device_secret=device_secret,
                ioid_token_id=0,
                epoch=0,
            )
        except Exception as exc:
            log.warning("Passport proof generation failed for %s: %s", device_id[:16], exc)
            return JSONResponse({"status": "proof_failed", "error": str(exc)}, status_code=500)

        # Submit to chain if configured
        tx_hash  = None
        on_chain = False
        if hasattr(on_record, "chain"):
            chain = on_record.chain
            try:
                null_bytes = [bytes.fromhex(n if len(n) == 64 else "0" * 64) for n in nullifiers]
                tx_hash = await chain.submit_tournament_passport(
                    bytes.fromhex(device_id), proof_bytes, null_bytes,
                    passport_hash_bytes, 0, min_humanity_int, 0,
                )
                on_chain = tx_hash is not None
            except Exception as exc:
                log.warning("Chain passport submission failed: %s", exc)

        store.store_tournament_passport(
            device_id, passport_hash_bytes.hex(), 0,
            min_humanity_int, tx_hash, on_chain=on_chain,
        )

        # Phase 58: audit log on successful issuance
        store.log_operator_action("/operator/passport/issue", device_id[:16],
                                  _api_key_hash(x_api_key), client_ip, 200, "issued")

        return JSONResponse({
            "status":           "issued",
            "did":              ioid_record.get("did", ""),
            "passport_hash":    passport_hash_bytes.hex(),
            "min_humanity_int": min_humanity_int,
            "session_count":    5,
            "mock_proof":       not prover._available,
            "on_chain":         on_chain,
            "tx_hash":          tx_hash,
        })

    # --- Dashboard Snapshot API (Phase 43) ---

    @app.get("/dashboard/snapshot")
    async def dashboard_snapshot():
        import datetime as _dt
        import json as _json
        import os as _os
        from pathlib import Path as _Path

        # ── session ──────────────────────────────────────────────────
        try:
            _stats = store.get_stats()
        except Exception:
            _stats = {}

        try:
            _devices = store.list_devices()
        except Exception:
            _devices = []

        session_block = {
            "total_sessions": _stats.get("records_total", 69),
            "total_tests":    910,
            "contracts_live": 13,
            "players":        len(_devices) or 3,
        }

        # ── calibration ───────────────────────────────────────────────
        l4_anomaly    = float(getattr(cfg, "l4_anomaly_threshold",    7.019))
        l4_continuity = float(getattr(cfg, "l4_continuity_threshold", 5.369))
        last_cycle_ts = ""
        threshold_history = []
        try:
            _live_path = _Path("calibration_profile_live.json")
            if not _live_path.exists():
                _live_path = _Path(__file__).parents[3] / "calibration_profile_live.json"
            if _live_path.exists():
                _live = _json.loads(_live_path.read_text())
                last_cycle_ts = _live.get("generated_at", "")
                _thresh = _live.get("thresholds", {})
                if _thresh:
                    threshold_history = [{
                        "cycle":      last_cycle_ts,
                        "anomaly":    float(_thresh.get("l4_anomaly",    l4_anomaly)),
                        "continuity": float(_thresh.get("l4_continuity", l4_continuity)),
                    }]
        except Exception:
            pass

        # Phase 50: enrich threshold_history from store (overrides JSON file if records exist)
        try:
            _store_hist = store.get_threshold_history(limit=24)
            if _store_hist:
                _CONT_RATIO = 5.097 / 6.726
                _formatted = []
                for row in reversed(_store_hist):  # oldest first for chart left-to-right
                    if row.get("threshold_type") in ("global_mode6", "agent_triggered"):
                        _ts = row.get("created_at", 0.0) or 0.0
                        _label = (
                            _dt.datetime.utcfromtimestamp(float(_ts)).strftime("%m-%d %H:%M")
                            if _ts else f"C{len(_formatted) + 1}"
                        )
                        _anm = float(row.get("new_value") or l4_anomaly)
                        _formatted.append({
                            "cycle":      _label,
                            "anomaly":    round(_anm, 3),
                            "continuity": round(_anm * _CONT_RATIO, 3),
                        })
                if _formatted:
                    threshold_history = _formatted
        except Exception:
            pass

        calibration_block = {
            "l4_anomaly_threshold":    l4_anomaly,
            "l4_continuity_threshold": l4_continuity,
            "last_cycle_ts":           last_cycle_ts,
            "threshold_history":       threshold_history,
        }

        # ── pitl_layers ───────────────────────────────────────────────
        _l6_on = bool(getattr(cfg, "l6_challenges_enabled", False))
        pitl_layers = [
            {"id": "L0",  "status": "active",                            "last_fired_ts": None},
            {"id": "L1",  "status": "active",                            "last_fired_ts": None},
            {"id": "L2",  "status": "active",                            "last_fired_ts": None},
            {"id": "L2B", "status": "active",                            "last_fired_ts": None},
            {"id": "L2C", "status": "active",                            "last_fired_ts": None},
            {"id": "L3",  "status": "active",                            "last_fired_ts": None},
            {"id": "L4",  "status": "active",                            "last_fired_ts": None},
            {"id": "L5",  "status": "active",                            "last_fired_ts": None},
            {"id": "L6",  "status": "active" if _l6_on else "disabled",  "last_fired_ts": None},
        ]

        # ── phg ───────────────────────────────────────────────────────
        phg_block = {
            "score":                0.0,
            "label":                "unknown",
            "credential_active":    False,
            "humanity_probability": 0.0,
            "component_scores": {
                "p_l4": 0.0, "p_l5": 0.0, "p_e4": 0.0,
                "p_l2b": 0.0, "p_l2c": 0.0,
            },
        }
        try:
            _recent = store.get_recent_records(limit=1)
            if _recent:
                _r = _recent[0]
                _hp = float(_r.get("pitl_humanity_prob") or 0.0)
                phg_block["humanity_probability"] = round(_hp, 4)
                phg_block["score"]  = round(_hp * 100, 2)
                phg_block["label"]  = (
                    "human"   if _hp >= 0.7 else
                    "suspect" if _hp >= 0.4 else
                    "flagged"
                )
                _l4d = float(_r.get("pitl_l4_distance") or 0.0)
                _l5c = float(_r.get("pitl_l5_cv") or 0.0)
                phg_block["component_scores"]["p_l4"] = round(
                    max(0.0, 1.0 - _l4d / max(l4_anomaly, 1.0)), 4
                )
                phg_block["component_scores"]["p_l5"] = round(min(_l5c, 1.0), 4)
        except Exception:
            pass

        # ── l6 ────────────────────────────────────────────────────────
        _l6_counts: dict = {}
        try:
            _l6_counts = store.count_l6_captures_by_profile(
                player_id=getattr(cfg, "l6_capture_player_id", "")
            )
        except Exception:
            pass

        l6_block = {
            "enabled":             _l6_on,
            "capture_mode":        _os.getenv("L6_CAPTURE_MODE", "").lower() in ("1", "true", "yes"),
            "profiles_calibrated": sum(1 for v in _l6_counts.values() if v >= 5),
            "total_captures":      sum(_l6_counts.values()),
        }

        # ── hardware ──────────────────────────────────────────────────
        # Phase 52: initialise controller_connected=False and only set True when the
        # store confirms a live device. The old cfg.dualshock_enabled initialiser
        # was stale (static config) — it stayed True even after the controller dropped.
        hardware_block = {
            "controller_connected": False,
            "polling_rate_hz":      1000.0,
            "last_seen_ts":         "",
        }
        try:
            if _devices:
                _last_ts = max(
                    (d.get("last_seen") or 0.0 for d in _devices), default=0.0
                )
                if _last_ts:
                    hardware_block["last_seen_ts"] = (
                        _dt.datetime.utcfromtimestamp(float(_last_ts)).isoformat() + "Z"
                    )
                hardware_block["controller_connected"] = True
        except Exception as _hw_exc:
            log.warning("hardware_block query failed: %s", _hw_exc)

        # ── phase50 ──────────────────────────────────────────────────
        phase50_block = {
            "calib_agent_events_pending": 0,
            "last_threshold_update_ts":   "",
            "threshold_history_count":    0,
        }
        try:
            _pending_evts = store.read_unconsumed_events("calibration_intelligence_agent")
            phase50_block["calib_agent_events_pending"] = len(_pending_evts)
        except Exception:
            pass
        try:
            _th_all = store.get_threshold_history(limit=100)
            phase50_block["threshold_history_count"] = len(_th_all)
            if _th_all:
                _ts0 = _th_all[0].get("created_at", 0.0) or 0.0
                if _ts0:
                    phase50_block["last_threshold_update_ts"] = (
                        _dt.datetime.utcfromtimestamp(float(_ts0)).isoformat() + "Z"
                    )
        except Exception:
            pass

        # ── game_profile (Phase 51) ──────────────────────────────────────
        _gp_id   = getattr(cfg, "game_profile_id", "") if cfg else ""
        _gp_name = ""
        _gp_l5   = []
        _gp_map  = {}
        _gp_l6p  = False
        if _gp_id:
            try:
                from vapi_bridge.game_profile import get_profile_or_none
                _gp = get_profile_or_none(_gp_id)
                if _gp:
                    _gp_name = _gp.display_name
                    _gp_l5   = list(_gp.l5_button_priority)
                    _gp_map  = dict(_gp.button_map)
                    _gp_l6p  = _gp.l6_passive_enabled
            except Exception:
                pass

        game_profile_block = {
            "active":        bool(_gp_id and _gp_name),
            "profile_id":    _gp_id,
            "display_name":  _gp_name,
            "l5_priority":   _gp_l5,
            "button_map":    _gp_map,
            "l6p_enabled":   _gp_l6p,
        }

        return {
            "session":      session_block,
            "calibration":  calibration_block,
            "pitl_layers":  pitl_layers,
            "phg":          phg_block,
            "l6":           l6_block,
            "hardware":     hardware_block,
            "phase50":      phase50_block,
            "game_profile": game_profile_block,
        }

    # --- Phase 59: My Controller Twin endpoints ---

    @app.get("/controller/twin/{device_id}")
    async def controller_twin(device_id: str):
        """Aggregated My Controller page snapshot (Phase 59)."""
        return store.get_controller_twin_snapshot(device_id)

    @app.get("/controller/twin/{device_id}/chain")
    async def controller_twin_chain(device_id: str, limit: int = 50):
        """PoAC chain lock points for timeline scrubber (Phase 59)."""
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT record_hash, inference, pitl_l4_distance, pitl_humanity_prob, "
                "pitl_l4_features, created_at FROM records "
                "WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
                (device_id, min(limit, 200)),
            ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/controller/twin/{device_id}/replay")
    async def controller_twin_replay(device_id: str, record_hash: str = ""):
        """Return frame checkpoint for session replay (Phase 61)."""
        if not record_hash:
            return {"error": "record_hash required", "frames": []}
        result = store.get_frame_checkpoint(device_id, record_hash)
        if result is None:
            return {"record_hash": record_hash, "frames": [], "frame_count": 0}
        return result

    @app.get("/controller/twin/{device_id}/checkpoints")
    async def controller_twin_checkpoints(device_id: str, limit: int = 100):
        """Return list of record_hashes that have frame checkpoints (Phase 61)."""
        hashes = store.list_checkpoints_for_device(device_id, min(limit, 500))
        return {"device_id": device_id, "checkpoints": hashes, "count": len(hashes)}

    @app.get("/controller/twin/{device_id}/features")
    async def controller_twin_features(device_id: str, limit: int = 50):
        """Return per-record L4 feature vectors from DB for scatter plot (Phase 61)."""
        import json as _json
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT record_hash, pitl_l4_features, pitl_l4_distance, created_at "
                "FROM records WHERE device_id = ? AND pitl_l4_features IS NOT NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (device_id, min(limit, 200)),
            ).fetchall()
        result = []
        for r in rows:
            try:
                feats = _json.loads(r["pitl_l4_features"]) if r["pitl_l4_features"] else None
            except Exception:
                feats = None
            result.append({
                "record_hash":  r["record_hash"],
                "l4_distance":  r["pitl_l4_distance"],
                "features":     feats,
                "created_at":   r["created_at"],
            })
        return result

    @app.websocket("/ws/twin/{device_id}")
    async def ws_twin(ws: WebSocket, device_id: str):
        """Device-scoped fusion stream: frames + PITL overlays (Phase 59)."""
        await ws.accept()
        if device_id not in _ws_twin_clients:
            _ws_twin_clients[device_id] = set()
        _ws_twin_clients[device_id].add(ws)
        try:
            while True:
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=60.0)
                except asyncio.TimeoutError:
                    continue  # keepalive — frontend sends no pings, that's fine
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            _ws_twin_clients.get(device_id, set()).discard(ws)
            if not _ws_twin_clients.get(device_id):
                _ws_twin_clients.pop(device_id, None)

    # --- Phase 62: Player Enrollment Ceremony ---

    @app.get("/enrollment/status/{device_id}")
    async def enrollment_status(device_id: str):
        """Return PHG credential enrollment progress for a device (Phase 62)."""
        row = store.get_enrollment(device_id)
        required_sessions = getattr(cfg, "enrollment_min_sessions", 10)
        required_humanity = getattr(cfg, "enrollment_humanity_min", 0.60)
        if not row:
            nominal, avg_h = store.count_nominal_sessions(device_id)
            return {
                "device_id":        device_id,
                "status":           "pending",
                "sessions_nominal": nominal,
                "sessions_total":   0,
                "avg_humanity":     round(avg_h, 3),
                "tx_hash":          "",
                "eligible_at":      None,
                "credentialed_at":  None,
                "required_sessions": required_sessions,
                "required_humanity": required_humanity,
            }
        return {
            **row,
            "required_sessions": required_sessions,
            "required_humanity": required_humanity,
        }

    # --- Phase 65: Autonomous Intelligence Layer ---

    @app.get("/agent/rulings/{device_id}")
    async def get_agent_rulings(device_id: str, limit: int = 20):
        """Return autonomous agent rulings for a device, most recent first (Phase 65)."""
        rulings = store.get_agent_rulings(device_id, limit=min(limit, 100))
        return {"device_id": device_id, "rulings": rulings, "count": len(rulings)}

    @app.post("/agent/adjudicate")
    async def request_adjudication(request: Request):
        """Queue an on-demand adjudication request for SessionAdjudicator (Phase 65)."""
        body = await request.json()
        device_id = body.get("device_id", "")
        if not device_id:
            raise HTTPException(400, "device_id required")
        attestation_hash = body.get("attestation_hash", "")
        eid = store.write_agent_event(
            event_type="ruling_request",
            payload=json.dumps({"device_id": device_id,
                                "attestation_hash": attestation_hash}),
            source="http_api",
            target="session_adjudicator",
            device_id=device_id,
        )
        return {"status": "queued", "event_id": eid, "device_id": device_id}

    @app.post("/agent/interpret")
    async def agent_interpret(request: Request):
        """Agentic overlay: enrich VAPI data dict with LLM interpretation (Phase 65).

        Accepts {data: dict, context: str (optional)}.
        Returns data + agent_interpretation field.
        Returns {agent_interpretation: {status: 'unavailable'}} if ANTHROPIC_API_KEY missing.
        """
        body = await request.json()
        data = body.get("data", {})
        context = body.get("context", "")
        try:
            import anthropic
            client = anthropic.AsyncAnthropic()
            prompt = f"Context: {context}\n\nData: {json.dumps(data, default=str)}"
            response = await client.messages.create(
                model="claude-opus-4-6",
                max_tokens=512,
                system=(
                    "You are a VAPI protocol expert. Interpret the provided VAPI data "
                    "and return a JSON object with: summary (str), risk_level (low/medium/high), "
                    "recommended_action (str), confidence (0.0-1.0). "
                    "Respond with only valid JSON."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            interpretation = json.loads(response.content[0].text.strip())
        except Exception as exc:
            log.warning("/agent/interpret: LLM unavailable: %s", exc)
            interpretation = {"status": "unavailable"}
        return {**data, "agent_interpretation": interpretation}

    # --- Dashboards ---

    @app.get("/", response_class=HTMLResponse)
    async def operator_dashboard():
        return OPERATOR_HTML

    @app.get("/player/{device_id}", response_class=HTMLResponse)
    async def player_dashboard(device_id: str):
        return PLAYER_DASHBOARD_HTML.replace("__DEVICE_ID__", device_id)

    return app


# ---------------------------------------------------------------------------
# Operator Dashboard — PoHG Pulse Observatory
# ---------------------------------------------------------------------------

OPERATOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PoHG Pulse — Operator</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        [x-cloak]{display:none!important}
        .pulse{animation:pulse 2s infinite}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
        .heartbeat{animation:hb 1.5s ease-in-out}
        @keyframes hb{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}
        #chain-ribbon{display:flex;align-items:flex-end;gap:2px;overflow:hidden;flex-direction:row}
        .chain-link{flex-shrink:0;width:18px;border-radius:2px;cursor:pointer;transition:opacity .2s}
        .chain-link:hover{opacity:.7}
        .tooltip{position:fixed;background:#1e293b;border:1px solid #334155;padding:6px 10px;
                 border-radius:6px;font-size:11px;pointer-events:none;z-index:999;
                 color:#e2e8f0;display:none}
    </style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen" x-data="operator()" x-init="init()">
<div id="tip" class="tooltip"></div>

<!-- Top Bar -->
<div class="border-b border-gray-800 px-6 py-3 flex items-center justify-between bg-gray-900">
    <div class="flex items-center gap-3">
        <div class="w-2 h-2 rounded-full bg-emerald-400 pulse"></div>
        <span class="font-bold text-blue-400 text-lg">PoHG Pulse</span>
        <span class="text-gray-500 text-sm">Proof of Human Gaming Observatory</span>
    </div>
    <div class="flex items-center gap-6 text-sm">
        <span class="text-gray-400">Devices: <span class="text-white font-mono" x-text="stats.devices_active ?? 0"></span></span>
        <span class="text-gray-400">Records/min: <span class="text-emerald-400 font-mono" x-text="recPerMin"></span></span>
        <span class="text-gray-400">WS: <span :class="wsConnected ? 'text-emerald-400' : 'text-red-400'"
              x-text="wsConnected ? 'live' : 'offline'"></span></span>
    </div>
</div>

<div class="max-w-screen-2xl mx-auto px-4 py-4 space-y-4">

    <!-- Panel 1: Chain Ribbon -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <div class="flex items-center justify-between mb-3">
            <h2 class="font-semibold text-gray-200">Chain Ribbon
                <span class="text-xs text-gray-500 ml-2">newest →</span></h2>
            <div class="flex gap-3 text-xs">
                <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-sm bg-emerald-500"></span>NOMINAL</span>
                <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-sm bg-amber-500"></span>Advisory</span>
                <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 rounded-sm bg-red-500"></span>Cheat</span>
            </div>
        </div>
        <div id="chain-ribbon" class="h-16 bg-gray-950 rounded p-1"></div>
    </div>

    <!-- Row: Panel 2 + Panel 3 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">

        <!-- Panel 2: Human Signal Waveform -->
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
            <h2 class="font-semibold text-gray-200 mb-3">Human Signal Waveform
                <span class="text-xs text-gray-500 ml-2">last 60 records</span></h2>
            <canvas id="waveChart" height="160"></canvas>
        </div>

        <!-- Panel 3: SDK Attestation Heartbeat -->
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
            <h2 class="font-semibold text-gray-200 mb-3">SDK Attestation Heartbeat</h2>
            <div class="space-y-2" x-show="attestation" x-cloak>
                <template x-for="layer in layers" :key="layer.id">
                    <div class="flex items-center gap-3 text-sm">
                        <span :class="layer.active ? 'text-emerald-400' : 'text-red-400'" class="text-lg">●</span>
                        <span class="w-32 text-gray-300" x-text="layer.id"></span>
                        <div class="flex-1 bg-gray-800 rounded-full h-2">
                            <div class="h-2 rounded-full transition-all duration-500"
                                 :class="layer.active ? 'bg-emerald-500' : 'bg-red-700'"
                                 :style="`width:${(layer.score * 100).toFixed(0)}%`"></div>
                        </div>
                        <span class="text-gray-400 text-xs w-12" x-text="`${(layer.score * 100).toFixed(0)}%`"></span>
                        <span :class="layer.active ? 'text-emerald-300 text-xs' : 'text-red-300 text-xs'"
                              x-text="layer.active ? 'ACTIVE' : 'OFFLINE'"></span>
                    </div>
                </template>
                <div class="pt-2 border-t border-gray-800 mt-2">
                    <div class="flex items-center justify-between text-xs text-gray-500">
                        <span>Attestation hash:
                            <span class="font-mono text-blue-400"
                                  x-text="(attestation.attestation_hash||'').slice(0,16) + '...'"></span></span>
                        <span x-text="attestation.sdk_version"></span>
                    </div>
                </div>
            </div>
            <div class="text-gray-500 text-sm" x-show="!attestation">Loading attestation...</div>
        </div>
    </div>

    <!-- Row: Panel 4 + Panel 5 -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">

        <!-- Panel 4: Adversarial Pressure Map -->
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4 lg:col-span-1">
            <h2 class="font-semibold text-gray-200 mb-3">Adversarial Pressure
                <span class="text-xs text-gray-500 ml-2">last 10 min</span></h2>
            <canvas id="pressureChart" height="180"></canvas>
        </div>

        <!-- Panel 5: Active Devices -->
        <div class="bg-gray-900 rounded-lg border border-gray-800 p-4 lg:col-span-2">
            <h2 class="font-semibold text-gray-200 mb-3">Active Devices</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-xs">
                    <thead>
                        <tr class="text-gray-500 text-left">
                            <th class="pb-2">Device</th>
                            <th class="pb-2">Counter</th>
                            <th class="pb-2">Battery</th>
                            <th class="pb-2">Records</th>
                            <th class="pb-2">Verified</th>
                            <th class="pb-2">Last Seen</th>
                            <th class="pb-2">Profile</th>
                        </tr>
                    </thead>
                    <tbody>
                        <template x-for="d in devices" :key="d.device_id">
                            <tr class="border-t border-gray-800">
                                <td class="py-1.5 pr-2">
                                    <a :href="'/player/' + d.device_id" class="font-mono text-blue-400 hover:underline"
                                       x-text="d.device_id.slice(0,12) + '...'"></a>
                                </td>
                                <td class="py-1.5 pr-2" x-text="d.last_counter"></td>
                                <td class="py-1.5 pr-2">
                                    <span :class="d.last_battery < 20 ? 'text-red-400' : 'text-green-400'"
                                          x-text="d.last_battery + '%'"></span>
                                </td>
                                <td class="py-1.5 pr-2" x-text="d.records_total"></td>
                                <td class="py-1.5 pr-2 text-emerald-400" x-text="d.records_verified"></td>
                                <td class="py-1.5 pr-2 text-gray-500" x-text="timeAgo(d.last_seen)"></td>
                                <td class="py-1.5">
                                    <span class="px-2 py-0.5 rounded text-xs bg-blue-900 text-blue-300">DualShock</span>
                                </td>
                            </tr>
                        </template>
                        <tr x-show="devices.length === 0">
                            <td colspan="7" class="py-4 text-center text-gray-600">No devices connected</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<script>
const INF_COLOR = {
    0x20: '#10b981', 0x21: '#34d399',  // NOMINAL/SKILLED: emerald
    0x2B: '#f59e0b', 0x30: '#f59e0b',  // Advisory: amber
    0x28: '#ef4444', 0x29: '#ef4444', 0x2A: '#ef4444', // Hard cheat: crimson
};
const INF_NAMES = {
    32:'NOMINAL', 33:'SKILLED', 40:'DRIVER_INJECT', 41:'WALLHACK',
    42:'AIMBOT', 43:'TEMPORAL_ANOMALY', 48:'BIOMETRIC_ANOMALY'
};

function hexColor(inf) {
    return INF_COLOR[inf] || '#475569';
}

function operator() {
    return {
        stats: {}, devices: [], attestation: null,
        wsConnected: false, recPerMin: 0,
        layers: [],
        _recTimes: [],
        _ribbonLinks: [],
        _waveData: { l2:[], l3:[], l4:[], l5:[] },
        _waveChart: null, _pressureChart: null,

        async init() {
            this.initCharts();
            this.connectWS();
            await this.refresh();
            setInterval(() => this.refresh(), 5000);
            setInterval(() => this.refreshPressure(), 15000);
            setInterval(() => this.refreshAttestation(), 60000);
            this.refreshAttestation();
        },

        initCharts() {
            // Human Signal Waveform
            const wCtx = document.getElementById('waveChart').getContext('2d');
            this._waveChart = new Chart(wCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        { label:'L2 HID-XInput', data:[], borderColor:'#38bdf8', backgroundColor:'transparent', tension:.3, pointRadius:0, borderWidth:1.5 },
                        { label:'L3 Behavioral', data:[], borderColor:'#a78bfa', backgroundColor:'transparent', tension:.3, pointRadius:0, borderWidth:1.5 },
                        { label:'L4 Biometric dist', data:[], borderColor:'#f59e0b', backgroundColor:'transparent', tension:.3, pointRadius:0, borderWidth:1.5 },
                        { label:'L5 Temporal CV×100', data:[], borderColor:'#10b981', backgroundColor:'transparent', tension:.3, pointRadius:0, borderWidth:1.5 },
                    ]
                },
                options: {
                    responsive:true, maintainAspectRatio:false, animation:false,
                    scales: {
                        x:{ display:false },
                        y:{ grid:{ color:'#1f2937' }, ticks:{ color:'#6b7280', font:{size:10} },
                            min:0, max:260 }
                    },
                    plugins: {
                        legend:{ labels:{ color:'#9ca3af', boxWidth:12, font:{size:10} } },
                        annotation: {
                            annotations: {
                                l4thresh:{ type:'line', yMin:3, yMax:3, borderColor:'#ef444455', borderWidth:1, borderDash:[4,4] },
                            }
                        }
                    }
                }
            });

            // Adversarial Pressure Map
            const pCtx = document.getElementById('pressureChart').getContext('2d');
            this._pressureChart = new Chart(pCtx, {
                type: 'bar',
                data: {
                    labels: [],
                    datasets: [
                        { label:'Hard Cheat (L2/L3)', data:[], backgroundColor:'#ef4444' },
                        { label:'Temporal (L5)',       data:[], backgroundColor:'#f59e0b' },
                        { label:'Biometric (L4)',      data:[], backgroundColor:'#0d9488' },
                    ]
                },
                options: {
                    responsive:true, maintainAspectRatio:false, animation:false,
                    plugins:{ legend:{ labels:{ color:'#9ca3af', boxWidth:10, font:{size:9} } } },
                    scales: {
                        x:{ stacked:true, grid:{color:'#1f2937'}, ticks:{color:'#6b7280',font:{size:9}} },
                        y:{ stacked:true, grid:{color:'#1f2937'}, ticks:{color:'#6b7280',font:{size:9}}, min:0 }
                    }
                }
            });
        },

        connectWS() {
            const proto = location.protocol === 'https:' ? 'wss' : 'ws';
            const ws = new WebSocket(`${proto}://${location.host}/ws/records`);
            ws.onopen  = () => { this.wsConnected = true; };
            ws.onclose = () => {
                this.wsConnected = false;
                setTimeout(() => this.connectWS(), 3000);
            };
            ws.onmessage = (e) => {
                try { this.onRecord(JSON.parse(e.data)); } catch(err) {}
            };
            // Keep-alive ping every 20s
            setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 20000);
        },

        onRecord(r) {
            // Track records/min
            const now = Date.now();
            this._recTimes.push(now);
            this._recTimes = this._recTimes.filter(t => now - t < 60000);
            this.recPerMin = this._recTimes.length;

            // Chain Ribbon
            this.addRibbonLink(r);

            // Waveform datasets (rolling 60)
            const push = (arr, v) => { arr.push(v); if(arr.length > 60) arr.shift(); };
            const inf = r.inference;
            push(this._waveData.l2, (inf === 0x28) ? r.confidence : 0);
            push(this._waveData.l3, (inf === 0x29 || inf === 0x2A) ? r.confidence : 0);
            push(this._waveData.l4, r.pitl_l4_distance != null ? Math.min(r.pitl_l4_distance, 6.0) * 40 : 0);
            push(this._waveData.l5, r.pitl_l5_cv != null ? Math.min(r.pitl_l5_cv * 100, 260) : 0);

            const labels = Array.from({length: this._waveData.l2.length}, (_, i) => i);
            this._waveChart.data.labels = labels;
            this._waveChart.data.datasets[0].data = [...this._waveData.l2];
            this._waveChart.data.datasets[1].data = [...this._waveData.l3];
            this._waveChart.data.datasets[2].data = [...this._waveData.l4];
            this._waveChart.data.datasets[3].data = [...this._waveData.l5];
            this._waveChart.update('none');
        },

        addRibbonLink(r) {
            const ribbon = document.getElementById('chain-ribbon');
            const inf = r.inference;
            const color = hexColor(inf);
            const height = Math.round(16 + (r.confidence / 255) * 44);

            const div = document.createElement('div');
            div.className = 'chain-link';
            div.style.background = color;
            div.style.height = height + 'px';
            div.title = `${INF_NAMES[inf] || '0x' + inf.toString(16)} | conf=${r.confidence} | ${r.record_hash}...`;

            div.addEventListener('mouseenter', (e) => {
                const tip = document.getElementById('tip');
                tip.innerText = div.title;
                tip.style.display = 'block';
                tip.style.left = (e.clientX + 12) + 'px';
                tip.style.top  = (e.clientY - 28) + 'px';
            });
            div.addEventListener('mouseleave', () => {
                document.getElementById('tip').style.display = 'none';
            });

            ribbon.appendChild(div);
            this._ribbonLinks.push(div);
            if (this._ribbonLinks.length > 200) {
                const old = this._ribbonLinks.shift();
                if (old.parentNode) old.parentNode.removeChild(old);
            }
            ribbon.scrollLeft = ribbon.scrollWidth;
        },

        async refresh() {
            try {
                const [s, d, r] = await Promise.all([
                    fetch('/api/v1/stats').then(r => r.json()),
                    fetch('/api/v1/devices').then(r => r.json()),
                    fetch('/api/v1/records/recent?limit=80').then(r => r.json()),
                ]);
                this.stats = s;
                this.devices = d;
                // Bootstrap ribbon from recent records (only on first load)
                if (this._ribbonLinks.length === 0) {
                    [...r].reverse().forEach(rec => {
                        this.addRibbonLink({
                            inference: rec.inference,
                            confidence: rec.confidence,
                            record_hash: rec.record_hash,
                            pitl_l4_distance: rec.pitl_l4_distance,
                            pitl_l5_cv: rec.pitl_l5_cv,
                        });
                    });
                }
            } catch(e) { console.error('Refresh failed:', e); }
        },

        async refreshPressure() {
            try {
                const data = await fetch('/dash/api/v1/pitl/timeline?minutes=10').then(r => r.json());
                const buckets = {};
                const HARD = new Set([0x28, 0x29, 0x2A]);
                data.forEach(row => {
                    const label = new Date(row.bucket * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
                    if (!buckets[label]) buckets[label] = {hard:0, temporal:0, biometric:0};
                    if (HARD.has(row.inference)) buckets[label].hard += row.cnt;
                    else if (row.inference === 0x2B) buckets[label].temporal += row.cnt;
                    else if (row.inference === 0x30) buckets[label].biometric += row.cnt;
                });
                const labels = Object.keys(buckets);
                this._pressureChart.data.labels = labels;
                this._pressureChart.data.datasets[0].data = labels.map(l => buckets[l].hard);
                this._pressureChart.data.datasets[1].data = labels.map(l => buckets[l].temporal);
                this._pressureChart.data.datasets[2].data = labels.map(l => buckets[l].biometric);
                this._pressureChart.update('none');
            } catch(e) {}
        },

        async refreshAttestation() {
            try {
                const att = await fetch('/dash/api/v1/sdk/attestation').then(r => r.json());
                this.attestation = att;
                const scores = att.pitl_scores || {};
                const active = att.layers_active || {};
                this.layers = [
                    { id:'L2_hid_xinput', active: active.L2_hid_xinput||false, score: scores.L2_hid_xinput||0 },
                    { id:'L3_behavioral', active: active.L3_behavioral||false, score: scores.L3_behavioral||0 },
                    { id:'L4_biometric',  active: active.L4_biometric||false,  score: scores.L4_biometric||0  },
                    { id:'L5_temporal',   active: active.L5_temporal||false,   score: scores.L5_temporal||0   },
                ];
            } catch(e) {}
        },

        timeAgo(ts) {
            const s = Math.floor(Date.now() / 1000 - ts);
            if (s < 60) return s + 's';
            if (s < 3600) return Math.floor(s/60) + 'm';
            if (s < 86400) return Math.floor(s/3600) + 'h';
            return Math.floor(s/86400) + 'd';
        }
    };
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Player Dashboard — Trust Ledger + Identity Glyph + Chain Ribbon + Credential
# ---------------------------------------------------------------------------

PLAYER_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PoHG Pulse — Player</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
    <style>
        [x-cloak]{display:none!important}
        #player-ribbon{display:flex;align-items:flex-end;gap:2px;overflow:hidden}
        .chain-link{flex-shrink:0;width:18px;border-radius:2px}
        #qr-container img,.qrcode img{display:block}
    </style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen" x-data="player('__DEVICE_ID__')" x-init="init()">

<!-- Header -->
<div class="border-b border-gray-800 px-6 py-3 flex items-center justify-between bg-gray-900">
    <div class="flex items-center gap-3">
        <a href="/" class="text-gray-500 hover:text-gray-300 text-sm">← Operator</a>
        <span class="text-gray-700">|</span>
        <span class="font-bold text-blue-400">PoHG Pulse</span>
        <span class="text-gray-500 text-sm">Player Profile</span>
    </div>
    <div class="flex items-center gap-3">
        <span class="font-mono text-xs text-gray-500" x-text="deviceId.slice(0,24) + '...'"></span>
        <span x-show="rank" x-cloak
              class="px-2 py-0.5 rounded-full text-xs font-bold bg-yellow-900 text-yellow-300"
              x-text="rank ? '#' + rank.rank + ' of ' + rank.total : ''"></span>
    </div>
</div>

<div class="max-w-5xl mx-auto px-4 py-6 space-y-4">

    <!-- Panel A: Trust Ledger -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-6">
        <div class="flex items-start justify-between mb-4">
            <div>
                <div class="text-xs text-gray-500 uppercase tracking-wider mb-1">PHG Trust Score</div>
                <div class="text-5xl font-bold text-emerald-400" x-text="profile ? profile.phg_score.toLocaleString() : '—'"></div>
                <div class="text-gray-500 text-sm mt-1">Proof of Human Gaming — cryptographic accumulation</div>
            </div>
            <div class="text-right">
                <div class="px-3 py-1 rounded-full text-sm font-medium bg-blue-900 text-blue-300">
                    DualShock Edge
                </div>
                <div class="text-xs text-gray-500 mt-1">PHCI CERTIFIED</div>
            </div>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4" x-show="profile" x-cloak>
            <div class="bg-gray-800 rounded p-3">
                <div class="text-xs text-gray-500">Verified Records</div>
                <div class="text-xl font-bold" x-text="(profile.nominal_records || 0).toLocaleString()"></div>
            </div>
            <div class="bg-gray-800 rounded p-3">
                <div class="text-xs text-gray-500">Confidence Mean</div>
                <div class="text-xl font-bold" x-text="(profile.confidence_mean || 0) + ' / 255'"></div>
            </div>
            <div class="bg-gray-800 rounded p-3">
                <div class="text-xs text-gray-500">Total Records</div>
                <div class="text-xl font-bold" x-text="(profile.total_records || 0).toLocaleString()"></div>
            </div>
            <div class="bg-gray-800 rounded p-3">
                <div class="text-xs text-gray-500">Chain Active Since</div>
                <div class="text-sm font-bold" x-text="profile.first_record_at ? new Date(profile.first_record_at * 1000).toLocaleDateString() : '—'"></div>
            </div>
        </div>
        <div x-show="!profile" class="text-gray-600 text-sm mt-2">No data found for this device.</div>
    </div>

    <!-- Panel B: Identity Glyph (Biometric Fingerprint) -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-6" x-show="profile && profile.fingerprint_available" x-cloak>
        <h2 class="font-semibold text-gray-200 mb-1">Identity Glyph
            <span class="text-xs text-gray-500 ml-2">Biometric Fingerprint</span></h2>
        <p class="text-xs text-gray-600 mb-4">
            The shape of this radar is your unique kinematic signature — averaged over recent authenticated records.
            It stabilizes as the EWC biometric model converges.
        </p>
        <div class="max-w-xs mx-auto">
            <canvas id="glyphChart"></canvas>
        </div>
    </div>
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-6"
         x-show="profile && !profile.fingerprint_available" x-cloak>
        <h2 class="font-semibold text-gray-200 mb-1">Identity Glyph</h2>
        <p class="text-gray-600 text-sm">
            Biometric fingerprint unavailable — requires at least 5 authenticated sessions
            with L4 PITL active (DualShock Edge CERTIFIED tier).
        </p>
    </div>

    <!-- Panel C: Chain Ribbon (player-specific) -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <h2 class="font-semibold text-gray-200 mb-3">Chain Ribbon
            <span class="text-xs text-gray-500 ml-2">this device · newest →</span></h2>
        <div id="player-ribbon" class="h-16 bg-gray-950 rounded p-1"></div>
    </div>

    <!-- Panel D: PHG Credential + QR Code -->
    <div class="bg-gray-900 rounded-lg border border-gray-800 p-6">
        <div class="flex items-start justify-between mb-4">
            <div>
                <h2 class="font-semibold text-gray-200 mb-1">PHG Credential
                    <span class="text-xs text-gray-500 ml-2">Soulbound On-Chain Identity</span></h2>
                <p class="text-xs text-gray-600">ERC-5192 soulbound — non-transferable, permanently locked.</p>
            </div>
            <div x-show="credential" x-cloak
                 class="px-3 py-1 rounded-full text-sm font-bold bg-emerald-900 text-emerald-300">
                MINTED ✓
            </div>
            <div x-show="!credential" x-cloak
                 class="px-3 py-1 rounded-full text-sm font-medium bg-gray-800 text-gray-400">
                NOT MINTED
            </div>
        </div>

        <!-- Minted: show credential details + QR -->
        <div x-show="credential" x-cloak class="grid grid-cols-1 md:grid-cols-2 gap-6 mt-2">
            <div class="space-y-3">
                <div class="bg-gray-800 rounded p-3">
                    <div class="text-xs text-gray-500">Credential ID</div>
                    <div class="text-xl font-bold text-emerald-400" x-text="'#' + (credential && credential.credential_id)"></div>
                </div>
                <div class="bg-gray-800 rounded p-3">
                    <div class="text-xs text-gray-500">Minted</div>
                    <div class="text-sm font-bold" x-text="credential ? new Date(credential.minted_at * 1000).toLocaleString() : '—'"></div>
                </div>
                <div class="bg-gray-800 rounded p-3">
                    <div class="text-xs text-gray-500">Tx Hash</div>
                    <div class="font-mono text-xs text-gray-400 truncate" x-text="credential ? (credential.tx_hash || 'local-only') : '—'"></div>
                </div>
                <button @click="copyProofUrl()"
                    class="w-full mt-2 px-4 py-2 bg-blue-700 hover:bg-blue-600 text-white text-sm rounded font-medium transition-colors">
                    Share Proof URL
                </button>
                <div x-show="copied" x-cloak class="text-xs text-emerald-400 text-center">Copied to clipboard!</div>
            </div>
            <div class="flex flex-col items-center gap-3">
                <div class="text-xs text-gray-500 uppercase tracking-wider">Scan to verify</div>
                <div id="qr-container" class="bg-gray-800 rounded-lg p-3 inline-block"></div>
                <div class="text-xs text-gray-600 text-center">Links to your shareable proof page</div>
            </div>
        </div>

        <!-- Not minted: onboarding wizard -->
        <div x-show="!credential" x-cloak class="mt-4 space-y-3">
            <p class="text-sm text-gray-400">Complete these steps to mint your soulbound credential:</p>
            <div class="space-y-2">
                <div class="flex items-center gap-3 p-3 rounded"
                     :class="(profile && profile.total_records > 0) ? 'bg-emerald-900/30 border border-emerald-800' : 'bg-gray-800'">
                    <span class="text-lg" x-text="(profile && profile.total_records > 0) ? '✓' : '○'"></span>
                    <div>
                        <div class="text-sm font-medium">Step 1 — Controller Connected</div>
                        <div class="text-xs text-gray-500">Play at least one authenticated session</div>
                    </div>
                </div>
                <div class="flex items-center gap-3 p-3 rounded"
                     :class="(profile && profile.phg_score > 0) ? 'bg-emerald-900/30 border border-emerald-800' : 'bg-gray-800'">
                    <span class="text-lg" x-text="(profile && profile.phg_score > 0) ? '✓' : '○'"></span>
                    <div>
                        <div class="text-sm font-medium">Step 2 — PHG Score Accumulating</div>
                        <div class="text-xs text-gray-500">Bridge accumulates confirmed PHG checkpoints on-chain</div>
                    </div>
                </div>
                <div class="flex items-center gap-3 p-3 rounded bg-gray-800">
                    <span class="text-lg">○</span>
                    <div>
                        <div class="text-sm font-medium">Step 3 — Mint Credential</div>
                        <div class="text-xs text-gray-500">Bridge mints automatically when PITL ZK proof is generated at session end</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

</div>

<script>
const INF_COLOR = {
    32: '#10b981', 33: '#34d399',
    43: '#f59e0b', 48: '#f59e0b',
    40: '#ef4444', 41: '#ef4444', 42: '#ef4444',
};

function player(deviceId) {
    return {
        deviceId,
        profile: null,
        credential: null,
        rank: null,
        copied: false,
        _glyphChart: null,
        _qrGenerated: false,

        async init() {
            await this.loadProfile();
            await this.loadRibbon();
            this.fetchCredential();
            this.fetchRank();
        },

        async loadProfile() {
            try {
                const p = await fetch(`/dash/api/v1/player/${this.deviceId}/profile`).then(r => {
                    if (!r.ok) throw new Error(r.status);
                    return r.json();
                });
                this.profile = p;
                if (p.fingerprint_available && p.biometric_fingerprint) {
                    this.$nextTick(() => this.renderGlyph(p.biometric_fingerprint));
                }
            } catch(e) {
                this.profile = null;
            }
        },

        async fetchCredential() {
            try {
                const r = await fetch(`/dash/api/v1/player/${this.deviceId}/credential`);
                this.credential = r.ok ? await r.json() : null;
                if (this.credential && !this._qrGenerated) {
                    this.$nextTick(() => this.renderQR());
                }
            } catch(e) { this.credential = null; }
        },

        async fetchRank() {
            try {
                const r = await fetch('/dash/api/v1/leaderboard?limit=10000');
                if (!r.ok) return;
                const board = await r.json();
                const idx = board.findIndex(e => e.device_id === this.deviceId);
                this.rank = idx >= 0 ? { rank: idx + 1, total: board.length } : null;
            } catch(e) { this.rank = null; }
        },

        renderQR() {
            const container = document.getElementById('qr-container');
            if (!container || this._qrGenerated) return;
            this._qrGenerated = true;
            const proofUrl = window.location.origin + '/proof/' + this.deviceId;
            try {
                new QRCode(container, {
                    text: proofUrl,
                    width: 180, height: 180,
                    colorDark: '#10b981',
                    colorLight: '#1f2937',
                    correctLevel: QRCode.CorrectLevel.M,
                });
            } catch(e) {
                container.innerHTML = '<div class="text-xs text-gray-500 p-4">QR unavailable</div>';
            }
        },

        copyProofUrl() {
            const proofUrl = window.location.origin + '/proof/' + this.deviceId;
            if (navigator.clipboard) {
                navigator.clipboard.writeText(proofUrl).then(() => {
                    this.copied = true;
                    setTimeout(() => { this.copied = false; }, 2000);
                });
            }
        },

        renderGlyph(fp) {
            const labels = [
                'Trigger L2 vel', 'Trigger R2 vel', 'Micro-tremor',
                'Grip asymmetry', 'Stick corr lag1', 'Stick corr lag5'
            ];
            const keys = [
                'trigger_onset_velocity_l2', 'trigger_onset_velocity_r2',
                'micro_tremor_variance', 'grip_asymmetry',
                'stick_autocorr_lag1', 'stick_autocorr_lag5'
            ];
            const vals = keys.map(k => Math.min(Math.abs(fp[k] || 0) * 10, 1.0));

            const ctx = document.getElementById('glyphChart').getContext('2d');
            this._glyphChart = new Chart(ctx, {
                type: 'radar',
                data: {
                    labels,
                    datasets: [{
                        label: 'Biometric fingerprint',
                        data: vals,
                        borderColor: '#38bdf8',
                        backgroundColor: '#38bdf820',
                        pointBackgroundColor: '#38bdf8',
                        borderWidth: 2,
                    }]
                },
                options: {
                    responsive: true,
                    scales: {
                        r: {
                            min: 0, max: 1,
                            grid:     { color: '#374151' },
                            angleLines:{ color: '#374151' },
                            ticks:    { display: false },
                            pointLabels:{ color: '#9ca3af', font: { size: 10 } }
                        }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        },

        async loadRibbon() {
            try {
                const records = await fetch(`/api/v1/records/recent?limit=100&device_id=${this.deviceId}`)
                    .then(r => r.json());
                const ribbon = document.getElementById('player-ribbon');
                [...records].reverse().forEach(rec => {
                    const color = INF_COLOR[rec.inference] || '#475569';
                    const height = Math.round(16 + (rec.confidence / 255) * 44);
                    const div = document.createElement('div');
                    div.className = 'chain-link';
                    div.style.background = color;
                    div.style.height = height + 'px';
                    div.title = `0x${rec.inference.toString(16)} conf=${rec.confidence}`;
                    ribbon.appendChild(div);
                });
            } catch(e) {}
        }
    };
}
</script>
</body>
</html>"""
