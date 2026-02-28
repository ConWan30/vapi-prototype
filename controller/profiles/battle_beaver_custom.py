"""
VAPI Device Profile — Battle Beaver Custom DualSense Edge

PHCITier.CERTIFIED — Battle Beaver Customs performs physical modifications to
stock DualSense Edge hardware (CFI-ZCP1): hair trigger locks, rear paddle
additions, button remapping switches, stick tension tuning, and shell
customization. Crucially, Battle Beaver does NOT change the HID descriptor —
the controller reports as the same VID/PID as a standard DualSense Edge (0x054C /
0x0DF2 or 0x0DF3).

Since the adaptive trigger mechanism is preserved, this profile is
PHCI_CERTIFIED with all 4 PITL layers active — identical to the base
DualSense Edge profile. The VID/PID overlap with the stock Edge means
auto-detection will return the first matching profile (DUALSHOCK_EDGE).
Users wanting explicit Battle Beaver attribution must set DEVICE_PROFILE_ID=
battle_beaver_dualshock_edge_v1 in the bridge config.

Partnership note: Battle Beaver is a premium controller customizer. VAPI
certification enables "VAPI PHCI Certified" branding on customized units.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from device_profile import ControllerFamily, DeviceProfile, PHCITier

BATTLE_BEAVER_EDGE = DeviceProfile(
    profile_id="battle_beaver_dualshock_edge_v1",
    display_name="Battle Beaver Custom DualSense Edge",
    manufacturer="Battle Beaver Customs",
    family=ControllerFamily.BATTLE_BEAVER,
    phci_tier=PHCITier.CERTIFIED,
    # Same HID as DualSense Edge — Battle Beaver does not alter HID descriptor
    hid_vendor_id=0x054C,
    hid_product_ids=(0x0DF2, 0x0DF3),
    has_adaptive_triggers=True,     # Adaptive triggers preserved from Edge base
    has_gyroscope=True,
    has_accelerometer=True,
    has_touchpad=True,
    back_paddle_count=2,            # BB standard mod adds 2 rear paddles (Edge has 4 natively; BB may vary)
    trigger_resolution_bits=8,
    stick_resolution_bits=16,
    schema_version=2,
    sensor_commitment_size_bytes=56,
    pitl_layers=(2, 3, 4, 5),
    certification_notes=(
        "Physical DualSense Edge modification by Battle Beaver Customs. "
        "Adaptive triggers retained — PHCI_CERTIFIED with full L2-L5 PITL stack. "
        "HID VID/PID identical to stock DualSense Edge (0x054C/0x0DF2). "
        "Explicit profile selection required: DEVICE_PROFILE_ID=battle_beaver_dualshock_edge_v1."
    ),
)
