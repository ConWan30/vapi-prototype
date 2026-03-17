"""Tests for L6bReflexAnalyzer — Phase 63."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from controller.l6b_reflex_analyzer import L6bReflexAnalyzer, L6bReflexResult


def _make_reports(n: int, ax: float = 0.0, ay: float = 0.0, az: float = 0.0) -> list[dict]:
    """Build a list of n identical HID report dicts."""
    return [{"ax": ax, "ay": ay, "az": az} for _ in range(n)]


def _make_impulse_reports(
    quiet_frames: int,
    impulse_mag: float,
    total_frames: int = 350,
    baseline_mag: float = 100.0,
) -> tuple[list[dict], list[dict]]:
    """Build pre + post report lists for a probe with impulse at quiet_frames ms."""
    pre = _make_reports(30, az=baseline_mag)
    post_quiet = _make_reports(quiet_frames, az=baseline_mag)
    post_impulse = _make_reports(
        total_frames - quiet_frames,
        az=baseline_mag + impulse_mag,
    )
    return pre, post_quiet + post_impulse


class TestL6bReflexAnalyzer:
    def setup_method(self):
        self.analyzer = L6bReflexAnalyzer(
            human_min_ms=80.0,
            human_max_ms=280.0,
            accel_delta_threshold_lsb=500.0,
        )

    def test_human_latency_classified_correctly(self):
        """Latency 140ms → HUMAN, p_human = 0.90."""
        pre, post = _make_impulse_reports(quiet_frames=140, impulse_mag=600.0)
        result = self.analyzer.analyze(pre, post, probe_ts=0.0)
        assert result.valid is True
        assert result.latency_ms == pytest.approx(140.0, abs=1.0)
        assert result.classification == "HUMAN"
        p = self.analyzer.classify(result)
        assert p == pytest.approx(0.90)

    def test_bot_latency_classified_correctly(self):
        """Latency 3ms → BOT, p_human = 0.05."""
        pre, post = _make_impulse_reports(quiet_frames=3, impulse_mag=600.0)
        result = self.analyzer.analyze(pre, post, probe_ts=0.0)
        assert result.valid is True
        assert result.latency_ms == pytest.approx(3.0, abs=1.0)
        assert result.classification == "BOT"
        p = self.analyzer.classify(result)
        assert p == pytest.approx(0.05)

    def test_inconclusive_latency_lower_bound(self):
        """Latency 50ms (between BOT_MAX=15 and HUMAN_MIN=80) → INCONCLUSIVE, p=0.5."""
        pre, post = _make_impulse_reports(quiet_frames=50, impulse_mag=600.0)
        result = self.analyzer.analyze(pre, post, probe_ts=0.0)
        assert result.valid is True
        assert result.classification == "INCONCLUSIVE"
        assert self.analyzer.classify(result) == pytest.approx(0.5)

    def test_inconclusive_latency_upper_bound(self):
        """Latency 310ms (above HUMAN_MAX=280) → INCONCLUSIVE, p=0.5."""
        pre, post = _make_impulse_reports(quiet_frames=310, impulse_mag=600.0, total_frames=350)
        result = self.analyzer.analyze(pre, post, probe_ts=0.0)
        assert result.classification == "INCONCLUSIVE"
        assert self.analyzer.classify(result) == pytest.approx(0.5)

    def test_no_response_returns_valid_false(self):
        """post_reports all below threshold → NO_RESPONSE, valid=False, p=0.5."""
        pre = _make_reports(30, az=100.0)
        post = _make_reports(350, az=100.0)  # delta = 0, no impulse
        result = self.analyzer.analyze(pre, post, probe_ts=1.23)
        assert result.valid is False
        assert result.classification == "NO_RESPONSE"
        assert result.latency_ms == -1.0
        assert self.analyzer.classify(result) == pytest.approx(0.5)

    def test_pre_mean_baseline_subtracted(self):
        """Delta is computed against pre_mean, not zero — baseline shift handled correctly."""
        # pre baseline at high mag, post impulse only slightly above
        baseline = 5000.0
        impulse_extra = 600.0
        pre = _make_reports(30, az=baseline)
        # post frames at baseline (no impulse) + one frame at baseline+impulse_extra
        post = _make_reports(99, az=baseline) + [{"ax": 0.0, "ay": 0.0, "az": baseline + impulse_extra}]
        result = self.analyzer.analyze(pre, post, probe_ts=0.0)
        assert result.valid is True
        assert result.latency_ms == pytest.approx(99.0, abs=1.0)
        # delta_peak should be approximately impulse_extra
        assert result.accel_delta_peak == pytest.approx(impulse_extra, rel=0.05)
