"""
VAPI Device Profile — HORI Fighting Commander for PS5

PHCITier.NONE — The HORI Fighting Commander is a licensed PlayStation 5
peripheral designed for 2D fighting games. It uses a directional pad instead
of analog sticks, has no gyroscope, no adaptive triggers, and no rear paddles.
The absence of sticks makes PITL Layer 3 (behavioral stick-motion ML) and
Layer 4 (biometric trigger dynamics) meaningless — only the HID-XInput oracle
(Layer 2) is applicable.

Note: Fighting game players have a distinct input profile. VAPI can still
provide on-chain attestation for fighting game matches using this profile,
confirming the human physical presence at the controller without the full
biometric suite.

Partnership note: HORI Co., Ltd. is a major Japanese licensed PlayStation
peripheral manufacturer. VAPI PHCI certification for fighting game controllers
opens the esports + fighting game community vertical.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from device_profile import ControllerFamily, DeviceProfile, PHCITier

HORI_FIGHTING_COMMANDER = DeviceProfile(
    profile_id="hori_fighting_commander_ps5_v1",
    display_name="HORI Fighting Commander for PS5",
    manufacturer="HORI Co., Ltd.",
    family=ControllerFamily.HORI,
    phci_tier=PHCITier.NONE,
    # USB VID 0x0F0D = HORI Co., Ltd.; PID 0x0133 = Fighting Commander for PS5
    hid_vendor_id=0x0F0D,
    hid_product_ids=(0x0133,),
    has_adaptive_triggers=False,
    has_gyroscope=False,
    has_accelerometer=False,
    has_touchpad=False,
    back_paddle_count=0,
    trigger_resolution_bits=8,
    stick_resolution_bits=8,         # D-pad only (digital), sticks absent
    schema_version=2,
    sensor_commitment_size_bytes=48,
    pitl_layers=(2,),                # Only HID-XInput oracle (L2) is meaningful
    certification_notes=(
        "HORI Fighting Commander for PS5 — HORI-licensed PS5 fighting game pad. "
        "D-pad only (no analog sticks), no IMU, no adaptive triggers. "
        "PITL: HID-XInput oracle (L2) only. PHCI_NONE tier. "
        "On-chain PoAC attestation available for fighting game matches. "
        "Certification partnership: HORI Co., Ltd."
    ),
)
