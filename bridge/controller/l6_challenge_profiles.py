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

PROFILE_VERSION = 1

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
    1: TriggerChallengeProfile(
        profile_id=1,
        name="RIGID_LIGHT",
        r2_mode=TRIGGER_RIGID, r2_forces=(80,),
        l2_mode=TRIGGER_RIGID, l2_forces=(60,),
        onset_threshold_ms=300.0,
        settle_threshold_ms=1500.0,
        description="Light rigid resistance — both triggers",
    ),
    2: TriggerChallengeProfile(
        profile_id=2,
        name="RIGID_HEAVY",
        r2_mode=TRIGGER_RIGID, r2_forces=(200,),
        l2_mode=TRIGGER_RIGID, l2_forces=(180,),
        onset_threshold_ms=300.0,
        settle_threshold_ms=1500.0,
        description="Heavy rigid resistance — both triggers",
    ),
    3: TriggerChallengeProfile(
        profile_id=3,
        name="PULSE_SLOW",
        r2_mode=TRIGGER_PULSE, r2_forces=(100, 50, 100, 50, 0, 0, 0),
        l2_mode=TRIGGER_PULSE, l2_forces=(80,  40, 80,  40, 0, 0, 0),
        onset_threshold_ms=400.0,
        settle_threshold_ms=2000.0,
        description="Slow alternating pulse — both triggers",
    ),
    4: TriggerChallengeProfile(
        profile_id=4,
        name="PULSE_FAST",
        r2_mode=TRIGGER_PULSE, r2_forces=(180, 20, 180, 20, 0, 0, 0),
        l2_mode=TRIGGER_PULSE, l2_forces=(150, 15, 150, 15, 0, 0, 0),
        onset_threshold_ms=400.0,
        settle_threshold_ms=2000.0,
        description="Fast alternating pulse — both triggers",
    ),
    5: TriggerChallengeProfile(
        profile_id=5,
        name="RIGID_ASYM",
        r2_mode=TRIGGER_RIGID, r2_forces=(150,),
        l2_mode=TRIGGER_RIGID, l2_forces=(30,),
        onset_threshold_ms=350.0,
        settle_threshold_ms=1800.0,
        description="Asymmetric: R2 heavy (150), L2 light (30)",
    ),
    6: TriggerChallengeProfile(
        profile_id=6,
        name="PULSE_BUILDUP",
        r2_mode=TRIGGER_PULSE, r2_forces=(20, 60, 120, 180, 220, 0, 0),
        l2_mode=TRIGGER_OFF,   l2_forces=(0,),
        onset_threshold_ms=450.0,
        settle_threshold_ms=2500.0,
        description="Progressive buildup on R2 only; L2 off",
    ),
    7: TriggerChallengeProfile(
        profile_id=7,
        name="RIGID_MAX",
        r2_mode=TRIGGER_RIGID, r2_forces=(255,),
        l2_mode=TRIGGER_RIGID, l2_forces=(255,),
        onset_threshold_ms=300.0,
        settle_threshold_ms=1500.0,
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
