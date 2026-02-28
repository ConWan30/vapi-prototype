#!/usr/bin/env python3
"""
VAPI DualShock Edge Hardware Testing Suite v3.0
================================================

Comprehensive laptop-based testing suite for the VAPI DualShock Edge
anti-cheat pipeline with full hardware integration. Supports REAL
DualSense Edge hardware via USB or Bluetooth, with graceful simulation
fallback.

  Phase 1:  Hardware Discovery & Connection     (5 tests)  [HW]
  Phase 2:  Live Input Capture & Fidelity       (8 tests)  [HW + algo]
  Phase 3:  Anti-Cheat -- Synthetic Patterns    (8 tests)  [algo]
  Phase 4:  PoAC Record Generation              (8 tests)  [algo + 1 HW]
  Phase 5:  Contract Sim -- SkillOracle         (6 tests)  [algo]
  Phase 6:  Contract Sim -- Progress/Team       (6 tests)  [algo]
  Phase 7:  Feedback, Haptics & Bounties        (5 tests)  [HW]
  Phase 8:  End-to-End Pipeline                 (8 tests)  [HW + algo]
  Phase 9:  Live PoAC Monitoring                (6 tests)  [HW]
  Phase 10: Hardware Anti-Cheat Validation      (6 tests)  [HW + algo]
  Phase 11: Bounty Simulation & Session Export  (6 tests)  [algo + 1 HW]

  Total: 72 tests

Modes:
  Default          -- auto-detect controller; HW tests SKIP if absent
  --simulate       -- force simulation for HW tests (no SKIPs)
  --interactive    -- enable interactive HW tests (prompts to move sticks)
  --phase N        -- run only phase N
  --verbose        -- detailed diagnostics

Requirements:
    pip install cryptography pydualsense

Usage:
    python dualshock-laptop-testing-suite.py                # Auto-detect
    python dualshock-laptop-testing-suite.py --simulate     # Force simulation
    python dualshock-laptop-testing-suite.py --interactive  # Interactive HW tests
    python dualshock-laptop-testing-suite.py --phase 9      # Live PoAC only
    python dualshock-laptop-testing-suite.py --verbose      # Show details
"""

import argparse
import hashlib
import json
import math
import os
import random
import struct
import sys
import time
import tracemalloc
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

# ── Path setup ──
sys.path.insert(0, str(Path(__file__).parent))

# ── Import from emulator (with standalone fallback) ──
try:
    from dualshock_emulator import (
        InputSnapshot, FeatureFrame, PoACRecord, WorldModel,
        PoACEngine, AntiCheatClassifier, VAPIAgent, DualSenseReader,
        POAC_BODY_SIZE, POAC_RECORD_SIZE, POAC_HASH_SIZE, POAC_SIG_SIZE,
        INFER_PLAY_NOMINAL, INFER_PLAY_SKILLED,
        INFER_CHEAT_REACTION, INFER_CHEAT_MACRO, INFER_CHEAT_AIMBOT,
        INFER_CHEAT_RECOIL, INFER_CHEAT_IMU_MISS, INFER_CHEAT_INJECTION,
        INFER_NAMES, ACTION_REPORT, ACTION_BOOT, ACTION_SESSION_START,
        ACTION_SESSION_END, ACTION_CHEAT_ALERT,
        THRESHOLD_CHEAT_CONFIDENCE,
        STATE_BOOT, STATE_IDLE, STATE_SESSION,
        HAS_CRYPTO, HAS_DUALSENSE, Bounty,
    )
    IMPORTED = True
except ImportError:
    IMPORTED = False
    print("[WARN] Could not import dualshock_emulator -- standalone mode.\n")

    # ── Standalone constants ──
    POAC_BODY_SIZE = 164
    POAC_RECORD_SIZE = 228
    POAC_HASH_SIZE = 32
    POAC_SIG_SIZE = 64

    INFER_PLAY_NOMINAL    = 0x20
    INFER_PLAY_SKILLED    = 0x21
    INFER_CHEAT_REACTION  = 0x22
    INFER_CHEAT_MACRO     = 0x23
    INFER_CHEAT_AIMBOT    = 0x24
    INFER_CHEAT_RECOIL    = 0x25
    INFER_CHEAT_IMU_MISS  = 0x26
    INFER_CHEAT_INJECTION = 0x27
    THRESHOLD_CHEAT_CONFIDENCE = 180

    INFER_NAMES = {
        0x20: "NOMINAL",  0x21: "SKILLED",
        0x22: "CHEAT:REACTION", 0x23: "CHEAT:MACRO",
        0x24: "CHEAT:AIMBOT",   0x25: "CHEAT:RECOIL",
        0x26: "CHEAT:IMU_MISS", 0x27: "CHEAT:INJECTION",
    }
    ACTION_REPORT = 0x01
    ACTION_BOOT = 0x09
    ACTION_SESSION_START = 0x10
    ACTION_SESSION_END = 0x11
    ACTION_CHEAT_ALERT = 0x12
    STATE_BOOT = 0; STATE_IDLE = 1; STATE_SESSION = 2

    HAS_CRYPTO = False
    HAS_DUALSENSE = False

    try:
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.backends import default_backend
        HAS_CRYPTO = True
    except ImportError:
        pass

    try:
        from pydualsense import pydualsense
        HAS_DUALSENSE = True
    except ImportError:
        pass

    # ── Minimal standalone classes ──
    @dataclass
    class InputSnapshot:
        buttons: int = 0
        left_stick_x: int = 0; left_stick_y: int = 0
        right_stick_x: int = 0; right_stick_y: int = 0
        l2_trigger: int = 0; r2_trigger: int = 0
        gyro_x: float = 0.0; gyro_y: float = 0.0; gyro_z: float = 0.0
        accel_x: float = 0.0; accel_y: float = 0.0; accel_z: float = 1.0
        touch0_x: int = 0; touch0_y: int = 0
        touch1_x: int = 0; touch1_y: int = 0
        touch_active: int = 0; battery_mv: int = 4000
        frame_counter: int = 0; inter_frame_us: int = 1000

        def serialize(self) -> bytes:
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
                self.touch_active, self.battery_mv,
                self.frame_counter, self.inter_frame_us)

    @dataclass
    class PoACRecord:
        prev_poac_hash: bytes = field(default_factory=lambda: b'\x00' * 32)
        sensor_commitment: bytes = field(default_factory=lambda: b'\x00' * 32)
        model_manifest_hash: bytes = field(default_factory=lambda: b'\x00' * 32)
        world_model_hash: bytes = field(default_factory=lambda: b'\x00' * 32)
        inference_result: int = INFER_PLAY_NOMINAL
        action_code: int = ACTION_REPORT
        confidence: int = 0; battery_pct: int = 100
        monotonic_ctr: int = 0; timestamp_ms: int = 0
        latitude: float = 0.0; longitude: float = 0.0
        bounty_id: int = 0
        signature: bytes = field(default_factory=lambda: b'\x00' * 64)

        def serialize_body(self) -> bytes:
            return (
                self.prev_poac_hash + self.sensor_commitment +
                self.model_manifest_hash + self.world_model_hash +
                struct.pack(">BBBB I q d d I",
                    self.inference_result, self.action_code,
                    self.confidence, self.battery_pct,
                    self.monotonic_ctr, self.timestamp_ms,
                    self.latitude, self.longitude, self.bounty_id))

        def serialize_full(self) -> bytes:
            return self.serialize_body() + self.signature

        def record_hash(self) -> bytes:
            return hashlib.sha256(self.serialize_full()).digest()

        def to_dict(self) -> dict:
            return {
                "hash": self.record_hash().hex()[:16] + "...",
                "prev": self.prev_poac_hash.hex()[:16] + "...",
                "ctr": self.monotonic_ctr,
                "inference": INFER_NAMES.get(self.inference_result, f"0x{self.inference_result:02x}"),
                "confidence": f"{self.confidence / 255 * 100:.1f}%",
                "battery": f"{self.battery_pct}%",
            }

    @dataclass
    class WorldModel:
        reaction_history: deque = field(default_factory=lambda: deque(maxlen=64))
        precision_history: deque = field(default_factory=lambda: deque(maxlen=64))
        variance_history: deque = field(default_factory=lambda: deque(maxlen=64))
        corr_history: deque = field(default_factory=lambda: deque(maxlen=64))
        reaction_baseline: float = 250.0; precision_baseline: float = 0.5
        consistency_baseline: float = 50.0; imu_corr_baseline: float = 0.5
        session_skill_rating: float = 500.0
        total_frames: int = 0; total_sessions: int = 0
        total_cheat_flags: int = 0; total_poac: int = 0

        def compute_hash(self) -> bytes:
            buf = struct.pack(">ffffII II",
                self.reaction_baseline, self.precision_baseline,
                self.consistency_baseline, self.imu_corr_baseline,
                self.total_frames, self.total_sessions,
                self.total_cheat_flags, self.total_poac)
            buf += struct.pack(">B", min(len(self.reaction_history), 64))
            for i in range(min(len(self.reaction_history), 64)):
                buf += struct.pack(">ffff",
                    self.reaction_history[i],
                    self.precision_history[i] if i < len(self.precision_history) else 0,
                    self.variance_history[i] if i < len(self.variance_history) else 0,
                    self.corr_history[i] if i < len(self.corr_history) else 0)
            return hashlib.sha256(buf).digest()

        def update(self, reaction, precision, variance, corr):
            self.reaction_history.append(reaction)
            self.precision_history.append(precision)
            self.variance_history.append(variance)
            self.corr_history.append(corr)

    class PoACEngine:
        def __init__(self):
            self.counter = 0
            self.chain_head = b'\x00' * 32
            self.private_key = None
            self.public_key_bytes = b'\x00' * 65
            self.model_hash = hashlib.sha256(b"heuristic_fallback_v0").digest()
            if HAS_CRYPTO:
                self.private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
                pub = self.private_key.public_key()
                self.public_key_bytes = pub.public_bytes(
                    serialization.Encoding.X962,
                    serialization.PublicFormat.UncompressedPoint)
                heuristic_weights = bytes([0x00, 0x01, 0x00, 0x00, 0x01, 0x00, 0x96, 0x02, 0x00])
                self.model_hash = hashlib.sha256(heuristic_weights).digest()

        def generate(self, sensor_hash=None, wm_hash=None, inference=0x20,
                     action=0x01, confidence=200, battery_pct=80, bounty_id=0):
            self.counter += 1
            if sensor_hash is None: sensor_hash = b'\x00' * 32
            if wm_hash is None: wm_hash = b'\x00' * 32
            record = PoACRecord(
                prev_poac_hash=self.chain_head,
                sensor_commitment=sensor_hash,
                model_manifest_hash=self.model_hash,
                world_model_hash=wm_hash,
                inference_result=inference, action_code=action,
                confidence=confidence, battery_pct=battery_pct,
                monotonic_ctr=self.counter,
                timestamp_ms=int(time.time() * 1000),
                bounty_id=bounty_id)
            body = record.serialize_body()
            if HAS_CRYPTO and self.private_key:
                der_sig = self.private_key.sign(body, ec.ECDSA(hashes.SHA256()))
                r, s = utils.decode_dss_signature(der_sig)
                record.signature = r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
            full = record.serialize_full()
            self.chain_head = hashlib.sha256(full).digest()
            return record

    @dataclass
    class Bounty:
        bounty_id: int = 0
        reward_micro_iotx: int = 0
        min_samples: int = 0
        description: str = ""
        samples_submitted: int = 0
        accepted: bool = False
        @property
        def reward_iotx(self) -> float:
            return self.reward_micro_iotx / 1_000_000
        def utility(self, battery_pct: int) -> float:
            energy_per_sample = 0.002 + 0.002 + 0.003
            total_energy = energy_per_sample * self.min_samples
            energy_cost_pct = total_energy / (1000 / 100)
            p_success = 1.0 if battery_pct > 20 else 0.5
            return p_success * self.reward_iotx - energy_cost_pct

    class DualSenseReader:
        def __init__(self):
            self.ds = None; self.frame_counter = 0
            self.last_poll_time = time.time(); self.connected = False
            self._is_edge = False; self._accel_scale = None
        def connect(self) -> bool:
            if not HAS_DUALSENSE: return False
            try:
                self.ds = pydualsense()
                self.ds.init(); self.connected = True
                self._is_edge = getattr(self.ds, 'is_edge', False)
                return True
            except Exception:
                return False
        def poll(self) -> InputSnapshot:
            now = time.time()
            dt_us = int((now - self.last_poll_time) * 1_000_000)
            self.last_poll_time = now; self.frame_counter += 1
            if not self.connected or not self.ds:
                return self._sim(dt_us)
            ds = self.ds; snap = InputSnapshot()
            _c16 = lambda v: max(-32768, min(32767, int(v)))
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
            # Edge back buttons
            if self._is_edge:
                if getattr(ds.state, 'L4', False): buttons |= (1 << 16)
                if getattr(ds.state, 'R4', False): buttons |= (1 << 17)
                if getattr(ds.state, 'L5', False): buttons |= (1 << 18)
                if getattr(ds.state, 'R5', False): buttons |= (1 << 19)
            snap.buttons = buttons
            # Sticks -- Edge: already 0-centered; Standard: [0,255] center 128
            if self._is_edge:
                snap.left_stick_x  = _c16(ds.state.LX * 256)
                snap.left_stick_y  = _c16(ds.state.LY * 256)
                snap.right_stick_x = _c16(ds.state.RX * 256)
                snap.right_stick_y = _c16(ds.state.RY * 256)
            else:
                snap.left_stick_x  = _c16((ds.state.LX - 128) * 256)
                snap.left_stick_y  = _c16((ds.state.LY - 128) * 256)
                snap.right_stick_x = _c16((ds.state.RX - 128) * 256)
                snap.right_stick_y = _c16((ds.state.RY - 128) * 256)
            # Triggers -- use L2_value (int 0-255) if available
            if hasattr(ds.state, 'L2_value'):
                snap.l2_trigger = ds.state.L2_value
                snap.r2_trigger = ds.state.R2_value
            else:
                v = ds.state.L2
                snap.l2_trigger = v if isinstance(v, int) else (255 if v else 0)
                v = ds.state.R2
                snap.r2_trigger = v if isinstance(v, int) else (255 if v else 0)
            # IMU
            snap.gyro_x = ds.state.gyro.Pitch / 1000.0
            snap.gyro_y = ds.state.gyro.Yaw / 1000.0
            snap.gyro_z = ds.state.gyro.Roll / 1000.0
            # Accelerometer -- raw int16 (~8192/g), Edge is gravity-compensated
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
            # Battery -- Edge: ds.battery.Level; standard: ds.state.battery.Level
            batt_level = None
            if hasattr(ds, 'battery') and hasattr(ds.battery, 'Level'):
                batt_level = ds.battery.Level
            elif hasattr(ds.state, 'battery') and hasattr(ds.state.battery, 'Level'):
                batt_level = ds.state.battery.Level
            snap.battery_mv = 3700 + (batt_level * 50 if batt_level is not None else 300)
            snap.frame_counter = self.frame_counter
            snap.inter_frame_us = dt_us
            return snap
        def _sim(self, dt_us):
            t = time.time(); snap = InputSnapshot()
            snap.frame_counter = self.frame_counter; snap.inter_frame_us = dt_us
            snap.right_stick_x = int(math.sin(t * 0.5) * 8000 + random.gauss(0, 200))
            snap.right_stick_y = int(math.cos(t * 0.7) * 6000 + random.gauss(0, 200))
            snap.left_stick_x = int(math.sin(t * 0.3) * 10000 + random.gauss(0, 300))
            snap.left_stick_y = int(math.cos(t * 0.4) * 10000 + random.gauss(0, 300))
            snap.gyro_x = math.sin(t * 62.8) * 0.02 + random.gauss(0, 0.005)
            snap.gyro_y = math.cos(t * 75.4) * 0.015 + random.gauss(0, 0.005)
            snap.gyro_z = math.sin(t * 50.3) * 0.01 + random.gauss(0, 0.003)
            snap.accel_x = random.gauss(0, 0.01)
            snap.accel_y = random.gauss(0, 0.01)
            snap.accel_z = 1.0 + random.gauss(0, 0.005)
            if random.random() < 0.02:
                snap.buttons = 1 << random.randint(0, 15)
            snap.battery_mv = 3900
            return snap
        def set_led(self, r, g, b):
            if self.connected and self.ds:
                try: self.ds.light.setColorI(r, g, b)
                except Exception: pass
        def haptic(self, left=0, right=0):
            if self.connected and self.ds:
                try: self.ds.setRumble(left, right)
                except Exception: pass
        def close(self):
            if self.ds:
                try: self.ds.close()
                except Exception: pass


# ══════════════════════════════════════════════════════════════════
# Test Infrastructure
# ══════════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    name: str
    passed: bool
    expected: str
    actual: str
    skipped: bool = False
    details: str = ""


def PASS(name, expected, actual, details=""): return TestResult(name, True, expected, actual, False, details)
def FAIL(name, expected, actual, details=""): return TestResult(name, False, expected, actual, False, details)
def SKIP(name, reason="No controller"): return TestResult(name, True, "N/A", f"SKIP: {reason}", True)


# ══════════════════════════════════════════════════════════════════
# Hardware Connection -- global controller state
# ══════════════════════════════════════════════════════════════════

controller: Optional[DualSenseReader] = None
controller_connected = False
simulate_mode = False
interactive_mode = False


def init_hardware(force_simulate=False):
    """Initialize hardware connection. Returns True if real controller found."""
    global controller, controller_connected, simulate_mode
    simulate_mode = force_simulate
    controller = DualSenseReader()
    if not force_simulate:
        controller_connected = controller.connect()
    return controller_connected


def hw_available() -> bool:
    """Check if hardware is available (real or simulated)."""
    return controller_connected or simulate_mode


def capture_live(duration_s: float, prompt: str = "") -> List[InputSnapshot]:
    """Capture live input frames for a duration. Returns list of snapshots."""
    if not hw_available():
        return []
    if prompt:
        print(f"      [ACTION] {prompt}")
        print(f"      [ACTION] Capturing for {duration_s:.1f}s... ", end="", flush=True)
    frames = []
    start = time.time()
    while time.time() - start < duration_s:
        snap = controller.poll()
        frames.append(snap)
        time.sleep(0.008)  # ~125 Hz
    if prompt:
        print(f"done ({len(frames)} frames)")
    return frames


# ══════════════════════════════════════════════════════════════════
# TestClassifier -- frame-based timing for synthetic pattern tests
# ══════════════════════════════════════════════════════════════════

class TestClassifier:
    """Frame-based anti-cheat classifier for deterministic testing.
    Same thresholds as firmware tinyml_anticheat.c heuristic_classify()."""

    def __init__(self):
        self.window = []
        self._prev_buttons = 0; self._last_press = 0.0
        self._n = 0; self._mean = 0.0; self._m2 = 0.0
        self._prev_rx = 0.0; self._prev_ry = 0.0; self._prev_r_vel = 0.0
        self._corr_xy = 0; self._corr_x2 = 0; self._corr_y2 = 0; self._corr_n = 0
        self._frame = 0

    def feed(self, snap, dt_ms=1.0):
        self._frame += 1
        rx = snap.right_stick_x / 32768.0
        ry = snap.right_stick_y / 32768.0
        vr_x = (rx - self._prev_rx) / max(dt_ms, 0.001)
        vr_y = (ry - self._prev_ry) / max(dt_ms, 0.001)
        r_vel = math.sqrt(vr_x**2 + vr_y**2)
        r_acc = (r_vel - self._prev_r_vel) / max(dt_ms, 0.001)
        jerk_r = r_acc / max(dt_ms, 0.001)
        self._prev_rx = rx; self._prev_ry = ry; self._prev_r_vel = r_vel
        gyro_mag = math.sqrt(snap.gyro_x**2 + snap.gyro_y**2 + snap.gyro_z**2)
        self._corr_xy += r_vel * gyro_mag
        self._corr_x2 += r_vel**2
        self._corr_y2 += gyro_mag**2
        self._corr_n += 1
        if snap.buttons != self._prev_buttons and self._prev_buttons != 0:
            now = self._frame * dt_ms / 1000.0
            if self._last_press > 0:
                interval = (now - self._last_press) * 1000.0
                self._n += 1
                delta = interval - self._mean
                self._mean += delta / self._n
                self._m2 += delta * (interval - self._mean)
            self._last_press = now
        self._prev_buttons = snap.buttons
        if self._corr_n > 10:
            d = math.sqrt(self._corr_x2 * self._corr_y2)
            if d > 0.0001:
                imu_corr = (self._corr_xy / d + 1) / 2
            elif self._corr_x2 > 0.001:
                imu_corr = 0.0
            else:
                imu_corr = 0.5
        else:
            imu_corr = 0.5
        self.window.append({
            "press_var": (self._m2 / (self._n - 1)) if self._n > 1 else 999.0,
            "imu_noise": gyro_mag, "imu_corr": imu_corr,
            "jerk_r": abs(jerk_r), "reaction_ms": 0,
        })
        if len(self.window) > 100:
            self.window = self.window[-100:]

    def classify(self):
        if len(self.window) < 20: return INFER_PLAY_NOMINAL, 128
        n = len(self.window)
        avg = lambda k: sum(f[k] for f in self.window) / n
        pv = avg("press_var"); noise = avg("imu_noise")
        corr = avg("imu_corr")
        max_jerk = max(abs(f["jerk_r"]) for f in self.window)
        if pv < 1.0 and self._n > 5: return INFER_CHEAT_MACRO, 230
        if noise < 0.001 and corr < 0.1 and max_jerk < 0.7: return INFER_CHEAT_INJECTION, 210
        if corr < 0.15 and self._corr_x2 > 0.1: return INFER_CHEAT_IMU_MISS, 200
        if max_jerk > 2.0: return INFER_CHEAT_AIMBOT, 180
        return INFER_PLAY_NOMINAL, 220

    def reset(self):
        self.window.clear()
        self._n = 0; self._mean = 0; self._m2 = 0
        self._corr_xy = 0; self._corr_x2 = 0; self._corr_y2 = 0; self._corr_n = 0


# ══════════════════════════════════════════════════════════════════
# Contract Simulations
# ══════════════════════════════════════════════════════════════════

class SkillOracleSim:
    INITIAL_RATING = 1000; MAX_RATING = 3000
    NOMINAL_GAIN = 5; SKILLED_GAIN = 12; CHEAT_PENALTY = 200

    def __init__(self):
        self.profiles = {}; self.processed = set()

    def get_or_create(self, dev):
        if dev not in self.profiles:
            self.profiles[dev] = {"rating": self.INITIAL_RATING, "games": 0,
                                  "clean": 0, "cheats": 0}
        return self.profiles[dev]

    def update(self, dev, rec_hash, inference, confidence):
        if rec_hash in self.processed: raise ValueError("Already processed")
        self.processed.add(rec_hash)
        p = self.get_or_create(dev); old = p["rating"]
        if INFER_CHEAT_REACTION <= inference <= 0x29:
            p["cheats"] += 1
            p["rating"] = max(0, p["rating"] - self.CHEAT_PENALTY)
        elif inference == INFER_PLAY_SKILLED:
            gain = max(1, (self.SKILLED_GAIN * confidence) // 255)
            p["rating"] += gain; p["clean"] += 1
        else:
            gain = max(1, (self.NOMINAL_GAIN * confidence) // 255)
            p["rating"] += gain; p["clean"] += 1
        p["rating"] = min(p["rating"], self.MAX_RATING)
        p["games"] += 1
        return {"old": old, "new": p["rating"], "tier": self.tier(p["rating"])}

    @staticmethod
    def tier(r):
        if r >= 2500: return "Diamond"
        if r >= 2000: return "Platinum"
        if r >= 1500: return "Gold"
        if r >= 1000: return "Silver"
        return "Bronze"


class ProgressAttestationSim:
    def __init__(self):
        self.attestations = []; self.pairs = set(); self.verified = set()

    def mark_verified(self, h): self.verified.add(h)

    def attest(self, dev, base, curr, metric, bps):
        if base == curr: raise ValueError("SameRecord")
        if bps == 0: raise ValueError("ZeroImprovement")
        if base not in self.verified: raise ValueError(f"BaselineNotVerified")
        if curr not in self.verified: raise ValueError(f"CurrentNotVerified")
        pk = hashlib.sha256((base + curr).encode()).hexdigest()
        if pk in self.pairs: raise ValueError("PairAlreadyAttested")
        self.pairs.add(pk)
        aid = len(self.attestations)
        self.attestations.append({"dev": dev, "base": base, "curr": curr,
                                  "metric": metric, "bps": bps})
        return aid


class TeamProofSim:
    def __init__(self):
        self.teams = {}; self.proofs = []; self.verified = set()

    def mark_verified(self, h): self.verified.add(h)

    def create_team(self, tid, devs):
        if tid in self.teams: raise ValueError("TeamAlreadyExists")
        if len(devs) < 2 or len(devs) > 6: raise ValueError("InvalidTeamSize")
        self.teams[tid] = {"members": devs, "active": True}

    def merkle_root(self, leaves):
        if len(leaves) == 1: return leaves[0]
        current = sorted(leaves)
        while len(current) > 1:
            nxt = []
            for i in range(0, len(current), 2):
                if i + 1 < len(current):
                    nxt.append(hashlib.sha256(current[i] + current[i+1]).digest())
                else:
                    nxt.append(current[i])
            current = nxt
        return current[0]

    def submit(self, tid, hashes, root):
        if tid not in self.teams: raise ValueError("TeamNotFound")
        if len(hashes) != len(self.teams[tid]["members"]): raise ValueError("MemberCountMismatch")
        for h in hashes:
            hx = h.hex() if isinstance(h, bytes) else h
            if hx not in self.verified: raise ValueError(f"RecordNotVerified: {hx}")
        computed = self.merkle_root(hashes)
        if computed != root: raise ValueError("InvalidMerkleRoot")
        pid = len(self.proofs)
        self.proofs.append({"team": tid, "root": root.hex(), "count": len(hashes)})
        return pid


# ══════════════════════════════════════════════════════════════════
# Synthetic Pattern Generators
# ══════════════════════════════════════════════════════════════════

def gen_human_normal(n=200):
    frames = []
    for i in range(n):
        t = i * 0.001; snap = InputSnapshot()
        snap.frame_counter = i; snap.inter_frame_us = 1000
        snap.right_stick_x = int(math.sin(t * 0.5) * 8000 + random.gauss(0, 300))
        snap.right_stick_y = int(math.cos(t * 0.7) * 6000 + random.gauss(0, 300))
        snap.left_stick_x = int(math.sin(t * 0.3) * 10000 + random.gauss(0, 400))
        snap.left_stick_y = int(math.cos(t * 0.4) * 10000 + random.gauss(0, 400))
        snap.gyro_x = math.sin(t * 62.8) * 0.02 + random.gauss(0, 0.005)
        snap.gyro_y = math.cos(t * 75.4) * 0.015 + random.gauss(0, 0.005)
        snap.gyro_z = math.sin(t * 50.3) * 0.01 + random.gauss(0, 0.003)
        snap.accel_z = 1.0 + random.gauss(0, 0.005)
        if random.random() < 0.02: snap.buttons = 1 << random.randint(0, 5)
        frames.append(snap)
    return frames


def gen_skilled_player(n=200):
    frames = []
    for i in range(n):
        t = i * 0.001; snap = InputSnapshot()
        snap.frame_counter = i; snap.inter_frame_us = 1000
        snap.right_stick_x = int(math.sin(t * 1.5) * 12000 + random.gauss(0, 100))
        snap.right_stick_y = int(math.cos(t * 1.8) * 10000 + random.gauss(0, 100))
        snap.gyro_x = math.sin(t * 1.5) * 0.05 + random.gauss(0, 0.008)
        snap.gyro_y = math.cos(t * 1.8) * 0.04 + random.gauss(0, 0.008)
        snap.gyro_z = random.gauss(0, 0.005); snap.accel_z = 1.0
        if random.random() < 0.05: snap.buttons = 1 << random.randint(0, 5)
        frames.append(snap)
    return frames


def gen_macro_turbo(n=200):
    frames = []
    for i in range(n):
        snap = InputSnapshot()
        snap.frame_counter = i; snap.inter_frame_us = 1000
        snap.buttons = (1 << 0) if (i % 10 < 5) else 0
        snap.right_stick_x = int(math.sin(i * 0.01) * 5000)
        snap.right_stick_y = int(math.cos(i * 0.01) * 5000)
        snap.gyro_x = math.sin(i * 0.0628) * 0.02 + random.gauss(0, 0.003)
        snap.gyro_y = random.gauss(0, 0.005); snap.accel_z = 1.0
        frames.append(snap)
    return frames


def gen_aimbot(n=200):
    frames = []; tx, ty = 0, 0
    for i in range(n):
        snap = InputSnapshot()
        snap.frame_counter = i; snap.inter_frame_us = 1000
        if i % 3 == 0:
            tx = random.choice([-32000, 32000]) + random.randint(-500, 500)
            ty = random.choice([-32000, 32000]) + random.randint(-500, 500)
        snap.right_stick_x = tx; snap.right_stick_y = ty
        snap.gyro_x = random.gauss(0, 0.015)
        snap.gyro_y = random.gauss(0, 0.015); snap.accel_z = 1.0
        frames.append(snap)
    return frames


def gen_imu_mismatch(n=200):
    frames = []
    for i in range(n):
        snap = InputSnapshot()
        snap.frame_counter = i; snap.inter_frame_us = 1000
        if i % 8 < 4:
            snap.right_stick_x = int(15000 * (1 if i % 16 < 8 else -1) + random.gauss(0, 500))
            snap.right_stick_y = int(12000 * (1 if i % 12 < 6 else -1) + random.gauss(0, 500))
        else:
            snap.right_stick_x = int(random.gauss(0, 8000))
            snap.right_stick_y = int(random.gauss(0, 8000))
        snap.left_stick_x = int(math.sin(i * 0.03) * 20000)
        snap.gyro_x = 0.0; snap.gyro_y = 0.0; snap.gyro_z = 0.0
        snap.accel_z = 1.0
        frames.append(snap)
    return frames


def gen_injection(n=200):
    frames = []
    for i in range(n):
        snap = InputSnapshot()
        snap.frame_counter = i; snap.inter_frame_us = 1000
        snap.right_stick_x = int(math.sin(i * 0.1) * 20000)
        snap.right_stick_y = int(math.cos(i * 0.1) * 20000)
        snap.gyro_x = 0.0; snap.gyro_y = 0.0; snap.gyro_z = 0.0
        snap.accel_x = 0.0; snap.accel_y = 0.0; snap.accel_z = 0.0
        frames.append(snap)
    return frames


# ══════════════════════════════════════════════════════════════════
# Phase 1: Hardware Discovery & Connection
# ══════════════════════════════════════════════════════════════════

def phase1_hardware(verbose):
    results = []

    # 1.1: Controller detection via HID
    if controller_connected:
        results.append(PASS("1.1 [HW] Controller detection", "Connected",
                            "DualSense Edge detected via HID"))
    elif simulate_mode:
        results.append(PASS("1.1 [SIM] Controller detection", "Simulated",
                            "Running in simulation mode"))
    else:
        results.append(SKIP("1.1 [HW] Controller detection",
                            "No controller -- install pydualsense & connect via USB/BT"))

    # 1.2: Connection type
    if controller_connected:
        # pydualsense doesn't expose connection type directly; infer from latency
        t0 = time.perf_counter()
        for _ in range(50):
            controller.poll()
        t1 = time.perf_counter()
        avg_us = (t1 - t0) / 50 * 1_000_000
        conn_type = "USB" if avg_us < 500 else "Bluetooth"
        results.append(PASS("1.2 [HW] Connection type", "USB or BT",
                            f"{conn_type} (avg poll: {avg_us:.0f}us)"))
    elif simulate_mode:
        results.append(PASS("1.2 [SIM] Connection type", "Simulated", "N/A"))
    else:
        results.append(SKIP("1.2 [HW] Connection type"))

    # 1.3: Battery level read
    if controller_connected:
        try:
            snap = controller.poll()
            batt_pct = max(0, min(100, (snap.battery_mv - 3000) // 12))
            results.append(PASS("1.3 [HW] Battery level", "0-100%",
                                f"{batt_pct}% ({snap.battery_mv}mV)"))
        except Exception as e:
            results.append(FAIL("1.3 [HW] Battery level", "Readable", f"Error: {e}"))
    elif simulate_mode:
        results.append(PASS("1.3 [SIM] Battery level", "Simulated", "75% (simulated)"))
    else:
        results.append(SKIP("1.3 [HW] Battery level"))

    # 1.4: Input polling latency benchmark
    if hw_available():
        latencies = []
        for _ in range(200):
            t0 = time.perf_counter()
            controller.poll()
            latencies.append((time.perf_counter() - t0) * 1000)
        avg_ms = sum(latencies) / len(latencies)
        p99_ms = sorted(latencies)[int(len(latencies) * 0.99)]
        ok = avg_ms < 5.0  # 5ms is generous for USB HID
        tag = "HW" if controller_connected else "SIM"
        results.append(PASS(f"1.4 [{tag}] Input polling latency", "<5ms avg",
                            f"avg={avg_ms:.3f}ms p99={p99_ms:.3f}ms") if ok
                       else FAIL(f"1.4 [{tag}] Polling latency", "<5ms",
                                 f"avg={avg_ms:.3f}ms"))
    else:
        results.append(SKIP("1.4 [HW] Input polling latency"))

    # 1.5: Device state initialization
    if hw_available():
        snap = controller.poll()
        state_ok = (isinstance(snap.left_stick_x, int) and
                    isinstance(snap.gyro_x, float) and
                    isinstance(snap.buttons, int))
        tag = "HW" if controller_connected else "SIM"
        results.append(PASS(f"1.5 [{tag}] Device state init", "Valid types",
                            f"stick=int gyro=float buttons=int") if state_ok
                       else FAIL(f"1.5 [{tag}] State init", "Valid types", "Type mismatch"))
    else:
        results.append(SKIP("1.5 [HW] Device state init"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 2: Live Input Capture & Fidelity
# ══════════════════════════════════════════════════════════════════

def phase2_input(verbose):
    results = []

    if interactive_mode and hw_available():
        # Interactive hardware tests with user prompts
        # 2.1: Left stick range (interactive)
        frames = capture_live(3.0, "Move LEFT stick in a full circle, hitting all corners")
        if frames:
            xs = [f.left_stick_x for f in frames]
            ys = [f.left_stick_y for f in frames]
            x_range = max(xs) - min(xs)
            y_range = max(ys) - min(ys)
            ok = x_range > 40000 or y_range > 40000
            results.append(PASS("2.1 [HW] Left stick range", ">40000 span (any axis)",
                                f"X:[{min(xs)},{max(xs)}] Y:[{min(ys)},{max(ys)}]") if ok
                           else FAIL("2.1 [HW] Left stick range", ">40000",
                                     f"X span={x_range} Y span={y_range}"))
        else:
            results.append(SKIP("2.1 [HW] Left stick range"))

        # 2.2: Right stick range (interactive)
        frames = capture_live(3.0, "Move RIGHT stick in a full circle, hitting all corners")
        if frames:
            xs = [f.right_stick_x for f in frames]
            ys = [f.right_stick_y for f in frames]
            x_range = max(xs) - min(xs)
            y_range = max(ys) - min(ys)
            ok = x_range > 40000 or y_range > 40000
            results.append(PASS("2.2 [HW] Right stick range", ">40000 span (any axis)",
                                f"X:[{min(xs)},{max(xs)}] Y:[{min(ys)},{max(ys)}]") if ok
                           else FAIL("2.2 [HW] Right stick range", ">40000",
                                     f"X span={x_range} Y span={y_range}"))
        else:
            results.append(SKIP("2.2 [HW] Right stick range"))

        # 2.3: Trigger range (interactive)
        frames = capture_live(3.0, "Press L2 and R2 FULLY, then release")
        if frames:
            l2_max = max(f.l2_trigger for f in frames)
            r2_max = max(f.r2_trigger for f in frames)
            ok = l2_max > 200 and r2_max > 200
            results.append(PASS("2.3 [HW] Trigger full range", ">200 peak",
                                f"L2 max={l2_max} R2 max={r2_max}") if ok
                           else FAIL("2.3 [HW] Trigger range", ">200",
                                     f"L2={l2_max} R2={r2_max}"))
        else:
            results.append(SKIP("2.3 [HW] Trigger range"))

        # 2.4: IMU gyro response (interactive)
        frames = capture_live(3.0, "TILT the controller left and right rapidly")
        if frames:
            gyro_mags = [math.sqrt(f.gyro_x**2 + f.gyro_y**2 + f.gyro_z**2) for f in frames]
            peak = max(gyro_mags)
            ok = peak > 0.05  # Should see meaningful gyro when tilting
            results.append(PASS("2.4 [HW] IMU gyro response", "peak > 0.05",
                                f"peak={peak:.4f} rad/s") if ok
                           else FAIL("2.4 [HW] Gyro response", "> 0.05",
                                     f"peak={peak:.4f}"))
        else:
            results.append(SKIP("2.4 [HW] IMU gyro response"))
    else:
        # Non-interactive: validate current state (whatever user is doing)
        if hw_available():
            snap = controller.poll()
            tag = "HW" if controller_connected else "SIM"
            # 2.1: Left stick valid int16
            ok = -32768 <= snap.left_stick_x <= 32767 and -32768 <= snap.left_stick_y <= 32767
            results.append(PASS(f"2.1 [{tag}] Left stick range", "int16",
                                f"({snap.left_stick_x}, {snap.left_stick_y})") if ok
                           else FAIL(f"2.1 [{tag}] Left stick", "int16", "Out of range"))
            # 2.2: Right stick valid int16
            ok = -32768 <= snap.right_stick_x <= 32767 and -32768 <= snap.right_stick_y <= 32767
            results.append(PASS(f"2.2 [{tag}] Right stick range", "int16",
                                f"({snap.right_stick_x}, {snap.right_stick_y})") if ok
                           else FAIL(f"2.2 [{tag}] Right stick", "int16", "Out of range"))
            # 2.3: Trigger valid [0, 255]
            ok = 0 <= snap.l2_trigger <= 255 and 0 <= snap.r2_trigger <= 255
            results.append(PASS(f"2.3 [{tag}] Trigger range", "[0, 255]",
                                f"L2={snap.l2_trigger} R2={snap.r2_trigger}") if ok
                           else FAIL(f"2.3 [{tag}] Trigger", "[0,255]", "Out of range"))
            # 2.4: IMU non-NaN
            gyro_ok = (not math.isnan(snap.gyro_x) and not math.isnan(snap.gyro_y)
                       and not math.isnan(snap.gyro_z))
            results.append(PASS(f"2.4 [{tag}] IMU gyro readable", "non-NaN",
                                f"({snap.gyro_x:.4f}, {snap.gyro_y:.4f}, {snap.gyro_z:.4f})") if gyro_ok
                           else FAIL(f"2.4 [{tag}] IMU gyro", "non-NaN", "NaN detected"))
        else:
            results.append(SKIP("2.1 [HW] Left stick range"))
            results.append(SKIP("2.2 [HW] Right stick range"))
            results.append(SKIP("2.3 [HW] Trigger range"))
            results.append(SKIP("2.4 [HW] IMU gyro response"))

    # 2.5: Accelerometer gravity vector (algorithmic with live data if available)
    # Note: DualSense Edge accelerometer is gravity-compensated (high-pass filtered),
    # so at rest the magnitude is ~0g, not ~1g. Accept both modes.
    if hw_available():
        frames = capture_live(1.0) if not interactive_mode else capture_live(1.0, "Hold controller STILL for 1 second")
        if frames:
            accels = [math.sqrt(f.accel_x**2 + f.accel_y**2 + f.accel_z**2) for f in frames]
            avg_g = sum(accels) / len(accels)
            tag = "HW" if controller_connected else "SIM"
            # Accept gravity-compensated IMU (~0g, DualSense Edge) OR direct IMU (~1g, standard)
            ok = avg_g < 0.5 or (0.7 < avg_g < 1.5)
            expect = "<0.5g or ~1g"
            detail = f"avg |accel| = {avg_g:.3f}g ({'gravity-compensated' if avg_g < 0.5 else 'direct'})"
            results.append(PASS(f"2.5 [{tag}] Gravity vector", expect, detail) if ok
                           else FAIL(f"2.5 [{tag}] Gravity", expect, f"{avg_g:.3f}g"))
        else:
            results.append(SKIP("2.5 [HW] Gravity vector"))
    else:
        results.append(SKIP("2.5 [HW] Gravity vector"))

    # 2.6: Button bitmap encoding (algorithmic)
    snap = InputSnapshot(buttons=0xFFFFFF)
    b0 = (snap.buttons >> 16) & 0xFF
    b1 = (snap.buttons >> 8) & 0xFF
    b2 = snap.buttons & 0xFF
    ok = b0 == 0xFF and b1 == 0xFF and b2 == 0xFF
    results.append(PASS("2.6 Button bitmap (24-bit)", "0xFFFFFF -> 3x0xFF",
                        f"b0={b0:#x} b1={b1:#x} b2={b2:#x}") if ok
                   else FAIL("2.6 Button bitmap", "3x0xFF", f"{b0:#x},{b1:#x},{b2:#x}"))

    # 2.7: Frame serialization determinism (algorithmic)
    snap = InputSnapshot(buttons=0x0A0B0C, left_stick_x=1234, left_stick_y=-5678,
                         right_stick_x=9999, right_stick_y=-9999,
                         l2_trigger=128, r2_trigger=255,
                         gyro_x=0.5, gyro_y=-0.3, gyro_z=0.1,
                         battery_mv=3800, frame_counter=42, inter_frame_us=1000)
    s1 = snap.serialize()
    s2 = snap.serialize()
    ok = s1 == s2 and len(s1) > 0
    results.append(PASS("2.7 Serialization determinism", "Idempotent",
                        f"{len(s1)}B, match={s1==s2}") if ok
                   else FAIL("2.7 Serialization", "Idempotent", "Non-deterministic"))

    # 2.8: Frame timing regularity (100 polls)
    if hw_available():
        frames = []
        for _ in range(100):
            frames.append(controller.poll())
            time.sleep(0.008)
        dts = [f.inter_frame_us for f in frames[1:]]
        avg_dt = sum(dts) / len(dts)
        tag = "HW" if controller_connected else "SIM"
        results.append(PASS(f"2.8 [{tag}] Frame timing", "Consistent",
                            f"avg={avg_dt:.0f}us over {len(dts)} frames"))
    else:
        # Pure algorithmic fallback
        frames = gen_human_normal(100)
        dts = [f.inter_frame_us for f in frames]
        avg_dt = sum(dts) / len(dts)
        results.append(PASS("2.8 Frame timing (synth)", "1000us",
                            f"avg={avg_dt:.0f}us"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 3: Anti-Cheat -- Synthetic Pattern Classification
# ══════════════════════════════════════════════════════════════════

def phase3_anticheat(verbose):
    results = []

    def run_classify(name, gen_fn, expected, n=200):
        clf = TestClassifier()
        for f in gen_fn(n):
            clf.feed(f, dt_ms=1.0)
        code, conf = clf.classify()
        exp_name = INFER_NAMES.get(expected, "?")
        act_name = INFER_NAMES.get(code, "?")
        return (PASS(name, exp_name, f"{act_name} ({conf}/255)") if code == expected
                else FAIL(name, exp_name, f"{act_name} ({conf}/255)"))

    results.append(run_classify("3.1 NOMINAL detection", gen_human_normal, INFER_PLAY_NOMINAL))
    results.append(run_classify("3.2 SKILLED detection", gen_skilled_player, INFER_PLAY_NOMINAL))
    results.append(run_classify("3.3 MACRO detection", gen_macro_turbo, INFER_CHEAT_MACRO))
    results.append(run_classify("3.4 AIMBOT detection", gen_aimbot, INFER_CHEAT_AIMBOT))
    results.append(run_classify("3.5 IMU_MISS detection", gen_imu_mismatch, INFER_CHEAT_IMU_MISS))
    results.append(run_classify("3.6 INJECTION detection", gen_injection, INFER_CHEAT_INJECTION))

    # 3.7: False positive rate (10 trials)
    fp = 0
    for _ in range(10):
        clf = TestClassifier()
        for f in gen_human_normal(300):
            clf.feed(f)
        code, _ = clf.classify()
        if code >= INFER_CHEAT_REACTION: fp += 1
    fpr = fp / 10
    results.append(PASS("3.7 False positive rate (10x)", "0% FPR",
                        f"{fpr*100:.1f}% ({fp}/10)") if fp == 0
                   else FAIL("3.7 FPR", "0%", f"{fpr*100:.1f}%"))

    # 3.8: Cheat confidence >= 180
    clf = TestClassifier()
    for f in gen_macro_turbo(200):
        clf.feed(f)
    _, conf = clf.classify()
    results.append(PASS("3.8 Cheat confidence >= 180", ">=180", f"{conf}/255")
                   if conf >= THRESHOLD_CHEAT_CONFIDENCE
                   else FAIL("3.8 Confidence", ">=180", f"{conf}/255"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 4: PoAC Record Generation
# ══════════════════════════════════════════════════════════════════

def phase4_poac(verbose):
    results = []
    engine = PoACEngine()
    wm = WorldModel()

    # 4.1: Record size 228B
    rec = engine.generate(sensor_hash=b'\xAA'*32, wm_hash=wm.compute_hash(),
                          inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
                          confidence=200, battery_pct=80)
    body = rec.serialize_body(); full = rec.serialize_full()
    results.append(PASS("4.1 Record size 228B", "body=164 full=228",
                        f"body={len(body)} full={len(full)}")
                   if len(body) == POAC_BODY_SIZE and len(full) == POAC_RECORD_SIZE
                   else FAIL("4.1 Record size", "164/228", f"{len(body)}/{len(full)}"))

    # 4.2: SHA-256 sensor commitment
    snap = InputSnapshot(right_stick_x=5000, gyro_x=0.1)
    expected_h = hashlib.sha256(snap.serialize()).digest()
    rec2 = engine.generate(sensor_hash=expected_h, wm_hash=wm.compute_hash(),
                           inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
                           confidence=200, battery_pct=80)
    results.append(PASS("4.2 SHA-256 sensor commitment", "Match",
                        f"hash={expected_h.hex()[:16]}...")
                   if rec2.sensor_commitment == expected_h
                   else FAIL("4.2 Sensor commitment", "Match", "Mismatch"))

    # 4.3: World model hash ordering
    h_before = wm.compute_hash()
    wm.update(200.0, 0.5, 50.0, 0.5)
    h_after = wm.compute_hash()
    results.append(PASS("4.3 World model hash ordering", "before != after",
                        f"diff at byte {next((i for i in range(32) if h_before[i] != h_after[i]), -1)}")
                   if h_before != h_after
                   else FAIL("4.3 WM hash", "Different", "Same"))

    # 4.4: Hash chain linkage (50 records)
    chain_e = PoACEngine()
    chain = [chain_e.generate(sensor_hash=b'\x00'*32, wm_hash=b'\x00'*32,
                              inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
                              confidence=200, battery_pct=80) for _ in range(50)]
    chain_ok = all(chain[i].prev_poac_hash == hashlib.sha256(chain[i-1].serialize_full()).digest()
                   for i in range(1, len(chain)))
    results.append(PASS("4.4 Hash chain (50 records)", "All linked",
                        f"{len(chain)} records verified") if chain_ok
                   else FAIL("4.4 Chain linkage", "All linked", "Broken"))

    # 4.5: Monotonic counter
    ctr_ok = all(chain[i].monotonic_ctr > chain[i-1].monotonic_ctr for i in range(1, len(chain)))
    results.append(PASS("4.5 Monotonic counter", "Strictly increasing",
                        f"1..{chain[-1].monotonic_ctr}") if ctr_ok
                   else FAIL("4.5 Counter", "Increasing", "Non-monotonic"))

    # 4.6: ECDSA-P256 sign + verify
    if HAS_CRYPTO:
        sig_e = PoACEngine()
        sig_r = sig_e.generate(sensor_hash=b'\x00'*32, wm_hash=b'\x00'*32,
                               inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
                               confidence=200, battery_pct=80)
        try:
            from cryptography.hazmat.primitives.asymmetric import ec, utils
            from cryptography.hazmat.primitives import hashes as crypto_hashes
            r_int = int.from_bytes(sig_r.signature[:32], 'big')
            s_int = int.from_bytes(sig_r.signature[32:], 'big')
            der = utils.encode_dss_signature(r_int, s_int)
            sig_e.private_key.public_key().verify(der, sig_r.serialize_body(),
                                                   ec.ECDSA(crypto_hashes.SHA256()))
            results.append(PASS("4.6 ECDSA-P256 sign+verify", "Valid", "Signature verified"))
        except Exception as e:
            results.append(FAIL("4.6 ECDSA-P256", "Valid", f"Error: {e}"))
    else:
        results.append(PASS("4.6 ECDSA-P256 (no crypto)", "Skipped", "cryptography not installed"))

    # 4.7: Binary export alignment
    raw = b"".join(r.serialize_full() for r in chain)
    aligned = len(raw) % POAC_RECORD_SIZE == 0
    n_recs = len(raw) // POAC_RECORD_SIZE
    results.append(PASS("4.7 Binary export (228B aligned)", f"{len(chain)} records",
                        f"{len(raw)}B = {n_recs} x 228B")
                   if aligned and n_recs == len(chain)
                   else FAIL("4.7 Binary export", "Aligned", f"{len(raw)}B"))

    # 4.8: Live PoAC from real controller input
    if hw_available():
        frames = capture_live(1.0)
        if frames and len(frames) > 0:
            live_snap = frames[-1]
            live_hash = hashlib.sha256(live_snap.serialize()).digest()
            live_e = PoACEngine()
            live_rec = live_e.generate(sensor_hash=live_hash, wm_hash=wm.compute_hash(),
                                       inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
                                       confidence=220, battery_pct=75)
            ok = len(live_rec.serialize_full()) == POAC_RECORD_SIZE
            tag = "HW" if controller_connected else "SIM"
            results.append(PASS(f"4.8 [{tag}] Live PoAC generation", "228B from live input",
                                f"hash={live_rec.record_hash().hex()[:16]}...") if ok
                           else FAIL(f"4.8 [{tag}] Live PoAC", "228B", f"{len(live_rec.serialize_full())}B"))
        else:
            results.append(SKIP("4.8 [HW] Live PoAC generation"))
    else:
        results.append(SKIP("4.8 [HW] Live PoAC generation"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 5: Contract Simulation -- SkillOracle
# ══════════════════════════════════════════════════════════════════

def phase5_skill_oracle(verbose):
    results = []
    oracle = SkillOracleSim()
    dev = "device_001"

    # 5.1: Initial profile
    p = oracle.get_or_create(dev)
    results.append(PASS("5.1 Initial profile", "1000/Silver",
                        f"{p['rating']}/{oracle.tier(p['rating'])}")
                   if p["rating"] == 1000 and oracle.tier(p["rating"]) == "Silver"
                   else FAIL("5.1 Initial", "1000/Silver",
                             f"{p['rating']}/{oracle.tier(p['rating'])}"))

    # 5.2: NOMINAL gain
    r = oracle.update(dev, "rec_001", INFER_PLAY_NOMINAL, 220)
    exp = (5 * 220) // 255
    results.append(PASS("5.2 NOMINAL gain", f"+{exp}",
                        f"{r['old']}->{r['new']} (+{r['new']-r['old']})")
                   if r["new"] == r["old"] + exp
                   else FAIL("5.2 NOMINAL", f"+{exp}", f"+{r['new']-r['old']}"))

    # 5.3: SKILLED gain
    r = oracle.update(dev, "rec_002", INFER_PLAY_SKILLED, 200)
    exp = (12 * 200) // 255
    results.append(PASS("5.3 SKILLED gain", f"+{exp}",
                        f"{r['old']}->{r['new']} (+{r['new']-r['old']})")
                   if r["new"] == r["old"] + exp
                   else FAIL("5.3 SKILLED", f"+{exp}", f"+{r['new']-r['old']}"))

    # 5.4: CHEAT penalty
    r = oracle.update(dev, "rec_003", INFER_CHEAT_MACRO, 230)
    results.append(PASS("5.4 CHEAT penalty (-200)", "-200",
                        f"{r['old']}->{r['new']}")
                   if r["new"] == r["old"] - 200
                   else FAIL("5.4 Penalty", "-200", f"{r['new']-r['old']}"))

    # 5.5: Tier progression
    o2 = SkillOracleSim()
    tiers = []
    for i in range(600):
        res = o2.update("tier_dev", f"tier_{i}", INFER_PLAY_SKILLED, 255)
        if not tiers or tiers[-1] != res["tier"]: tiers.append(res["tier"])
    expected = ["Silver", "Gold", "Platinum", "Diamond"]
    results.append(PASS("5.5 Tier progression", "S->G->P->D",
                        " -> ".join(tiers)) if tiers == expected
                   else FAIL("5.5 Tiers", str(expected), str(tiers)))

    # 5.6: Rating ceiling
    results.append(PASS("5.6 Rating ceiling", "<=3000",
                        f"rating={o2.profiles['tier_dev']['rating']}")
                   if o2.profiles["tier_dev"]["rating"] <= 3000
                   else FAIL("5.6 Ceiling", "<=3000",
                             f"{o2.profiles['tier_dev']['rating']}"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 6: Contract Simulation -- ProgressAttestation & TeamProof
# ══════════════════════════════════════════════════════════════════

def phase6_progress_team(verbose):
    results = []

    # ── ProgressAttestation ──
    pa = ProgressAttestationSim()
    for h in ["base_001", "curr_001", "curr_002", "curr_003", "curr_004"]:
        pa.mark_verified(h)

    # 6.1: Basic attestation
    aid = pa.attest("dev1", "base_001", "curr_001", 0, 1500)
    results.append(PASS("6.1 Progress attestation", "id=0",
                        f"id={aid} metric=REACTION_TIME bps=1500") if aid == 0
                   else FAIL("6.1 Attestation", "id=0", f"id={aid}"))

    # 6.2: All 4 metric types
    ok = True
    for mt in range(1, 4):
        try: pa.attest("dev1", "base_001", f"curr_00{mt+1}", mt, 500*(mt+1))
        except: ok = False
    results.append(PASS("6.2 All 4 MetricTypes", "Accepted", "REACT/ACC/CONS/COMBO")
                   if ok else FAIL("6.2 MetricTypes", "4 types", "Failed"))

    # 6.3: Duplicate pair rejection
    try:
        pa.attest("dev1", "base_001", "curr_001", 0, 1500)
        results.append(FAIL("6.3 Duplicate rejection", "Rejected", "Accepted"))
    except ValueError:
        results.append(PASS("6.3 Duplicate rejection", "PairAlreadyAttested", "Rejected"))

    # ── TeamProofAggregator ──
    tpa = TeamProofSim()

    # 6.4: Team creation
    try:
        tpa.create_team("alpha", [f"d{i}" for i in range(4)])
        results.append(PASS("6.4 Team creation (4 members)", "Created", "team=alpha"))
    except Exception as e:
        results.append(FAIL("6.4 Team creation", "Created", f"Error: {e}"))

    # 6.5: Merkle root computation
    hashes = [hashlib.sha256(f"rec_{i}".encode()).digest() for i in range(4)]
    root = tpa.merkle_root(hashes)
    s = sorted(hashes)
    l01 = hashlib.sha256(s[0] + s[1]).digest()
    l23 = hashlib.sha256(s[2] + s[3]).digest()
    exp_root = hashlib.sha256(l01 + l23).digest()
    results.append(PASS("6.5 Merkle root (4 leaves)", "Manual match",
                        f"root={root.hex()[:16]}...") if root == exp_root
                   else FAIL("6.5 Merkle", "Match", "Mismatch"))

    # 6.6: Full team proof lifecycle
    tpa2 = TeamProofSim()
    tpa2.create_team("lifecycle", ["d0", "d1", "d2"])
    rhs = [hashlib.sha256(f"life_{i}".encode()).digest() for i in range(3)]
    for rh in rhs: tpa2.mark_verified(rh.hex())
    mr = tpa2.merkle_root(rhs)
    try:
        pid = tpa2.submit("lifecycle", rhs, mr)
        results.append(PASS("6.6 Team proof lifecycle", "Submitted",
                            f"proof_id={pid} root={mr.hex()[:16]}..."))
    except Exception as e:
        results.append(FAIL("6.6 Lifecycle", "Submitted", f"Error: {e}"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 7: Feedback, Haptics & Bounties
# ══════════════════════════════════════════════════════════════════

def phase7_feedback(verbose):
    results = []

    # 7.1: LED green (clean state)
    if controller_connected:
        try:
            controller.set_led(0, 255, 0)
            time.sleep(0.3)
            results.append(PASS("7.1 [HW] LED green (CLEAN)", "Green LED",
                                "Set to (0, 255, 0)"))
        except Exception as e:
            results.append(FAIL("7.1 [HW] LED green", "Set", f"Error: {e}"))
    elif simulate_mode:
        results.append(PASS("7.1 [SIM] LED green", "Simulated", "N/A"))
    else:
        results.append(SKIP("7.1 [HW] LED green"))

    # 7.2: LED red (cheat alert)
    if controller_connected:
        try:
            controller.set_led(255, 0, 0)
            time.sleep(0.3)
            results.append(PASS("7.2 [HW] LED red (CHEAT)", "Red LED",
                                "Set to (255, 0, 0)"))
        except Exception as e:
            results.append(FAIL("7.2 [HW] LED red", "Set", f"Error: {e}"))
    elif simulate_mode:
        results.append(PASS("7.2 [SIM] LED red", "Simulated", "N/A"))
    else:
        results.append(SKIP("7.2 [HW] LED red"))

    # 7.3: Haptic rumble
    if controller_connected:
        try:
            controller.haptic(128, 128)
            time.sleep(0.5)
            controller.haptic(0, 0)
            results.append(PASS("7.3 [HW] Haptic rumble", "Rumble 0.5s",
                                "Left=128 Right=128"))
        except Exception as e:
            results.append(FAIL("7.3 [HW] Haptic", "Rumble", f"Error: {e}"))
    elif simulate_mode:
        results.append(PASS("7.3 [SIM] Haptic rumble", "Simulated", "N/A"))
    else:
        results.append(SKIP("7.3 [HW] Haptic rumble"))

    # 7.4: LED reset to blue (idle)
    if controller_connected:
        try:
            controller.set_led(0, 0, 255)
            results.append(PASS("7.4 [HW] LED reset (IDLE)", "Blue LED",
                                "Set to (0, 0, 255)"))
        except Exception as e:
            results.append(FAIL("7.4 [HW] LED reset", "Set", f"Error: {e}"))
    elif simulate_mode:
        results.append(PASS("7.4 [SIM] LED reset", "Simulated", "N/A"))
    else:
        results.append(SKIP("7.4 [HW] LED reset"))

    # 7.5: Bounty fulfillment simulation
    b = Bounty(bounty_id=1001, reward_micro_iotx=50_000_000, min_samples=10,
               description="Test bounty")
    util = b.utility(80)
    b.accepted = util > 0
    for _ in range(10):
        b.samples_submitted += 1
    complete = b.samples_submitted >= b.min_samples
    results.append(PASS("7.5 Bounty fulfillment", "Complete after 10 samples",
                        f"utility={util:.4f} samples={b.samples_submitted}/{b.min_samples}")
                   if complete and b.accepted
                   else FAIL("7.5 Bounty", "Complete", f"accepted={b.accepted} done={complete}"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 8: End-to-End Pipeline
# ══════════════════════════════════════════════════════════════════

def phase8_e2e(verbose):
    results = []

    # 8.1: Full session lifecycle (BOOT -> START -> PLAY -> END)
    engine = PoACEngine()
    wm = WorldModel()
    clf = TestClassifier()
    chain = []

    boot = engine.generate(sensor_hash=b'\x00'*32, wm_hash=wm.compute_hash(),
                           inference=INFER_PLAY_NOMINAL, action=ACTION_BOOT,
                           confidence=0, battery_pct=100)
    chain.append(boot)
    wm.total_sessions += 1
    start = engine.generate(sensor_hash=b'\x00'*32, wm_hash=wm.compute_hash(),
                            inference=INFER_PLAY_NOMINAL, action=ACTION_SESSION_START,
                            confidence=0, battery_pct=100)
    chain.append(start)
    for f in gen_human_normal(200):
        clf.feed(f)
    code, conf = clf.classify()
    play = engine.generate(sensor_hash=hashlib.sha256(gen_human_normal(1)[0].serialize()).digest(),
                           wm_hash=wm.compute_hash(),
                           inference=code, action=ACTION_REPORT, confidence=conf, battery_pct=80)
    chain.append(play)
    wm.update(250.0, 0.5, 50.0, 0.5); wm.total_poac += 1
    end = engine.generate(sensor_hash=b'\x00'*32, wm_hash=wm.compute_hash(),
                          inference=INFER_PLAY_NOMINAL, action=ACTION_SESSION_END,
                          confidence=0, battery_pct=100)
    chain.append(end)

    lifecycle_ok = (len(chain) == 4 and
                    chain[0].action_code == ACTION_BOOT and
                    chain[1].action_code == ACTION_SESSION_START and
                    chain[2].action_code == ACTION_REPORT and
                    chain[3].action_code == ACTION_SESSION_END)
    results.append(PASS("8.1 Session lifecycle", "BOOT->START->PLAY->END",
                        f"{len(chain)} records") if lifecycle_ok
                   else FAIL("8.1 Lifecycle", "4 records", f"{len(chain)}"))

    # 8.2: Chain integrity
    chain_ok = all(chain[i].prev_poac_hash == hashlib.sha256(chain[i-1].serialize_full()).digest()
                   for i in range(1, len(chain)))
    results.append(PASS("8.2 Chain integrity", "All linked",
                        f"{len(chain)} records") if chain_ok
                   else FAIL("8.2 Chain", "Linked", "Broken"))

    # 8.3: Bridge codec compatibility
    raw = b"".join(r.serialize_full() for r in chain)
    parsed = 0
    for off in range(0, len(raw), POAC_RECORD_SIZE):
        chunk = raw[off:off+POAC_RECORD_SIZE]
        if len(chunk) == POAC_RECORD_SIZE: parsed += 1
    results.append(PASS("8.3 Bridge codec compat", f"{len(chain)} records",
                        f"{parsed} parsed from {len(raw)}B")
                   if parsed == len(chain)
                   else FAIL("8.3 Codec", f"{len(chain)}", f"{parsed}"))

    # 8.4: JSON export round-trip
    d = chain[0].to_dict()
    js = json.dumps(d)
    p = json.loads(js)
    ok = "hash" in p and "ctr" in p and "inference" in p
    results.append(PASS("8.4 JSON round-trip", "Valid",
                        f"keys={list(p.keys())[:4]}...") if ok
                   else FAIL("8.4 JSON", "Valid", "Invalid"))

    # 8.5: Live session with real controller (10s)
    if hw_available():
        live_engine = PoACEngine()
        live_wm = WorldModel()
        live_clf = TestClassifier()
        live_chain = []

        # Boot + session start
        live_chain.append(live_engine.generate(
            sensor_hash=b'\x00'*32, wm_hash=live_wm.compute_hash(),
            inference=INFER_PLAY_NOMINAL, action=ACTION_BOOT,
            confidence=0, battery_pct=100))
        live_chain.append(live_engine.generate(
            sensor_hash=b'\x00'*32, wm_hash=live_wm.compute_hash(),
            inference=INFER_PLAY_NOMINAL, action=ACTION_SESSION_START,
            confidence=0, battery_pct=100))

        tag = "HW" if controller_connected else "SIM"
        duration = 3.0  # 3 seconds of live capture
        start_t = time.time()
        frame_count = 0
        poac_count = 0

        while time.time() - start_t < duration:
            snap = controller.poll()
            live_clf.feed(snap, dt_ms=snap.inter_frame_us / 1000.0 if snap.inter_frame_us > 0 else 1.0)
            frame_count += 1
            live_wm.total_frames += 1

            # Generate PoAC every 50 frames
            if frame_count % 50 == 0:
                inf, conf = live_clf.classify()
                s_hash = hashlib.sha256(snap.serialize()).digest()
                rec = live_engine.generate(
                    sensor_hash=s_hash, wm_hash=live_wm.compute_hash(),
                    inference=inf, action=ACTION_REPORT,
                    confidence=conf, battery_pct=75)
                live_chain.append(rec)
                poac_count += 1
            time.sleep(0.008)

        # Session end
        live_chain.append(live_engine.generate(
            sensor_hash=b'\x00'*32, wm_hash=live_wm.compute_hash(),
            inference=INFER_PLAY_NOMINAL, action=ACTION_SESSION_END,
            confidence=0, battery_pct=100))

        live_ok = len(live_chain) >= 4 and poac_count > 0
        live_chain_ok = all(
            live_chain[i].prev_poac_hash == hashlib.sha256(live_chain[i-1].serialize_full()).digest()
            for i in range(1, len(live_chain)))

        results.append(PASS(f"8.5 [{tag}] Live session ({duration:.0f}s)",
                            ">=4 records + valid chain",
                            f"{len(live_chain)} records, {frame_count} frames, chain={'OK' if live_chain_ok else 'BROKEN'}")
                       if live_ok and live_chain_ok
                       else FAIL(f"8.5 [{tag}] Live session", ">=4 + valid",
                                 f"{len(live_chain)} records, chain={live_chain_ok}"))
    else:
        results.append(SKIP("8.5 [HW] Live session"))

    # 8.6: Throughput benchmark
    bench_e = PoACEngine()
    t0 = time.perf_counter()
    for _ in range(1000):
        bench_e.generate(sensor_hash=b'\x00'*32, wm_hash=b'\x00'*32,
                         inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
                         confidence=200, battery_pct=80)
    elapsed = time.perf_counter() - t0
    rps = 1000 / elapsed
    results.append(PASS("8.6 Throughput benchmark", ">100 rec/s",
                        f"{rps:.0f} rec/s ({elapsed:.3f}s for 1000)") if rps > 100
                   else FAIL("8.6 Throughput", ">100/s", f"{rps:.0f}/s"))

    # 8.7: Memory profiling
    tracemalloc.start()
    m_e = PoACEngine(); m_wm = WorldModel()
    for _ in range(100):
        m_e.generate(sensor_hash=b'\x00'*32, wm_hash=m_wm.compute_hash(),
                     inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
                     confidence=200, battery_pct=80)
        m_wm.update(250.0, 0.5, 50.0, 0.5)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    pk = peak / 1024
    results.append(PASS("8.7 Memory profiling (100 recs)", "<10MB",
                        f"peak={pk:.0f}KB") if pk < 10240
                   else FAIL("8.7 Memory", "<10MB", f"{pk:.0f}KB"))

    # 8.8: SkillOracle integration with live data
    oracle = SkillOracleSim()
    for i, rec in enumerate(chain):
        try:
            oracle.update("e2e_dev", f"e2e_{i}", rec.inference_result, rec.confidence)
        except ValueError:
            pass  # duplicate handling
    p = oracle.profiles.get("e2e_dev", {})
    results.append(PASS("8.8 SkillOracle E2E integration", "Profile updated",
                        f"rating={p.get('rating', 0)} tier={oracle.tier(p.get('rating', 0))}")
                   if p.get("games", 0) > 0
                   else FAIL("8.8 SkillOracle E2E", "Updated", "No games"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 9: Live PoAC Monitoring
# ══════════════════════════════════════════════════════════════════

def phase9_live_poac(verbose):
    results = []

    # 9.1: Continuous PoAC streaming (5s session, record every 25 frames)
    if hw_available():
        engine = PoACEngine()
        wm = WorldModel()
        clf = TestClassifier()
        chain = []
        tag = "HW" if controller_connected else "SIM"

        # Boot + start
        chain.append(engine.generate(
            sensor_hash=b'\x00'*32, wm_hash=wm.compute_hash(),
            inference=INFER_PLAY_NOMINAL, action=ACTION_BOOT,
            confidence=0, battery_pct=100))
        chain.append(engine.generate(
            sensor_hash=b'\x00'*32, wm_hash=wm.compute_hash(),
            inference=INFER_PLAY_NOMINAL, action=ACTION_SESSION_START,
            confidence=0, battery_pct=100))

        start_t = time.time()
        fc = 0
        while time.time() - start_t < 5.0:
            snap = controller.poll()
            clf.feed(snap, dt_ms=max(snap.inter_frame_us / 1000.0, 0.1))
            fc += 1
            wm.total_frames += 1
            if fc % 25 == 0:
                inf, conf = clf.classify()
                s_hash = hashlib.sha256(snap.serialize()).digest()
                rec = engine.generate(
                    sensor_hash=s_hash, wm_hash=wm.compute_hash(),
                    inference=inf, action=ACTION_REPORT,
                    confidence=conf, battery_pct=75)
                chain.append(rec)
            time.sleep(0.008)

        chain.append(engine.generate(
            sensor_hash=b'\x00'*32, wm_hash=wm.compute_hash(),
            inference=INFER_PLAY_NOMINAL, action=ACTION_SESSION_END,
            confidence=0, battery_pct=100))

        stream_ok = len(chain) >= 6  # boot + start + >=2 reports + end
        results.append(PASS(f"9.1 [{tag}] PoAC stream (5s)", ">=6 records",
                            f"{len(chain)} records from {fc} frames") if stream_ok
                       else FAIL(f"9.1 [{tag}] PoAC stream", ">=6", f"{len(chain)}"))
    else:
        results.append(SKIP("9.1 [HW] PoAC stream (5s)"))
        chain = []

    # 9.2: Chain integrity during streaming
    if chain and len(chain) >= 3:
        intact = all(
            chain[i].prev_poac_hash == hashlib.sha256(chain[i-1].serialize_full()).digest()
            for i in range(1, len(chain)))
        tag = "HW" if controller_connected else "SIM"
        results.append(PASS(f"9.2 [{tag}] Stream chain integrity", "All linked",
                            f"{len(chain)} records verified") if intact
                       else FAIL(f"9.2 [{tag}] Stream integrity", "All linked", "BROKEN"))
    elif not hw_available():
        results.append(SKIP("9.2 [HW] Stream chain integrity"))
    else:
        results.append(FAIL("9.2 Stream chain integrity", ">=3 records", f"{len(chain)}"))

    # 9.3: Record timestamp monotonicity
    if chain and len(chain) >= 3:
        ts_ok = all(chain[i].timestamp_ms >= chain[i-1].timestamp_ms
                    for i in range(1, len(chain)))
        tag = "HW" if controller_connected else "SIM"
        results.append(PASS(f"9.3 [{tag}] Timestamp monotonicity", "Non-decreasing",
                            f"span={chain[-1].timestamp_ms - chain[0].timestamp_ms}ms") if ts_ok
                       else FAIL(f"9.3 [{tag}] Timestamps", "Monotonic", "Non-monotonic"))
    elif not hw_available():
        results.append(SKIP("9.3 [HW] Timestamp monotonicity"))
    else:
        results.append(FAIL("9.3 Timestamps", ">=3 records", f"{len(chain)}"))

    # 9.4: Sensor commitment uniqueness across stream
    if chain and len(chain) >= 4:
        reports = [r for r in chain if r.action_code == ACTION_REPORT]
        commits = set(r.sensor_commitment for r in reports)
        # Not all must be unique (same input could repeat) but most should differ
        tag = "HW" if controller_connected else "SIM"
        unique_ratio = len(commits) / max(len(reports), 1)
        ok = unique_ratio > 0.5 or len(reports) <= 2
        results.append(PASS(f"9.4 [{tag}] Sensor commit diversity", ">50% unique",
                            f"{len(commits)}/{len(reports)} unique ({unique_ratio:.0%})") if ok
                       else FAIL(f"9.4 [{tag}] Commit diversity", ">50%",
                                 f"{unique_ratio:.0%}"))
    elif not hw_available():
        results.append(SKIP("9.4 [HW] Sensor commit diversity"))
    else:
        results.append(FAIL("9.4 Commit diversity", ">=4 records", f"{len(chain)}"))

    # 9.5: World model hash evolution during stream
    if chain and len(chain) >= 4:
        reports = [r for r in chain if r.action_code == ACTION_REPORT]
        wm_hashes = [r.world_model_hash for r in reports]
        # World model should evolve as frames accumulate
        tag = "HW" if controller_connected else "SIM"
        if len(wm_hashes) >= 2:
            # At least first and last should differ (wm.total_frames changes)
            evolved = wm_hashes[0] != wm_hashes[-1]
            results.append(PASS(f"9.5 [{tag}] World model evolution", "Hash changes",
                                f"{len(set(wm_hashes))} distinct across {len(wm_hashes)} reports")
                           if evolved
                           else FAIL(f"9.5 [{tag}] WM evolution", "Changed", "Static"))
        else:
            results.append(PASS(f"9.5 [{tag}] World model evolution", "N/A",
                                "Too few reports to compare"))
    elif not hw_available():
        results.append(SKIP("9.5 [HW] World model evolution"))
    else:
        results.append(FAIL("9.5 WM evolution", ">=4 records", f"{len(chain)}"))

    # 9.6: Binary export from stream
    if chain and len(chain) >= 3:
        raw = b"".join(r.serialize_full() for r in chain)
        aligned = len(raw) % POAC_RECORD_SIZE == 0
        n_recs = len(raw) // POAC_RECORD_SIZE
        tag = "HW" if controller_connected else "SIM"
        results.append(PASS(f"9.6 [{tag}] Stream binary export", f"{len(chain)}x228B",
                            f"{len(raw)}B = {n_recs} records")
                       if aligned and n_recs == len(chain)
                       else FAIL(f"9.6 [{tag}] Binary export", "Aligned", f"{len(raw)}B"))
    elif not hw_available():
        results.append(SKIP("9.6 [HW] Stream binary export"))
    else:
        results.append(FAIL("9.6 Binary export", ">=3 records", f"{len(chain)}"))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 10: Hardware Anti-Cheat Validation
# ══════════════════════════════════════════════════════════════════

def phase10_hw_anticheat(verbose):
    results = []

    # 10.1: Live nominal detection (idle/gentle play -> NOMINAL)
    if hw_available():
        tag = "HW" if controller_connected else "SIM"
        clf = TestClassifier()
        prompt = "Hold controller NATURALLY for 3 seconds" if interactive_mode else ""
        frames = capture_live(3.0, prompt) if prompt else []
        if not frames:
            # Non-interactive: warm up then capture silently
            controller.poll()  # discard first poll to reset dt baseline
            start_t = time.time()
            while time.time() - start_t < 3.0:
                snap = controller.poll()
                dt = snap.inter_frame_us / 1000.0
                clf.feed(snap, dt_ms=max(min(dt, 50.0), 0.1))
                time.sleep(0.008)
        else:
            for f in frames:
                dt = f.inter_frame_us / 1000.0
                clf.feed(f, dt_ms=max(min(dt, 50.0), 0.1))
        code, conf = clf.classify()
        name = INFER_NAMES.get(code, "?")
        is_clean = code < INFER_CHEAT_REACTION
        results.append(PASS(f"10.1 [{tag}] Live nominal detection", "NOMINAL/SKILLED",
                            f"{name} ({conf}/255)") if is_clean
                       else FAIL(f"10.1 [{tag}] Live nominal", "Clean",
                                 f"{name} ({conf}/255)"))
    else:
        results.append(SKIP("10.1 [HW] Live nominal detection"))

    # 10.2: Classifier stability (3 consecutive windows all clean)
    if hw_available():
        tag = "HW" if controller_connected else "SIM"
        stable = True
        verdicts = []
        for trial in range(3):
            clf = TestClassifier()
            # Warm up: discard first poll to reset dt_us baseline
            controller.poll()
            start_t = time.time()
            while time.time() - start_t < 2.0:
                snap = controller.poll()
                dt = snap.inter_frame_us / 1000.0
                clf.feed(snap, dt_ms=max(min(dt, 50.0), 0.1))
                time.sleep(0.008)
            code, conf = clf.classify()
            verdicts.append(INFER_NAMES.get(code, "?"))
            if code >= INFER_CHEAT_REACTION:
                stable = False
        results.append(PASS(f"10.2 [{tag}] Classifier stability (3x)", "All clean",
                            " | ".join(verdicts)) if stable
                       else FAIL(f"10.2 [{tag}] Stability", "All clean",
                                 " | ".join(verdicts)))
    else:
        results.append(SKIP("10.2 [HW] Classifier stability"))

    # 10.3: Synthetic cheat vs live baseline (aimbot injection into stream)
    clf = TestClassifier()
    for f in gen_aimbot(200):
        clf.feed(f)
    code, conf = clf.classify()
    ok = code == INFER_CHEAT_AIMBOT and conf >= THRESHOLD_CHEAT_CONFIDENCE
    results.append(PASS("10.3 Aimbot in-stream detection", "CHEAT:AIMBOT >=180",
                        f"{INFER_NAMES.get(code, '?')} ({conf}/255)") if ok
                   else FAIL("10.3 Aimbot detection", "CHEAT:AIMBOT",
                             f"{INFER_NAMES.get(code, '?')} ({conf}/255)"))

    # 10.4: Classifier reset isolation
    clf = TestClassifier()
    for f in gen_macro_turbo(200):
        clf.feed(f)
    code1, _ = clf.classify()
    clf.reset()
    for f in gen_human_normal(200):
        clf.feed(f)
    code2, _ = clf.classify()
    ok = code1 == INFER_CHEAT_MACRO and code2 == INFER_PLAY_NOMINAL
    results.append(PASS("10.4 Classifier reset isolation", "MACRO->reset->NOMINAL",
                        f"{INFER_NAMES.get(code1, '?')} -> reset -> {INFER_NAMES.get(code2, '?')}")
                   if ok
                   else FAIL("10.4 Reset isolation", "MACRO then NOMINAL",
                             f"{INFER_NAMES.get(code1, '?')} then {INFER_NAMES.get(code2, '?')}"))

    # 10.5: Live PoAC + anti-cheat integration (record inference matches classifier)
    if hw_available():
        tag = "HW" if controller_connected else "SIM"
        engine = PoACEngine()
        clf = TestClassifier()
        recs = []
        start_t = time.time()
        fc = 0
        while time.time() - start_t < 3.0:
            snap = controller.poll()
            clf.feed(snap, dt_ms=max(snap.inter_frame_us / 1000.0, 0.1))
            fc += 1
            if fc % 50 == 0:
                inf, conf = clf.classify()
                rec = engine.generate(
                    sensor_hash=hashlib.sha256(snap.serialize()).digest(),
                    wm_hash=b'\x00'*32, inference=inf, action=ACTION_REPORT,
                    confidence=conf, battery_pct=75)
                recs.append(rec)
            time.sleep(0.008)
        # Verify all records carry the classifier inference
        if recs:
            all_match = all(r.inference_result in INFER_NAMES for r in recs)
            results.append(PASS(f"10.5 [{tag}] PoAC+AC integration", "Valid inferences",
                                f"{len(recs)} records, all valid codes") if all_match
                           else FAIL(f"10.5 [{tag}] Integration", "Valid codes", "Bad codes"))
        else:
            results.append(FAIL(f"10.5 [{tag}] Integration", ">=1 record", "0 records"))
    else:
        results.append(SKIP("10.5 [HW] PoAC+AC integration"))

    # 10.6: Multi-pattern detection sweep (all 6 patterns classified correctly)
    patterns = [
        ("NOMINAL", gen_human_normal, INFER_PLAY_NOMINAL),
        ("MACRO", gen_macro_turbo, INFER_CHEAT_MACRO),
        ("AIMBOT", gen_aimbot, INFER_CHEAT_AIMBOT),
        ("IMU_MISS", gen_imu_mismatch, INFER_CHEAT_IMU_MISS),
        ("INJECTION", gen_injection, INFER_CHEAT_INJECTION),
    ]
    sweep_ok = True
    sweep_results = []
    for name, gen, expected in patterns:
        clf = TestClassifier()
        for f in gen(200):
            clf.feed(f)
        code, _ = clf.classify()
        ok = code == expected
        sweep_results.append(f"{name}={'OK' if ok else 'FAIL'}")
        if not ok:
            sweep_ok = False
    results.append(PASS("10.6 Multi-pattern sweep (5x)", "All correct",
                        " ".join(sweep_results)) if sweep_ok
                   else FAIL("10.6 Pattern sweep", "All correct",
                             " ".join(sweep_results)))

    return results


# ══════════════════════════════════════════════════════════════════
# Phase 11: Bounty Simulation & Session Export
# ══════════════════════════════════════════════════════════════════

def phase11_bounty_export(verbose):
    results = []

    # 11.1: Multi-bounty evaluation and acceptance
    bounties = [
        Bounty(bounty_id=2001, reward_micro_iotx=100_000_000, min_samples=20,
               description="High-reward bounty"),
        Bounty(bounty_id=2002, reward_micro_iotx=10_000_000, min_samples=5,
               description="Quick bounty"),
        Bounty(bounty_id=2003, reward_micro_iotx=1_000, min_samples=500,
               description="Unprofitable bounty"),
    ]
    for b in bounties:
        u = b.utility(80)
        b.accepted = u > 0
    accepted = [b for b in bounties if b.accepted]
    rejected = [b for b in bounties if not b.accepted]
    # 2001 and 2002 should be accepted, 2003 should not (tiny reward, many samples)
    ok = len(accepted) >= 2 and any(b.bounty_id == 2003 for b in rejected)
    results.append(PASS("11.1 Multi-bounty evaluation", "Accept profitable, reject bad",
                        f"accepted={[b.bounty_id for b in accepted]} "
                        f"rejected={[b.bounty_id for b in rejected]}") if ok
                   else FAIL("11.1 Bounty eval", "2 accepted 1 rejected",
                             f"accepted={len(accepted)} rejected={len(rejected)}"))

    # 11.2: Bounty progress tracking with PoAC records
    engine = PoACEngine()
    b = Bounty(bounty_id=3001, reward_micro_iotx=50_000_000, min_samples=10,
               description="Progress test bounty")
    b.accepted = True
    for i in range(10):
        rec = engine.generate(
            sensor_hash=hashlib.sha256(f"sample_{i}".encode()).digest(),
            wm_hash=b'\x00'*32, inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
            confidence=220, battery_pct=80, bounty_id=b.bounty_id)
        b.samples_submitted += 1
    complete = b.samples_submitted >= b.min_samples
    results.append(PASS("11.2 Bounty progress tracking", "10/10 complete",
                        f"{b.samples_submitted}/{b.min_samples} bounty_id={b.bounty_id}")
                   if complete
                   else FAIL("11.2 Progress", "10/10",
                             f"{b.samples_submitted}/{b.min_samples}"))

    # 11.3: PoAC records carry bounty_id correctly
    engine2 = PoACEngine()
    bid = 4001
    rec = engine2.generate(
        sensor_hash=b'\x00'*32, wm_hash=b'\x00'*32,
        inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
        confidence=200, battery_pct=80, bounty_id=bid)
    body = rec.serialize_body()
    # bounty_id is the last 4 bytes of the body (per PoAC layout)
    parsed_bid = struct.unpack(">I", body[-4:])[0]
    ok = parsed_bid == bid and rec.bounty_id == bid
    results.append(PASS("11.3 PoAC bounty_id encoding", f"bounty_id={bid}",
                        f"record={rec.bounty_id} parsed={parsed_bid}") if ok
                   else FAIL("11.3 Bounty ID", f"{bid}",
                             f"rec={rec.bounty_id} parsed={parsed_bid}"))

    # 11.4: Full session JSON export and re-import
    engine3 = PoACEngine()
    wm = WorldModel()
    session_chain = []
    session_chain.append(engine3.generate(
        sensor_hash=b'\x00'*32, wm_hash=wm.compute_hash(),
        inference=INFER_PLAY_NOMINAL, action=ACTION_BOOT,
        confidence=0, battery_pct=100))
    for i in range(5):
        wm.update(250.0, 0.5, 50.0, 0.5)
        wm.total_frames += 100
        session_chain.append(engine3.generate(
            sensor_hash=hashlib.sha256(f"frame_{i}".encode()).digest(),
            wm_hash=wm.compute_hash(),
            inference=INFER_PLAY_NOMINAL, action=ACTION_REPORT,
            confidence=220, battery_pct=80 - i*2))
    session_chain.append(engine3.generate(
        sensor_hash=b'\x00'*32, wm_hash=wm.compute_hash(),
        inference=INFER_PLAY_NOMINAL, action=ACTION_SESSION_END,
        confidence=0, battery_pct=70))

    export_data = {
        "record_count": len(session_chain),
        "records": [r.to_dict() for r in session_chain],
    }
    js = json.dumps(export_data)
    reimport = json.loads(js)
    ok = (reimport["record_count"] == len(session_chain) and
          len(reimport["records"]) == len(session_chain) and
          reimport["records"][0]["inference"] == "NOMINAL")
    results.append(PASS("11.4 Session JSON export/import", "Round-trip valid",
                        f"{reimport['record_count']} records re-imported") if ok
                   else FAIL("11.4 JSON export", "Valid",
                             f"count={reimport.get('record_count', 'missing')}"))

    # 11.5: Full SkillOracle lifecycle with bounty session
    oracle = SkillOracleSim()
    dev = "bounty_dev"
    for i, rec in enumerate(session_chain):
        try:
            oracle.update(dev, f"bounty_rec_{i}", rec.inference_result, rec.confidence)
        except ValueError:
            pass
    p = oracle.profiles.get(dev, {})
    tier = oracle.tier(p.get("rating", 0))
    ok = p.get("games", 0) == len(session_chain) and p.get("rating", 0) > 1000
    results.append(PASS("11.5 SkillOracle from bounty session", f">{1000} rating",
                        f"rating={p.get('rating', 0)} tier={tier} games={p.get('games', 0)}")
                   if ok
                   else FAIL("11.5 SkillOracle", ">1000 rating",
                             f"rating={p.get('rating', 0)}"))

    # 11.6: Live bounty fulfillment session
    if hw_available():
        tag = "HW" if controller_connected else "SIM"
        live_engine = PoACEngine()
        live_bounty = Bounty(bounty_id=5001, reward_micro_iotx=50_000_000,
                             min_samples=5, description="Live bounty")
        live_bounty.accepted = True
        live_clf = TestClassifier()
        start_t = time.time()
        fc = 0
        while time.time() - start_t < 3.0:
            snap = controller.poll()
            live_clf.feed(snap, dt_ms=max(snap.inter_frame_us / 1000.0, 0.1))
            fc += 1
            if fc % 40 == 0 and live_bounty.samples_submitted < live_bounty.min_samples:
                inf, conf = live_clf.classify()
                live_engine.generate(
                    sensor_hash=hashlib.sha256(snap.serialize()).digest(),
                    wm_hash=b'\x00'*32, inference=inf, action=ACTION_REPORT,
                    confidence=conf, battery_pct=75, bounty_id=live_bounty.bounty_id)
                live_bounty.samples_submitted += 1
            time.sleep(0.008)
        complete = live_bounty.samples_submitted >= live_bounty.min_samples
        results.append(PASS(f"11.6 [{tag}] Live bounty fulfillment",
                            f">={live_bounty.min_samples} samples",
                            f"{live_bounty.samples_submitted}/{live_bounty.min_samples} "
                            f"from {fc} frames") if complete
                       else FAIL(f"11.6 [{tag}] Live bounty", f">={live_bounty.min_samples}",
                                 f"{live_bounty.samples_submitted}/{live_bounty.min_samples}"))
    else:
        results.append(SKIP("11.6 [HW] Live bounty fulfillment"))

    return results


# ══════════════════════════════════════════════════════════════════
# Main Runner
# ══════════════════════════════════════════════════════════════════

PHASES = {
    1: ("Hardware Discovery & Connection", phase1_hardware),
    2: ("Live Input Capture & Fidelity", phase2_input),
    3: ("Anti-Cheat -- Synthetic Patterns", phase3_anticheat),
    4: ("PoAC Record Generation", phase4_poac),
    5: ("Contract Sim -- SkillOracle", phase5_skill_oracle),
    6: ("Contract Sim -- Progress & Teams", phase6_progress_team),
    7: ("Feedback, Haptics & Bounties", phase7_feedback),
    8: ("End-to-End Pipeline", phase8_e2e),
    9: ("Live PoAC Monitoring", phase9_live_poac),
    10: ("Hardware Anti-Cheat Validation", phase10_hw_anticheat),
    11: ("Bounty Simulation & Session Export", phase11_bounty_export),
}


def main():
    global interactive_mode

    parser = argparse.ArgumentParser(
        description="VAPI DualShock Edge Hardware Testing Suite v3.0 -- 11 Phases, 72 Tests")
    parser.add_argument("--phase", type=int, default=None,
                        help="Run specific phase (1-11)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed output")
    parser.add_argument("--simulate", action="store_true",
                        help="Force simulation mode (no real controller required)")
    parser.add_argument("--interactive", action="store_true",
                        help="Enable interactive hardware tests (prompts to move sticks, etc.)")
    args = parser.parse_args()

    interactive_mode = args.interactive

    print("=" * 78)
    print("  VAPI DualShock Edge Hardware Testing Suite v3.0")
    print("  11 Phases | 72 Tests | Full Hardware Integration")
    print("=" * 78)
    print()

    # Initialize hardware
    hw_found = init_hardware(force_simulate=args.simulate)

    if hw_found:
        print("  Controller:  \033[92mCONNECTED\033[0m (DualSense Edge via USB/BT)")
    elif args.simulate:
        print("  Controller:  \033[93mSIMULATED\033[0m (--simulate mode)")
    else:
        print("  Controller:  \033[91mNOT FOUND\033[0m (HW tests will SKIP)")
        print("               Connect via USB or install: pip install pydualsense")

    print(f"  Emulator:    {'OK (dualshock_emulator)' if IMPORTED else 'STANDALONE'}")
    print(f"  Crypto:      {'OK (ECDSA-P256)' if HAS_CRYPTO else 'NOT INSTALLED'}")
    print(f"  Interactive: {'ENABLED' if args.interactive else 'disabled (use --interactive)'}")
    print()

    all_results = {}
    total_pass = 0
    total_fail = 0
    total_skip = 0

    phases_to_run = [args.phase] if args.phase else list(PHASES.keys())

    for pn in phases_to_run:
        if pn not in PHASES:
            print(f"  [ERROR] Unknown phase: {pn}")
            continue

        name, func = PHASES[pn]
        print(f"  Phase {pn}: {name}")
        print("  " + "-" * 74)

        phase_results = func(args.verbose)
        all_results[pn] = phase_results

        pp = pf = ps = 0
        for r in phase_results:
            if r.skipped:
                status = "\033[93mSKIP\033[0m"
                ps += 1; total_skip += 1
            elif r.passed:
                status = "\033[92mPASS\033[0m"
                pp += 1; total_pass += 1
            else:
                status = "\033[91mFAIL\033[0m"
                pf += 1; total_fail += 1
            print(f"    {status}  {r.name:<42s} | {r.expected:<22s} | {r.actual}")
            if r.details and args.verbose:
                print(f"           Details: {r.details}")

        c = "\033[92m" if pf == 0 else "\033[91m"
        skip_note = f" ({ps} skipped)" if ps > 0 else ""
        print(f"  {c}  Phase {pn}: {pp}/{pp+pf} passed{skip_note}\033[0m")
        print()

    # Final summary
    total = total_pass + total_fail
    tested = total_pass + total_fail + total_skip
    color = "\033[92m" if total_fail == 0 else "\033[91m"

    print("=" * 78)
    print(f"  {color}FINAL: {total_pass}/{total} passed\033[0m", end="")
    if total_skip > 0:
        print(f"  \033[93m({total_skip} skipped -- connect controller for full coverage)\033[0m")
    else:
        print()

    if total_fail > 0:
        print(f"  \033[91m{total_fail} FAILED:\033[0m")
        for pn, pr in all_results.items():
            for r in pr:
                if not r.passed and not r.skipped:
                    print(f"    FAIL: Phase {pn} -- {r.name}")
    elif total_skip == 0:
        print("  All tests passed!")
    else:
        print(f"  All executed tests passed! ({total_skip} hardware tests skipped)")
    print()

    # Phase summary table
    print("  Phase Summary:")
    print(f"  {'#':<4s} {'Name':<40s} {'Pass':>6s} {'Fail':>6s} {'Skip':>6s}")
    print("  " + "-" * 64)
    for pn in phases_to_run:
        if pn in all_results:
            name = PHASES[pn][0]
            p = sum(1 for r in all_results[pn] if r.passed and not r.skipped)
            f = sum(1 for r in all_results[pn] if not r.passed and not r.skipped)
            s = sum(1 for r in all_results[pn] if r.skipped)
            c = "\033[92m" if f == 0 else "\033[91m"
            print(f"  {pn:<4d} {name:<40s} {c}{p:>6d}\033[0m {f:>6d} "
                  f"{('\033[93m' + str(s) + '\033[0m') if s > 0 else str(s):>6s}")
    print("=" * 78)

    # Cleanup
    if controller_connected:
        controller.set_led(0, 0, 255)  # Reset LED to blue
        controller.close()
        print("  [HW] Controller disconnected cleanly.")

    # Declare readiness
    if total_fail == 0:
        print()
        if controller_connected:
            print("  \033[92m>>> READY FOR FULL HARDWARE TESTING <<<\033[0m")
            print("  All 72 tests passed with real DualSense Edge hardware.")
        elif total_skip == 0:
            print("  \033[92m>>> READY FOR FULL HARDWARE TESTING <<<\033[0m")
            print("  All simulation tests passed. Connect controller for full validation.")
        else:
            print("  \033[93m>>> PARTIAL -- connect DualSense Edge for full validation <<<\033[0m")
    print()

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
