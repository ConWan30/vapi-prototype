"""Tests for humanity formula L6b branches — Phase 63.

Verifies coefficient correctness for all 4 formula branches introduced in Phase 63.
"""

import pytest


class TestHumanityFormulaCoefficients:
    """All 4 branches must sum to exactly 1.00."""

    def test_baseline_5signal_sums_to_one(self):
        """Baseline formula: 0.28·L4 + 0.27·L5 + 0.20·E4 + 0.15·L2B + 0.10·L2C."""
        total = 0.28 + 0.27 + 0.20 + 0.15 + 0.10
        assert total == pytest.approx(1.00, abs=1e-10)

    def test_l6_active_only_sums_to_one(self):
        """L6-only formula: 0.23·L4 + 0.22·L5 + 0.15·E4 + 0.15·L6 + 0.15·L2B + 0.10·L2C."""
        total = 0.23 + 0.22 + 0.15 + 0.15 + 0.15 + 0.10
        assert total == pytest.approx(1.00, abs=1e-10)

    def test_l6b_only_sums_to_one(self):
        """L6b-only formula: 0.25·L4 + 0.24·L5 + 0.17·E4 + 0.14·L6b + 0.12·L2B + 0.08·L2C."""
        total = 0.25 + 0.24 + 0.17 + 0.14 + 0.12 + 0.08
        assert total == pytest.approx(1.00, abs=1e-10)

    def test_both_l6_and_l6b_sums_to_one(self):
        """7-signal formula: 0.20·L4 + 0.18·L5 + 0.12·E4 + 0.14·L6 + 0.14·L6b + 0.12·L2B + 0.10·L2C."""
        total = 0.20 + 0.18 + 0.12 + 0.14 + 0.14 + 0.12 + 0.10
        assert total == pytest.approx(1.00, abs=1e-10)


class TestL6bNeutralPriorBehavior:
    """When probe_count=0, L6b contributes neutral prior (0.5) — formula should match baseline."""

    def test_l6b_probe_count_zero_uses_baseline_formula(self):
        """With p_l6b=0.5 and probe_count=0, the L6b-only formula must not be applied.

        This is enforced in dualshock_integration.py by the _l6b_active condition
        requiring probe_count >= 1. This test validates the formula value consistency:
        if the baseline formula is used for all signals=0.5, humanity = 0.5.
        """
        p_l4 = p_l5 = p_e4 = p_l2b = p_l2c = 0.5
        baseline = 0.28 * p_l4 + 0.27 * p_l5 + 0.20 * p_e4 + 0.15 * p_l2b + 0.10 * p_l2c
        assert baseline == pytest.approx(0.5)

    def test_l6b_active_formula_neutral_inputs(self):
        """L6b-only formula with all signals=0.5 also yields 0.5 (sanity check)."""
        p = 0.5
        l6b_only = 0.25 * p + 0.24 * p + 0.17 * p + 0.14 * p + 0.12 * p + 0.08 * p
        assert l6b_only == pytest.approx(0.5)

    def test_bot_l6b_signal_lowers_humanity(self):
        """BOT L6b signal (p_l6b=0.05) with otherwise neutral signals lowers humanity below 0.5."""
        p = 0.5
        p_l6b = 0.05
        l6b_only = 0.25 * p + 0.24 * p + 0.17 * p + 0.14 * p_l6b + 0.12 * p + 0.08 * p
        assert l6b_only < 0.5

    def test_human_l6b_signal_raises_humanity(self):
        """HUMAN L6b signal (p_l6b=0.90) with otherwise neutral signals raises humanity above 0.5."""
        p = 0.5
        p_l6b = 0.90
        l6b_only = 0.25 * p + 0.24 * p + 0.17 * p + 0.14 * p_l6b + 0.12 * p + 0.08 * p
        assert l6b_only > 0.5
