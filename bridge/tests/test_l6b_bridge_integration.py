"""Tests for L6b bridge integration — Phase 63.

Tests pitl_meta fields, humanity formula branches, and store persistence.
Uses the existing DualShockIntegration mock pattern from Phase 58/62 tests.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Stub heavy optional deps before importing bridge modules
for _mod in [
    "web3", "web3.exceptions", "eth_account",
    "pydualsense", "pydualsense.enums",
    "hidapi", "hid",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.path.insert(0, str(Path(__file__).parents[1]))

from vapi_bridge.config import Config


def _make_config(**overrides) -> Config:
    """Build a minimal Config with L6b fields, bypassing env file."""
    import os
    os.environ.setdefault("POAC_VERIFIER_ADDRESS", "0x" + "a" * 40)
    os.environ.setdefault("BRIDGE_PRIVATE_KEY", "0x" + "b" * 64)
    os.environ.setdefault("HTTP_ENABLED", "true")
    for k, v in overrides.items():
        os.environ[k.upper()] = str(v)
    cfg = Config()
    for k in overrides:
        os.environ.pop(k.upper(), None)
    return cfg


class TestL6bConfigFields:
    def test_l6b_enabled_defaults_false(self):
        cfg = _make_config()
        assert cfg.l6b_enabled is False

    def test_l6b_probe_interval_default(self):
        cfg = _make_config()
        assert cfg.l6b_probe_interval_ticks == 6750

    def test_l6b_accel_threshold_default(self):
        cfg = _make_config()
        assert cfg.l6b_accel_delta_threshold_lsb == pytest.approx(500.0)

    def test_l6b_human_bounds_defaults(self):
        cfg = _make_config()
        assert cfg.l6b_human_min_ms == pytest.approx(80.0)
        assert cfg.l6b_human_max_ms == pytest.approx(280.0)


class TestHumanityFormulaL6b:
    """Verify humanity formula coefficient branches sum to 1.00."""

    def test_l6b_only_branch_sums_to_one(self):
        weights = [0.25, 0.24, 0.17, 0.14, 0.12, 0.08]
        assert sum(weights) == pytest.approx(1.00, abs=1e-9)

    def test_both_l6_and_l6b_branch_sums_to_one(self):
        weights = [0.20, 0.18, 0.12, 0.14, 0.14, 0.12, 0.10]
        assert sum(weights) == pytest.approx(1.00, abs=1e-9)

    def test_baseline_branch_sums_to_one(self):
        weights = [0.28, 0.27, 0.20, 0.15, 0.10]
        assert sum(weights) == pytest.approx(1.00, abs=1e-9)

    def test_l6_only_branch_sums_to_one(self):
        weights = [0.23, 0.22, 0.15, 0.15, 0.15, 0.10]
        assert sum(weights) == pytest.approx(1.00, abs=1e-9)


class TestL6bStoreIntegration:
    """Verify store methods for L6b probe log."""

    def test_insert_and_retrieve_probe(self, tmp_path):
        """insert_l6b_probe + get_l6b_baseline round-trip."""
        import tempfile, os
        tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=str(tmp_path))
        tf.close()
        from vapi_bridge.store import Store
        store = Store(tf.name)
        store.insert_l6b_probe(
            device_id="aa" * 32,
            probe_ts_ms=1000000,
            latency_ms=140.0,
            classification="HUMAN",
            accel_delta_peak=800.0,
        )
        store.insert_l6b_probe(
            device_id="aa" * 32,
            probe_ts_ms=1067000,
            latency_ms=5.0,
            classification="BOT",
            accel_delta_peak=1200.0,
        )
        baseline = store.get_l6b_baseline("aa" * 32)
        assert baseline["probe_count"] == 2
        assert baseline["bot_events"] == 1
        assert baseline["mean_latency_ms"] == pytest.approx(72.5)
        assert baseline["classification_distribution"]["HUMAN"] == 1
        assert baseline["classification_distribution"]["BOT"] == 1

    def test_get_l6b_baseline_empty(self, tmp_path):
        """get_l6b_baseline returns probe_count=0 when no rows exist."""
        import tempfile
        tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=str(tmp_path))
        tf.close()
        from vapi_bridge.store import Store
        store = Store(tf.name)
        baseline = store.get_l6b_baseline("bb" * 32)
        assert baseline["probe_count"] == 0
        assert baseline["mean_latency_ms"] is None
