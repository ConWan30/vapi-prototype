"""
L6 Active Physical Challenge-Response — Trigger Output Driver

Provides:
  ChallengeSequencer  — selects random profile (never BASELINE_OFF), generates nonce
  L6TriggerDriver     — async wrapper that sends challenge profiles to pydualsense
                        using asyncio.to_thread() because pydualsense is synchronous

pydualsense DualSense object API (from installed package / dualshock_integration.py):
  ds.triggerL         — DSTrigger instance for L2 (left trigger)
  ds.triggerR         — DSTrigger instance for R2 (right trigger)
  trigger.setMode(mode_int_or_enum)   — sets resistance mode
  trigger.setForce(index, value)      — sets force value (index 0-6, value 0-255)
  pydualsense auto-commits on next output report — no explicit flush call needed.
  Pass self._reader.ds (the raw pydualsense object) as the 'ds' argument.

Safety invariants (never bypass):
  - Never challenge when player is idle (r2==0 AND l2==0 for last 10 reports)
  - Always restore BASELINE_OFF (profile 0) in shutdown cleanup
  - L6 disabled by default (config.l6_challenges_enabled = False)
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
import uuid
from random import Random
from typing import TYPE_CHECKING

from bridge.controller.l6_challenge_profiles import (
    CHALLENGE_PROFILES,
    TriggerChallengeProfile,
)

if TYPE_CHECKING:
    pass  # DualSense is not imported at module level to avoid hardware dependency

# Set L6_CAPTURE_MODE=true (or 1/yes) to log every challenge dispatch+response
# to the l6_capture_sessions SQLite table for human baseline calibration.
L6_CAPTURE_MODE: bool = os.getenv("L6_CAPTURE_MODE", "").lower() in ("1", "true", "yes")


class ChallengeSequencer:
    """Selects random challenge profiles and generates per-challenge nonces."""

    def __init__(self) -> None:
        self._rng = Random(secrets.randbits(64))
        self.current_nonce: bytes = secrets.token_bytes(4)
        self._last_profile_id: int = 0

    def select_random_profile(self) -> int:
        """Select a random profile_id, never returning 0 (BASELINE_OFF).

        Regenerates the current_nonce for each selection.
        """
        available = [pid for pid in CHALLENGE_PROFILES if pid != 0]
        self._last_profile_id = self._rng.choice(available)
        self.current_nonce = secrets.token_bytes(4)
        return self._last_profile_id

    def is_response_window_open(self, sent_ts: float, timeout_s: float) -> bool:
        """Return True if we are still within the response collection window."""
        return (time.monotonic() - sent_ts) < timeout_s


class L6TriggerDriver:
    """Async driver that sends trigger challenge profiles to a DualSense controller.

    Uses asyncio.to_thread() to run pydualsense's synchronous API without
    blocking the main event loop.

    If L6_CAPTURE_MODE env var is set, pass a store instance to enable automatic
    logging of every challenge+response pair to l6_capture_sessions.
    """

    def __init__(self, store=None) -> None:
        self.sequencer = ChallengeSequencer()
        self._store = store  # VAPIStore reference for capture logging (optional)

    async def send_challenge(self, profile_id: int, ds: object) -> float:
        """Send the given profile to the controller. Returns monotonic timestamp."""
        profile = CHALLENGE_PROFILES[profile_id]
        await asyncio.to_thread(self._sync_write, ds, profile)
        return time.monotonic()

    async def clear_triggers(self, ds: object) -> None:
        """Restore both triggers to BASELINE_OFF (no resistance)."""
        baseline = CHALLENGE_PROFILES[0]
        await asyncio.to_thread(self._sync_write, ds, baseline)

    async def log_capture(
        self,
        metrics,
        challenge_sent_ts: float,
        r2_pre_mean: float = 0.0,
        player_id: str = "",
        game_title: str = "",
        hw_session_ref: str = "",
        notes: str = "",
    ) -> None:
        """Log one L6 challenge-response to l6_capture_sessions (Phase 42).

        Only writes when L6_CAPTURE_MODE is True and a store has been provided.
        Safe to call for both valid and null (window-expired) responses.
        Never raises — capture failure must not interrupt the main bridge loop.

        Args:
            metrics:           L6ResponseMetrics from L6ResponseAnalyzer.compute_metrics().
            challenge_sent_ts: Monotonic timestamp returned by send_challenge().
            r2_pre_mean:       Mean R2 ADC value during the pre-challenge baseline window.
            player_id:         Operator-assigned player identifier (e.g. "P1").
            game_title:        Game title for this session (e.g. "Warzone").
            hw_session_ref:    Reference to the corresponding hw_*.json session file.
            notes:             Free-form operator notes.
        """
        import logging as _log
        _logger = _log.getLogger("vapi_bridge.l6_capture")
        if not L6_CAPTURE_MODE or self._store is None:
            _logger.debug("log_capture skipped: CAPTURE_MODE=%s store=%s", L6_CAPTURE_MODE, self._store is not None)
            return
        try:
            from bridge.controller.l6_challenge_profiles import CHALLENGE_PROFILES as _CP
            _profile_name = _CP.get(metrics.profile_id, None)
            pname = _profile_name.name if _profile_name and hasattr(_profile_name, "name") else str(metrics.profile_id)
            self._store.store_l6_capture(
                session_id=str(uuid.uuid4()),
                profile_id=metrics.profile_id,
                profile_name=pname,
                challenge_sent_ts=challenge_sent_ts,
                onset_ms=metrics.onset_ms,
                settle_ms=metrics.settle_ms,
                peak_delta=metrics.peak_delta,
                grip_variance=metrics.grip_variance,
                r2_pre_mean=r2_pre_mean,
                accel_variance=metrics.grip_variance,  # same signal, different column name
                player_id=player_id,
                game_title=game_title,
                hw_session_ref=hw_session_ref,
                notes=notes,
            )
            _logger.info(
                "L6 capture logged: profile=%d (%s) onset_ms=%.1f peak_delta=%.1f p_human=%.3f player=%s",
                metrics.profile_id, pname, metrics.onset_ms, metrics.peak_delta,
                0.0, player_id,
            )
        except Exception as _exc:
            _logger.warning("L6 capture FAILED (silent): %s", _exc)  # was: pass

    @staticmethod
    def _sync_write(ds: object, profile: TriggerChallengeProfile) -> None:
        """Synchronous — called inside asyncio.to_thread().

        Sets trigger modes and forces via pydualsense DSTrigger API.
        pydualsense auto-commits trigger state on its next output report cycle;
        no explicit flush is required. Mirrors dualshock_integration.set_trigger_effect().
        """
        from pydualsense import TriggerModes
        lt = ds.triggerL
        lt.setMode(TriggerModes(profile.l2_mode))
        for i, f in enumerate(profile.l2_forces):
            lt.setForce(i, f)

        rt = ds.triggerR
        rt.setMode(TriggerModes(profile.r2_mode))
        for i, f in enumerate(profile.r2_forces):
            rt.setForce(i, f)
