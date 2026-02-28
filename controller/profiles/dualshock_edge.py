"""
VAPI Device Profile — Sony DualSense Edge (CFI-ZCP1)

Primary PHCI-CERTIFIED device. The DualSense Edge's adaptive trigger system
(motorised L2/R2 resistance dynamics) is the hardware-rooted biometric surface
that drives Layer 4 (biometric Mahalanobis) and Layer 5 (temporal rhythm oracle).

This profile reproduces all existing VAPI defaults so the DualShock transport
is 100% backward-compatible with pre-Phase-19 operation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from device_profile import ControllerFamily, DeviceProfile, PHCITier

DUALSHOCK_EDGE = DeviceProfile(
    profile_id="sony_dualshock_edge_v1",
    display_name="Sony DualSense Edge (CFI-ZCP1)",
    manufacturer="Sony Interactive Entertainment",
    family=ControllerFamily.SONY_DUALSENSE,
    phci_tier=PHCITier.CERTIFIED,
    # USB VID 0x054C = Sony; PIDs: 0x0DF2 (USB wired), 0x0DF3 (Bluetooth)
    hid_vendor_id=0x054C,
    hid_product_ids=(0x0DF2, 0x0DF3),
    has_adaptive_triggers=True,     # Motorised L2/R2 — PHCI_CERTIFIED requirement
    has_gyroscope=True,
    has_accelerometer=True,
    has_touchpad=True,
    back_paddle_count=4,
    trigger_resolution_bits=8,
    stick_resolution_bits=16,
    schema_version=2,               # v2 kinematic/haptic
    sensor_commitment_size_bytes=56, # 48B base + 8B biometric distance extension
    pitl_layers=(2, 3, 4, 5),      # All PITL layers active
    certification_notes=(
        "Primary VAPI PHCI-CERTIFIED device. Adaptive triggers enable "
        "Layer 4 biometric Mahalanobis and Layer 5 temporal rhythm oracle. "
        "Hardware: Sony CFI-ZCP1 (DualSense Edge). "
        "Inference codes: 0x20-0x2B + 0x30."
    ),
)
