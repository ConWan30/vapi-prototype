"""
l0_bluetooth_presence.py — L0 Bluetooth Physical Presence Verifier.

Provides advisory scoring for whether a Bluetooth-connected DualShock Edge
is physically present (real device) vs a virtual HID emulator.

Three signals combined into a single [0.0, 1.0] overall_score:

  A) Sequence counter integrity (weight 0.5)
     BT reports include a monotonically incrementing sequence byte at
     ds.states[0].  Gaps indicate missing reports — virtual HID emulators
     rarely replicate the hardware sequence counter faithfully.
     score = 1.0 - min(1.0, gap_count / max(1, N))

  B) Inter-report latency distribution (weight 0.4)
     Real DualShock Edge in gaming mode: ~4 ms mean, CV < 0.3.
     Virtual HID: typically >= 30 ms or highly variable (scheduling jitter).
     score = 1.0 if mean_ms <= 10 else max(0.0, 1.0 - (mean_ms - 10) / 50)

  C) RSSI (weight 0.1)
     hidapi on Windows does not expose BT RSSI — always returns 0.5 (neutral).
     Retained as a structural hook for future platform support.

Scoring is ADVISORY ONLY:
  - Does not affect humanity_probability or PoAC inference codes.
  - Used for logging and get_startup_diagnostics() dashboard surface.
  - Logged as WARNING when overall_score < 0.3 AND transport == bt.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class BTPresenceResult:
    """Result from a single BluetoothPresenceVerifier.verify_presence() call."""

    transport: str             # "bt" or "usb"
    is_bluetooth: bool         # True only when transport == "bt"

    # Component scores
    rssi_score: float          # Always 0.5 — RSSI unavailable on Windows/hidapi
    latency_score: float       # [0, 1] based on inter-report mean interval
    sequence_score: float      # [0, 1] based on BT sequence counter gap rate

    # Weighted composite
    overall_score: float       # 0.5*sequence + 0.4*latency + 0.1*rssi

    # Diagnostics
    sequence_gap_count: int    # Number of detected sequence counter jumps
    mean_interval_ms: float    # Mean inter-report interval in milliseconds
    n_reports: int             # Number of reports analysed


class BluetoothPresenceVerifier:
    """
    Verifies physical presence of a Bluetooth DualShock Edge over a batch of reports.

    Usage:
        verifier = BluetoothPresenceVerifier(transport_str)
        result = verifier.verify_presence(snaps, bt_counter_bytes)
        if result.overall_score < 0.3 and result.is_bluetooth:
            log.warning("BT physical presence check failed")
    """

    # Component weights — must sum to 1.0
    _W_SEQUENCE = 0.5
    _W_LATENCY  = 0.4
    _W_RSSI     = 0.1

    # Latency scoring: below LOW_MS → score 1.0; above HIGH_MS → score 0.0
    _LATENCY_LOW_MS  = 10.0
    _LATENCY_HIGH_MS = 60.0   # _LOW + 50

    def __init__(self, transport: str) -> None:
        """
        Args:
            transport: "bt" or "usb" (value from TransportType.value).
        """
        self._transport = transport.lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_presence(
        self,
        snaps: list,
        bt_counter_bytes: Optional[List[int]] = None,
    ) -> BTPresenceResult:
        """
        Compute a physical presence score from a batch of controller snapshots.

        Args:
            snaps:            List of InputSnapshot objects (or any object with
                              an `inter_frame_us` attribute).  Used for latency
                              signal.
            bt_counter_bytes: Optional list of raw sequence counter bytes
                              (ds.states[0] for each report).  When None or
                              empty, sequence_score defaults to 0.5 (unknown).

        Returns:
            BTPresenceResult with all component scores populated.
        """
        is_bt = self._transport == "bt"
        n = len(snaps)

        if not is_bt or n == 0:
            # Non-BT transport or no data: neutral score
            return BTPresenceResult(
                transport=self._transport,
                is_bluetooth=is_bt,
                rssi_score=0.5,
                latency_score=0.5,
                sequence_score=0.5,
                overall_score=0.5,
                sequence_gap_count=0,
                mean_interval_ms=0.0,
                n_reports=n,
            )

        # --- Signal A: sequence counter ---
        seq_score, gap_count = self._sequence_score(bt_counter_bytes, n)

        # --- Signal B: inter-report latency ---
        lat_score, mean_ms = self._latency_score(snaps)

        # --- Signal C: RSSI (always neutral on Windows/hidapi) ---
        rssi_score = 0.5

        overall = (
            self._W_SEQUENCE * seq_score
            + self._W_LATENCY  * lat_score
            + self._W_RSSI     * rssi_score
        )

        return BTPresenceResult(
            transport=self._transport,
            is_bluetooth=True,
            rssi_score=rssi_score,
            latency_score=lat_score,
            sequence_score=seq_score,
            overall_score=round(overall, 4),
            sequence_gap_count=gap_count,
            mean_interval_ms=round(mean_ms, 3),
            n_reports=n,
        )

    # ------------------------------------------------------------------
    # Internal signal scorers
    # ------------------------------------------------------------------

    def _sequence_score(
        self,
        counter_bytes: Optional[List[int]],
        n: int,
    ) -> tuple[float, int]:
        """Return (score, gap_count).  score=0.5 when counter unavailable."""
        if not counter_bytes:
            return 0.5, 0

        gap_count = 0
        for i in range(1, len(counter_bytes)):
            expected = (counter_bytes[i - 1] + 1) & 0xFF
            if counter_bytes[i] != expected:
                gap_count += 1

        score = 1.0 - min(1.0, gap_count / max(1, n))
        return round(score, 4), gap_count

    def _latency_score(self, snaps: list) -> tuple[float, float]:
        """Return (score, mean_ms).  score=0.5 when inter_frame_us unavailable."""
        intervals_us: List[float] = []
        for s in snaps:
            ift = getattr(s, "inter_frame_us", None)
            if ift is not None and ift > 0:
                intervals_us.append(float(ift))

        if not intervals_us:
            return 0.5, 0.0

        mean_ms = statistics.mean(intervals_us) / 1000.0
        if mean_ms <= self._LATENCY_LOW_MS:
            score = 1.0
        elif mean_ms >= self._LATENCY_HIGH_MS:
            score = 0.0
        else:
            score = 1.0 - (mean_ms - self._LATENCY_LOW_MS) / (
                self._LATENCY_HIGH_MS - self._LATENCY_LOW_MS
            )

        return round(score, 4), round(mean_ms, 3)
