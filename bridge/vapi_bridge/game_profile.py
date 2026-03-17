"""
Phase 51: Game-Aware Profile System.

Maps game titles to button semantics, L5 priority overrides, and L6-Passive config.
Registry is populated at import time. Query via get_profile() / get_profile_or_none().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GameProfile:
    """Immutable game-specific biometric profiling configuration."""

    profile_id: str
    """Unique slug, e.g. 'ncaa_cfb_26'."""

    display_name: str
    """Human-readable name shown in dashboard and agent responses."""

    publisher: str
    """Game publisher / developer."""

    platform: str
    """Target platform, e.g. 'ps5'."""

    # --- L5 oracle ---
    l5_button_priority: List[str]
    """
    Ordered list of button names for L5 temporal rhythm scoring.
    First button with >= 20 samples wins. Names must match TemporalRhythmOracle
    _DEQUE_MAP keys: 'cross', 'l2_dig', 'r2', 'triangle'.
    """

    # --- L6-Passive ---
    l6_passive_enabled: bool
    """
    When True, the bridge passively measures sprint-button onset timing per press.
    No controller writes — zero conflict with PS5 Bluetooth haptics.
    """

    l6_passive_button: str
    """Which button to observe for L6-Passive. Typically 'r2' (sprint)."""

    l6_passive_ema_alpha: float
    """EMA smoothing factor for running baseline. Lower = slower adaptation."""

    l6_passive_baseline_n: int
    """Number of bootstrap presses before EMA kicks in."""

    l6_passive_flag_ratio: float
    """
    Onset_ms / baseline_ms ratio that triggers a 'resistance event' flag.
    E.g. 1.5 = onset 50% slower than personal baseline = PS5 haptic resistance likely.
    """

    # --- Semantic button map ---
    button_map: Dict[str, str]
    """
    Maps button identifiers to game-semantic role descriptions.
    Used by BridgeAgent to give game-contextual explanations.
    e.g. {'r2': 'Sprint / Bullet pass modifier', 'cross': 'Snap / Receiver select'}
    """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, GameProfile] = {}


def register_profile(profile: GameProfile) -> None:
    """Register a GameProfile. Overwrites if profile_id already exists."""
    _REGISTRY[profile.profile_id] = profile


def get_profile(profile_id: str) -> GameProfile:
    """Return profile by ID. Raises KeyError if not found."""
    return _REGISTRY[profile_id]


def get_profile_or_none(profile_id: str) -> Optional[GameProfile]:
    """Return profile by ID, or None if not registered."""
    return _REGISTRY.get(profile_id)


def all_profiles() -> List[GameProfile]:
    """Return all registered profiles (copy of values)."""
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# NCAA College Football 26
# ---------------------------------------------------------------------------

NCAA_CFB_26 = GameProfile(
    profile_id="ncaa_cfb_26",
    display_name="NCAA College Football 26",
    publisher="EA Sports",
    platform="ps5",

    # R2 = sprint (primary, sustained holds 200-2500ms, every running/defensive play)
    # Cross = snap confirmation + receiver selection (high frequency taps)
    # L2_dig = lob pass modifier + ball protection (moderate, short holds)
    # Triangle = switch player / stiff arm / throw away (situational)
    l5_button_priority=["r2", "cross", "l2_dig", "triangle"],

    l6_passive_enabled=True,
    l6_passive_button="r2",
    l6_passive_ema_alpha=0.15,   # slow EMA — sprint hold times vary widely by game situation
    l6_passive_baseline_n=20,    # bootstrap on first 20 sprint presses
    l6_passive_flag_ratio=1.5,   # 50% slower onset than personal mean = resistance event

    button_map={
        "r2":       "Sprint / Bullet pass modifier (primary — held every play)",
        "l2":       "Lob pass modifier / Ball protection / Strip attempt",
        "cross":    "Snap ball / Receiver select / Dive / Low tackle",
        "circle":   "QB slide / Pitch / Receiver (high routes)",
        "square":   "Juke cut / Speed rush / Receiver (low routes)",
        "triangle": "Switch player / Stiff arm / Throw away",
        "r1":       "Pass protection / Hot route / Hurry up offense",
        "l1":       "Audible / Flip play / Motion",
        "r_stick":  "Ball carrier moves (juke, spin, truck)",
        "l_stick":  "Player movement (360 analog)",
        "d_pad":    "Formation / Play selection / Snap count",
    },
)

register_profile(NCAA_CFB_26)
