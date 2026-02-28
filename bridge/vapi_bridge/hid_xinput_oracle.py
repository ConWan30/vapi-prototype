"""
VAPI Phase 8 — HID-XInput Discrepancy Oracle (Layer 2: Input Pipeline Integrity)

Detects driver-level input injection by comparing raw HID values (read by
pydualsense directly from USB) against XInput values (what the game sees via
DirectX/XInput API).

On an unmodified system:  HID ≈ XInput (within deadzone tolerance).
On a system running vJoy/virtual-device injection: HID ≠ XInput.

Architecture:
    - Windows-only for the XInput read path (ctypes → XInput1_4.dll)
    - Graceful no-op on Linux/macOS: available=False, all methods return None
    - Non-blocking: designed to be called once per frame in _poll_frames()
    - No third-party dependencies (ctypes only)
    - XInput1_4 (Win8+) with fallback to XInput9_1_0 (Win7)
"""

import ctypes
import ctypes.wintypes
import logging
import platform
import sys
from collections import deque
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .dualshock_integration import InputSnapshot  # type: ignore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 8 inference code (also declared in dualshock_integration for export)
# ---------------------------------------------------------------------------
INFER_DRIVER_INJECT = 0x28  # HID-XInput pipeline injection detected


# ---------------------------------------------------------------------------
# ctypes structs for Windows XInput
# ---------------------------------------------------------------------------
class _XINPUT_GAMEPAD(ctypes.Structure):
    """Maps to XINPUT_GAMEPAD in XInput.h."""
    _fields_ = [
        ("wButtons",       ctypes.wintypes.WORD),
        ("bLeftTrigger",   ctypes.c_ubyte),
        ("bRightTrigger",  ctypes.c_ubyte),
        ("sThumbLX",       ctypes.c_short),
        ("sThumbLY",       ctypes.c_short),
        ("sThumbRX",       ctypes.c_short),
        ("sThumbRY",       ctypes.c_short),
    ]


class XInputState(ctypes.Structure):
    """Maps to XINPUT_STATE in XInput.h."""
    _fields_ = [
        ("dwPacketNumber", ctypes.wintypes.DWORD),
        ("Gamepad",        _XINPUT_GAMEPAD),
    ]


# ---------------------------------------------------------------------------
# Oracle implementation
# ---------------------------------------------------------------------------
class HidXInputOracle:
    """
    Detects driver-level input injection by comparing raw HID values
    (read by pydualsense directly from USB) vs XInput values (what the game sees).

    On an unmodified system: HID ≈ XInput (within deadzone tolerance).
    On a system running vJoy/virtual device injection: HID ≠ XInput.

    Usage::

        oracle = HidXInputOracle(threshold=0.15, window_size=30)
        # In polling loop:
        discrepancy = oracle.update(snap)          # once per frame
        # After interval:
        result = oracle.classify()                 # (0x28, confidence) or None
    """

    def __init__(
        self,
        threshold: float = 0.15,
        window_size: int = 30,
        gamepad_index: int = 0,
    ):
        """
        Args:
            threshold:     Normalized discrepancy [0, 1] above which a frame counts
                           as "suspicious". Default 0.15 (15% normalized distance).
            window_size:   Number of consecutive frames to accumulate before
                           declaring DRIVER_INJECT. Default 30 (~0.25 s at 120 Hz).
            gamepad_index: XInput controller index (0–3). Default 0.
        """
        self._threshold = threshold
        self._window_size = window_size
        self._gamepad_index = gamepad_index

        self._discrepancy_history: deque[float] = deque(maxlen=window_size)
        self._trigger_count: int = 0
        self._last_discrepancy: float = 0.0
        self._last_classify: Optional[tuple[int, int]] = None

        # Try to load XInput DLL (Windows only)
        self._xinput = None
        self._available: bool = False
        self._init_xinput()

    def _init_xinput(self) -> None:
        """Attempt to load XInput DLL. Sets self._available."""
        if platform.system() != "Windows":
            log.debug("HidXInputOracle: non-Windows platform — oracle disabled")
            return

        for dll_name in ("XInput1_4", "XInput9_1_0", "XInput1_3"):
            try:
                dll = ctypes.windll.LoadLibrary(dll_name)  # type: ignore[attr-defined]
                # Verify GetState is callable
                _ = dll.XInputGetState
                self._xinput = dll
                self._available = True
                log.info("HidXInputOracle: loaded %s — oracle enabled", dll_name)
                return
            except (OSError, AttributeError):
                continue

        log.warning(
            "HidXInputOracle: no XInput DLL found "
            "(XInput1_4/XInput9_1_0/XInput1_3) — oracle disabled"
        )

    @property
    def available(self) -> bool:
        """True if XInput DLL loaded successfully on Windows."""
        return self._available

    def poll_xinput(self) -> Optional[tuple[float, float, float, float]]:
        """
        Read XInput state for the configured gamepad index.

        Returns:
            (lx, ly, rx, ry) normalized to [-1.0, 1.0], or None if XInput
            is not available or no controller found at the given index.
        """
        if not self._available or self._xinput is None:
            return None

        state = XInputState()
        try:
            ret = self._xinput.XInputGetState(self._gamepad_index, ctypes.byref(state))
        except Exception as exc:
            log.debug("XInputGetState failed: %s", exc)
            return None

        # ERROR_SUCCESS = 0; ERROR_DEVICE_NOT_CONNECTED = 1167
        if ret != 0:
            return None

        g = state.Gamepad
        # Normalize short [-32768, 32767] → float [-1, 1]
        # Avoid division by zero: 32768 for negative, 32767 for positive
        def _norm(v: int) -> float:
            if v < 0:
                return v / 32768.0
            elif v > 0:
                return v / 32767.0
            return 0.0

        return (
            _norm(g.sThumbLX),
            _norm(g.sThumbLY),
            _norm(g.sThumbRX),
            _norm(g.sThumbRY),
        )

    def compute_discrepancy(
        self,
        snap: "InputSnapshot",
        xinput: tuple[float, float, float, float],
    ) -> float:
        """
        Compute normalized discrepancy between raw HID snapshot and XInput values.

        Uses Euclidean distance in normalized 4D stick space (left + right axes),
        scaled so maximum possible discrepancy = 1.0.

        Formula:
            delta = sqrt((Δlx² + Δly² + Δrx² + Δry²) / 4)

        Where each stick axis is pre-normalized to [-1, 1]:
            HID:    snap.left_stick_x / 32768, snap.left_stick_y / 32768, ...
            XInput: (lx, ly, rx, ry) already in [-1, 1]

        Args:
            snap:   InputSnapshot with raw HID stick values in [-32768, 32767].
            xinput: (lx, ly, rx, ry) normalized XInput values.

        Returns:
            Discrepancy score in [0.0, 1.0].
        """
        # Normalize HID stick values (pydualsense reports [-32768, 32767])
        hid_lx = getattr(snap, "left_stick_x", 0) / 32768.0
        hid_ly = getattr(snap, "left_stick_y", 0) / 32768.0
        hid_rx = getattr(snap, "right_stick_x", 0) / 32768.0
        hid_ry = getattr(snap, "right_stick_y", 0) / 32768.0

        xi_lx, xi_ly, xi_rx, xi_ry = xinput

        # Squared differences
        d_lx = (hid_lx - xi_lx) ** 2
        d_ly = (hid_ly - xi_ly) ** 2
        d_rx = (hid_rx - xi_rx) ** 2
        d_ry = (hid_ry - xi_ry) ** 2

        # Mean squared distance, square-rooted → normalized [0, 1]
        # Max possible: each delta = 2.0 (e.g., HID=+1, XInput=-1)
        # Mean of 4 squared-deltas max = 4.0/4 = 1.0; sqrt = 1.0 ✓
        score = ((d_lx + d_ly + d_rx + d_ry) / 4.0) ** 0.5
        return min(1.0, score)

    def update(self, snap: "InputSnapshot") -> Optional[float]:
        """
        Poll XInput, compute discrepancy against raw HID snap, and update window.

        Call once per frame in the polling loop. Non-blocking.

        Args:
            snap: Current InputSnapshot from DualSenseReader.poll().

        Returns:
            Discrepancy score [0.0, 1.0] if XInput is available, else None.
        """
        if not self._available:
            return None

        xinput = self.poll_xinput()
        if xinput is None:
            # Controller not connected on XInput side — not an injection signal
            return None

        score = self.compute_discrepancy(snap, xinput)
        self._discrepancy_history.append(score)
        self._last_discrepancy = score

        if score > self._threshold:
            self._trigger_count += 1
        else:
            self._trigger_count = max(0, self._trigger_count - 1)

        return score

    def classify(self) -> Optional[tuple[int, int]]:
        """
        Classify the accumulated window for driver injection.

        Trigger condition: >= window_size / 2 frames in the current history window
        have discrepancy > threshold.

        Confidence: scaled from avg_discrepancy in the window, clamped to [180, 255].

        Returns:
            (INFER_DRIVER_INJECT, confidence) if sustained injection detected.
            None if clean or oracle unavailable / insufficient data.
        """
        if not self._available or len(self._discrepancy_history) < self._window_size // 2:
            return None

        above_threshold = sum(
            1 for d in self._discrepancy_history if d > self._threshold
        )
        trigger_required = self._window_size // 2

        if above_threshold < trigger_required:
            self._last_classify = None
            return None

        avg_discrepancy = sum(self._discrepancy_history) / len(self._discrepancy_history)
        confidence = int(avg_discrepancy * 255)
        confidence = max(180, min(255, confidence))

        self._last_classify = (INFER_DRIVER_INJECT, confidence)
        return self._last_classify

    def reset(self) -> None:
        """Clear accumulated window state. Call at session start."""
        self._discrepancy_history.clear()
        self._trigger_count = 0
        self._last_discrepancy = 0.0
        self._last_classify = None

    def summary(self) -> dict:
        """
        Return a diagnostics snapshot.

        Returns:
            dict with keys:
                available (bool), current_discrepancy (float),
                trigger_rate (float in [0,1]), last_classify (tuple or None)
        """
        history = list(self._discrepancy_history)
        trigger_rate = (
            sum(1 for d in history if d > self._threshold) / len(history)
            if history else 0.0
        )
        return {
            "available":           self._available,
            "current_discrepancy": self._last_discrepancy,
            "trigger_rate":        trigger_rate,
            "last_classify":       self._last_classify,
        }
