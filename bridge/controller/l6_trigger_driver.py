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
import secrets
import time
from random import Random
from typing import TYPE_CHECKING

from bridge.controller.l6_challenge_profiles import (
    CHALLENGE_PROFILES,
    TriggerChallengeProfile,
)

if TYPE_CHECKING:
    pass  # DualSense is not imported at module level to avoid hardware dependency


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
    """

    def __init__(self) -> None:
        self.sequencer = ChallengeSequencer()

    async def send_challenge(self, profile_id: int, ds: object) -> float:
        """Send the given profile to the controller. Returns monotonic timestamp."""
        profile = CHALLENGE_PROFILES[profile_id]
        await asyncio.to_thread(self._sync_write, ds, profile)
        return time.monotonic()

    async def clear_triggers(self, ds: object) -> None:
        """Restore both triggers to BASELINE_OFF (no resistance)."""
        baseline = CHALLENGE_PROFILES[0]
        await asyncio.to_thread(self._sync_write, ds, baseline)

    @staticmethod
    def _sync_write(ds: object, profile: TriggerChallengeProfile) -> None:
        """Synchronous — called inside asyncio.to_thread().

        Sets trigger modes and forces via pydualsense DSTrigger API.
        pydualsense auto-commits trigger state on its next output report cycle;
        no explicit flush is required. Mirrors dualshock_integration.set_trigger_effect().
        """
        lt = ds.triggerL
        lt.setMode(profile.l2_mode)
        for i, f in enumerate(profile.l2_forces):
            lt.setForce(i, f)

        rt = ds.triggerR
        rt.setMode(profile.r2_mode)
        for i, f in enumerate(profile.r2_forces):
            rt.setForce(i, f)
