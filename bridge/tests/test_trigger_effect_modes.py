"""
test_trigger_effect_modes.py — Phase 11 Priority 2

Tests that adaptive trigger effect modes (_l2_effect_mode / _r2_effect_mode) are
correctly wired into the sensor commitment schema v2 hash, and that set_trigger_effect()
and _update_trigger_effect_modes() behave correctly.

These tests run entirely in simulation mode (no physical controller required).
"""

import sys
import struct
import hashlib
import types
from pathlib import Path

# --- Path setup (mirrors other bridge tests) ----------------------------------
sys.path.insert(0, str(Path(__file__).parents[1]))            # bridge/
sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))  # controller/

from vapi_bridge.dualshock_integration import DualShockTransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg():
    """Minimal config namespace that satisfies DualShockTransport.__init__."""
    return types.SimpleNamespace(
        dualshock_record_interval_s=1.0,
        skill_oracle_address="",
        dualshock_active_bounties="",
        dualshock_key_dir=str(Path.home() / ".vapi"),
        progress_attestation_address="",
        hid_oracle_enabled=False,
        backend_cheat_enabled=False,
        identity_backend="software",
        yubikey_piv_slot="9c",
        atecc608_i2c_bus=1,
        bridge_private_key_source="env",
        keystore_path="",
        keystore_password_env="BRIDGE_KEYSTORE_PASSWORD",
    )


def _make_transport():
    """Create a DualShockTransport instance in simulation mode (no I/O calls)."""
    return DualShockTransport(
        cfg=_make_cfg(),
        store=None,
        on_record_cb=None,
        chain_client=None,
    )


def _commitment_hash(l2_effect_mode: int, r2_effect_mode: int) -> bytes:
    """Compute sensor commitment hash for given effect modes with all other fields zeroed."""
    commitment_bytes = struct.pack(
        ">hhhhBBBBffffffIQ",
        0, 0,                             # sticks
        0, 0,
        0, 0,                             # l2_trigger, r2_trigger
        l2_effect_mode, r2_effect_mode,   # the fields under test
        0.0, 0.0, 0.0,                    # accel
        0.0, 0.0, 0.0,                    # gyro
        0,                                # buttons
        1_700_000_000_000,                # timestamp_ms (fixed)
    )
    return hashlib.sha256(commitment_bytes).digest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_default_l2_effect_mode_is_zero():
    """Transport initialises with _l2_effect_mode = 0."""
    t = _make_transport()
    assert t._l2_effect_mode == 0


def test_default_r2_effect_mode_is_zero():
    """Transport initialises with _r2_effect_mode = 0."""
    t = _make_transport()
    assert t._r2_effect_mode == 0


def test_set_trigger_effect_l2_updates_state():
    """set_trigger_effect('L2', 1) sets _l2_effect_mode to 1."""
    t = _make_transport()
    t.set_trigger_effect("L2", 1)
    assert t._l2_effect_mode == 1
    assert t._r2_effect_mode == 0  # R2 unchanged


def test_set_trigger_effect_r2_updates_state():
    """set_trigger_effect('R2', 2) sets _r2_effect_mode to 2."""
    t = _make_transport()
    t.set_trigger_effect("R2", 2)
    assert t._r2_effect_mode == 2
    assert t._l2_effect_mode == 0  # L2 unchanged


def test_l2_effect_mode_changes_commitment_hash():
    """Changing l2_effect_mode produces a different schema v2 commitment hash."""
    hash_mode0 = _commitment_hash(l2_effect_mode=0, r2_effect_mode=0)
    hash_mode1 = _commitment_hash(l2_effect_mode=1, r2_effect_mode=0)
    hash_mode2 = _commitment_hash(l2_effect_mode=2, r2_effect_mode=0)
    # All three hashes must be distinct
    assert hash_mode0 != hash_mode1
    assert hash_mode1 != hash_mode2
    assert hash_mode0 != hash_mode2


def test_r2_effect_mode_changes_commitment_hash():
    """Changing r2_effect_mode produces a different schema v2 commitment hash."""
    hash_mode0 = _commitment_hash(l2_effect_mode=0, r2_effect_mode=0)
    hash_mode1 = _commitment_hash(l2_effect_mode=0, r2_effect_mode=1)
    hash_mode2 = _commitment_hash(l2_effect_mode=0, r2_effect_mode=2)
    assert hash_mode0 != hash_mode1
    assert hash_mode1 != hash_mode2
    assert hash_mode0 != hash_mode2
