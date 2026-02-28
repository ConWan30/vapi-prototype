"""
VAPI Device Profile — Sony DualSense (CFI-ZCT1W, standard edition)

PHCITier.STANDARD — the standard DualSense has all sensors (gyro, accel,
touchpad) but does NOT have motorised adaptive trigger resistance. Without
the adaptive trigger surface, Layer 4 (biometric trigger dynamics) and
Layer 5 (temporal rhythm oracle) are unavailable.

PITL layers active: L2 (HID-XInput oracle) + L3 (behavioral ML classifier).
Sensor commitment: 48B v2 base (no biometric extension).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from device_profile import ControllerFamily, DeviceProfile, PHCITier

GENERIC_DUALSENSE = DeviceProfile(
    profile_id="sony_dualsense_v1",
    display_name="Sony DualSense (CFI-ZCT1W)",
    manufacturer="Sony Interactive Entertainment",
    family=ControllerFamily.SONY_DUALSENSE,
    phci_tier=PHCITier.STANDARD,
    # USB VID 0x054C = Sony; PIDs: 0x0CE6 (USB wired), 0x0CE7 (Bluetooth)
    hid_vendor_id=0x054C,
    hid_product_ids=(0x0CE6, 0x0CE7),
    has_adaptive_triggers=False,    # Standard spring triggers, not motorised
    has_gyroscope=True,
    has_accelerometer=True,
    has_touchpad=True,
    back_paddle_count=0,
    trigger_resolution_bits=8,
    stick_resolution_bits=16,
    schema_version=2,
    sensor_commitment_size_bytes=48,  # 48B base only (no biometric extension)
    pitl_layers=(2, 3),               # L2 + L3 only
    certification_notes=(
        "Standard DualSense controller (CFI-ZCT1W). PHCI_STANDARD tier: "
        "digital presence proof + behavioral classification active. "
        "No adaptive triggers → L4/L5 biometric/temporal layers unavailable."
    ),
)
