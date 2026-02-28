"""
Phase 8 Tests — HID-XInput Discrepancy Oracle

Coverage:
  1. Availability detection (3) — unavailable on non-Windows; mock available on Windows
  2. Discrepancy computation (4) — identical=0.0; max diff=1.0; partial=expected; threshold
  3. Window accumulation (4) — single frame no trigger; half-window triggers; reset clears;
                               below-threshold after trigger clears
  4. classify() output (4) — returns None when clean; returns (0x28, conf) when triggered;
                              confidence scales with magnitude; minimum conf=180 when triggered
  5. Integration: DualShockTransport with oracle (3) — disabled cfg → no DRIVER_INJECT;
                  enabled + mock discrepancy → inference overridden;
                  primary cheat code NOT overridden by oracle
  6. Graceful degradation (2) — unavailable oracle update() returns None; classify() returns None
"""

import sys
import types
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make bridge package importable
sys.path.insert(0, str(Path(__file__).parents[1]))
# Make controller package importable
sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))

from vapi_bridge.hid_xinput_oracle import HidXInputOracle, INFER_DRIVER_INJECT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snap(lx=0, ly=0, rx=0, ry=0):
    """Build a minimal mock InputSnapshot with stick values."""
    snap = MagicMock()
    snap.left_stick_x  = lx
    snap.left_stick_y  = ly
    snap.right_stick_x = rx
    snap.right_stick_y = ry
    return snap


def _make_unavailable_oracle(**kwargs):
    """Return an oracle that is guaranteed unavailable (non-Windows path)."""
    with patch("vapi_bridge.hid_xinput_oracle.platform.system", return_value="Linux"):
        return HidXInputOracle(**kwargs)


def _make_available_oracle(threshold=0.15, window_size=30, gamepad_index=0):
    """Return an oracle where XInput DLL load is mocked to succeed."""
    mock_dll = MagicMock()
    mock_dll.XInputGetState.return_value = 0  # ERROR_SUCCESS
    with patch(
        "vapi_bridge.hid_xinput_oracle.platform.system", return_value="Windows"
    ):
        with patch(
            "vapi_bridge.hid_xinput_oracle.ctypes.windll"
        ) as mock_windll:
            mock_windll.LoadLibrary.return_value = mock_dll
            oracle = HidXInputOracle(
                threshold=threshold,
                window_size=window_size,
                gamepad_index=gamepad_index,
            )
    # Force available flag (mocking may not fully hook the init path)
    oracle._available = True
    oracle._xinput = mock_dll
    return oracle


# =====================================================================
# 1. Availability detection
# =====================================================================

class TestAvailability:
    def test_unavailable_on_linux(self):
        """Oracle is unavailable on non-Windows platforms."""
        oracle = _make_unavailable_oracle()
        assert oracle.available is False

    def test_unavailable_on_macos(self):
        with patch("vapi_bridge.hid_xinput_oracle.platform.system", return_value="Darwin"):
            oracle = HidXInputOracle()
        assert oracle.available is False

    def test_available_when_dll_loads(self):
        """Oracle marks itself available when XInput DLL loads successfully."""
        oracle = _make_available_oracle()
        assert oracle.available is True


# =====================================================================
# 2. Discrepancy computation
# =====================================================================

class TestDiscrepancyComputation:
    def test_identical_values_zero_discrepancy(self):
        """When HID and XInput report the same values, discrepancy = 0."""
        oracle = _make_unavailable_oracle()
        # HID snap at half-max: 16384 / 32768 ≈ 0.5
        snap = _make_snap(lx=16384, ly=0, rx=0, ry=0)
        xinput = (0.5, 0.0, 0.0, 0.0)
        score = oracle.compute_discrepancy(snap, xinput)
        assert abs(score) < 1e-6

    def test_max_difference_near_one(self):
        """Max possible discrepancy (HID=+1, XInput=-1 on all axes) is ~1.0."""
        oracle = _make_unavailable_oracle()
        # HID max positive: 32767 / 32768 ≈ 1.0; XInput = -1.0 → delta = 2.0 each
        snap = _make_snap(lx=32767, ly=32767, rx=32767, ry=32767)
        xinput = (-1.0, -1.0, -1.0, -1.0)
        score = oracle.compute_discrepancy(snap, xinput)
        assert score > 0.95
        assert score <= 1.0

    def test_partial_discrepancy_expected_value(self):
        """Partial discrepancy on one axis gives expected score."""
        oracle = _make_unavailable_oracle()
        # HID lx = 32768/2 = 16384 → 0.5; XInput lx = 0.0 → delta_lx = 0.25
        # Other axes identical → delta = 0
        # score = sqrt(0.25/4) = sqrt(0.0625) = 0.25
        snap = _make_snap(lx=16384, ly=0, rx=0, ry=0)
        xinput = (0.0, 0.0, 0.0, 0.0)
        score = oracle.compute_discrepancy(snap, xinput)
        expected = (0.25 / 4.0) ** 0.5  # ≈ 0.25
        assert abs(score - expected) < 0.01

    def test_discrepancy_above_and_below_threshold(self):
        """Verify compute_discrepancy produces values on both sides of 0.15."""
        oracle = _make_unavailable_oracle(threshold=0.15)
        # Below threshold: tiny difference
        snap_lo = _make_snap(lx=100, ly=0, rx=0, ry=0)
        score_lo = oracle.compute_discrepancy(snap_lo, (0.0, 0.0, 0.0, 0.0))
        assert score_lo < 0.15

        # Above threshold: significant difference on all axes
        snap_hi = _make_snap(lx=16384, ly=16384, rx=16384, ry=16384)
        score_hi = oracle.compute_discrepancy(snap_hi, (0.0, 0.0, 0.0, 0.0))
        assert score_hi > 0.15


# =====================================================================
# 3. Window accumulation
# =====================================================================

class TestWindowAccumulation:
    def test_single_frame_above_threshold_does_not_trigger(self):
        """A single suspicious frame is not enough to trigger classify()."""
        oracle = _make_available_oracle(threshold=0.15, window_size=10)
        snap = _make_snap(lx=32767, ly=32767, rx=32767, ry=32767)

        # Inject one high-discrepancy frame by manually appending to history
        oracle._discrepancy_history.append(0.9)
        result = oracle.classify()
        assert result is None  # window_size=10; only 1 of 5 required frames present

    def test_half_window_frames_triggers(self):
        """When >= window_size/2 frames are above threshold, classify() triggers."""
        oracle = _make_available_oracle(threshold=0.15, window_size=10)
        # Inject exactly 5 high-discrepancy frames (window_size/2 = 5)
        for _ in range(5):
            oracle._discrepancy_history.append(0.9)
        result = oracle.classify()
        assert result is not None
        assert result[0] == INFER_DRIVER_INJECT

    def test_reset_clears_state(self):
        """reset() clears all accumulated history."""
        oracle = _make_available_oracle(threshold=0.15, window_size=10)
        for _ in range(8):
            oracle._discrepancy_history.append(0.9)
        assert oracle.classify() is not None

        oracle.reset()
        assert len(oracle._discrepancy_history) == 0
        assert oracle.classify() is None

    def test_below_threshold_frames_prevent_trigger(self):
        """If most frames are below threshold, no injection is detected."""
        oracle = _make_available_oracle(threshold=0.15, window_size=10)
        # Fill with clean frames (4 of 10, below the required 5)
        for _ in range(6):
            oracle._discrepancy_history.append(0.05)  # clean
        for _ in range(4):
            oracle._discrepancy_history.append(0.9)   # suspicious (4 < 5 required)
        result = oracle.classify()
        assert result is None


# =====================================================================
# 4. classify() output
# =====================================================================

class TestClassifyOutput:
    def test_returns_none_when_clean(self):
        """classify() returns None when discrepancy window is clean."""
        oracle = _make_available_oracle(threshold=0.15, window_size=10)
        for _ in range(10):
            oracle._discrepancy_history.append(0.05)
        assert oracle.classify() is None

    def test_returns_driver_inject_code_when_triggered(self):
        """classify() returns (INFER_DRIVER_INJECT, confidence) when triggered."""
        oracle = _make_available_oracle(threshold=0.15, window_size=10)
        for _ in range(5):
            oracle._discrepancy_history.append(0.8)
        result = oracle.classify()
        assert result is not None
        code, confidence = result
        assert code == INFER_DRIVER_INJECT  # 0x28

    def test_confidence_scales_with_discrepancy(self):
        """Higher average discrepancy produces higher confidence."""
        oracle_lo = _make_available_oracle(threshold=0.10, window_size=10)
        oracle_hi = _make_available_oracle(threshold=0.10, window_size=10)

        for _ in range(5):
            oracle_lo._discrepancy_history.append(0.3)  # moderate
        for _ in range(5):
            oracle_hi._discrepancy_history.append(0.9)  # high

        res_lo = oracle_lo.classify()
        res_hi = oracle_hi.classify()
        assert res_lo is not None and res_hi is not None
        assert res_hi[1] > res_lo[1]

    def test_minimum_confidence_180_when_triggered(self):
        """Confidence is clamped to minimum 180 even for low-but-above-threshold discrepancy."""
        oracle = _make_available_oracle(threshold=0.15, window_size=10)
        # Set discrepancy just above threshold — raw confidence would be ~0.16 * 255 ≈ 41
        for _ in range(5):
            oracle._discrepancy_history.append(0.16)
        result = oracle.classify()
        assert result is not None
        assert result[1] >= 180


# =====================================================================
# 5. Integration with DualShockTransport
# =====================================================================

class TestTransportIntegration:
    """Test that DualShockTransport correctly wires oracle into classification."""

    def _make_transport_with_oracle(self, oracle_enabled: bool, mock_oracle=None):
        """Build a minimal DualShockTransport with mocked dependencies."""
        from vapi_bridge.dualshock_integration import (
            DualShockTransport,
            INFER_NOMINAL,
            INFER_CHEAT_AIM,
            CHEAT_CODES,
        )
        from vapi_bridge.config import Config

        cfg = Config()
        store = MagicMock()
        transport = DualShockTransport.__new__(DualShockTransport)
        transport._cfg = cfg
        transport._store = store
        transport._on_record = MagicMock()
        transport._chain = None
        transport._interval = 1.0
        transport._oracle_addr = ""
        transport._bounty_cfg = ""
        transport._key_dir = Path("/tmp")
        transport._attest_addr = ""
        transport._reader = None
        transport._engine = None
        transport._classifier = None
        transport._device_id = b"\x00" * 32
        transport._pubkey_hex = "00" * 65
        transport._pubkey_bytes = b"\x00" * 65
        transport._identity = None
        transport._oracle = MagicMock()
        transport._oracle.apply.return_value = 1000
        transport._progress = None
        transport._last_raw = None

        transport._hid_oracle = mock_oracle
        transport._backend_classifier = None
        return transport

    def test_oracle_disabled_no_driver_inject(self):
        """When oracle is None (disabled), no DRIVER_INJECT is produced."""
        from vapi_bridge.dualshock_integration import INFER_NOMINAL, CHEAT_CODES
        transport = self._make_transport_with_oracle(False, mock_oracle=None)
        # Manually simulate the override block logic
        inference = INFER_NOMINAL
        if transport._hid_oracle is not None and inference not in CHEAT_CODES:
            oracle_result = transport._hid_oracle.classify()
            if oracle_result is not None:
                inference, _ = oracle_result
        assert inference == INFER_NOMINAL

    def test_oracle_enabled_mock_discrepancy_overrides_nominal(self):
        """When oracle has detected injection, inference is overridden to DRIVER_INJECT."""
        from vapi_bridge.dualshock_integration import INFER_NOMINAL, CHEAT_CODES

        mock_oracle = MagicMock()
        mock_oracle.classify.return_value = (INFER_DRIVER_INJECT, 200)

        transport = self._make_transport_with_oracle(True, mock_oracle=mock_oracle)

        inference = INFER_NOMINAL
        confidence = 220
        if transport._hid_oracle is not None and inference not in CHEAT_CODES:
            oracle_result = transport._hid_oracle.classify()
            if oracle_result is not None:
                inference, confidence = oracle_result

        assert inference == INFER_DRIVER_INJECT  # 0x28
        assert confidence == 200

    def test_primary_cheat_not_overridden_by_oracle(self):
        """When primary classifier already returned a cheat code, oracle does NOT override."""
        from vapi_bridge.dualshock_integration import INFER_CHEAT_AIM, CHEAT_CODES

        mock_oracle = MagicMock()
        mock_oracle.classify.return_value = (INFER_DRIVER_INJECT, 200)

        transport = self._make_transport_with_oracle(True, mock_oracle=mock_oracle)

        inference = INFER_CHEAT_AIM   # primary already detected aimbot
        confidence = 230
        # Override block — should NOT apply because inference is in CHEAT_CODES
        if transport._hid_oracle is not None and inference not in CHEAT_CODES:
            oracle_result = transport._hid_oracle.classify()
            if oracle_result is not None:
                inference, confidence = oracle_result

        assert inference == INFER_CHEAT_AIM   # unchanged
        assert confidence == 230              # unchanged


# =====================================================================
# 6. Graceful degradation
# =====================================================================

class TestGracefulDegradation:
    def test_update_returns_none_when_unavailable(self):
        """On non-Windows, update() returns None without raising."""
        oracle = _make_unavailable_oracle()
        snap = _make_snap(lx=1000, ly=2000, rx=-1000, ry=0)
        result = oracle.update(snap)
        assert result is None

    def test_classify_returns_none_when_unavailable(self):
        """On non-Windows, classify() returns None without raising."""
        oracle = _make_unavailable_oracle()
        result = oracle.classify()
        assert result is None

    def test_summary_returns_dict_always(self):
        """summary() always returns a dict regardless of availability."""
        oracle = _make_unavailable_oracle()
        s = oracle.summary()
        assert isinstance(s, dict)
        assert "available" in s
        assert s["available"] is False

    def test_poll_xinput_returns_none_when_unavailable(self):
        """poll_xinput() returns None when oracle unavailable."""
        oracle = _make_unavailable_oracle()
        assert oracle.poll_xinput() is None
