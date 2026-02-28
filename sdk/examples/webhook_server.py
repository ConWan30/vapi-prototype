"""
VAPI SDK — Webhook Server Example
FastAPI server that receives VAPI cheat-detection webhooks and acts on them.

Demonstrates:
1. HMAC-SHA256 signature verification on incoming webhooks
2. Parsing the CheatDetected event payload
3. Real-time game-server response (kick player, flag match, update ban list)
4. Idempotency guard (dedup by record_hash)

Run:
    pip install fastapi uvicorn
    VAPI_WEBHOOK_SECRET=your_secret_here uvicorn sdk.examples.webhook_server:app --port 9000

Register webhook URL in VAPI dashboard: https://your-server.com/vapi/webhook
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

log = logging.getLogger("vapi.webhook")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="VAPI Webhook Receiver", version="1.0.0-phase20")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WEBHOOK_SECRET: str = os.environ.get("VAPI_WEBHOOK_SECRET", "change-me-in-production")
VAPI_BRIDGE_URL: str = os.environ.get("VAPI_BRIDGE_URL", "http://localhost:8080/v1")

# In-memory dedup set — replace with Redis/DB in production
_processed_record_hashes: set[str] = set()

# Simulated ban list — replace with your game server's player management
_flagged_devices: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Inference code registry (mirrors vapi_sdk.py INFERENCE_NAMES)
# ---------------------------------------------------------------------------

INFERENCE_NAMES: dict[int, str] = {
    0x20: "NOMINAL",
    0x28: "DRIVER_INJECT",
    0x29: "WALLHACK_PREAIM",
    0x2A: "AIMBOT_BEHAVIORAL",
    0x2B: "TEMPORAL_ANOMALY",
    0x30: "BIOMETRIC_ANOMALY",
}

HARD_CHEATS: set[int] = {0x28, 0x29, 0x2A}
ADVISORY_CODES: set[int] = {0x2B, 0x30}


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 X-VAPI-Signature header."""
    expected = hmac.new(
        key=secret.encode(),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    # Constant-time comparison prevents timing attacks
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------

@app.post("/vapi/webhook", status_code=status.HTTP_200_OK)
async def receive_webhook(request: Request) -> JSONResponse:
    """
    Receive a VAPI cheat-detection webhook.

    Expected headers:
      X-VAPI-Signature: <hmac-sha256-hex>
      Content-Type: application/json

    Expected body (see openapi.yaml webhooks.CheatDetected):
    {
        "event":            "cheat_detected",
        "session_id":       "sess_abc",
        "device_id":        "0xabc...",
        "record_hash":      "deadbeef...",
        "inference_result": 40,
        "inference_name":   "DRIVER_INJECT",
        "confidence":       210,
        "timestamp_ms":     1700000000000
    }
    """
    raw_body = await request.body()

    # 1. Verify signature
    sig = request.headers.get("X-VAPI-Signature", "")
    if not sig or not verify_signature(raw_body, sig, WEBHOOK_SECRET):
        log.warning("Webhook signature verification failed — possible replay/forgery")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 2. Parse payload
    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event")
    if event != "cheat_detected":
        # Unknown event type — accept and ignore (forward-compat)
        return JSONResponse({"status": "ignored", "event": event})

    record_hash     = payload.get("record_hash", "")
    device_id       = payload.get("device_id", "")
    session_id      = payload.get("session_id", "")
    inference_code  = payload.get("inference_result", 0)
    inference_name  = payload.get("inference_name", INFERENCE_NAMES.get(inference_code, "UNKNOWN"))
    confidence      = payload.get("confidence", 0)
    timestamp_ms    = payload.get("timestamp_ms", 0)

    # 3. Idempotency guard
    if record_hash in _processed_record_hashes:
        log.info(f"Duplicate webhook for record_hash={record_hash[:16]}... — skipped")
        return JSONResponse({"status": "duplicate", "record_hash": record_hash})
    _processed_record_hashes.add(record_hash)

    log.info(
        f"CHEAT DETECTED | device={device_id[:16]}... | "
        f"inference={inference_name}(0x{inference_code:02X}) | "
        f"confidence={confidence} | session={session_id}"
    )

    # 4. Dispatch to game server response
    action = await handle_cheat_detection(
        device_id=device_id,
        session_id=session_id,
        inference_code=inference_code,
        inference_name=inference_name,
        confidence=confidence,
        record_hash=record_hash,
        timestamp_ms=timestamp_ms,
    )

    return JSONResponse({
        "status":       "ok",
        "record_hash":  record_hash,
        "action_taken": action,
    })


# ---------------------------------------------------------------------------
# Game-server response logic
# ---------------------------------------------------------------------------

async def handle_cheat_detection(
    device_id: str,
    session_id: str,
    inference_code: int,
    inference_name: str,
    confidence: int,
    record_hash: str,
    timestamp_ms: int,
) -> str:
    """
    Implement your game-server anti-cheat response here.

    Escalation policy:
    - DRIVER_INJECT (L2): immediate kick + flag for review
    - WALLHACK/AIMBOT (L3): immediate kick + flag if confidence >= 200
    - TEMPORAL_ANOMALY (L5): flag for review (advisory)
    - BIOMETRIC_ANOMALY (L4): flag for review (advisory)
    """
    if device_id not in _flagged_devices:
        _flagged_devices[device_id] = {
            "device_id":       device_id,
            "first_flag_at":   datetime.utcnow().isoformat(),
            "detections":      [],
            "kick_count":      0,
        }

    entry = _flagged_devices[device_id]
    entry["detections"].append({
        "inference_name": inference_name,
        "confidence":     confidence,
        "record_hash":    record_hash,
        "timestamp_ms":   timestamp_ms,
    })

    # Hard cheat response
    if inference_code in HARD_CHEATS:
        if confidence >= 180:
            entry["kick_count"] += 1
            action = f"KICKED | reason={inference_name} conf={confidence}"
            log.warning(f"[KICK] device={device_id[:16]} | {action}")
            await kick_player(device_id, session_id, inference_name)

            # Escalate to ban on 3rd hard cheat
            if entry["kick_count"] >= 3:
                action += " | BANNED (3 kicks)"
                await ban_device(device_id, inference_name)
        else:
            action = f"FLAGGED | reason={inference_name} conf={confidence} (below 180 threshold)"
        return action

    # Advisory response
    if inference_code in ADVISORY_CODES:
        action = f"ADVISORY_FLAGGED | reason={inference_name}"
        log.info(f"[ADVISORY] device={device_id[:16]} | {action}")
        return action

    return "NO_ACTION"


async def kick_player(device_id: str, session_id: str, reason: str) -> None:
    """
    Implement: send kick command to your game server.
    Example: POST to your game server's admin API.
    """
    log.info(f"[KICK_IMPL] Would kick device={device_id[:16]} from session={session_id} reason={reason}")
    # TODO: call your game server kick endpoint
    # await game_server.kick(device_id=device_id, reason=reason)


async def ban_device(device_id: str, reason: str) -> None:
    """
    Implement: add device to platform ban list.
    Example: write to your ban database or call your platform API.
    """
    log.warning(f"[BAN_IMPL] Would ban device={device_id[:16]} reason={reason}")
    # TODO: write to ban database
    # await ban_db.insert(device_id=device_id, reason=reason)


# ---------------------------------------------------------------------------
# Health + stats endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "sdk_version": "1.0.0-phase20"}


@app.get("/stats")
async def stats():
    """Return webhook processing statistics."""
    return {
        "processed_records":  len(_processed_record_hashes),
        "flagged_devices":    len(_flagged_devices),
        "total_detections":   sum(len(v["detections"]) for v in _flagged_devices.values()),
        "total_kicks":        sum(v["kick_count"] for v in _flagged_devices.values()),
    }


@app.get("/flagged")
async def flagged_devices():
    """Return all flagged device entries (admin endpoint — protect with auth in production)."""
    return {"devices": list(_flagged_devices.values())}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 9000))
    log.info(f"Starting VAPI webhook server on port {port}")
    uvicorn.run("webhook_server:app", host="0.0.0.0", port=port, reload=True)
