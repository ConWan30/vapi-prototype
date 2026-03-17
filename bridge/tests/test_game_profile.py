"""
Phase 51: Game-Aware Profile System -- unit tests.

Tests cover: profile registry, NCAA CFB 26 profile correctness,
L5 button priority override in TemporalRhythmOracle.
"""
import sys
import os
import random

import pytest

# sys.path: controller/ for oracle, bridge/ for vapi_bridge
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONTROLLER = os.path.join(_ROOT, "controller")
_BRIDGE = os.path.join(_ROOT, "bridge")
for _p in [_CONTROLLER, _BRIDGE]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from vapi_bridge.game_profile import (
    GameProfile,
    get_profile,
    get_profile_or_none,
    all_profiles,
    register_profile,
)
from temporal_rhythm_oracle import TemporalRhythmOracle  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(r2=0, cross=False, l2=0, triangle=False):
    """Minimal InputSnapshot stub."""
    class _S:
        r2_trigger = r2
        l2_trigger = l2
        buttons = (1 if cross else 0) | (8 if triangle else 0)
        left_stick_x = 128
        left_stick_y = 128
        right_stick_x = 128
        right_stick_y = 128
        accel_x = 0
        accel_y = 0
        accel_z = 0
        gyro_x = 0
        gyro_y = 0
        gyro_z = 0
    return _S()


def _push_r2_intervals(oracle, n: int, mean_ms: float = 300.0, std_ms: float = 5.0):
    """Push n synthetic R2 IBI intervals into oracle."""
    for _ in range(n):
        dur = max(10.0, random.gauss(mean_ms, std_ms))
        oracle._intervals.append(dur)


def _push_cross_intervals(oracle, n: int, mean_ms: float = 200.0, std_ms: float = 100.0):
    """Push n synthetic Cross IBI intervals into oracle."""
    for _ in range(n):
        dur = max(10.0, random.gauss(mean_ms, std_ms))
        oracle._cross_intervals.append(dur)


# ---------------------------------------------------------------------------
# Group 1: Registry
# ---------------------------------------------------------------------------

class TestGameProfileRegistry:

    def test_ncaa_cfb_26_registered(self):
        p = get_profile("ncaa_cfb_26")
        assert p.profile_id == "ncaa_cfb_26"
        assert p.display_name == "NCAA College Football 26"
        assert p.publisher == "EA Sports"

    def test_get_unknown_profile_raises_key_error(self):
        with pytest.raises(KeyError):
            get_profile("no_such_game_xyzzy")

    def test_get_profile_or_none_returns_none(self):
        result = get_profile_or_none("no_such_game_xyzzy")
        assert result is None

    def test_all_profiles_includes_ncaa(self):
        profiles = all_profiles()
        ids = [p.profile_id for p in profiles]
        assert "ncaa_cfb_26" in ids


# ---------------------------------------------------------------------------
# Group 2: NCAA CFB 26 profile correctness
# ---------------------------------------------------------------------------

class TestNcaaCfb26Profile:

    def setup_method(self):
        self.p = get_profile("ncaa_cfb_26")

    def test_r2_is_first_l5_priority(self):
        assert self.p.l5_button_priority[0] == "r2", (
            "R2 (sprint) must be the primary L5 signal for NCAA CFB 26"
        )

    def test_cross_is_second_priority(self):
        assert self.p.l5_button_priority[1] == "cross"

    def test_l6_passive_enabled_on_r2(self):
        assert self.p.l6_passive_enabled is True
        assert self.p.l6_passive_button == "r2"

    def test_l6_passive_flag_ratio_is_1_5(self):
        assert self.p.l6_passive_flag_ratio == 1.5

    def test_button_map_has_sprint_description(self):
        assert "r2" in self.p.button_map
        assert "Sprint" in self.p.button_map["r2"]

    def test_profile_is_ps5(self):
        assert self.p.platform == "ps5"


# ---------------------------------------------------------------------------
# Group 3: L5 priority override in TemporalRhythmOracle
# ---------------------------------------------------------------------------

class TestL5PriorityOverride:

    def test_r2_priority_override_wins_over_cross(self):
        """With R2 first override, R2 scores even when Cross also has samples."""
        oracle = TemporalRhythmOracle(
            button_priority_override=["r2", "cross", "l2_dig", "triangle"]
        )
        # Both buttons have 25 samples -- R2 should win
        _push_r2_intervals(oracle, 25, mean_ms=350.0, std_ms=30.0)
        _push_cross_intervals(oracle, 25, mean_ms=200.0, std_ms=80.0)

        feats = oracle.extract_features()
        assert feats is not None
        assert feats.source == "r2", (
            f"Expected source='r2' with R2-first override, got '{feats.source}'"
        )

    def test_default_priority_cross_wins(self):
        """Default priority: Cross wins when both R2 and Cross have samples."""
        oracle = TemporalRhythmOracle()  # no override
        _push_r2_intervals(oracle, 25, mean_ms=350.0, std_ms=30.0)
        _push_cross_intervals(oracle, 25, mean_ms=200.0, std_ms=80.0)

        feats = oracle.extract_features()
        assert feats is not None
        assert feats.source == "cross", (
            f"Expected source='cross' with default priority, got '{feats.source}'"
        )

    def test_rhythm_hash_unaffected_by_override(self):
        """rhythm_hash() canonical order must be identical regardless of priority override."""
        oracle_default  = TemporalRhythmOracle()
        oracle_override = TemporalRhythmOracle(
            button_priority_override=["r2", "cross", "l2_dig", "triangle"]
        )
        # Push identical intervals into both oracles
        for oracle in (oracle_default, oracle_override):
            _push_r2_intervals(oracle, 10, mean_ms=300.0, std_ms=0.0)
            _push_cross_intervals(oracle, 10, mean_ms=200.0, std_ms=0.0)

        assert oracle_default.rhythm_hash() == oracle_override.rhythm_hash(), (
            "rhythm_hash() must be priority-override-independent (sensor commitment invariant)"
        )
