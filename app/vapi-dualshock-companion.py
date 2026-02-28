#!/usr/bin/env python3
"""
VAPI DualShock Companion App — PC/Mobile Backend

FastAPI application that serves as the human interface for the VAPI DualShock
anti-cheat controller. Connects to the controller via BLE, displays live PoAC
chains, manages gaming bounties, and provides development tools.

Architecture alignment with Pebble VAPI:
  - Reuses vapi_bridge.codec for PoAC record parsing (same 228-byte format)
  - Reuses vapi_bridge.chain for IoTeX on-chain submission
  - Reuses vapi_bridge.store for SQLite persistence
  - Same bridge relay protocol (MQTT/HTTP to existing VAPI bridge service)

Gaming-specific additions:
  - BLE transport (bleak library) for controller pairing
  - Real-time anti-cheat dashboard via WebSocket
  - Gaming bounty marketplace UI
  - Firmware flasher (esptool wrapper)
  - TinyML model management (upload, deploy to controller via BLE OTA)

Usage:
    pip install fastapi uvicorn bleak websockets aiosqlite pydantic cryptography
    python vapi-dualshock-companion.py

    # Or with uvicorn directly:
    uvicorn vapi-dualshock-companion:app --host 0.0.0.0 --port 8080 --reload
"""

import asyncio
import hashlib
import json
import logging
import os
import struct
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ─── Conditional imports (graceful degradation) ───────────────────
try:
    from bleak import BleakClient, BleakScanner
    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False
    logging.warning("bleak not installed — BLE features disabled")

try:
    import aiosqlite
    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False
    logging.warning("aiosqlite not installed — persistence disabled")

try:
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from cryptography.hazmat.primitives import hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logging.warning("cryptography not installed — signature verification disabled")

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    logging.warning("httpx not installed — bridge integration disabled")


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vapi-companion")

# ── Bridge integration config (Phase 31) ──
BRIDGE_URL       = os.environ.get("BRIDGE_URL", "http://localhost:8080")
BRIDGE_DEVICE_ID = os.environ.get("BRIDGE_DEVICE_ID", "")
BRIDGE_API_KEY   = os.environ.get("BRIDGE_API_KEY", "")


# ══════════════════════════════════════════════════════════════════
# Constants — aligned with firmware dualshock_agent.h and poac.h
# ══════════════════════════════════════════════════════════════════

POAC_HASH_SIZE = 32
POAC_SIG_SIZE = 64
POAC_BODY_SIZE = 164
POAC_RECORD_SIZE = POAC_BODY_SIZE + POAC_SIG_SIZE  # 228 bytes

# BLE VAPI Service UUIDs (custom 128-bit based on 0xVA0x)
BLE_SERVICE_UUID = "0000fa00-0000-1000-8000-00805f9b34fb"
BLE_CHAR_POAC = "0000fa01-0000-1000-8000-00805f9b34fb"
BLE_CHAR_INPUT = "0000fa02-0000-1000-8000-00805f9b34fb"
BLE_CHAR_STATUS = "0000fa03-0000-1000-8000-00805f9b34fb"
BLE_CHAR_STATE = "0000fa04-0000-1000-8000-00805f9b34fb"
BLE_CHAR_COMMAND = "0000fa05-0000-1000-8000-00805f9b34fb"
BLE_CHAR_CONFIG = "0000fa06-0000-1000-8000-00805f9b34fb"
BLE_CHAR_OTA = "0000fa07-0000-1000-8000-00805f9b34fb"
BLE_CHAR_WORLDMODEL = "0000fa08-0000-1000-8000-00805f9b34fb"

# Gaming inference result codes (from dualshock_agent.h)
INFER_PLAY_NOMINAL = 0x20
INFER_PLAY_SKILLED = 0x21
INFER_CHEAT_REACTION = 0x22
INFER_CHEAT_MACRO = 0x23
INFER_CHEAT_AIMBOT = 0x24
INFER_CHEAT_RECOIL = 0x25
INFER_CHEAT_IMU_MISS = 0x26
INFER_CHEAT_INJECTION = 0x27

INFER_NAMES = {
    0x20: "NOMINAL", 0x21: "SKILLED",
    0x22: "CHEAT:REACTION", 0x23: "CHEAT:MACRO",
    0x24: "CHEAT:AIMBOT", 0x25: "CHEAT:RECOIL",
    0x26: "CHEAT:IMU_MISMATCH", 0x27: "CHEAT:INJECTION",
    0x28: "SKILL:COMBO", 0x29: "SKILL:SPEEDRUN",
}

# Gaming action codes
ACTION_SESSION_START = 0x10
ACTION_SESSION_END = 0x11
ACTION_CHEAT_ALERT = 0x12
ACTION_NAMES = {
    0x01: "REPORT", 0x09: "BOOT",
    0x10: "SESSION_START", 0x11: "SESSION_END",
    0x12: "CHEAT_ALERT", 0x13: "SKILL_PROOF",
    0x14: "TOURNAMENT_FRAME", 0x15: "CALIBRATION",
}


# ══════════════════════════════════════════════════════════════════
# PoAC Record Parser
# Reuses the same wire format as Pebble's vapi_bridge.codec.
# 228 bytes: 164-byte body + 64-byte ECDSA-P256 signature.
# ══════════════════════════════════════════════════════════════════

@dataclass
class PoACRecord:
    """Parsed PoAC record — identical format for both Pebble and DualShock."""
    prev_poac_hash: bytes       # 32 B
    sensor_commitment: bytes    # 32 B
    model_manifest_hash: bytes  # 32 B
    world_model_hash: bytes     # 32 B
    inference_result: int       # 1 B
    action_code: int            # 1 B
    confidence: int             # 1 B
    battery_pct: int            # 1 B
    monotonic_ctr: int          # 4 B uint32 BE
    timestamp_ms: int           # 8 B int64 BE
    latitude: float             # 8 B double BE
    longitude: float            # 8 B double BE
    bounty_id: int              # 4 B uint32 BE
    signature: bytes            # 64 B
    raw_body: bytes = b""       # Original 164-byte body for verification
    record_hash: bytes = b""    # SHA-256 of full 228-byte record

    @classmethod
    def from_bytes(cls, data: bytes) -> "PoACRecord":
        """Parse a 228-byte PoAC record. Same format as Pebble VAPI."""
        if len(data) != POAC_RECORD_SIZE:
            raise ValueError(f"Expected {POAC_RECORD_SIZE} bytes, got {len(data)}")

        body = data[:POAC_BODY_SIZE]
        sig = data[POAC_BODY_SIZE:]

        # Unpack body fields (big-endian, matching firmware serialization)
        offset = 0
        prev_hash = body[offset:offset + 32]; offset += 32
        sensor_commit = body[offset:offset + 32]; offset += 32
        model_hash = body[offset:offset + 32]; offset += 32
        wm_hash = body[offset:offset + 32]; offset += 32

        inference_result = body[offset]; offset += 1
        action_code = body[offset]; offset += 1
        confidence = body[offset]; offset += 1
        battery_pct = body[offset]; offset += 1

        monotonic_ctr = struct.unpack(">I", body[offset:offset + 4])[0]; offset += 4
        timestamp_ms = struct.unpack(">q", body[offset:offset + 8])[0]; offset += 8
        latitude = struct.unpack(">d", body[offset:offset + 8])[0]; offset += 8
        longitude = struct.unpack(">d", body[offset:offset + 8])[0]; offset += 8
        bounty_id = struct.unpack(">I", body[offset:offset + 4])[0]; offset += 4

        record_hash = hashlib.sha256(data).digest()

        return cls(
            prev_poac_hash=prev_hash,
            sensor_commitment=sensor_commit,
            model_manifest_hash=model_hash,
            world_model_hash=wm_hash,
            inference_result=inference_result,
            action_code=action_code,
            confidence=confidence,
            battery_pct=battery_pct,
            monotonic_ctr=monotonic_ctr,
            timestamp_ms=timestamp_ms,
            latitude=latitude,
            longitude=longitude,
            bounty_id=bounty_id,
            signature=sig,
            raw_body=body,
            record_hash=record_hash,
        )

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict for WebSocket/API responses."""
        return {
            "record_hash": self.record_hash.hex(),
            "prev_poac_hash": self.prev_poac_hash.hex(),
            "sensor_commitment": self.sensor_commitment.hex(),
            "model_manifest_hash": self.model_manifest_hash.hex(),
            "world_model_hash": self.world_model_hash.hex(),
            "inference_result": self.inference_result,
            "inference_name": INFER_NAMES.get(self.inference_result, f"0x{self.inference_result:02x}"),
            "action_code": self.action_code,
            "action_name": ACTION_NAMES.get(self.action_code, f"0x{self.action_code:02x}"),
            "confidence": self.confidence,
            "confidence_pct": round(self.confidence / 255 * 100, 1),
            "battery_pct": self.battery_pct,
            "monotonic_ctr": self.monotonic_ctr,
            "timestamp_ms": self.timestamp_ms,
            "bounty_id": self.bounty_id,
            "is_cheat": self.inference_result >= INFER_CHEAT_REACTION,
            "signature": self.signature.hex(),
        }

    def verify_chain_link(self, previous: "PoACRecord") -> bool:
        """Verify hash-chain linkage to a predecessor record."""
        expected = hashlib.sha256(
            previous.raw_body + previous.signature
        ).digest()
        return self.prev_poac_hash == expected

    def verify_signature(self, pubkey_bytes: bytes) -> bool:
        """Verify ECDSA-P256 signature. Same as Pebble bridge codec."""
        if not HAS_CRYPTO:
            logger.warning("cryptography not available — skipping sig check")
            return True

        try:
            pubkey = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), pubkey_bytes
            )
            r = int.from_bytes(self.signature[:32], "big")
            s = int.from_bytes(self.signature[32:], "big")
            der_sig = utils.encode_dss_signature(r, s)
            pubkey.verify(der_sig, self.raw_body, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception as e:
            logger.warning(f"Signature verification failed: {e}")
            return False


# ══════════════════════════════════════════════════════════════════
# BLE Controller Manager
# ══════════════════════════════════════════════════════════════════

class ControllerManager:
    """Manages BLE connection to a VAPI DualShock controller."""

    def __init__(self):
        self.client: Optional["BleakClient"] = None
        self.address: Optional[str] = None
        self.connected = False
        self.poac_chain: list[PoACRecord] = []
        self.device_pubkey: Optional[bytes] = None
        self.session_active = False
        self.tournament_mode = False
        self._ws_clients: set[WebSocket] = set()
        self._stats = {
            "total_records": 0,
            "clean_records": 0,
            "cheat_detections": 0,
            "session_count": 0,
            "current_battery": 0,
        }

    async def scan(self, timeout: float = 10.0) -> list[dict]:
        """Scan for VAPI DualShock controllers."""
        if not HAS_BLEAK:
            raise HTTPException(503, "BLE not available (install bleak)")

        devices = []
        scanner = BleakScanner()
        found = await scanner.discover(timeout=timeout)
        for d in found:
            # Filter for VAPI service UUID in advertisement
            if d.name and ("VAPI" in d.name.upper() or "DualShock" in d.name):
                devices.append({
                    "address": d.address,
                    "name": d.name,
                    "rssi": d.rssi,
                })
        return devices

    async def connect(self, address: str) -> bool:
        """Connect to a VAPI DualShock controller."""
        if not HAS_BLEAK:
            raise HTTPException(503, "BLE not available")

        try:
            self.client = BleakClient(address)
            await self.client.connect()
            self.address = address
            self.connected = True

            # Subscribe to PoAC notifications
            await self.client.start_notify(
                BLE_CHAR_POAC, self._on_poac_notify
            )
            # Subscribe to anti-cheat status notifications
            await self.client.start_notify(
                BLE_CHAR_STATUS, self._on_status_notify
            )

            logger.info(f"Connected to controller at {address}")
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.connected = False
            return False

    async def disconnect(self):
        """Disconnect from the controller."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
        self.connected = False
        self.address = None
        logger.info("Disconnected from controller")

    async def send_command(self, cmd_type: int, payload: bytes = b"") -> bool:
        """Send a command to the controller via BLE write."""
        if not self.connected or not self.client:
            return False
        try:
            cmd = struct.pack(">B", cmd_type) + payload
            await self.client.write_gatt_char(BLE_CHAR_COMMAND, cmd)
            return True
        except Exception as e:
            logger.error(f"Command send failed: {e}")
            return False

    async def start_session(self) -> bool:
        """Send session start command to controller."""
        ok = await self.send_command(0x01)  # CMD_SESSION_START
        if ok:
            self.session_active = True
            self._stats["session_count"] += 1
        return ok

    async def end_session(self) -> bool:
        """Send session end command to controller."""
        ok = await self.send_command(0x02)  # CMD_SESSION_END
        if ok:
            self.session_active = False
            self.tournament_mode = False
        return ok

    async def toggle_tournament(self, enable: bool) -> bool:
        """Toggle tournament mode on controller."""
        payload = struct.pack(">B", 1 if enable else 0)
        ok = await self.send_command(0x03, payload)  # CMD_TOURNAMENT
        if ok:
            self.tournament_mode = enable
        return ok

    async def read_world_model(self) -> Optional[dict]:
        """Read the gaming world model from the controller."""
        if not self.connected or not self.client:
            return None
        try:
            data = await self.client.read_gatt_char(BLE_CHAR_WORLDMODEL)
            # Parse world model binary (simplified — full parser would mirror struct)
            return {"raw_hex": data.hex(), "size": len(data)}
        except Exception as e:
            logger.error(f"World model read failed: {e}")
            return None

    async def flash_firmware(self, firmware_path: str) -> bool:
        """Flash firmware to controller via BLE OTA."""
        if not self.connected or not self.client:
            return False

        path = Path(firmware_path)
        if not path.exists():
            raise HTTPException(404, f"Firmware file not found: {firmware_path}")

        data = path.read_bytes()
        chunk_size = 240  # BLE MTU - overhead
        total_chunks = (len(data) + chunk_size - 1) // chunk_size

        logger.info(f"Flashing {len(data)} bytes ({total_chunks} chunks)...")

        for i in range(total_chunks):
            offset = i * chunk_size
            chunk = data[offset:offset + chunk_size]
            header = struct.pack(">IH", offset, len(chunk))
            try:
                await self.client.write_gatt_char(
                    BLE_CHAR_OTA, header + chunk, response=True
                )
            except Exception as e:
                logger.error(f"OTA chunk {i}/{total_chunks} failed: {e}")
                return False

            # Broadcast progress to WebSocket clients
            await self._broadcast({
                "type": "ota_progress",
                "chunk": i + 1,
                "total": total_chunks,
                "pct": round((i + 1) / total_chunks * 100, 1),
            })

        logger.info("Firmware flash complete")
        return True

    async def upload_model(self, model_data: bytes) -> bool:
        """Upload a new TinyML anti-cheat model to the controller."""
        if len(model_data) > 60 * 1024:
            raise HTTPException(400, "Model too large (max 60 KB)")
        # Send via OTA characteristic with model flag
        header = struct.pack(">BII", 0x02, 0, len(model_data))  # 0x02 = MODEL_UPDATE
        payload = header + model_data
        return await self.send_command(0x04, payload)

    # ── BLE Notification Handlers ──

    def _on_poac_notify(self, sender, data: bytearray):
        """Handle incoming PoAC record from controller BLE notification."""
        try:
            record = PoACRecord.from_bytes(bytes(data))

            # Verify chain linkage
            if self.poac_chain:
                if not record.verify_chain_link(self.poac_chain[-1]):
                    logger.warning(f"Chain break at counter {record.monotonic_ctr}!")

            self.poac_chain.append(record)
            self._stats["total_records"] += 1
            self._stats["current_battery"] = record.battery_pct

            if record.inference_result >= INFER_CHEAT_REACTION:
                self._stats["cheat_detections"] += 1
            else:
                self._stats["clean_records"] += 1

            # Broadcast to WebSocket clients
            asyncio.get_event_loop().create_task(
                self._broadcast({
                    "type": "poac_record",
                    "record": record.to_dict(),
                    "chain_length": len(self.poac_chain),
                    "stats": self._stats,
                })
            )
        except Exception as e:
            logger.error(f"Failed to parse PoAC notification: {e}")

    def _on_status_notify(self, sender, data: bytearray):
        """Handle anti-cheat status notification."""
        if len(data) >= 2:
            inference = data[0]
            confidence = data[1]
            asyncio.get_event_loop().create_task(
                self._broadcast({
                    "type": "anticheat_status",
                    "inference": inference,
                    "inference_name": INFER_NAMES.get(inference, "UNKNOWN"),
                    "confidence": confidence,
                    "confidence_pct": round(confidence / 255 * 100, 1),
                    "is_cheat": inference >= INFER_CHEAT_REACTION,
                })
            )

    async def _broadcast(self, message: dict):
        """Broadcast a message to all connected WebSocket clients."""
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    def register_ws(self, ws: WebSocket):
        self._ws_clients.add(ws)

    def unregister_ws(self, ws: WebSocket):
        self._ws_clients.discard(ws)


# ══════════════════════════════════════════════════════════════════
# SQLite Persistence (same schema pattern as Pebble bridge store.py)
# ══════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).parent / "vapi_companion.db"

async def init_db():
    """Initialize SQLite database with gaming-specific schema."""
    if not HAS_SQLITE:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                record_hash TEXT PRIMARY KEY,
                device_address TEXT NOT NULL,
                raw_data BLOB NOT NULL,
                inference_result INTEGER NOT NULL,
                action_code INTEGER NOT NULL,
                confidence INTEGER NOT NULL,
                monotonic_ctr INTEGER NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                bounty_id INTEGER DEFAULT 0,
                is_cheat BOOLEAN DEFAULT FALSE,
                chain_valid BOOLEAN DEFAULT TRUE,
                on_chain_status TEXT DEFAULT 'pending',
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_address TEXT NOT NULL,
                start_time_ms INTEGER NOT NULL,
                end_time_ms INTEGER,
                total_records INTEGER DEFAULT 0,
                cheat_detections INTEGER DEFAULT 0,
                tournament_mode BOOLEAN DEFAULT FALSE,
                skill_rating REAL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bounties (
                bounty_id INTEGER PRIMARY KEY,
                reward_micro_iotx INTEGER NOT NULL,
                bounty_type TEXT NOT NULL,
                description TEXT,
                min_samples INTEGER NOT NULL,
                deadline_ms INTEGER NOT NULL,
                status TEXT DEFAULT 'discovered',
                samples_submitted INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_records_device ON records(device_address);
            CREATE INDEX IF NOT EXISTS idx_records_timestamp ON records(timestamp_ms);
            CREATE INDEX IF NOT EXISTS idx_records_cheat ON records(is_cheat);
        """)
        await db.commit()
        logger.info(f"Database initialized at {DB_PATH}")


async def store_record(record: PoACRecord, device_address: str):
    """Persist a PoAC record to SQLite."""
    if not HAS_SQLITE:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO records
               (record_hash, device_address, raw_data, inference_result,
                action_code, confidence, monotonic_ctr, timestamp_ms,
                bounty_id, is_cheat, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.record_hash.hex(),
                device_address,
                record.raw_body + record.signature,
                record.inference_result,
                record.action_code,
                record.confidence,
                record.monotonic_ctr,
                record.timestamp_ms,
                record.bounty_id,
                record.inference_result >= INFER_CHEAT_REACTION,
                time.time(),
            ),
        )
        await db.commit()


# ══════════════════════════════════════════════════════════════════
# API Models
# ══════════════════════════════════════════════════════════════════

class ConnectRequest(BaseModel):
    address: str

class SessionCommand(BaseModel):
    action: str  # "start", "end"

class TournamentCommand(BaseModel):
    enable: bool

class BountyInject(BaseModel):
    bounty_id: int
    reward_micro_iotx: int
    bounty_type: str = "anti_cheat_proof"
    min_samples: int = 100
    deadline_ms: int = 0
    description: str = ""

class ConfigUpdate(BaseModel):
    poac_interval_ms: Optional[int] = None
    tournament_poac_ms: Optional[int] = None
    cheat_threshold: Optional[int] = None
    anticheat_interval_ms: Optional[int] = None

class BridgeAgentRequest(BaseModel):
    session_id: str
    message: str


# ══════════════════════════════════════════════════════════════════
# FastAPI Application
# ══════════════════════════════════════════════════════════════════

controller = ControllerManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown."""
    await init_db()
    logger.info("VAPI DualShock Companion App started")
    logger.info("Dashboard: http://localhost:8080")
    yield
    await controller.disconnect()
    logger.info("Companion app shutdown")

app = FastAPI(
    title="VAPI DualShock Companion",
    description="Anti-cheat gaming controller companion app with PoAC verification",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Dashboard ──

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Main dashboard — Phase 31 modernized VAPI intelligence surface."""
    bridge_url_js = BRIDGE_URL
    bridge_device_js = BRIDGE_DEVICE_ID
    bridge_key_js = BRIDGE_API_KEY
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VAPI DualShock Companion</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  [x-cloak] {{ display: none !important; }}
  body {{ background: #0a0a0a; color: #e2e8f0; font-family: 'Courier New', monospace; }}
  .panel {{ background: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 16px; }}
  .badge-nominal {{ background: #064e3b; color: #6ee7b7; border: 1px solid #065f46; }}
  .badge-anomaly {{ background: #451a03; color: #fdba74; border: 1px solid #7c2d12; }}
  .badge-cheat   {{ background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; }}
  .pulse-medium  {{ border-left: 4px solid #f59e0b; background: #1c1407; }}
  .pulse-critical {{ border-left: 4px solid #ef4444; background: #1a0505; }}
  .chat-bubble-user {{ background: #1e3a5f; border-radius: 8px 8px 2px 8px; }}
  .chat-bubble-agent {{ background: #1a2730; border-radius: 8px 8px 8px 2px; }}
  ::-webkit-scrollbar {{ width: 4px; }} ::-webkit-scrollbar-track {{ background: #111; }}
  ::-webkit-scrollbar-thumb {{ background: #374151; border-radius: 2px; }}
</style>
</head>
<body class="min-h-screen p-4">

<!-- ── Header ── -->
<div class="flex items-center justify-between mb-4">
  <div>
    <h1 class="text-xl font-bold text-green-400">VAPI DualShock Companion</h1>
    <p class="text-xs text-gray-500">Phase 31 — Two Brains, One Body</p>
  </div>
  <div class="flex gap-2 text-xs">
    <span id="conn-badge" class="px-2 py-1 rounded bg-red-900 text-red-300">● Disconnected</span>
    <span id="bridge-badge" class="px-2 py-1 rounded bg-gray-800 text-gray-400">Bridge: —</span>
  </div>
</div>

<!-- ── Stats Row ── -->
<div class="grid grid-cols-5 gap-2 mb-4">
  <div class="panel text-center">
    <div class="text-2xl font-bold text-green-400" id="s-total">0</div>
    <div class="text-xs text-gray-500">PoAC Records</div>
  </div>
  <div class="panel text-center">
    <div class="text-2xl font-bold text-green-400" id="s-clean">0</div>
    <div class="text-xs text-gray-500">Clean</div>
  </div>
  <div class="panel text-center">
    <div class="text-2xl font-bold text-red-400" id="s-cheat">0</div>
    <div class="text-xs text-gray-500">Anomalies</div>
  </div>
  <div class="panel text-center">
    <div class="text-2xl font-bold text-blue-400" id="s-battery">--%</div>
    <div class="text-xs text-gray-500">Battery</div>
  </div>
  <div class="panel text-center" id="phg-score-mini">
    <div class="text-2xl font-bold text-yellow-400">--</div>
    <div class="text-xs text-gray-500">PHG Score</div>
  </div>
</div>

<!-- ── Main 3-Column Grid ── -->
<div class="grid grid-cols-12 gap-3 mb-3">

  <!-- Left: Leaderboard -->
  <div class="col-span-3 panel" x-data="leaderboard()" x-init="load()" style="max-height:520px;overflow-y:auto">
    <div class="flex items-center justify-between mb-2">
      <h2 class="text-sm font-bold text-green-400">⬆ Leaderboard</h2>
      <button @click="load()" class="text-xs text-gray-500 hover:text-green-400">↺</button>
    </div>
    <template x-if="loading"><div class="text-xs text-gray-500 italic">Loading...</div></template>
    <template x-if="!loading && entries.length === 0"><div class="text-xs text-gray-500 italic">No data — bridge not connected</div></template>
    <template x-for="(e, i) in entries" :key="e.device_id">
      <div class="flex items-center gap-2 py-1 border-b border-gray-800 text-xs"
           :class="{{ 'bg-green-950 rounded': e.device_id === '{bridge_device_js}' }}">
        <span class="text-gray-500 w-4" x-text="i+1"></span>
        <span class="font-mono text-gray-300 truncate flex-1" x-text="e.device_id.slice(0,12)+'...'"></span>
        <span class="text-yellow-400 font-bold" x-text="e.cumulative_score"></span>
      </div>
    </template>
  </div>

  <!-- Center: PoAC Chain -->
  <div class="col-span-6 panel" style="max-height:520px;display:flex;flex-direction:column">
    <h2 class="text-sm font-bold text-green-400 mb-2">⛓ Live PoAC Chain</h2>
    <div id="chain" style="overflow-y:auto;flex:1">
      <p class="text-xs text-gray-500 italic">Connect controller and start session to see records...</p>
    </div>
  </div>

  <!-- Right: PHG Intelligence -->
  <div class="col-span-3 panel" x-data="phgIntelligence()" x-init="load()" style="max-height:520px;overflow-y:auto">
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-sm font-bold text-green-400">🧠 PHG Intelligence</h2>
      <button @click="load()" class="text-xs text-gray-500 hover:text-green-400">↺</button>
    </div>
    <template x-if="loading"><div class="text-xs text-gray-500 italic">Loading from bridge...</div></template>
    <template x-if="!loading && !data">
      <div class="text-xs text-gray-500 italic">Bridge not configured.<br>Set BRIDGE_URL + BRIDGE_DEVICE_ID.</div>
    </template>
    <template x-if="data">
      <div>
        <div class="text-center mb-4">
          <div class="text-4xl font-bold text-yellow-400" x-text="data.phg_score_weighted || data.phg_score || 0"></div>
          <div class="text-xs text-gray-500">PHG Trust Score</div>
          <div class="mt-1">
            <div class="w-full bg-gray-800 rounded h-2 mt-1">
              <div class="bg-yellow-400 h-2 rounded" :style="`width:${{Math.min((data.phg_score_weighted||data.phg_score||0)/1000*100,100)}}%`"></div>
            </div>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-2 text-xs mb-3">
          <div class="panel text-center p-2">
            <div class="text-green-400 font-bold" x-text="((data.humanity_prob_avg||0)*100).toFixed(1)+'%'"></div>
            <div class="text-gray-500">Humanity Prob</div>
          </div>
          <div class="panel text-center p-2">
            <div class="text-blue-400 font-bold" x-text="data.nominal_records||0"></div>
            <div class="text-gray-500">NOMINAL</div>
          </div>
        </div>
        <div class="mb-2">
          <template x-if="cred">
            <div class="text-xs bg-green-900 border border-green-700 rounded p-2 text-center text-green-300">
              ✓ PHG Credential Minted<br>
              <span class="text-gray-400" x-text="'ID: '+cred.credential_id"></span>
            </div>
          </template>
          <template x-if="!cred">
            <div class="text-xs bg-gray-800 rounded p-2 text-center text-gray-500">No credential yet</div>
          </template>
        </div>
      </div>
    </template>
  </div>
</div>

<!-- ── BridgeAgent Chat ── -->
<div class="panel mb-3" x-data="agentChat()" x-init="init()">
  <div class="flex items-center gap-2 mb-2">
    <h2 class="text-sm font-bold text-green-400">💬 BridgeAgent</h2>
    <span class="text-xs text-gray-600">— Protocol Intelligence</span>
    <span class="ml-auto text-xs" :class="streaming ? 'text-green-400 animate-pulse' : 'text-gray-600'"
          x-text="streaming ? '● Streaming...' : '○ Ready'"></span>
  </div>
  <div id="chat-log" class="mb-2" style="height:160px;overflow-y:auto;padding:4px">
    <template x-if="messages.length === 0">
      <p class="text-xs text-gray-500 italic">Ask about player profiles, leaderboard, PITL signals, or system diagnostics...</p>
    </template>
    <template x-for="(m, i) in messages" :key="i">
      <div class="mb-2 text-xs">
        <template x-if="m.role === 'user'">
          <div class="chat-bubble-user p-2 ml-8" x-text="m.content"></div>
        </template>
        <template x-if="m.role === 'assistant'">
          <div class="chat-bubble-agent p-2 mr-8 text-green-300" x-html="m.content.replace(/\\n/g,'<br>')"></div>
        </template>
        <template x-if="m.role === 'tool'">
          <div class="text-gray-600 pl-2" x-text="'↳ ' + m.content"></div>
        </template>
      </div>
    </template>
    <div x-show="streaming && currentText" class="chat-bubble-agent p-2 mr-8 text-green-300 text-xs mb-2"
         x-text="currentText + '▊'"></div>
  </div>
  <div class="flex gap-2">
    <input x-model="input" @keydown.enter="send()" type="text"
           class="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-1 text-xs text-green-300 outline-none focus:border-green-600"
           placeholder="Ask about the protocol..." :disabled="streaming">
    <button @click="send()" :disabled="streaming || !input.trim()"
            class="px-4 py-1 bg-green-800 hover:bg-green-700 disabled:opacity-40 text-green-200 text-xs rounded font-bold">
      Send
    </button>
  </div>
</div>

<!-- ── Protocol Pulse (anomaly alerts) ── -->
<div class="panel" x-data="protocolPulse()">
  <div class="flex items-center gap-2 mb-2">
    <h2 class="text-sm font-bold text-orange-400">⚡ Protocol Pulse</h2>
    <span class="text-xs text-gray-600">— Real-time anomaly interpretation</span>
    <span class="ml-auto text-xs text-gray-600" x-text="alerts.length + ' alerts'"></span>
  </div>
  <template x-if="alerts.length === 0">
    <p class="text-xs text-gray-500 italic">Monitoring for BIOMETRIC_ANOMALY (L4) and TEMPORAL_ANOMALY (L5) events...</p>
  </template>
  <div class="grid grid-cols-1 gap-2">
    <template x-for="a in alerts" :key="a.id">
      <div class="rounded p-3 text-xs" :class="a.severity === 'critical' ? 'pulse-critical' : 'pulse-medium'">
        <div class="flex items-center gap-2 mb-1">
          <span class="font-bold" :class="a.severity === 'critical' ? 'text-red-400' : 'text-amber-400'"
                x-text="a.inference"></span>
          <span class="text-gray-500" x-text="a.device_id.slice(0,12)+'...'"></span>
          <span class="ml-auto text-gray-600" x-text="a.time"></span>
        </div>
        <div class="text-gray-300" x-text="a.explanation || 'Analyzing...'"></div>
      </div>
    </template>
  </div>
</div>

<script>
const BRIDGE_URL = '{bridge_url_js}';
const BRIDGE_DEVICE_ID = '{bridge_device_js}';
const BRIDGE_API_KEY = '{bridge_key_js}';

// ── WebSocket ──
const ws = new WebSocket(`ws://${{location.host}}/ws`);

ws.onopen = () => {{
  document.getElementById('conn-badge').className = 'px-2 py-1 rounded bg-green-900 text-green-300';
  document.getElementById('conn-badge').textContent = '● App Online';
}};

ws.onmessage = (e) => {{
  const msg = JSON.parse(e.data);
  if (msg.type === 'poac_record') {{
    const r = msg.record;
    const chain = document.getElementById('chain');
    const div = document.createElement('div');
    const isAnomaly = r.inference_result === 0x2B || r.inference_result === 0x30;
    const isCheat = r.is_cheat;
    const badgeCls = isCheat ? 'badge-cheat' : isAnomaly ? 'badge-anomaly' : 'badge-nominal';
    div.className = 'flex items-center gap-2 py-1 border-b border-gray-800 text-xs font-mono';
    div.innerHTML =
      `<span class="text-gray-600 w-8">#${{r.monotonic_ctr}}</span>` +
      `<span class="px-1 rounded text-xs ${{badgeCls}}">${{r.inference_name}}</span>` +
      `<span class="text-gray-400">${{r.confidence_pct}}%</span>` +
      `<span class="text-gray-600 truncate flex-1">${{r.record_hash.slice(0,12)}}...</span>` +
      `<span class="text-blue-400">${{r.battery_pct}}%</span>`;
    const firstChild = chain.querySelector('p');
    if (firstChild) firstChild.remove();
    chain.prepend(div);
    if (chain.children.length > 100) chain.removeChild(chain.lastChild);

    // Update stats
    const s = msg.stats;
    document.getElementById('s-total').textContent = s.total_records;
    document.getElementById('s-clean').textContent = s.clean_records;
    document.getElementById('s-cheat').textContent = s.cheat_detections;
    document.getElementById('s-battery').textContent = s.current_battery + '%';

    // Dispatch anomaly event for Protocol Pulse
    if (isAnomaly || isCheat) {{
      document.dispatchEvent(new CustomEvent('vapi:anomaly', {{ detail: r }}));
    }}
  }}
}};

// ── Leaderboard Alpine component ──
function leaderboard() {{
  return {{
    entries: [], loading: false,
    async load() {{
      this.loading = true;
      try {{
        const r = await fetch('/api/bridge/leaderboard');
        if (r.ok) {{ const d = await r.json(); this.entries = d.leaderboard || d || []; }}
      }} catch(e) {{}}
      this.loading = false;
      setTimeout(() => this.load(), 60000);
    }}
  }};
}}

// ── PHG Intelligence Alpine component ──
function phgIntelligence() {{
  return {{
    data: null, cred: null, loading: false,
    async load() {{
      this.loading = true;
      try {{
        const r = await fetch('/api/bridge/phg');
        if (r.ok) {{ this.data = await r.json(); }}
        const r2 = await fetch('/api/bridge/credential');
        if (r2.ok) {{ this.cred = await r2.json(); if (this.cred && this.cred.error) this.cred = null; }}
        // Update mini score
        if (this.data) {{
          const el = document.querySelector('#phg-score-mini .text-2xl');
          if (el) el.textContent = this.data.phg_score_weighted || this.data.phg_score || '--';
        }}
      }} catch(e) {{}}
      this.loading = false;
      setTimeout(() => this.load(), 30000);
    }}
  }};
}}

// ── BridgeAgent Chat Alpine component ──
function agentChat() {{
  return {{
    messages: [], input: '', streaming: false, currentText: '',
    sessionId: 'companion-' + Math.random().toString(36).slice(2, 8),
    eventSource: null,
    init() {{ /* nothing on init */ }},
    scrollToBottom() {{
      const log = document.getElementById('chat-log');
      if (log) log.scrollTop = log.scrollHeight;
    }},
    async send() {{
      const msg = this.input.trim();
      if (!msg || this.streaming) return;
      this.input = '';
      this.messages.push({{ role: 'user', content: msg }});
      this.scrollToBottom();

      // Try SSE streaming via bridge
      if (BRIDGE_URL && BRIDGE_API_KEY) {{
        this.streaming = true; this.currentText = '';
        const url = BRIDGE_URL + '/operator/agent/stream?' +
          new URLSearchParams({{ session_id: this.sessionId, message: msg, api_key: BRIDGE_API_KEY }});
        const es = new EventSource(url);
        es.onmessage = (e) => {{
          try {{
            const ev = JSON.parse(e.data);
            if (ev.type === 'text_delta') {{ this.currentText += ev.text; this.scrollToBottom(); }}
            else if (ev.type === 'tool_start') {{ this.messages.push({{ role: 'tool', content: 'Querying ' + ev.tool + '...' }}); }}
            else if (ev.type === 'done') {{
              if (this.currentText) this.messages.push({{ role: 'assistant', content: this.currentText }});
              this.currentText = ''; this.streaming = false; es.close(); this.scrollToBottom();
            }}
            else if (ev.type === 'error') {{
              this.messages.push({{ role: 'assistant', content: '⚠ ' + (ev.message || 'Agent error') }});
              this.streaming = false; es.close();
            }}
          }} catch(e2) {{}}
        }};
        es.onerror = () => {{ this.streaming = false; es.close(); }};
      }} else {{
        // Fallback: POST /api/bridge/agent via companion proxy
        this.streaming = true;
        try {{
          const r = await fetch('/api/bridge/agent', {{
            method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ session_id: this.sessionId, message: msg }})
          }});
          const d = await r.json();
          this.messages.push({{ role: 'assistant', content: d.response || JSON.stringify(d) }});
        }} catch(e) {{
          this.messages.push({{ role: 'assistant', content: '⚠ Bridge unreachable: ' + e.message }});
        }}
        this.streaming = false; this.scrollToBottom();
      }}
    }}
  }};
}}

// ── Protocol Pulse Alpine component ──
function protocolPulse() {{
  return {{
    alerts: [],
    init() {{
      document.addEventListener('vapi:anomaly', async (e) => {{
        const r = e.detail;
        const id = Date.now();
        const alert = {{
          id, inference: r.inference_name,
          device_id: r.record_hash || 'unknown',
          severity: r.is_cheat ? 'critical' : 'medium',
          time: new Date().toLocaleTimeString(),
          explanation: null,
        }};
        this.alerts.unshift(alert);
        if (this.alerts.length > 5) this.alerts = this.alerts.slice(0, 5);
        // Auto-explain via bridge agent
        try {{
          const resp = await fetch('/api/bridge/agent', {{
            method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              session_id: '__pulse_' + r.record_hash.slice(0,8),
              message: `Detected ${{r.inference_name}} inference=${{r.inference_result}} conf=${{r.confidence_pct}}%. Explain in 2 sentences.`
            }})
          }});
          if (resp.ok) {{
            const d = await resp.json();
            const found = this.alerts.find(a => a.id === id);
            if (found) found.explanation = d.response || 'Explanation unavailable.';
          }}
        }} catch(e) {{
          const found = this.alerts.find(a => a.id === id);
          if (found) found.explanation = 'Agent unavailable — ' + e.message;
        }}
      }});
    }}
  }};
}}
</script>
</body></html>"""


# ── WebSocket for real-time updates ──

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    controller.register_ws(ws)
    try:
        while True:
            # Keep connection alive, handle client messages
            data = await ws.receive_text()
            msg = json.loads(data)
            # Handle client commands (e.g., session start/stop from dashboard)
            if msg.get("action") == "start_session":
                await controller.start_session()
            elif msg.get("action") == "end_session":
                await controller.end_session()
    except WebSocketDisconnect:
        controller.unregister_ws(ws)


# ── BLE Controller Endpoints ──

@app.get("/api/scan")
async def scan_controllers(timeout: float = 10.0):
    """Scan for VAPI DualShock controllers via BLE."""
    devices = await controller.scan(timeout)
    return {"devices": devices}

@app.post("/api/connect")
async def connect_controller(req: ConnectRequest):
    """Connect to a controller by BLE address."""
    ok = await controller.connect(req.address)
    return {"connected": ok, "address": req.address}

@app.post("/api/disconnect")
async def disconnect_controller():
    """Disconnect from the current controller."""
    await controller.disconnect()
    return {"disconnected": True}

@app.get("/api/status")
async def get_status():
    """Get current controller and session status."""
    return {
        "connected": controller.connected,
        "address": controller.address,
        "session_active": controller.session_active,
        "tournament_mode": controller.tournament_mode,
        "chain_length": len(controller.poac_chain),
        "stats": controller._stats,
    }


# ── Session Management ──

@app.post("/api/session")
async def manage_session(cmd: SessionCommand):
    """Start or end a game session."""
    if cmd.action == "start":
        ok = await controller.start_session()
        return {"session_started": ok}
    elif cmd.action == "end":
        ok = await controller.end_session()
        return {"session_ended": ok}
    raise HTTPException(400, "Invalid action. Use 'start' or 'end'.")

@app.post("/api/tournament")
async def toggle_tournament(cmd: TournamentCommand):
    """Enable/disable tournament mode (10 Hz PoAC)."""
    ok = await controller.toggle_tournament(cmd.enable)
    return {"tournament_mode": cmd.enable, "success": ok}


# ── PoAC Chain Endpoints ──

@app.get("/api/chain")
async def get_chain(offset: int = 0, limit: int = 50):
    """Get PoAC records from the current session chain."""
    records = controller.poac_chain[offset:offset + limit]
    return {
        "records": [r.to_dict() for r in records],
        "total": len(controller.poac_chain),
        "offset": offset,
    }

@app.get("/api/chain/{counter}")
async def get_record(counter: int):
    """Get a specific PoAC record by monotonic counter."""
    for r in controller.poac_chain:
        if r.monotonic_ctr == counter:
            return r.to_dict()
    raise HTTPException(404, f"Record with counter {counter} not found")

@app.get("/api/chain/verify")
async def verify_chain():
    """Verify integrity of the entire PoAC chain."""
    chain = controller.poac_chain
    if len(chain) < 2:
        return {"valid": True, "length": len(chain), "breaks": []}

    breaks = []
    for i in range(1, len(chain)):
        if not chain[i].verify_chain_link(chain[i - 1]):
            breaks.append({
                "index": i,
                "counter": chain[i].monotonic_ctr,
                "expected_prev": hashlib.sha256(
                    chain[i - 1].raw_body + chain[i - 1].signature
                ).digest().hex(),
                "actual_prev": chain[i].prev_poac_hash.hex(),
            })

    return {
        "valid": len(breaks) == 0,
        "length": len(chain),
        "breaks": breaks,
    }

@app.get("/api/chain/export")
async def export_chain(format: str = "json"):
    """Export the PoAC chain for external verification or bounty submission."""
    if format == "json":
        return {"records": [r.to_dict() for r in controller.poac_chain]}
    elif format == "binary":
        # Return concatenated raw 228-byte records
        data = b"".join(r.raw_body + r.signature for r in controller.poac_chain)
        from fastapi.responses import Response
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename=poac_chain.bin"},
        )
    raise HTTPException(400, "Format must be 'json' or 'binary'")


# ── Bounty Marketplace ──

@app.get("/api/bounties")
async def list_bounties():
    """List available gaming bounties."""
    # TODO: Fetch from IoTeX BountyMarket contract via bridge
    # Placeholder bounties for development:
    return {"bounties": [
        {
            "bounty_id": 1001,
            "type": "anti_cheat_proof",
            "description": "Play 100 clean matches (all NOMINAL/SKILLED inference)",
            "reward_micro_iotx": 50000000,
            "reward_iotx": 50.0,
            "min_samples": 100,
            "status": "available",
        },
        {
            "bounty_id": 1002,
            "type": "speedrun_verify",
            "description": "Complete Elden Ring Margit fight in <90 seconds with PoAC chain",
            "reward_micro_iotx": 25000000,
            "reward_iotx": 25.0,
            "min_samples": 180,
            "status": "available",
        },
        {
            "bounty_id": 1003,
            "type": "tournament_integrity",
            "description": "Provide complete PoAC chain for 1 tournament bracket match",
            "reward_micro_iotx": 100000000,
            "reward_iotx": 100.0,
            "min_samples": 1200,
            "status": "available",
        },
    ]}

@app.post("/api/bounties/accept")
async def accept_bounty(bounty: BountyInject):
    """Send bounty to controller for evaluation by the economic optimizer."""
    # Serialize bounty descriptor and send via BLE command
    payload = struct.pack(
        ">IIHI",
        bounty.bounty_id,
        bounty.reward_micro_iotx,
        bounty.min_samples,
        bounty.deadline_ms,
    )
    ok = await controller.send_command(0x05, payload)  # CMD_BOUNTY_INJECT
    return {"sent": ok, "bounty_id": bounty.bounty_id}


# ── Bridge Proxy Endpoints (Phase 31) ──

def _require_httpx():
    if not HAS_HTTPX:
        raise HTTPException(503, "httpx not installed — bridge integration disabled (pip install httpx)")

@app.get("/api/bridge/status")
async def bridge_status():
    """Proxy to bridge /health — checks bridge connectivity (Phase 31)."""
    _require_httpx()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BRIDGE_URL}/health")
            return resp.json()
    except Exception as exc:
        raise HTTPException(503, f"Bridge unreachable: {exc}")

@app.get("/api/bridge/phg")
async def bridge_phg():
    """Proxy to bridge player profile — PHG Trust Score for configured device (Phase 31)."""
    _require_httpx()
    if not BRIDGE_DEVICE_ID:
        raise HTTPException(400, "BRIDGE_DEVICE_ID not configured")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{BRIDGE_URL}/dash/api/v1/player/{BRIDGE_DEVICE_ID}/profile"
            )
            return resp.json()
    except Exception as exc:
        raise HTTPException(503, f"Bridge unreachable: {exc}")

@app.get("/api/bridge/leaderboard")
async def bridge_leaderboard():
    """Proxy to bridge leaderboard — top 10 players by PHG score (Phase 31)."""
    _require_httpx()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BRIDGE_URL}/dash/api/v1/leaderboard?limit=10")
            return resp.json()
    except Exception as exc:
        raise HTTPException(503, f"Bridge unreachable: {exc}")

@app.get("/api/bridge/credential")
async def bridge_credential():
    """Proxy to bridge credential status for configured device (Phase 31)."""
    _require_httpx()
    if not BRIDGE_DEVICE_ID:
        raise HTTPException(400, "BRIDGE_DEVICE_ID not configured")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{BRIDGE_URL}/dash/api/v1/player/{BRIDGE_DEVICE_ID}/credential"
            )
            return resp.json()
    except Exception as exc:
        raise HTTPException(503, f"Bridge unreachable: {exc}")

@app.post("/api/bridge/agent")
async def bridge_agent(req: BridgeAgentRequest):
    """Proxy to bridge BridgeAgent — conversational protocol intelligence (Phase 31)."""
    _require_httpx()
    if not BRIDGE_API_KEY:
        raise HTTPException(400, "BRIDGE_API_KEY not configured")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{BRIDGE_URL}/operator/agent",
                json={"session_id": req.session_id, "message": req.message},
                params={"api_key": BRIDGE_API_KEY},
            )
            return resp.json()
    except Exception as exc:
        raise HTTPException(503, f"Bridge unreachable: {exc}")


# ── Developer Tools ──

@app.post("/api/dev/flash")
async def flash_firmware(firmware_path: str):
    """Flash firmware to controller via BLE OTA."""
    ok = await controller.flash_firmware(firmware_path)
    return {"success": ok}

@app.post("/api/dev/model")
async def upload_model(model_path: str):
    """Upload a new TinyML anti-cheat model to the controller."""
    path = Path(model_path)
    if not path.exists():
        raise HTTPException(404, f"Model file not found: {model_path}")
    data = path.read_bytes()
    ok = await controller.upload_model(data)
    return {"success": ok, "size_bytes": len(data)}

@app.get("/api/dev/world-model")
async def get_world_model():
    """Read the gaming world model (player skill profile) from the controller."""
    wm = await controller.read_world_model()
    return {"world_model": wm}

@app.post("/api/dev/config")
async def update_config(cfg: ConfigUpdate):
    """Update agent configuration on the controller."""
    payload = b""
    if cfg.poac_interval_ms is not None:
        payload += struct.pack(">BH", 0x01, cfg.poac_interval_ms)
    if cfg.cheat_threshold is not None:
        payload += struct.pack(">BB", 0x02, cfg.cheat_threshold)
    if payload:
        ok = await controller.send_command(0x06, payload)  # CMD_CONFIG
        return {"success": ok}
    return {"success": False, "error": "No config fields provided"}


# ══════════════════════════════════════════════════════════════════
# Entry Point
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "vapi-dualshock-companion:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info",
    )
