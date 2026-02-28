"""
VAPI Phase 19 — Controller Profile Registry

Provides two public functions for profile resolution:

    get_profile(profile_id: str) -> DeviceProfile
        Raise KeyError if the profile_id is not registered.

    detect_profile(vendor_id: int, product_id: int) -> DeviceProfile | None
        Return the best-matching profile for a USB VID/PID pair, or None.

    all_profiles() -> list[DeviceProfile]
        Return all registered profiles in registration order.

Profiles are registered at import time. To add a new certified device:
    1. Create controller/profiles/<name>.py with a DeviceProfile constant.
    2. Import the constant here and add it to _ALL_PROFILES_LIST.
    3. Write a PHCICertifier.certify() call in test_phci_certification.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the controller/ directory is on sys.path so device_profile imports work
_controller_dir = str(Path(__file__).parents[1])
if _controller_dir not in sys.path:
    sys.path.insert(0, _controller_dir)

from device_profile import DeviceProfile

from .battle_beaver_custom import BATTLE_BEAVER_EDGE
from .dualshock_edge import DUALSHOCK_EDGE
from .generic_dualsense import GENERIC_DUALSENSE
from .hori_fighting import HORI_FIGHTING_COMMANDER
from .scuf_reflex_pro import SCUF_REFLEX_PRO
from .xbox_elite_s2 import XBOX_ELITE_S2

# Registration order matters: profiles earlier in the list take priority
# when multiple profiles share the same VID/PID (e.g., Battle Beaver vs. Edge).
_ALL_PROFILES_LIST: list[DeviceProfile] = [
    DUALSHOCK_EDGE,
    GENERIC_DUALSENSE,
    SCUF_REFLEX_PRO,
    BATTLE_BEAVER_EDGE,
    HORI_FIGHTING_COMMANDER,
    XBOX_ELITE_S2,          # Phase 28: Microsoft XInput STANDARD tier
]

# profile_id → DeviceProfile
_ALL_PROFILES: dict[str, DeviceProfile] = {
    p.profile_id: p for p in _ALL_PROFILES_LIST
}

# (vendor_id, product_id) → DeviceProfile
# When multiple profiles share a VID/PID pair (Battle Beaver vs. DualShock Edge),
# the first profile in _ALL_PROFILES_LIST wins (DUALSHOCK_EDGE takes priority).
_VID_PID_INDEX: dict[tuple, DeviceProfile] = {}
for _p in reversed(_ALL_PROFILES_LIST):   # reversed so earlier entries overwrite
    for _pid in _p.hid_product_ids:
        _VID_PID_INDEX[(_p.hid_vendor_id, _pid)] = _p


def get_profile(profile_id: str) -> DeviceProfile:
    """
    Return the DeviceProfile for the given profile_id.

    Raises KeyError if the profile_id is not registered.
    """
    return _ALL_PROFILES[profile_id]


def detect_profile(vendor_id: int, product_id: int) -> "DeviceProfile | None":
    """
    Return the best-matching DeviceProfile for a USB VID/PID pair.

    Returns None if no profile matches.
    When multiple profiles share a VID/PID (e.g., Battle Beaver and DualSense Edge
    both present as 0x054C/0x0DF2), the higher-priority profile is returned
    (determined by registration order in _ALL_PROFILES_LIST).
    """
    return _VID_PID_INDEX.get((vendor_id, product_id))


def all_profiles() -> list[DeviceProfile]:
    """Return all registered DeviceProfile objects in registration order."""
    return list(_ALL_PROFILES_LIST)


__all__ = [
    "DeviceProfile",
    "get_profile",
    "detect_profile",
    "all_profiles",
    "DUALSHOCK_EDGE",
    "GENERIC_DUALSENSE",
    "SCUF_REFLEX_PRO",
    "BATTLE_BEAVER_EDGE",
    "HORI_FIGHTING_COMMANDER",
    "XBOX_ELITE_S2",
]
