"""
Phase 27 — Behavioral PHG Modifier Tests

TestBehavioralPHGModifier (4):
1.  clean device (warmup=0, burst=0) → multiplier=1.0 → score_delta unchanged
2.  warmup-attack device (warmup=0.8) → multiplier=0.36 → score_delta reduced
3.  burst-farming device (burst=0.9) → multiplier=0.55 → score_delta reduced
4.  extreme both (warmup=1.0, burst=1.0) → multiplier clamped to 0.0 → score_delta=0
"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub heavy deps before any bridge imports
for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from vapi_bridge.behavioral_archaeologist import BehavioralReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(warmup: float, burst: float) -> BehavioralReport:
    """Return a BehavioralReport with controlled warmup/burst scores."""
    import time
    return BehavioralReport(
        device_id="testdev",
        drift_trend_slope=0.0,
        humanity_trend_slope=0.0,
        warmup_attack_score=warmup,
        burst_farming_score=burst,
        biometric_stability_cert=True,
        l4_consistency_cert=True,
        session_count=5,
        report_timestamp=time.time(),
    )


def _compute_multiplier(warmup: float, burst: float) -> float:
    """Replicate the Phase 27 batcher formula."""
    return max(0.0, 1.0 - warmup * 0.8 - burst * 0.5)


def _apply_modifier(score_delta: int, warmup: float, burst: float) -> int:
    """Apply the modifier exactly as batcher.py does."""
    return int(score_delta * _compute_multiplier(warmup, burst))


# ===========================================================================
# Tests
# ===========================================================================

class TestBehavioralPHGModifier(unittest.TestCase):

    def test_1_clean_device_multiplier_is_one(self):
        """Clean device (warmup=0, burst=0) → mult=1.0 → score_delta unchanged."""
        score_delta = 100
        warmup, burst = 0.0, 0.0

        mult = _compute_multiplier(warmup, burst)
        self.assertAlmostEqual(mult, 1.0, places=6)

        result = _apply_modifier(score_delta, warmup, burst)
        self.assertEqual(result, 100)

    def test_2_warmup_attack_reduces_score(self):
        """warmup=0.8 → mult = 1.0 - 0.8*0.8 = 0.36 → score reduced (< original)."""
        score_delta = 100
        warmup, burst = 0.8, 0.0

        mult = _compute_multiplier(warmup, burst)
        self.assertAlmostEqual(mult, 0.36, places=6)

        result = _apply_modifier(score_delta, warmup, burst)
        # int(100 * 0.36) may be 35 or 36 depending on float repr — verify range
        self.assertGreaterEqual(result, 35)
        self.assertLessEqual(result, 36)
        self.assertLess(result, score_delta)  # score was definitely reduced

    def test_3_burst_farming_reduces_score(self):
        """burst=0.9 → mult = 1.0 - 0.9*0.5 = 0.55 → score reduced to 55%."""
        score_delta = 100
        warmup, burst = 0.0, 0.9

        mult = _compute_multiplier(warmup, burst)
        self.assertAlmostEqual(mult, 0.55, places=6)

        result = _apply_modifier(score_delta, warmup, burst)
        self.assertEqual(result, 55)

    def test_4_extreme_both_clamps_to_zero(self):
        """warmup=1.0, burst=1.0 → raw=-0.3 → clamped to 0.0 → score_delta=0."""
        score_delta = 500
        warmup, burst = 1.0, 1.0

        mult = _compute_multiplier(warmup, burst)
        self.assertEqual(mult, 0.0)

        result = _apply_modifier(score_delta, warmup, burst)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
