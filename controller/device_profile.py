"""
VAPI Phase 19 — Device Profile (Universal Controller Abstraction Layer)

Declarative, immutable description of a controller model's sensor capabilities,
PHCI certification tier, HID identity, and PITL layer configuration.

This is the VAPI hardware licensing surface: controller manufacturers license the
right to register a DeviceProfile with PHCI certification. Once certified, their
hardware can be stamped "VAPI PHCI Certified" and users' gameplay records are
cryptographically attested on the IoTeX chain.

Usage:
    from profiles import get_profile, detect_profile
    profile = get_profile("sony_dualshock_edge_v1")
    # or auto-detect from USB VID/PID:
    profile = detect_profile(0x054C, 0x0DF2)  # → DUALSHOCK_EDGE
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ControllerFamily(IntEnum):
    """Broad family of controllers sharing hardware architecture."""
    SONY_DUALSENSE = 1    # Standard DualSense / DualSense Edge
    SCUF           = 2    # SCUF Gaming (licensed Sony tech + custom PCB)
    BATTLE_BEAVER  = 3    # Battle Beaver (physical DualSense mods)
    HORI           = 4    # HORI licensed peripherals
    RAZER          = 5    # Razer licensed PlayStation peripherals
    GENERIC_XINPUT = 10   # Any XInput-compatible controller
    CUSTOM         = 99   # Custom / developer profiles


class PHCITier(IntEnum):
    """
    Physical Human Controller Input certification tier.

    NONE      — No PITL layers beyond basic HID presence (no adaptive trigger surface).
    STANDARD  — Basic PHCI: digital presence proof + behavioral layer (L2+L3).
                No biometric layer (adaptive triggers absent or not certified).
    CERTIFIED — Full PHCI: adaptive triggers + biometric Mahalanobis (L4) +
                temporal rhythm oracle (L5). Maximum Sybil resistance.
    """
    NONE      = 0
    STANDARD  = 1
    CERTIFIED = 2


@dataclass(frozen=True)
class DeviceProfile:
    """
    Declarative VAPI device profile. Immutable once loaded.

    Defines sensor capabilities, schema routing, and PITL configuration
    for a specific controller model. Frozen so profiles are hashable and
    can be used as dict keys or stored in sets.

    Controller manufacturers license the right to register a DeviceProfile
    with PHCI certification — the profile is what they license. Partners
    stamp "VAPI PHCI Certified" on hardware after certification passes.

    Fields
    ------
    profile_id : str
        Unique, URL-safe slug. Convention: "<manufacturer_snake>_<model_snake>_v<N>".
        Example: "sony_dualshock_edge_v1", "scuf_reflex_pro_v1".

    display_name : str
        Human-readable name for dashboards and certification reports.

    manufacturer : str
        Legal entity name (for certification paperwork).

    family : ControllerFamily
        Broad hardware family — groups devices sharing the same PCB/HID stack.

    phci_tier : PHCITier
        Certification level determines which PITL layers are active.

    hid_vendor_id : int
        USB VID (16-bit). Used for auto-detection when a device is plugged in.

    hid_product_ids : tuple[int, ...]
        USB PIDs for this model (may include USB and Bluetooth variants).
        All PIDs in this tuple map to this profile in the VID/PID index.

    has_adaptive_triggers : bool
        Whether L2/R2 have motorised resistance (DualSense Edge haptic triggers).
        Required for PHCITier.CERTIFIED — without adaptive triggers, biometric
        trigger-dynamics features are unavailable.

    has_gyroscope : bool
        IMU gyroscope present (3-axis angular velocity).

    has_accelerometer : bool
        IMU accelerometer present (3-axis linear acceleration).

    has_touchpad : bool
        Capacitive touchpad present (DualSense style).

    back_paddle_count : int
        Number of back/rear paddles: 0, 2, or 4.

    trigger_resolution_bits : int
        Bit depth of trigger axis (8 for DualSense, typically).

    stick_resolution_bits : int
        Bit depth of analog stick axes (16 for DualSense).

    schema_version : int
        PoAC sensor commitment schema version:
            0 = unknown / unset
            1 = v1 environmental (Pebble Tracker)
            2 = v2 kinematic/haptic (DualSense family)

    sensor_commitment_size_bytes : int
        Byte size of the sensor commitment preimage before hashing:
            48 = v2 base (sticks + triggers + gyro + accel + timestamp)
            56 = v2 + biometric distance extension (PHCI_CERTIFIED only)

    pitl_layers : tuple[int, ...]
        PITL layer ordinals active for this device:
            2 = HID-XInput oracle (Layer 2, driver injection detection)
            3 = behavioral ML classifier (Layer 3, aimbot/wallhack)
            4 = biometric Mahalanobis (Layer 4, requires adaptive triggers)
            5 = temporal rhythm oracle (Layer 5, requires adaptive triggers)
        Layers not listed are skipped in the session loop.

    certification_notes : str
        Optional plain-text notes for certification documentation.
    """
    # --- Identity ---
    profile_id:   str
    display_name: str
    manufacturer: str
    family:       ControllerFamily
    phci_tier:    PHCITier

    # --- USB HID identity (auto-detection) ---
    hid_vendor_id:   int
    hid_product_ids: tuple  # tuple[int, ...]

    # --- Sensor capabilities ---
    has_adaptive_triggers:  bool
    has_gyroscope:          bool
    has_accelerometer:      bool
    has_touchpad:           bool
    back_paddle_count:      int
    trigger_resolution_bits: int
    stick_resolution_bits:   int

    # --- Schema and chain routing ---
    schema_version:              int
    sensor_commitment_size_bytes: int

    # --- PITL configuration ---
    pitl_layers: tuple  # tuple[int, ...]

    # --- Optional certification notes ---
    certification_notes: str = ""
