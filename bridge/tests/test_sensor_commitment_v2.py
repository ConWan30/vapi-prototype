"""
Tests for VAPI sensor commitment schema v2 (kinematic/haptic).

Schema v2: SHA-256 of 48-byte kinematic/haptic input:
    struct.pack(">hhhhBBBBffffffIQ",
        left_stick_x, left_stick_y, right_stick_x, right_stick_y,  # h x4 = 8
        l2_trigger, r2_trigger, l2_effect_mode, r2_effect_mode,     # B x4 = 4
        accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z,          # f x6 = 24
        buttons,                                                       # I    = 4
        timestamp_ms,                                                  # Q    = 8
    )                                                                  # Total = 48

The 228-byte PoAC wire format is UNCHANGED. Schema v2 only changes what
data is packed into the 32-byte sensor_commitment field of each record.
"""

import hashlib
import struct


# ---------------------------------------------------------------------------
# Standalone v2 helper — no external deps required
# ---------------------------------------------------------------------------

def compute_v2(
    left_stick_x: int = 0,
    left_stick_y: int = 0,
    right_stick_x: int = 0,
    right_stick_y: int = 0,
    l2_trigger: int = 0,
    r2_trigger: int = 0,
    l2_effect_mode: int = 0,
    r2_effect_mode: int = 0,
    accel_x: float = 0.0,
    accel_y: float = 0.0,
    accel_z: float = 0.0,
    gyro_x: float = 0.0,
    gyro_y: float = 0.0,
    gyro_z: float = 0.0,
    buttons: int = 0,
    timestamp_ms: int = 0,
) -> bytes:
    """Compute sensor commitment schema v2 — returns 32-byte SHA-256 digest."""
    raw = struct.pack(
        ">hhhhBBBBffffffIQ",
        left_stick_x, left_stick_y,
        right_stick_x, right_stick_y,
        l2_trigger, r2_trigger,
        l2_effect_mode, r2_effect_mode,
        accel_x, accel_y, accel_z,
        gyro_x, gyro_y, gyro_z,
        buttons,
        timestamp_ms,
    )
    return hashlib.sha256(raw).digest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_raw_bytes_length():
    """Schema v2 input to SHA-256 must be exactly 48 bytes."""
    raw = struct.pack(
        ">hhhhBBBBffffffIQ",
        0, 0, 0, 0,
        0, 0, 0, 0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0,
        0,
    )
    assert len(raw) == 48


def test_sha256_output_length():
    """SHA-256 output is always 32 bytes."""
    result = compute_v2()
    assert len(result) == 32


def test_determinism():
    """Same inputs always produce the identical hash."""
    h1 = compute_v2(left_stick_x=100, gyro_x=0.5, timestamp_ms=1000)
    h2 = compute_v2(left_stick_x=100, gyro_x=0.5, timestamp_ms=1000)
    assert h1 == h2


def test_timestamp_sensitivity():
    """Different timestamp values produce different hashes."""
    h1 = compute_v2(timestamp_ms=1000)
    h2 = compute_v2(timestamp_ms=1001)
    assert h1 != h2


def test_l2_trigger_sensitivity():
    """Different L2 trigger depression values produce different hashes."""
    h1 = compute_v2(l2_trigger=0)
    h2 = compute_v2(l2_trigger=255)
    assert h1 != h2


def test_r2_trigger_sensitivity():
    """Different R2 trigger depression values produce different hashes."""
    h1 = compute_v2(r2_trigger=0)
    h2 = compute_v2(r2_trigger=128)
    assert h1 != h2


def test_l2_effect_mode_sensitivity():
    """Different L2 adaptive trigger effect modes produce different hashes."""
    h1 = compute_v2(l2_effect_mode=0)
    h2 = compute_v2(l2_effect_mode=1)
    assert h1 != h2


def test_r2_effect_mode_sensitivity():
    """Different R2 adaptive trigger effect modes produce different hashes."""
    h1 = compute_v2(r2_effect_mode=0)
    h2 = compute_v2(r2_effect_mode=3)
    assert h1 != h2


def test_stick_axis_sensitivity():
    """Different stick axis values produce different hashes."""
    h1 = compute_v2(left_stick_x=0, right_stick_y=0)
    h2 = compute_v2(left_stick_x=32767, right_stick_y=-32768)
    assert h1 != h2


def test_buttons_sensitivity():
    """Different button states produce different hashes."""
    h1 = compute_v2(buttons=0)
    h2 = compute_v2(buttons=0xFFFF)
    assert h1 != h2
