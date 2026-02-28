#!/usr/bin/env python3
"""
VAPI DualSense Edge Laptop Emulator
====================================

Reads REAL inputs from a DualSense Edge controller connected via USB or Bluetooth,
runs the full VAPI agent stack (PoAC, three-layer architecture, TinyML anti-cheat,
economic bounties) ON THE LAPTOP, and displays live anti-cheat verdicts.

This is the laptop-based test harness for the VAPI protocol. The agent logic is
identical to what runs on the ESP32-S3 inside the controller — only the execution
environment differs (Python/laptop instead of C/FreeRTOS).

VAPI Protocol Alignment:
  - Same 228-byte PoAC record format (164B body + 64B ECDSA-P256 signature)
  - Same SHA-256 commitments (sensor, model, world model)
  - Same three-layer agent architecture (reflexive/deliberative/strategic)
  - Same anti-cheat heuristic thresholds (from tinyml_anticheat.c)
  - Same economic utility function (from economic.c)
  - Same world_model_hash commitment pattern

Requirements:
    pip install pydualsense cryptography

Usage:
    # USB connection (plug in controller):
    python dualshock_emulator.py

    # With live dashboard:
    python dualshock_emulator.py --dashboard

    # Export PoAC chain:
    python dualshock_emulator.py --export chain.json

    # Verbose mode (log every record):
    python dualshock_emulator.py --verbose

    # Simulated controller (no hardware needed):
    python dualshock_emulator.py --simulate
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import struct
import sys
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Cryptography ──
try:
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("[WARN] cryptography not installed. Install: pip install cryptography")
    print("       PoAC records will NOT be signed (signature field zeroed).")

# ── DualSense Input ──
try:
    from pydualsense import pydualsense, TriggerModes
    HAS_DUALSENSE = True
except ImportError:
    HAS_DUALSENSE = False
    print("[WARN] pydualsense not installed. Install: pip install pydualsense")
    print("       Use --simulate flag for synthetic input mode.")


# ══════════════════════════════════════════════════════════════════
# Constants — byte-identical to firmware dualshock_agent.h / poac.h
# ══════════════════════════════════════════════════════════════════

POAC_HASH_SIZE = 32
POAC_SIG_SIZE = 64
POAC_BODY_SIZE = 164
POAC_RECORD_SIZE = 228

# Gaming inference codes (from dualshock_agent.h)
INFER_PLAY_NOMINAL    = 0x20
INFER_PLAY_SKILLED    = 0x21
INFER_CHEAT_REACTION  = 0x22
INFER_CHEAT_MACRO     = 0x23
INFER_CHEAT_AIMBOT    = 0x24
INFER_CHEAT_RECOIL    = 0x25
INFER_CHEAT_IMU_MISS  = 0x26
INFER_CHEAT_INJECTION = 0x27

INFER_NAMES = {
    0x20: "NOMINAL",  0x21: "SKILLED",
    0x22: "CHEAT:REACTION", 0x23: "CHEAT:MACRO",
    0x24: "CHEAT:AIMBOT",   0x25: "CHEAT:RECOIL",
    0x26: "CHEAT:IMU_MISS", 0x27: "CHEAT:INJECTION",
}

# Gaming action codes
ACTION_REPORT         = 0x01
ACTION_BOOT           = 0x09
ACTION_SESSION_START  = 0x10
ACTION_SESSION_END    = 0x11
ACTION_CHEAT_ALERT    = 0x12
ACTION_TOURNAMENT     = 0x14

ACTION_NAMES = {
    0x01: "REPORT", 0x09: "BOOT",
    0x10: "SESSION_START", 0x11: "SESSION_END",
    0x12: "CHEAT_ALERT", 0x14: "TOURNAMENT",
}

# Agent states
STATE_BOOT       = 0
STATE_IDLE       = 1
STATE_SESSION    = 2
STATE_TOURNAMENT = 3
STATE_CHEAT      = 4

STATE_NAMES = {0: "BOOT", 1: "IDLE", 2: "SESSION", 3: "TOURNAMENT", 4: "CHEAT_ALERT"}

# Anti-cheat thresholds (from tinyml_anticheat.c heuristic fallback)
THRESHOLD_MACRO_VARIANCE   = 1.0     # ms² — below this = macro detected
THRESHOLD_IMU_NOISE        = 0.001   # rad/s — below this = desk/no human
THRESHOLD_IMU_CORR         = 0.15    # correlation — below this = IMU mismatch
THRESHOLD_REACTION_MS      = 150.0   # ms — below this sustained = inhuman
THRESHOLD_AIMBOT_JERK      = 2.0     # stick jerk — above this = aimbot snap
THRESHOLD_CHEAT_CONFIDENCE = 180     # [0-255] — above this = flag as cheat
CHEAT_RESOLVE_COUNT        = 10      # clean windows needed to clear alert

# EMA smoothing
WM_EMA_ALPHA = 0.05


# ══════════════════════════════════════════════════════════════════
# Data Structures — Python equivalents of firmware structs
# ══════════════════════════════════════════════════════════════════

@dataclass
class InputSnapshot:
    """50-byte gaming input snapshot — mirrors ds_input_snapshot_t."""
    buttons: int = 0               # 18 buttons packed into 3 bytes
    left_stick_x: int = 0         # [-32768, 32767]
    left_stick_y: int = 0
    right_stick_x: int = 0
    right_stick_y: int = 0
    l2_trigger: int = 0           # [0, 255]
    r2_trigger: int = 0
    gyro_x: float = 0.0           # rad/s
    gyro_y: float = 0.0
    gyro_z: float = 0.0
    accel_x: float = 0.0          # g
    accel_y: float = 0.0
    accel_z: float = 1.0          # gravity
    touch0_x: int = 0
    touch0_y: int = 0
    touch1_x: int = 0
    touch1_y: int = 0
    touch_active: int = 0
    battery_mv: int = 4000
    frame_counter: int = 0
    inter_frame_us: int = 1000
    # Adaptive trigger resistance mode (tracked state; write-only on real hardware)
    l2_effect_mode: int = 0   # TriggerMode ordinal; 0 = Off
    r2_effect_mode: int = 0

    def serialize(self) -> bytes:
        """Deterministic big-endian serialization — same as firmware."""
        b0 = (self.buttons >> 16) & 0xFF
        b1 = (self.buttons >> 8) & 0xFF
        b2 = self.buttons & 0xFF
        return struct.pack(">BBB hhhh BB fff fff HHHH B H II",
            b0, b1, b2,
            self.left_stick_x, self.left_stick_y,
            self.right_stick_x, self.right_stick_y,
            self.l2_trigger, self.r2_trigger,
            self.gyro_x, self.gyro_y, self.gyro_z,
            self.accel_x, self.accel_y, self.accel_z,
            self.touch0_x, self.touch0_y,
            self.touch1_x, self.touch1_y,
            self.touch_active,
            self.battery_mv,
            self.frame_counter, self.inter_frame_us,
        )


@dataclass
class FeatureFrame:
    """30-feature vector — mirrors ac_feature_frame_t."""
    stick_lx: float = 0.0; stick_ly: float = 0.0
    stick_rx: float = 0.0; stick_ry: float = 0.0
    stick_l_vel: float = 0.0; stick_r_vel: float = 0.0
    stick_l_acc: float = 0.0; stick_r_acc: float = 0.0
    trigger_l2: float = 0.0; trigger_r2: float = 0.0
    button_packed: float = 0.0
    inter_press_ms: float = 0.0; press_variance: float = 999.0
    press_rate: float = 0.0; hold_asymmetry: float = 0.5
    gyro_x: float = 0.0; gyro_y: float = 0.0; gyro_z: float = 0.0
    accel_mag: float = 1.0; gyro_mag: float = 0.0
    imu_corr: float = 0.5; imu_noise: float = 0.01
    touch_x: float = -1.0; touch_y: float = -1.0; touch_entropy: float = 1.0
    frame_dt_ms: float = 1.0; reaction_ms: float = 0.0; dir_changes: float = 0.0
    jerk_l: float = 0.0; jerk_r: float = 0.0

    def to_vector(self) -> "np.ndarray":
        """Return all 30 features as a float32 numpy array (INPUT_DIM=30 order)."""
        import numpy as np
        return np.array([
            self.stick_lx,       self.stick_ly,       self.stick_rx,       self.stick_ry,
            self.stick_l_vel,    self.stick_r_vel,    self.stick_l_acc,    self.stick_r_acc,
            self.trigger_l2,     self.trigger_r2,     self.button_packed,
            self.inter_press_ms, self.press_variance, self.press_rate,     self.hold_asymmetry,
            self.gyro_x,         self.gyro_y,         self.gyro_z,
            self.accel_mag,      self.gyro_mag,       self.imu_corr,       self.imu_noise,
            self.touch_x,        self.touch_y,        self.touch_entropy,
            self.frame_dt_ms,    self.reaction_ms,    self.dir_changes,
            self.jerk_l,         self.jerk_r,
        ], dtype=np.float32)


@dataclass
class PoACRecord:
    """228-byte PoAC record — byte-identical to Pebble/DualShock firmware."""
    prev_poac_hash: bytes = b'\x00' * 32
    sensor_commitment: bytes = b'\x00' * 32
    model_manifest_hash: bytes = b'\x00' * 32
    world_model_hash: bytes = b'\x00' * 32
    inference_result: int = INFER_PLAY_NOMINAL
    action_code: int = ACTION_REPORT
    confidence: int = 0
    battery_pct: int = 100
    monotonic_ctr: int = 0
    timestamp_ms: int = 0
    latitude: float = 0.0
    longitude: float = 0.0
    bounty_id: int = 0
    signature: bytes = b'\x00' * 64

    def serialize_body(self) -> bytes:
        """Serialize the 164-byte body for signing — deterministic, big-endian."""
        return (
            self.prev_poac_hash +
            self.sensor_commitment +
            self.model_manifest_hash +
            self.world_model_hash +
            struct.pack(">BBBB I q d d I",
                self.inference_result, self.action_code,
                self.confidence, self.battery_pct,
                self.monotonic_ctr, self.timestamp_ms,
                self.latitude, self.longitude,
                self.bounty_id)
        )

    def serialize_full(self) -> bytes:
        """Serialize the complete 228-byte record."""
        return self.serialize_body() + self.signature

    def record_hash(self) -> bytes:
        """SHA-256 of the full 228-byte record (for chain linkage)."""
        return hashlib.sha256(self.serialize_full()).digest()

    def to_dict(self) -> dict:
        return {
            "hash": self.record_hash().hex()[:16] + "...",
            "prev": self.prev_poac_hash.hex()[:16] + "...",
            "ctr": self.monotonic_ctr,
            "inference": INFER_NAMES.get(self.inference_result, f"0x{self.inference_result:02x}"),
            "action": ACTION_NAMES.get(self.action_code, f"0x{self.action_code:02x}"),
            "confidence": f"{self.confidence / 255 * 100:.1f}%",
            "battery": f"{self.battery_pct}%",
            "bounty": self.bounty_id,
            "is_cheat": self.inference_result >= INFER_CHEAT_REACTION,
        }


@dataclass
class WorldModel:
    """Gaming world model — mirrors ds_world_model_t."""
    reaction_history: deque = field(default_factory=lambda: deque(maxlen=64))
    precision_history: deque = field(default_factory=lambda: deque(maxlen=64))
    variance_history: deque = field(default_factory=lambda: deque(maxlen=64))
    corr_history: deque = field(default_factory=lambda: deque(maxlen=64))

    reaction_baseline: float = 250.0
    precision_baseline: float = 0.5
    consistency_baseline: float = 50.0
    imu_corr_baseline: float = 0.5

    session_skill_rating: float = 500.0
    total_frames: int = 0
    total_sessions: int = 0
    total_cheat_flags: int = 0
    total_poac: int = 0

    def compute_hash(self) -> bytes:
        """Deterministic SHA-256 — same serialization as firmware wm_compute_hash()."""
        buf = struct.pack(">ffffII II",
            self.reaction_baseline, self.precision_baseline,
            self.consistency_baseline, self.imu_corr_baseline,
            self.total_frames, self.total_sessions,
            self.total_cheat_flags, self.total_poac,
        )
        buf += struct.pack(">B", min(len(self.reaction_history), 64))
        for i in range(min(len(self.reaction_history), 64)):
            buf += struct.pack(">ffff",
                self.reaction_history[i],
                self.precision_history[i] if i < len(self.precision_history) else 0,
                self.variance_history[i] if i < len(self.variance_history) else 0,
                self.corr_history[i] if i < len(self.corr_history) else 0,
            )
        return hashlib.sha256(buf).digest()

    def update(self, reaction: float, precision: float, variance: float, corr: float):
        self.reaction_history.append(reaction)
        self.precision_history.append(precision)
        self.variance_history.append(variance)
        self.corr_history.append(corr)
        self.reaction_baseline = WM_EMA_ALPHA * reaction + (1 - WM_EMA_ALPHA) * self.reaction_baseline
        self.precision_baseline = WM_EMA_ALPHA * precision + (1 - WM_EMA_ALPHA) * self.precision_baseline


# ══════════════════════════════════════════════════════════════════
# PoAC Crypto Engine — ECDSA-P256 + SHA-256
# ══════════════════════════════════════════════════════════════════

class PoACEngine:
    """PoAC record generation with ECDSA-P256 signing.
    Same algorithm as CryptoCell-310 (Pebble) and mbedTLS (ESP32-S3)."""

    def __init__(self):
        self.counter = 0
        self.chain_head = b'\x00' * 32  # Genesis: all zeros
        self.private_key = None
        self.public_key_bytes = b'\x00' * 65
        self.model_hash = b'\x00' * 32

        if HAS_CRYPTO:
            # Generate ephemeral ECDSA-P256 keypair (same curve as firmware)
            self.private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
            pub = self.private_key.public_key()
            self.public_key_bytes = pub.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
            print(f"[CRYPTO] ECDSA-P256 keypair generated")
            print(f"[CRYPTO] Public key: {self.public_key_bytes.hex()[:32]}...")

            # Compute model manifest hash (heuristic fallback "weights")
            heuristic_weights = bytes([0x00, 0x01, 0x00, 0x00, 0x01, 0x00, 0x96, 0x02, 0x00])
            self.model_hash = hashlib.sha256(heuristic_weights).digest()
        else:
            self.model_hash = hashlib.sha256(b"heuristic_fallback_v0").digest()

    def generate(self, sensor_hash: bytes, wm_hash: bytes,
                 inference: int, action: int, confidence: int,
                 battery_pct: int, bounty_id: int = 0) -> PoACRecord:
        """Generate a signed PoAC record — same algorithm as poac_generate() in firmware."""
        self.counter += 1

        record = PoACRecord(
            prev_poac_hash=self.chain_head,
            sensor_commitment=sensor_hash,
            model_manifest_hash=self.model_hash,
            world_model_hash=wm_hash,
            inference_result=inference,
            action_code=action,
            confidence=confidence,
            battery_pct=battery_pct,
            monotonic_ctr=self.counter,
            timestamp_ms=int(time.time() * 1000),
            latitude=0.0,
            longitude=0.0,
            bounty_id=bounty_id,
        )

        # Sign the 164-byte body
        body = record.serialize_body()
        assert len(body) == POAC_BODY_SIZE, f"Body size mismatch: {len(body)}"

        if HAS_CRYPTO and self.private_key:
            digest = hashlib.sha256(body).digest()
            der_sig = self.private_key.sign(body, ec.ECDSA(hashes.SHA256()))
            r, s = utils.decode_dss_signature(der_sig)
            raw_sig = r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
            record.signature = raw_sig
        else:
            record.signature = b'\x00' * 64

        full = record.serialize_full()
        assert len(full) == POAC_RECORD_SIZE, f"Record size mismatch: {len(full)}"

        # Update chain head
        self.chain_head = hashlib.sha256(full).digest()

        return record


# ══════════════════════════════════════════════════════════════════
# Anti-Cheat Heuristic Classifier
# Identical thresholds to tinyml_anticheat.c heuristic_classify()
# ══════════════════════════════════════════════════════════════════

class AntiCheatClassifier:
    """Port of the firmware's heuristic fallback anti-cheat classifier."""

    def __init__(self):
        self.window: deque[FeatureFrame] = deque(maxlen=100)
        self.prev_buttons = 0
        self.last_press_time = 0.0
        self.press_intervals: deque[float] = deque(maxlen=32)
        self.press_count = 0

        # Welford's running variance
        self._n = 0
        self._mean = 0.0
        self._m2 = 0.0

        # IMU correlation accumulator
        self._corr_xy = 0.0
        self._corr_x2 = 0.0
        self._corr_y2 = 0.0
        self._corr_n = 0

        # Stick state
        self._prev_lx = 0.0; self._prev_ly = 0.0
        self._prev_rx = 0.0; self._prev_ry = 0.0
        self._prev_l_vel = 0.0; self._prev_r_vel = 0.0
        self._stick_was_idle = True
        self._idle_time = time.time()
        self._dir_changes = 0
        self._prev_angle = 0.0

    def update_press_stats(self, interval_ms: float):
        """Welford's online algorithm — O(1) per update, same as firmware."""
        self._n += 1
        delta = interval_ms - self._mean
        self._mean += delta / self._n
        delta2 = interval_ms - self._mean
        self._m2 += delta * delta2

    @property
    def press_variance(self) -> float:
        return (self._m2 / (self._n - 1)) if self._n > 1 else 999.0

    def extract_features(self, snap: InputSnapshot, dt_ms: float) -> FeatureFrame:
        """Extract 30-feature vector — same pipeline as ac_push_frame() in firmware."""
        f = FeatureFrame()

        # Stick normalization
        f.stick_lx = snap.left_stick_x / 32768.0
        f.stick_ly = snap.left_stick_y / 32768.0
        f.stick_rx = snap.right_stick_x / 32768.0
        f.stick_ry = snap.right_stick_y / 32768.0

        # Velocity
        if dt_ms > 0.001:
            vl_x = (f.stick_lx - self._prev_lx) / dt_ms
            vl_y = (f.stick_ly - self._prev_ly) / dt_ms
            vr_x = (f.stick_rx - self._prev_rx) / dt_ms
            vr_y = (f.stick_ry - self._prev_ry) / dt_ms
            f.stick_l_vel = math.sqrt(vl_x**2 + vl_y**2)
            f.stick_r_vel = math.sqrt(vr_x**2 + vr_y**2)

            # Acceleration
            f.stick_l_acc = (f.stick_l_vel - self._prev_l_vel) / dt_ms
            f.stick_r_acc = (f.stick_r_vel - self._prev_r_vel) / dt_ms

            # Jerk (third derivative)
            f.jerk_l = f.stick_l_acc / dt_ms
            f.jerk_r = f.stick_r_acc / dt_ms

            self._prev_l_vel = f.stick_l_vel
            self._prev_r_vel = f.stick_r_vel

        self._prev_lx = f.stick_lx; self._prev_ly = f.stick_ly
        self._prev_rx = f.stick_rx; self._prev_ry = f.stick_ry

        # Triggers
        f.trigger_l2 = snap.l2_trigger / 255.0
        f.trigger_r2 = snap.r2_trigger / 255.0

        # Button timing
        f.button_packed = snap.buttons / 16777216.0
        if snap.buttons != self.prev_buttons and self.prev_buttons != 0:
            now = time.time()
            if self.last_press_time > 0:
                interval = (now - self.last_press_time) * 1000.0
                self.press_intervals.append(interval)
                self.update_press_stats(interval)
                f.inter_press_ms = interval
            self.last_press_time = now
            self.press_count += 1
        self.prev_buttons = snap.buttons
        f.press_variance = self.press_variance
        f.press_rate = self.press_count  # simplified

        # IMU
        f.gyro_x = snap.gyro_x; f.gyro_y = snap.gyro_y; f.gyro_z = snap.gyro_z
        f.accel_mag = math.sqrt(snap.accel_x**2 + snap.accel_y**2 + snap.accel_z**2)
        f.gyro_mag = math.sqrt(snap.gyro_x**2 + snap.gyro_y**2 + snap.gyro_z**2)

        # IMU-stick cross-correlation (running accumulator)
        stick_vel = f.stick_r_vel
        gyro_total = f.gyro_mag
        self._corr_xy += stick_vel * gyro_total
        self._corr_x2 += stick_vel ** 2
        self._corr_y2 += gyro_total ** 2
        self._corr_n += 1

        denom = math.sqrt(self._corr_x2 * self._corr_y2) if self._corr_n > 10 else 1.0
        f.imu_corr = (self._corr_xy / denom + 1.0) / 2.0 if denom > 0.0001 else 0.5
        f.imu_noise = f.gyro_mag

        # Reaction time proxy
        stick_mag = math.sqrt(f.stick_rx**2 + f.stick_ry**2)
        if self._stick_was_idle and stick_mag > 0.15:
            f.reaction_ms = (time.time() - self._idle_time) * 1000.0
            self._stick_was_idle = False
        elif stick_mag < 0.05:
            self._stick_was_idle = True
            self._idle_time = time.time()

        # Direction changes
        angle = math.atan2(f.stick_ry, f.stick_rx)
        if abs(angle - self._prev_angle) > 1.5:
            self._dir_changes += 1
        self._prev_angle = angle
        f.dir_changes = float(self._dir_changes)

        f.frame_dt_ms = dt_ms

        self.window.append(f)
        return f

    def classify(self) -> tuple[int, int]:
        """Classify the current window — same rules as heuristic_classify() in firmware.
        Returns: (inference_code, confidence)"""
        if len(self.window) < 20:
            return INFER_PLAY_NOMINAL, 128

        n = len(self.window)
        avg_press_var = sum(f.press_variance for f in self.window) / n
        avg_imu_noise = sum(f.imu_noise for f in self.window) / n
        avg_reaction = sum(f.reaction_ms for f in self.window if f.reaction_ms > 0)
        reaction_count = sum(1 for f in self.window if f.reaction_ms > 0)
        avg_reaction = avg_reaction / reaction_count if reaction_count > 0 else 250.0
        avg_imu_corr = sum(f.imu_corr for f in self.window) / n
        avg_jerk = sum(abs(f.jerk_r) for f in self.window) / n

        # Rule 1: Macro/turbo (same threshold as firmware: σ² < 1.0 ms²)
        if 0.0001 < avg_press_var < THRESHOLD_MACRO_VARIANCE:
            return INFER_CHEAT_MACRO, 230

        # Rule 2: Input injection (no IMU noise = no human holding controller)
        if avg_imu_noise < THRESHOLD_IMU_NOISE and avg_imu_corr < 0.1:
            return INFER_CHEAT_INJECTION, 210

        # Rule 3: IMU mismatch (stick moves but controller doesn't)
        if avg_imu_corr < THRESHOLD_IMU_CORR and avg_jerk > 0.5:
            return INFER_CHEAT_IMU_MISS, 200

        # Rule 4: Impossible reaction time (<150ms sustained)
        if 0 < avg_reaction < THRESHOLD_REACTION_MS:
            return INFER_CHEAT_REACTION, 190

        # Rule 5: Aimbot (extremely high stick jerk)
        if avg_jerk > THRESHOLD_AIMBOT_JERK:
            return INFER_CHEAT_AIMBOT, 180

        # Rule 6: Skilled play (fast but human-plausible)
        if 0 < avg_reaction < 250.0 and avg_imu_corr > 0.6:
            return INFER_PLAY_SKILLED, 200

        # Rule 7: Default nominal
        return INFER_PLAY_NOMINAL, 220

    def reset(self):
        self.window.clear()
        self._n = 0; self._mean = 0.0; self._m2 = 0.0
        self._corr_xy = 0; self._corr_x2 = 0; self._corr_y2 = 0; self._corr_n = 0
        self.press_count = 0
        self._dir_changes = 0


# ══════════════════════════════════════════════════════════════════
# Economic Evaluator — Same utility function as economic.c
# ══════════════════════════════════════════════════════════════════

@dataclass
class Bounty:
    bounty_id: int
    reward_micro_iotx: int
    min_samples: int
    description: str = ""
    samples_submitted: int = 0
    accepted: bool = False

    @property
    def reward_iotx(self) -> float:
        return self.reward_micro_iotx / 1_000_000

    def utility(self, battery_pct: int) -> float:
        """Same utility function as economic.c evaluate_bounty()."""
        energy_per_sample = 0.002 + 0.002 + 0.003  # sense + crypto + BLE
        total_energy = energy_per_sample * self.min_samples
        energy_cost_pct = total_energy / (1000 / 100)  # 1000 mAh battery
        p_success = 1.0 if battery_pct > 20 else 0.5
        reward_value = p_success * self.reward_iotx
        return reward_value - energy_cost_pct


# ══════════════════════════════════════════════════════════════════
# DualSense Edge Input Reader
# ══════════════════════════════════════════════════════════════════

class DualSenseReader:
    """Reads real inputs from DualSense Edge via pydualsense library."""

    def __init__(self):
        self.ds = None
        self.frame_counter = 0
        self.last_poll_time = time.time()
        self.connected = False
        self._is_edge = False
        self._accel_scale = None  # Auto-calibrated on first poll

    def connect(self) -> bool:
        if not HAS_DUALSENSE:
            return False
        try:
            self.ds = pydualsense()
            self.ds.init()
            self.connected = True
            self._is_edge = getattr(self.ds, 'is_edge', False)
            edge_str = " Edge" if self._is_edge else ""
            print(f"[CONTROLLER] DualSense{edge_str} connected!")
            batt = getattr(self.ds, 'battery', None)
            batt_lvl = getattr(batt, 'Level', None) if batt else None
            if batt_lvl is not None:
                print(f"[CONTROLLER] Battery: {batt_lvl}%")
            else:
                print("[CONTROLLER] Battery: checking...")
            return True
        except Exception as e:
            print(f"[CONTROLLER] Connection failed: {e}")
            print("[CONTROLLER] Make sure controller is connected via USB or Bluetooth")
            return False

    def poll(self) -> InputSnapshot:
        now = time.time()
        dt_us = int((now - self.last_poll_time) * 1_000_000)
        self.last_poll_time = now
        self.frame_counter += 1

        if not self.connected or not self.ds:
            return self._simulate_input(dt_us)

        ds = self.ds
        snap = InputSnapshot()

        # Buttons → 18-bit packed integer
        buttons = 0
        buttons |= (1 << 0) if ds.state.cross else 0
        buttons |= (1 << 1) if ds.state.circle else 0
        buttons |= (1 << 2) if ds.state.square else 0
        buttons |= (1 << 3) if ds.state.triangle else 0
        buttons |= (1 << 4) if ds.state.L1 else 0
        buttons |= (1 << 5) if ds.state.R1 else 0
        buttons |= (1 << 6) if ds.state.L3 else 0
        buttons |= (1 << 7) if ds.state.R3 else 0
        buttons |= (1 << 8) if ds.state.DpadUp else 0
        buttons |= (1 << 9) if ds.state.DpadDown else 0
        buttons |= (1 << 10) if ds.state.DpadLeft else 0
        buttons |= (1 << 11) if ds.state.DpadRight else 0
        buttons |= (1 << 12) if ds.state.share else 0
        buttons |= (1 << 13) if ds.state.options else 0
        buttons |= (1 << 14) if ds.state.ps else 0
        buttons |= (1 << 15) if ds.state.touchBtn else 0
        snap.buttons = buttons

        # Sticks — Edge: already centered at 0 (range ~[-128,127])
        #          Standard: [0,255] center 128
        _clamp16 = lambda v: max(-32768, min(32767, int(v)))
        if self._is_edge:
            # Edge: LX is already 0-centered, scale to int16 range
            snap.left_stick_x  = _clamp16(ds.state.LX * 256)
            snap.left_stick_y  = _clamp16(ds.state.LY * 256)
            snap.right_stick_x = _clamp16(ds.state.RX * 256)
            snap.right_stick_y = _clamp16(ds.state.RY * 256)
        else:
            # Standard DualSense: [0,255] center 128
            snap.left_stick_x  = _clamp16((ds.state.LX - 128) * 256)
            snap.left_stick_y  = _clamp16((ds.state.LY - 128) * 256)
            snap.right_stick_x = _clamp16((ds.state.RX - 128) * 256)
            snap.right_stick_y = _clamp16((ds.state.RY - 128) * 256)

        # Triggers — use L2_value (int 0-255) if available, else bool fallback
        if hasattr(ds.state, 'L2_value'):
            snap.l2_trigger = ds.state.L2_value
            snap.r2_trigger = ds.state.R2_value
        else:
            val = ds.state.L2
            snap.l2_trigger = val if isinstance(val, int) else (255 if val else 0)
            val = ds.state.R2
            snap.r2_trigger = val if isinstance(val, int) else (255 if val else 0)

        # Edge back buttons (L4/R4/L5/R5 on bits 16-19)
        if self._is_edge:
            if getattr(ds.state, 'L4', False): buttons |= (1 << 16)
            if getattr(ds.state, 'R4', False): buttons |= (1 << 17)
            if getattr(ds.state, 'L5', False): buttons |= (1 << 18)
            if getattr(ds.state, 'R5', False): buttons |= (1 << 19)
            snap.buttons = buttons  # re-assign with edge buttons

        # IMU — pydualsense gyro uses Pitch/Yaw/Roll, accel uses X/Y/Z
        snap.gyro_x = ds.state.gyro.Pitch / 1000.0
        snap.gyro_y = ds.state.gyro.Yaw / 1000.0
        snap.gyro_z = ds.state.gyro.Roll / 1000.0

        # Accelerometer — raw int16 (~8192/g). Edge is gravity-compensated.
        raw_ax = ds.state.accelerometer.X
        raw_ay = ds.state.accelerometer.Y
        raw_az = ds.state.accelerometer.Z
        if self._accel_scale is None:
            self._accel_scale = 8192.0
        snap.accel_x = raw_ax / self._accel_scale
        snap.accel_y = raw_ay / self._accel_scale
        snap.accel_z = raw_az / self._accel_scale

        # Touchpad
        if ds.state.trackPadTouch0.isActive:
            snap.touch0_x = ds.state.trackPadTouch0.X
            snap.touch0_y = ds.state.trackPadTouch0.Y
            snap.touch_active |= 0x01
        if ds.state.trackPadTouch1.isActive:
            snap.touch1_x = ds.state.trackPadTouch1.X
            snap.touch1_y = ds.state.trackPadTouch1.Y
            snap.touch_active |= 0x02

        # Battery — Edge: ds.battery.Level; standard: ds.state.battery.Level
        batt_level = None
        if hasattr(ds, 'battery') and hasattr(ds.battery, 'Level'):
            batt_level = ds.battery.Level
        elif hasattr(ds.state, 'battery') and hasattr(ds.state.battery, 'Level'):
            batt_level = ds.state.battery.Level
        snap.battery_mv = 3700 + (batt_level * 50 if batt_level is not None else 300)
        snap.frame_counter = self.frame_counter
        snap.inter_frame_us = dt_us

        # Phase 11: Adaptive trigger mode — output-only on hardware; read back with safe fallback.
        # pydualsense may expose the last-set mode via triggerL/triggerR.mode; if not, stays 0.
        snap.l2_effect_mode = int(getattr(getattr(ds, 'triggerL', None), 'mode', 0) or 0)
        snap.r2_effect_mode = int(getattr(getattr(ds, 'triggerR', None), 'mode', 0) or 0)

        return snap

    def _simulate_input(self, dt_us: int) -> InputSnapshot:
        """Generate synthetic human-like input for testing without hardware."""
        t = time.time()
        snap = InputSnapshot()
        snap.frame_counter = self.frame_counter
        snap.inter_frame_us = dt_us

        # Simulate gentle stick movement with micro-jitter
        import random
        snap.right_stick_x = int(math.sin(t * 0.5) * 8000 + random.gauss(0, 200))
        snap.right_stick_y = int(math.cos(t * 0.7) * 6000 + random.gauss(0, 200))
        snap.left_stick_x = int(math.sin(t * 0.3) * 10000 + random.gauss(0, 300))
        snap.left_stick_y = int(math.cos(t * 0.4) * 10000 + random.gauss(0, 300))

        # Simulate human hand tremor on IMU (8-12 Hz micro-oscillation)
        snap.gyro_x = math.sin(t * 62.8) * 0.02 + random.gauss(0, 0.005)
        snap.gyro_y = math.cos(t * 75.4) * 0.015 + random.gauss(0, 0.005)
        snap.gyro_z = math.sin(t * 50.3) * 0.01 + random.gauss(0, 0.003)
        snap.accel_x = random.gauss(0, 0.01)
        snap.accel_y = random.gauss(0, 0.01)
        snap.accel_z = 1.0 + random.gauss(0, 0.005)

        # Occasional button presses with human variance
        if random.random() < 0.02:
            snap.buttons = 1 << random.randint(0, 15)
        snap.battery_mv = 3900

        # Phase 11: Periodically simulate non-zero trigger effect modes so schema v2
        # commitments exercise all three mode ordinals (0=Off, 1=Rigid, 2=Pulse).
        # Every 500 frames (~8 min at 1 fps) the modes rotate to a new random value.
        if self.frame_counter > 0 and self.frame_counter % 500 == 0:
            snap.l2_effect_mode = random.randint(0, 2)
            snap.r2_effect_mode = random.randint(0, 2)

        return snap

    def set_led(self, r: int, g: int, b: int):
        if self.connected and self.ds:
            try:
                self.ds.light.setColorI(r, g, b)
            except Exception:
                pass

    def haptic(self, left: int = 0, right: int = 0):
        if self.connected and self.ds:
            try:
                self.ds.setRumble(left, right)
            except Exception:
                pass

    def close(self):
        if self.ds:
            try:
                self.ds.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════
# VAPI Agent — Three-Layer Architecture (Python implementation)
# Same structure as dualshock_agent.c
# ══════════════════════════════════════════════════════════════════

class VAPIAgent:
    """Full VAPI three-layer agent running on laptop."""

    def __init__(self, reader: DualSenseReader, verbose: bool = False):
        self.reader = reader
        self.verbose = verbose
        self.poac = PoACEngine()
        self.classifier = AntiCheatClassifier()
        self.world_model = WorldModel()
        self.state = STATE_BOOT
        self.running = False
        self.chain: list[PoACRecord] = []
        self.bounties: list[Bounty] = [
            Bounty(1001, 50_000_000, 50, "Play 50 clean PoAC windows"),
            Bounty(1002, 25_000_000, 100, "Speedrun verification"),
        ]
        self.consecutive_clean = 0
        self.stats = {
            "total_records": 0, "clean": 0, "cheats": 0,
            "session_frames": 0, "cheat_types": {},
        }

    def start(self):
        """Start the agent and enter main loop."""
        self.running = True
        self.state = STATE_BOOT

        # Generate BOOT PoAC (same as firmware ds_agent_start)
        boot_record = self.poac.generate(
            sensor_hash=b'\x00' * 32,
            wm_hash=self.world_model.compute_hash(),
            inference=INFER_PLAY_NOMINAL,
            action=ACTION_BOOT,
            confidence=0,
            battery_pct=100,
        )
        self.chain.append(boot_record)
        self._print_record(boot_record, "BOOT")

        self.state = STATE_IDLE
        self._print_state_change(STATE_BOOT, STATE_IDLE)

    def start_session(self):
        """Start a game session (IDLE → SESSION)."""
        if self.state != STATE_IDLE:
            print("[AGENT] Cannot start session: not in IDLE state")
            return

        self.classifier.reset()
        self.world_model.total_sessions += 1

        record = self.poac.generate(
            sensor_hash=b'\x00' * 32,
            wm_hash=self.world_model.compute_hash(),
            inference=INFER_PLAY_NOMINAL,
            action=ACTION_SESSION_START,
            confidence=0,
            battery_pct=100,
        )
        self.chain.append(record)
        self._print_record(record, "SESSION START")

        self.state = STATE_SESSION
        self._print_state_change(STATE_IDLE, STATE_SESSION)
        self.reader.set_led(0, 255, 0)  # GREEN

    def end_session(self):
        """End game session → IDLE."""
        record = self.poac.generate(
            sensor_hash=b'\x00' * 32,
            wm_hash=self.world_model.compute_hash(),
            inference=INFER_PLAY_NOMINAL,
            action=ACTION_SESSION_END,
            confidence=0,
            battery_pct=100,
        )
        self.chain.append(record)
        self._print_record(record, "SESSION END")
        self.state = STATE_IDLE
        self.reader.set_led(0, 0, 255)  # BLUE

    def l1_cycle(self, snapshot: InputSnapshot):
        """Layer 1: Gaming Reflexive — runs every input poll.
        Same pipeline as l1_gaming_reflexive_task() in firmware."""
        if self.state < STATE_SESSION:
            return

        dt_ms = snapshot.inter_frame_us / 1000.0
        self.stats["session_frames"] += 1

        # 1. Feature extraction (every frame)
        features = self.classifier.extract_features(snapshot, dt_ms)
        self.world_model.total_frames += 1

        # 2. TinyML classification (every 100 frames = 100ms at 1kHz)
        if self.world_model.total_frames % 100 == 0:
            inference, confidence = self.classifier.classify()
            is_cheat = inference >= INFER_CHEAT_REACTION

            if is_cheat and confidence >= THRESHOLD_CHEAT_CONFIDENCE:
                if self.state != STATE_CHEAT:
                    self.state = STATE_CHEAT
                    self.reader.set_led(255, 0, 0)  # RED
                    self.reader.haptic(200, 200)
                self.consecutive_clean = 0
                self.world_model.total_cheat_flags += 1
                name = INFER_NAMES.get(inference, "UNKNOWN")
                self.stats["cheats"] += 1
                self.stats["cheat_types"][name] = self.stats["cheat_types"].get(name, 0) + 1
            else:
                if self.state == STATE_CHEAT:
                    self.consecutive_clean += 1
                    if self.consecutive_clean >= CHEAT_RESOLVE_COUNT:
                        self.state = STATE_SESSION
                        self.reader.set_led(0, 255, 0)
                        self.consecutive_clean = 0

            # 3. Generate PoAC record (2 Hz normal, every ~500ms)
            if self.world_model.total_frames % 500 == 0 or is_cheat:
                timestamp_ms = int(time.time() * 1000)
                commitment_bytes = struct.pack(
                    ">hhhhBBBBffffffIQ",
                    snapshot.left_stick_x,    snapshot.left_stick_y,
                    snapshot.right_stick_x,   snapshot.right_stick_y,
                    snapshot.l2_trigger,      snapshot.r2_trigger,
                    snapshot.l2_effect_mode,  snapshot.r2_effect_mode,
                    snapshot.accel_x, snapshot.accel_y, snapshot.accel_z,
                    snapshot.gyro_x,  snapshot.gyro_y,  snapshot.gyro_z,
                    snapshot.buttons,
                    timestamp_ms,
                )
                sensor_hash = hashlib.sha256(commitment_bytes).digest()
                wm_hash = self.world_model.compute_hash()

                action = ACTION_CHEAT_ALERT if self.state == STATE_CHEAT else ACTION_REPORT
                batt = int((snapshot.battery_mv - 3000) / 12)
                batt = max(0, min(100, batt))

                record = self.poac.generate(
                    sensor_hash=sensor_hash,
                    wm_hash=wm_hash,
                    inference=inference,
                    action=action,
                    confidence=confidence,
                    battery_pct=batt,
                )
                self.chain.append(record)
                self.stats["total_records"] += 1
                if not is_cheat:
                    self.stats["clean"] += 1
                self.world_model.total_poac += 1
                self._print_record(record)

    def l2_cycle(self):
        """Layer 2: Anti-Cheat Deliberative — runs every 5 seconds.
        Updates world model baselines, same as L2 in firmware."""
        if self.state < STATE_SESSION:
            return

        # Update skill profile from recent features
        recent = list(self.classifier.window)
        if len(recent) > 10:
            avg_react = sum(f.reaction_ms for f in recent if f.reaction_ms > 0)
            react_count = sum(1 for f in recent if f.reaction_ms > 0)
            avg_react = avg_react / react_count if react_count > 0 else 250.0
            avg_prec = sum(f.stick_r_vel for f in recent) / len(recent)
            avg_var = self.classifier.press_variance
            avg_corr = sum(f.imu_corr for f in recent) / len(recent)

            self.world_model.update(avg_react, avg_prec, avg_var, avg_corr)

        # Evaluate bounties (same as economic_optimize_bounties)
        for bounty in self.bounties:
            if not bounty.accepted and bounty.utility(100) > 0:
                bounty.accepted = True
                print(f"  [BOUNTY] Accepted #{bounty.bounty_id}: {bounty.description} "
                      f"(reward: {bounty.reward_iotx} IOTX)")

        # Increment bounty progress
        for bounty in self.bounties:
            if bounty.accepted and bounty.samples_submitted < bounty.min_samples:
                bounty.samples_submitted += 1

    def verify_chain(self) -> tuple[bool, list]:
        """Verify integrity of entire PoAC chain — same checks as PoACVerifier contract."""
        breaks = []
        for i in range(1, len(self.chain)):
            expected = hashlib.sha256(self.chain[i-1].serialize_full()).digest()
            if self.chain[i].prev_poac_hash != expected:
                breaks.append(i)
            if self.chain[i].monotonic_ctr <= self.chain[i-1].monotonic_ctr:
                breaks.append(i)
        return len(breaks) == 0, breaks

    def export_chain(self, path: str):
        """Export PoAC chain to JSON."""
        data = {
            "device_pubkey": self.poac.public_key_bytes.hex(),
            "record_count": len(self.chain),
            "records": [r.to_dict() for r in self.chain],
            "stats": self.stats,
            "world_model": {
                "skill_rating": self.world_model.session_skill_rating,
                "total_frames": self.world_model.total_frames,
                "total_sessions": self.world_model.total_sessions,
                "total_cheat_flags": self.world_model.total_cheat_flags,
                "reaction_baseline": self.world_model.reaction_baseline,
            },
        }
        Path(path).write_text(json.dumps(data, indent=2))
        print(f"[EXPORT] Chain exported to {path} ({len(self.chain)} records)")

    def export_chain_binary(self, path: str):
        """Export PoAC chain as raw 228-byte concatenated records."""
        raw = b"".join(r.serialize_full() for r in self.chain)
        Path(path).write_bytes(raw)
        print(f"[EXPORT] Binary chain exported to {path} ({len(raw)} bytes)")

    def _print_record(self, record: PoACRecord, label: str = ""):
        cheat = record.inference_result >= INFER_CHEAT_REACTION
        marker = "\033[91m!!! CHEAT" if cheat else "\033[92m    CLEAN"
        name = INFER_NAMES.get(record.inference_result, "?")
        act = ACTION_NAMES.get(record.action_code, "?")
        conf = f"{record.confidence / 255 * 100:.0f}%"
        h = record.record_hash().hex()[:12]

        if label:
            print(f"{marker}\033[0m  #{record.monotonic_ctr:>5d} | {label:18s} | {h}")
        else:
            print(f"{marker}\033[0m  #{record.monotonic_ctr:>5d} | {name:18s} ({conf:>4s}) "
                  f"| {act:14s} | bat:{record.battery_pct:>3d}% | {h}")

        if self.verbose and not cheat:
            pass  # Only log cheats in non-verbose
        elif cheat:
            print(f"         \033[91m>>> ALERT: {name} detected with {conf} confidence\033[0m")

    def _print_state_change(self, old: int, new: int):
        print(f"  [STATE] {STATE_NAMES[old]} → {STATE_NAMES[new]}")

    def print_summary(self):
        valid, breaks = self.verify_chain()
        print("\n" + "=" * 70)
        print("  VAPI DualSense Edge — Session Summary")
        print("=" * 70)
        print(f"  PoAC Records Generated:  {len(self.chain)}")
        print(f"  Clean Windows:           {self.stats['clean']}")
        print(f"  Cheat Detections:        {self.stats['cheats']}")
        if self.stats['cheat_types']:
            for ctype, count in self.stats['cheat_types'].items():
                print(f"    - {ctype}: {count}")
        print(f"  Total Input Frames:      {self.stats['session_frames']}")
        print(f"  Chain Integrity:         {'VALID' if valid else f'BROKEN at {breaks}'}")
        print(f"  Chain Length:            {len(self.chain)} records")
        print(f"  Device Public Key:       {self.poac.public_key_bytes.hex()[:32]}...")
        print(f"  Skill Rating:            {self.world_model.session_skill_rating:.0f}")
        print(f"  Signed:                  {'YES (ECDSA-P256)' if HAS_CRYPTO else 'NO (unsigned)'}")
        for b in self.bounties:
            status = f"{b.samples_submitted}/{b.min_samples}"
            print(f"  Bounty #{b.bounty_id}: {status} {'COMPLETE' if b.samples_submitted >= b.min_samples else 'active' if b.accepted else 'pending'}")
        print("=" * 70)


# ══════════════════════════════════════════════════════════════════
# Main Loop
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VAPI DualSense Edge Laptop Emulator — Anti-Cheat Testing"
    )
    parser.add_argument("--simulate", action="store_true",
                        help="Use simulated inputs (no controller required)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Log every PoAC record")
    parser.add_argument("--export", type=str, default=None,
                        help="Export PoAC chain to JSON file on exit")
    parser.add_argument("--export-binary", type=str, default=None,
                        help="Export PoAC chain as raw binary on exit")
    parser.add_argument("--duration", type=int, default=60,
                        help="Session duration in seconds (default: 60)")
    parser.add_argument("--poll-rate", type=int, default=100,
                        help="Input poll rate in Hz (default: 100, max 1000)")
    args = parser.parse_args()

    print("=" * 70)
    print("  VAPI DualSense Edge Laptop Emulator v1.0")
    print("  Proof of Autonomous Cognition — Anti-Cheat Testing")
    print("=" * 70)
    print()

    # Initialize controller reader
    reader = DualSenseReader()
    if not args.simulate:
        if not reader.connect():
            print("\n[INFO] No controller found. Use --simulate for synthetic input.")
            print("[INFO] Falling back to simulation mode.\n")
            args.simulate = True

    agent = VAPIAgent(reader, verbose=args.verbose)

    # Boot
    print("\n--- Agent Boot ---")
    agent.start()

    # Start session
    print("\n--- Starting Game Session ---")
    agent.start_session()

    poll_interval = 1.0 / args.poll_rate
    l2_interval = 5.0
    last_l2 = time.time()
    start_time = time.time()

    print(f"\n[RUN] Polling at {args.poll_rate} Hz for {args.duration} seconds...")
    print(f"[RUN] PoAC records generated every ~500 frames")
    print(f"[RUN] Press Ctrl+C to stop early\n")

    try:
        while time.time() - start_time < args.duration:
            # L1: Poll + process
            snapshot = reader.poll()
            agent.l1_cycle(snapshot)

            # L2: Deliberative (every 5 seconds)
            if time.time() - last_l2 >= l2_interval:
                agent.l2_cycle()
                last_l2 = time.time()

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\n[STOP] Interrupted by user")

    # End session
    print("\n--- Ending Game Session ---")
    agent.end_session()

    # Summary
    agent.print_summary()

    # Export
    if args.export:
        agent.export_chain(args.export)
    if args.export_binary:
        agent.export_chain_binary(args.export_binary)

    # Cleanup
    reader.close()


if __name__ == "__main__":
    main()
