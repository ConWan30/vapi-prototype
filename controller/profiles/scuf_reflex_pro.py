"""
VAPI Device Profile — SCUF Reflex Pro

PHCITier.STANDARD — SCUF Reflex Pro is built on a custom DualSense-licensed
PCB (USB VID 0x2F24, SCUF Gaming). It includes 4 rear paddles and hair-trigger
locks but does NOT have motorised adaptive triggers. PITL L2 + L3 active.

Partnership note: SCUF Gaming licenses Sony DualSense technology. VAPI can
extend a PHCI_STANDARD certification to SCUF Reflex Pro with this profile,
enabling SCUF to market controllers as "VAPI PHCI Certified (Standard)".
Future SCUF models with adaptive triggers would qualify for PHCI_CERTIFIED.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from device_profile import ControllerFamily, DeviceProfile, PHCITier

SCUF_REFLEX_PRO = DeviceProfile(
    profile_id="scuf_reflex_pro_v1",
    display_name="SCUF Reflex Pro",
    manufacturer="SCUF Gaming",
    family=ControllerFamily.SCUF,
    phci_tier=PHCITier.STANDARD,
    # USB VID 0x2F24 = SCUF Gaming; PID 0x0011 = Reflex Pro
    hid_vendor_id=0x2F24,
    hid_product_ids=(0x0011,),
    has_adaptive_triggers=False,    # SCUF uses mechanical triggers (no motorised resistance)
    has_gyroscope=True,
    has_accelerometer=True,
    has_touchpad=True,
    back_paddle_count=4,            # Signature SCUF 4 rear paddles
    trigger_resolution_bits=8,
    stick_resolution_bits=16,
    schema_version=2,
    sensor_commitment_size_bytes=48,
    pitl_layers=(2, 3),
    certification_notes=(
        "SCUF Reflex Pro — SCUF Gaming's flagship PlayStation 5 controller. "
        "Custom PCB (VID=0x2F24) licensed from Sony. 4 rear paddles. "
        "PHCI_STANDARD: L2+L3 active. No adaptive triggers → L4/L5 unavailable. "
        "Certification partnership: SCUF Gaming."
    ),
)
