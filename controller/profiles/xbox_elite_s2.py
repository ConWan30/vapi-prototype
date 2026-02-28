"""
VAPI Device Profile — Xbox Elite Series 2

PHCITier.STANDARD — The Xbox Elite Wireless Controller Series 2 is Microsoft's
flagship competitive gaming controller. It features 4 back paddles, adjustable
trigger locks, rubberized grips, and up to 40 hours battery life. However, it
uses XInput/USB HID (no Sony DualSense haptics API) and critically lacks:
  - IMU (no gyroscope or accelerometer)
  - Adaptive triggers (fixed resistance)
  - Touchpad

Without IMU data, PITL Layer 4 (biometric Mahalanobis distance via micro-tremor
variance, accelerometer-based grip) is not computable. Without adaptive triggers,
Layer 5 TemporalRhythmOracle loses trigger-mode transition cadence signals.

Therefore, the Xbox Elite S2 achieves PHCITier.STANDARD (L2+L3 only):
  - L2: HID-XInput oracle (USB HID 0x045E/0x0B00)
  - L3: Behavioral ML (stick motion, button timing patterns)
  - L4: NOT AVAILABLE (no IMU)
  - L5: NOT AVAILABLE (no adaptive triggers)

The FeatureNormalizer (controller/feature_normalizer.py) zero-fills unsupported
features for cross-controller biometric comparison.

Market note: Xbox controllers dominate competitive PC gaming. Supporting the Elite
S2 makes VAPI relevant to the largest segment of competitive FPS/Battle Royale
players on PC, opening the PC esports vertical.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from device_profile import ControllerFamily, DeviceProfile, PHCITier

XBOX_ELITE_S2 = DeviceProfile(
    profile_id="xbox_elite_s2_v1",
    display_name="Xbox Elite Series 2",
    manufacturer="Microsoft",
    family=ControllerFamily.GENERIC_XINPUT,    # XInput HID stack
    phci_tier=PHCITier.STANDARD,               # L2 + L3 only (no IMU, no adaptive triggers)
    # USB VID 0x045E = Microsoft; PID 0x0B00 = Elite Series 2 (USB wired HID)
    hid_vendor_id=0x045E,
    hid_product_ids=(0x0B00,),
    has_adaptive_triggers=False,               # Fixed trigger resistance
    has_gyroscope=False,                       # No IMU
    has_accelerometer=False,                   # No IMU
    has_touchpad=False,                        # No touchpad
    back_paddle_count=4,                       # 4 mappable back paddles (P1-P4)
    trigger_resolution_bits=10,               # XInput 10-bit trigger axis
    stick_resolution_bits=16,                 # XInput 16-bit signed stick axes
    schema_version=2,
    sensor_commitment_size_bytes=48,           # Standard 48B (no biometric extension)
    pitl_layers=(2, 3),                        # L2: HID-XInput, L3: Behavioral ML
    certification_notes=(
        "Xbox Elite Series 2 — Microsoft flagship competitive controller. "
        "4 back paddles, adjustable trigger locks. No IMU = L4 biometric unavailable. "
        "No adaptive triggers = L5 rhythm calibration unavailable. "
        "STANDARD tier: HID-XInput oracle (L2) + Behavioral ML (L3). "
        "FeatureNormalizer zero-fills micro_tremor_accel_variance and "
        "trigger_resistance_change_rate for cross-controller comparison."
    ),
)
