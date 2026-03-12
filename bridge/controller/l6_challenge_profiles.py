"""
L6 Active Physical Challenge-Response — Challenge Profile Library

Defines the 8 named trigger resistance profiles used to probe human motor response.
Each profile specifies pydualsense TriggerModes values and force parameters for
both R2 and L2 adaptive trigger actuators on the DualShock Edge.

USB Output Report 0x02:
  R2 mode  @ byte [11], R2 forces @ bytes [12-17,20]
  L2 mode  @ byte [22], L2 forces @ bytes [23-28,31]
  (no CRC32 required for USB — that is Bluetooth-only)

pydualsense API (synchronous):
  trigger.setMode(mode_int)
  trigger.setForce(index, value)  # index 0-6, value 0-255
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Tuple

PROFILE_VERSION = 2  # v2: onset/settle thresholds hardware-calibrated (N=43 real responses, 2026-03-11)

# TriggerModes values (from pydualsense.enums.TriggerModes)
TRIGGER_OFF   = 0x00
TRIGGER_RIGID = 0x01
TRIGGER_PULSE = 0x02


@dataclass(frozen=True)
class TriggerChallengeProfile:
    """Immutable specification for a single L6 trigger challenge."""
    profile_id: int
    name: str
    r2_mode: int                    # TriggerModes value for right trigger
    r2_forces: Tuple[int, ...]      # up to 7 force values (0-255 each)
    l2_mode: int                    # TriggerModes value for left trigger
    l2_forces: Tuple[int, ...]      # up to 7 force values (0-255 each)
    onset_threshold_ms: float       # max valid human onset latency (ms)
    settle_threshold_ms: float      # max valid settle time (ms)
    description: str


CHALLENGE_PROFILES: dict[int, TriggerChallengeProfile] = {
    0: TriggerChallengeProfile(
        profile_id=0,
        name="BASELINE_OFF",
        r2_mode=TRIGGER_OFF, r2_forces=(0,),
        l2_mode=TRIGGER_OFF, l2_forces=(0,),
        onset_threshold_ms=500.0,
        settle_threshold_ms=2000.0,
        description="No resistance — baseline / reset state",
    ),
    # Thresholds: onset = mean+3σ (upper bound for valid human response),
    #             settle = mean+3σ; both calibrated from N=43 real DualShock Edge responses.
    # Profile 7 (RIGID_MAX) retains estimated values — only 2 captures, not production-grade.
    1: TriggerChallengeProfile(
        profile_id=1,
        name="RIGID_LIGHT",
        r2_mode=TRIGGER_RIGID, r2_forces=(80,),
        l2_mode=TRIGGER_RIGID, l2_forces=(60,),
        onset_threshold_ms=401.4,   # calibrated: mean=123ms, std=92.7ms, n=6
        settle_threshold_ms=664.5,  # calibrated: mean=226ms, std=146ms, n=6
        description="Light rigid resistance — both triggers",
    ),
    2: TriggerChallengeProfile(
        profile_id=2,
        name="RIGID_HEAVY",
        r2_mode=TRIGGER_RIGID, r2_forces=(200,),
        l2_mode=TRIGGER_RIGID, l2_forces=(180,),
        onset_threshold_ms=293.8,   # calibrated: mean=90.9ms, std=67.6ms, n=9
        settle_threshold_ms=678.3,  # calibrated: mean=236ms, std=147ms, n=9
        description="Heavy rigid resistance — both triggers",
    ),
    3: TriggerChallengeProfile(
        profile_id=3,
        name="PULSE_SLOW",
        r2_mode=TRIGGER_PULSE, r2_forces=(100, 50, 100, 50, 0, 0, 0),
        l2_mode=TRIGGER_PULSE, l2_forces=(80,  40, 80,  40, 0, 0, 0),
        onset_threshold_ms=139.0,   # calibrated: mean=76.8ms, std=20.7ms, n=10; injection_window_confirmed
        settle_threshold_ms=629.7,  # calibrated: mean=323ms, std=102ms, n=10
        description="Slow alternating pulse — both triggers",
    ),
    4: TriggerChallengeProfile(
        profile_id=4,
        name="PULSE_FAST",
        r2_mode=TRIGGER_PULSE, r2_forces=(180, 20, 180, 20, 0, 0, 0),
        l2_mode=TRIGGER_PULSE, l2_forces=(150, 15, 150, 15, 0, 0, 0),
        onset_threshold_ms=312.8,   # calibrated: mean=88.4ms, std=74.8ms, n=5
        settle_threshold_ms=585.0,  # calibrated: mean=188ms, std=132ms, n=5
        description="Fast alternating pulse — both triggers",
    ),
    5: TriggerChallengeProfile(
        profile_id=5,
        name="RIGID_ASYM",
        r2_mode=TRIGGER_RIGID, r2_forces=(150,),
        l2_mode=TRIGGER_RIGID, l2_forces=(30,),
        onset_threshold_ms=92.6,    # calibrated: mean=65.6ms, std=9.0ms, n=8; injection_window_confirmed
        settle_threshold_ms=535.5,  # calibrated: mean=164ms, std=123ms, n=8
        description="Asymmetric: R2 heavy (150), L2 light (30)",
    ),
    6: TriggerChallengeProfile(
        profile_id=6,
        name="PULSE_BUILDUP",
        r2_mode=TRIGGER_PULSE, r2_forces=(20, 60, 120, 180, 220, 0, 0),
        l2_mode=TRIGGER_OFF,   l2_forces=(0,),
        onset_threshold_ms=118.6,   # calibrated: mean=77.0ms, std=13.9ms, n=5; injection_window_confirmed
        settle_threshold_ms=624.0,  # calibrated: mean=209ms, std=138ms, n=5
        description="Progressive buildup on R2 only; L2 off",
    ),
    7: TriggerChallengeProfile(
        profile_id=7,
        name="RIGID_MAX",
        r2_mode=TRIGGER_RIGID, r2_forces=(255,),
        l2_mode=TRIGGER_RIGID, l2_forces=(255,),
        onset_threshold_ms=300.0,   # estimated — only 2 captures, not production-grade
        settle_threshold_ms=1500.0, # estimated — only 2 captures, not production-grade
        description="Maximum rigid resistance — both triggers",
    ),
}


def get_profile_hash(profile_id: int) -> int:
    """Return a 16-bit fingerprint for the given profile (for PoAC commitment).

    Deterministic: same profile_id always returns the same hash value.
    Different profiles always return different values (by construction).
    """
    p = CHALLENGE_PROFILES[profile_id]
    raw = json.dumps({
        "id":  p.profile_id,
        "r2m": p.r2_mode,
        "r2f": list(p.r2_forces),
        "l2m": p.l2_mode,
        "l2f": list(p.l2_forces),
        "v":   PROFILE_VERSION,
    }, sort_keys=True).encode()
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:2], "big")
