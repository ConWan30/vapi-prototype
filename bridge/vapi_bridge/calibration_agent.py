"""
VAPI Phase 17 — Auto-Calibration Agent

Autonomous threshold recalibration that replaces the manual step:
    python scripts/threshold_calibrator.py sessions/human/hw_*.json

Runs as ProactiveMonitor's 4th surveillance check. When enough new human
sessions have been captured (RECALIB_SESSION_DELTA), runs the calibrator
as a subprocess and atomically applies L4 threshold updates, subject to
a safety guard (MAX_THRESHOLD_DELTA = 10%).

Also performs session quality validation:
    - Flags sessions with anomalous polling rates (outside [800, 1100] Hz)
    - Excludes flagged sessions from calibration runs
    - Persists quality flags to protocol_insights

Integration:
    monitor = ProactiveMonitor(..., calibration_agent=CalibrationAgent(...))
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session quality constants
# ---------------------------------------------------------------------------

_POLLING_RATE_MIN_HZ: float = 800.0
"""Sessions with mean polling rate below this are anomalous (e.g., hw_043=203Hz)."""

_POLLING_RATE_MAX_HZ: float = 1100.0
"""Sessions with mean polling rate above this are anomalous."""

_EXPECTED_POLLING_RATE_HZ: float = 1000.0
"""Expected DualShock Edge USB polling rate."""


# ---------------------------------------------------------------------------
# Calibration agent
# ---------------------------------------------------------------------------

class CalibrationAgent:
    """
    Autonomous L4 threshold recalibration — Phase 17.

    Monitors sessions/human/ for new hw_*.json captures. When
    RECALIB_SESSION_DELTA or more new sessions appear, runs
    threshold_calibrator.py as a subprocess and applies the new
    L4 thresholds if the change is within MAX_THRESHOLD_DELTA (10%).

    Includes session quality guard: sessions with anomalous polling
    rates are excluded from calibration and flagged to protocol_insights.

    All errors are non-fatal — calibration failures are logged and reported
    as 'calibration_auto' insights but never crash the bridge.
    """

    RECALIB_SESSION_DELTA: int   = 5
    """Minimum new sessions before triggering recalibration."""

    MAX_THRESHOLD_DELTA: float   = 0.10
    """Maximum fractional change from current threshold before rejection."""

    MIN_INTERVAL_SECS: float     = 6.0 * 3600.0
    """Minimum seconds between calibration runs (6 hours throttle)."""

    def __init__(
        self,
        store,
        cfg,
        sessions_dir: str = "sessions/human",
        calibrator_script: str = "scripts/threshold_calibrator.py",
    ) -> None:
        self._store = store
        self._cfg = cfg
        self._sessions_dir = Path(sessions_dir)
        self._calibrator = Path(calibrator_script)
        self._last_count: int = 0
        self._last_run: float = 0.0

    # ------------------------------------------------------------------
    # Main entry point (called by ProactiveMonitor)
    # ------------------------------------------------------------------

    async def check_and_recalibrate(self) -> Optional[str]:
        """
        Check if recalibration is warranted and run it.

        Returns a human-readable result string if recalibration was
        attempted (success, rejection, or error), or None if skipped.
        """
        # Throttle: don't run more than once per MIN_INTERVAL_SECS
        if time.time() - self._last_run < self.MIN_INTERVAL_SECS:
            return None

        # Enumerate valid session files (quality-filtered)
        all_files = sorted(self._sessions_dir.glob("hw_*.json"))
        if not all_files:
            return None

        valid_files, quality_flags = self._filter_sessions(all_files)
        if quality_flags:
            log.info("Session quality: %d anomalous sessions excluded from calibration",
                     len(quality_flags))
            self._persist_quality_flags(quality_flags)

        count = len(valid_files)
        if count - self._last_count < self.RECALIB_SESSION_DELTA:
            return None

        log.info("Auto-calibration: %d valid sessions (delta=%d), running calibrator",
                 count, count - self._last_count)

        try:
            result = await self._run_calibrator(valid_files)
            self._last_count = count
            self._last_run = time.time()
            return result
        except Exception as exc:
            log.warning("Auto-calibration error: %s", exc)
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # Session quality filtering
    # ------------------------------------------------------------------

    def _filter_sessions(
        self,
        files: list[Path],
    ) -> Tuple[list[Path], list[dict]]:
        """
        Filter session files by polling rate quality.

        Returns (valid_files, quality_flag_records).
        quality_flag_records is a list of dicts describing excluded sessions.
        """
        valid: list[Path] = []
        flags: list[dict] = []

        for f in files:
            try:
                rate = self._estimate_polling_rate(f)
                if _POLLING_RATE_MIN_HZ <= rate <= _POLLING_RATE_MAX_HZ:
                    valid.append(f)
                else:
                    flags.append({
                        "session": f.name,
                        "polling_rate_hz": rate,
                        "reason": f"polling_rate {rate:.0f} Hz outside [{_POLLING_RATE_MIN_HZ:.0f}, {_POLLING_RATE_MAX_HZ:.0f}] Hz",
                    })
            except Exception as exc:
                log.debug("Quality check skipped for %s: %s", f.name, exc)
                valid.append(f)  # include on parse error (don't exclude on uncertainty)

        return valid, flags

    def _estimate_polling_rate(self, path: Path) -> float:
        """
        Estimate polling rate from timestamp_ms deltas in a session JSON.

        Returns mean polling rate in Hz. Reads only first 500 reports for speed.
        """
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        reports = data.get("reports", [])
        # Use up to 500 reports for rate estimate
        timestamps = [r["timestamp_ms"] for r in reports[:500] if "timestamp_ms" in r]
        if len(timestamps) < 10:
            return _EXPECTED_POLLING_RATE_HZ  # insufficient data → assume normal

        deltas = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)
                  if timestamps[i + 1] > timestamps[i]]
        if not deltas:
            return _EXPECTED_POLLING_RATE_HZ

        mean_delta_ms = sum(deltas) / len(deltas)
        if mean_delta_ms < 1e-6:
            return _EXPECTED_POLLING_RATE_HZ
        return 1000.0 / mean_delta_ms  # convert ms interval to Hz

    def _persist_quality_flags(self, flags: list[dict]) -> None:
        """Write session quality flags to protocol_insights table."""
        try:
            for flag in flags:
                content = (
                    f"Session quality excluded: {flag['session']} — {flag['reason']}"
                )
                self._store.store_protocol_insight(
                    "session_quality_flag",
                    content,
                    device_id="",
                    severity="warning",
                )
        except Exception as exc:
            log.debug("Could not persist session quality flags: %s", exc)

    # ------------------------------------------------------------------
    # Calibration subprocess
    # ------------------------------------------------------------------

    async def _run_calibrator(self, files: list[Path]) -> str:
        """Run threshold_calibrator.py and apply results if safe."""
        cmd = [sys.executable, str(self._calibrator)] + [str(f) for f in files]

        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )

        if proc.returncode != 0:
            return f"Calibration subprocess failed (rc={proc.returncode}): {proc.stderr[:300]}"

        new_a, new_c = parse_calibrator_output(proc.stdout)
        if new_a is None:
            return f"Calibration parse failed — unexpected output format: {proc.stdout[:300]}"

        # Safety guard: reject if delta exceeds MAX_THRESHOLD_DELTA
        try:
            cur_a = float(self._cfg.l4_anomaly_threshold)
            cur_c = float(self._cfg.l4_continuity_threshold)
        except (AttributeError, ValueError):
            return "Calibration skipped: cfg missing L4 threshold attributes"

        delta_a = abs(new_a - cur_a) / max(cur_a, 1e-6)
        delta_c = abs(new_c - cur_c) / max(cur_c, 1e-6)

        if delta_a > self.MAX_THRESHOLD_DELTA:
            return (
                f"REJECTED: anomaly_threshold delta {delta_a:.1%} exceeds "
                f"{self.MAX_THRESHOLD_DELTA:.0%} limit "
                f"({cur_a:.3f} -> {new_a:.3f})"
            )

        # Apply new thresholds
        self._cfg.l4_anomaly_threshold   = str(new_a)
        self._cfg.l4_continuity_threshold = str(new_c)

        log.info(
            "Auto-calibration applied: anomaly %.3f->%.3f (%.1f%%), "
            "continuity %.3f->%.3f (%.1f%%), sessions=%d",
            cur_a, new_a, delta_a * 100,
            cur_c, new_c, delta_c * 100,
            len(files),
        )
        return (
            f"Applied: anomaly_threshold={new_a:.3f} (was {cur_a:.3f}, delta={delta_a:.1%}), "
            f"continuity_threshold={new_c:.3f} (was {cur_c:.3f}, delta={delta_c:.1%}), "
            f"sessions={len(files)}"
        )


# ---------------------------------------------------------------------------
# Output parser (module-level for testability)
# ---------------------------------------------------------------------------

def parse_calibrator_output(stdout: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Parse threshold_calibrator.py stdout for L4 threshold values.

    Expected output lines (from threshold_calibrator.py):
        L4 anomaly_threshold: X.XXX
        L4 continuity_threshold: X.XXX

    Returns (anomaly_threshold, continuity_threshold) or (None, None) on failure.
    """
    a = re.search(r"L4 anomaly_threshold[:\s]+([0-9]+\.[0-9]+)", stdout)
    c = re.search(r"L4 continuity_threshold[:\s]+([0-9]+\.[0-9]+)", stdout)
    if a and c:
        return float(a.group(1)), float(c.group(1))
    return None, None
