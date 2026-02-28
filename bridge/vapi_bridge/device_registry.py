"""
VAPI Phase 19 — Device Profile Registry

DeviceProfileRegistry resolves the active DeviceProfile for the current
bridge session. Resolution priority:

    1. Explicit override via cfg.device_profile_id (env: DEVICE_PROFILE_ID)
    2. Auto-detect from connected USB HID devices (VID/PID matching)
    3. Default fallback: sony_dualshock_edge_v1

The registry is instantiated once in DualShockTransport._init_hardware() and
held as self._device_profile. The bridge's sensor_commitment size and
schema_version are then driven by the resolved profile.

This module is the entry point for hardware partner integrations: registering
a new DeviceProfile in controller/profiles/ is all that's needed to make VAPI
support a new controller model.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------

class DeviceProfileRegistry:
    """
    Loads DeviceProfile objects from controller/profiles/ and resolves the
    active profile for the current session.

    Parameters
    ----------
    controller_dir : Path
        Path to the controller/ directory (added to sys.path if needed).
        Typically: Path(__file__).parents[3] / "controller"
    """

    def __init__(self, controller_dir: Path) -> None:
        self._controller_dir = controller_dir
        controller_dir_str = str(controller_dir)
        if controller_dir_str not in sys.path:
            sys.path.insert(0, controller_dir_str)

        # Import the profiles package (controller/profiles/__init__.py)
        try:
            from profiles import get_profile, detect_profile, all_profiles  # type: ignore
            self._get_profile    = get_profile
            self._detect_profile = detect_profile
            self._all_profiles   = all_profiles
            log.debug(
                "DeviceProfileRegistry loaded %d profiles",
                len(all_profiles()),
            )
        except ImportError as exc:
            raise RuntimeError(
                f"Cannot import controller profiles from {controller_dir}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def resolve(self, cfg) -> "DeviceProfile":
        """
        Resolve the active DeviceProfile for the current session.

        Priority:
            1. cfg.device_profile_id — explicit override (DEVICE_PROFILE_ID env var).
               If set and recognised, this profile is used unconditionally.
            2. Auto-detect from connected USB HID devices.
               Enumerates hid.enumerate() and matches VID/PID against the index.
            3. Default: sony_dualshock_edge_v1 (the primary PHCI-certified device).

        Parameters
        ----------
        cfg : Config
            Bridge configuration object. Reads device_profile_id and
            auto_detect_device attributes (both optional, see config.py).

        Returns
        -------
        DeviceProfile
            The resolved profile. Never raises — always returns a valid profile.
        """
        # Priority 1: explicit override
        profile_id = getattr(cfg, "device_profile_id", "")
        if profile_id:
            try:
                profile = self._get_profile(profile_id)
                log.info(
                    "Device profile (explicit override): %s (PHCI=%s)",
                    profile.display_name, profile.phci_tier.name,
                )
                return profile
            except KeyError:
                log.warning(
                    "DEVICE_PROFILE_ID=%r not found — falling back to auto-detect",
                    profile_id,
                )

        # Priority 2: auto-detect from HID enumeration
        if getattr(cfg, "auto_detect_device", True):
            detected = self._try_detect_hid()
            if detected is not None:
                log.info(
                    "Device profile (auto-detected VID/PID): %s (PHCI=%s)",
                    detected.display_name, detected.phci_tier.name,
                )
                return detected

        # Priority 3: default to DualSense Edge
        default = self._get_profile("sony_dualshock_edge_v1")
        log.info(
            "Device profile (default): %s (PHCI=%s)",
            default.display_name, default.phci_tier.name,
        )
        return default

    def get_profile(self, profile_id: str) -> "DeviceProfile":
        """Return a DeviceProfile by profile_id. Raises KeyError if not found."""
        return self._get_profile(profile_id)

    def all_profiles(self) -> list:
        """Return all registered DeviceProfile objects."""
        return self._all_profiles()

    # ------------------------------------------------------------------
    # HID auto-detection
    # ------------------------------------------------------------------

    def _try_detect_hid(self) -> "Optional[DeviceProfile]":
        """
        Enumerate connected USB HID devices and return the first profile match.

        Returns None if hid is unavailable or no matching device is found.
        Errors are silently swallowed — auto-detection is a best-effort feature.
        """
        try:
            import hid  # type: ignore
            for device_info in hid.enumerate():
                vid = device_info.get("vendor_id", 0)
                pid = device_info.get("product_id", 0)
                profile = self._detect_profile(vid, pid)
                if profile is not None:
                    log.debug(
                        "HID auto-detect: VID=0x%04X PID=0x%04X → %s",
                        vid, pid, profile.profile_id,
                    )
                    return profile
        except Exception as exc:
            log.debug("HID auto-detection unavailable: %s", exc)
        return None
