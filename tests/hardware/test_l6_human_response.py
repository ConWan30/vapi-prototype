"""
test_l6_human_response.py — L6 active haptic challenge-response (live hardware baseline).

Goal:
  Produce first real hardware evidence for L6 claim: human response latency is
  physiologically plausible (tens to hundreds of ms), not near-zero.

Notes:
  - Uses pydualsense to WRITE adaptive trigger effect changes.
  - Reads L2 ADC via pydualsense state (no extra HID handle) to avoid Windows
    interface conflicts.
  - Saves raw latency samples to sessions/l6_calibration/.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest


# pydualsense TriggerModes ordinals (mirrors bridge/controller/l6_challenge_profiles.py)
TRIGGER_OFF = 0x00
TRIGGER_RIGID = 0x01
TRIGGER_PULSE = 0x02


@dataclass
class _LatencyResult:
    latency_ms: Optional[float]
    baseline_l2: float
    max_delta: float


def _get_l2_value(ds: object) -> int:
    """Return current L2 ADC (0..255) from pydualsense state."""
    st = getattr(ds, "state", None)
    if st is None:
        return 0
    if hasattr(st, "L2_value"):
        try:
            return int(st.L2_value)
        except Exception:
            pass
    # Fallback: some versions expose L2 as int or bool
    v = getattr(st, "L2", 0)
    if isinstance(v, bool):
        return 255 if v else 0
    try:
        return int(v)
    except Exception:
        return 0


def _apply_l2_profile(ds: object, mode: int, forces: tuple[int, ...]) -> None:
    """Write L2 trigger mode + forces via pydualsense DSTrigger API."""
    # pydualsense expects TriggerModes enum values, not raw ints.
    from pydualsense import TriggerModes  # type: ignore
    modes = list(TriggerModes)
    mode_enum = modes[mode] if 0 <= mode < len(modes) else modes[0]
    tl = getattr(ds, "triggerL", None)
    if tl is None:
        raise RuntimeError("pydualsense object has no triggerL")
    tl.setMode(mode_enum)
    for i, f in enumerate(forces[:7]):
        tl.setForce(i, int(f) & 0xFF)


def _baseline_hold(ds: object, timeout_s: float = 15.0) -> float:
    """Wait until user holds L2 at mid-depth. Return baseline mean L2 value."""
    t0 = time.monotonic()
    samples: list[int] = []
    while time.monotonic() - t0 < timeout_s:
        v = _get_l2_value(ds)
        # Mid-depth gate: "about 50%" (tolerant)
        if 40 <= v <= 220:
            samples.append(v)
            if len(samples) >= 60:
                # ~60 samples over ~0.25–1s depending on polling; good enough
                return float(sum(samples[-60:]) / 60.0)
        else:
            samples.clear()
        time.sleep(0.01)
    raise TimeoutError("Timed out waiting for L2 hold window (40..220).")


def _run_single_challenge(
    ds: object,
    response_window_s: float = 1.5,
    response_delta_lsb: int = 20,
    require_hold: bool = True,
) -> _LatencyResult:
    """
    Send a single trigger effect change and measure time-to-response.

    Response definition:
      abs(L2 - baseline_mean) > response_delta_lsb within response_window_s.
    """
    baseline = 0.0
    if require_hold:
        baseline = _baseline_hold(ds)
    else:
        baseline = float(_get_l2_value(ds))

    # Phase A: light rigid (establish a non-off state)
    _apply_l2_profile(ds, TRIGGER_RIGID, (180,))
    time.sleep(0.05)

    # Challenge: switch to pulse pattern abruptly
    t_sent = time.monotonic()
    _apply_l2_profile(ds, TRIGGER_PULSE, (180, 20, 180, 20, 0, 0, 0))

    max_delta = 0.0
    while time.monotonic() - t_sent < response_window_s:
        v = float(_get_l2_value(ds))
        d = abs(v - baseline)
        max_delta = max(max_delta, d)
        if d > response_delta_lsb:
            return _LatencyResult(latency_ms=(time.monotonic() - t_sent) * 1000.0,
                                  baseline_l2=baseline,
                                  max_delta=max_delta)
        time.sleep(0.005)

    return _LatencyResult(latency_ms=None, baseline_l2=baseline, max_delta=max_delta)


def _save_latencies(latencies_ms: list[float], meta: dict) -> str:
    out_dir = Path("sessions") / "l6_calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d")
    path = out_dir / f"l6_response_{ts}.json"
    payload = {"meta": meta, "latencies_ms": latencies_ms}
    path.write_text(json.dumps(payload, indent=2))
    return str(path)


@pytest.mark.hardware
def test_l6_response_detected_within_window():
    """
    Single challenge: assert at least 1 response is detected within 1.5s.
    """
    pydualsense = pytest.importorskip("pydualsense", reason="pydualsense not installed")
    ds = pydualsense.pydualsense()
    try:
        ds.init()
    except Exception as exc:
        pytest.skip(f"pydualsense init failed: {exc}")

    try:
        print("\n[L6] ACTION: Hold L2 at about 50% depth for ~2 seconds.")
        print("[L6] You will feel a sudden resistance/pulse change. Keep holding and react naturally.")
        # Sanity: ensure we can observe L2 changing at all
        print("[L6] Sanity check: press L2 fully once now (1 second).")
        t0 = time.monotonic()
        mx = 0
        while time.monotonic() - t0 < 1.0:
            mx = max(mx, _get_l2_value(ds))
            time.sleep(0.01)
        if mx < 20:
            pytest.skip("pydualsense did not report L2 changes (L2_value stayed near 0).")
        try:
            res = _run_single_challenge(ds, response_window_s=1.5, response_delta_lsb=20, require_hold=True)
        except TimeoutError:
            pytest.skip("Did not observe sustained L2 hold in the mid-depth window. Hold L2 at ~50% depth for this test.")
        assert res.latency_ms is not None, (
            f"No response detected within window. baseline={res.baseline_l2:.1f} max_delta={res.max_delta:.1f}."
        )
        print(f"[L6] Response latency = {res.latency_ms:.1f} ms (baseline={res.baseline_l2:.1f}, max_delta={res.max_delta:.1f})")
    finally:
        try:
            _apply_l2_profile(ds, TRIGGER_OFF, (0,))
        except Exception:
            pass
        try:
            ds.close()
        except Exception:
            pass


@pytest.mark.hardware
def test_l6_no_false_challenge_without_trigger_press():
    """
    When triggers are released (L2 < 10), challenge write should not produce a
    response detection purely from noise.
    """
    pydualsense = pytest.importorskip("pydualsense", reason="pydualsense not installed")
    ds = pydualsense.pydualsense()
    try:
        ds.init()
    except Exception as exc:
        pytest.skip(f"pydualsense init failed: {exc}")

    try:
        print("\n[L6] ACTION: Fully release L2 (do not touch).")
        time.sleep(0.8)
        v0 = _get_l2_value(ds)
        if v0 >= 10:
            pytest.skip(f"L2 not released (value={v0}). Fully release L2 to run this test.")

        res = _run_single_challenge(ds, response_window_s=1.0, response_delta_lsb=20, require_hold=False)
        assert res.latency_ms is None, (
            f"False response detected at {res.latency_ms:.1f} ms with L2 released. "
            f"baseline={res.baseline_l2:.1f} max_delta={res.max_delta:.1f}."
        )
        print(f"[L6] PASS: No false response with trigger released (max_delta={res.max_delta:.1f} LSB).")
    finally:
        try:
            _apply_l2_profile(ds, TRIGGER_OFF, (0,))
        except Exception:
            pass
        try:
            ds.close()
        except Exception:
            pass


@pytest.mark.hardware
def test_l6_baseline_latency_distribution():
    """
    20 trigger challenges -> collect human response latency distribution.

    Passes if:
      median latency in [50, 800] ms (physiologically plausible for a prompted response).
    Fails if:
      median < 10 ms (indicates measurement is not human response) or > 2000 ms.
    """
    pydualsense = pytest.importorskip("pydualsense", reason="pydualsense not installed")
    ds = pydualsense.pydualsense()
    try:
        ds.init()
    except Exception as exc:
        pytest.skip(f"pydualsense init failed: {exc}")

    latencies: list[float] = []
    try:
        print("\n[L6] Baseline latency distribution (20 challenges).")
        print("[L6] For each trial, hold L2 at about 50% depth until you feel a change; react naturally.")
        print("[L6] Estimated time: ~5 minutes.")
        print("[L6] Sanity check: press L2 fully once now (1 second).")
        t0 = time.monotonic()
        mx = 0
        while time.monotonic() - t0 < 1.0:
            mx = max(mx, _get_l2_value(ds))
            time.sleep(0.01)
        if mx < 20:
            pytest.skip("pydualsense did not report L2 changes (L2_value stayed near 0).")

        for i in range(20):
            print(f"\n[L6] Trial {i + 1}/20: hold L2 at ~50% depth now.")
            try:
                res = _run_single_challenge(ds, response_window_s=1.5, response_delta_lsb=20, require_hold=True)
            except TimeoutError:
                pytest.skip(
                    "Did not observe sustained L2 mid-depth hold. "
                    "This test requires you to hold L2 around mid-depth before each challenge."
                )
            if res.latency_ms is None:
                print(f"[L6]   No response detected (baseline={res.baseline_l2:.1f}, max_delta={res.max_delta:.1f}).")
            else:
                latencies.append(res.latency_ms)
                print(f"[L6]   latency={res.latency_ms:.1f} ms (baseline={res.baseline_l2:.1f}, max_delta={res.max_delta:.1f})")
            # Reset to off briefly between trials
            _apply_l2_profile(ds, TRIGGER_OFF, (0,))
            time.sleep(0.15)

        assert len(latencies) >= 10, f"Only {len(latencies)} responses detected; need >=10 to form a distribution."

        p50 = statistics.median(latencies)
        p10 = sorted(latencies)[max(0, int(0.10 * len(latencies)) - 1)]
        p90 = sorted(latencies)[min(len(latencies) - 1, int(0.90 * len(latencies)))]

        print("\n[L6] Latency distribution (ms):")
        print(f"  n={len(latencies)}  p10={p10:.1f}  p50={p50:.1f}  p90={p90:.1f}")

        meta = {
            "response_window_s": 1.5,
            "response_delta_lsb": 20,
            "n_trials": 20,
            "n_detected": len(latencies),
        }
        saved = _save_latencies(latencies, meta)
        print(f"[L6] Saved latencies to {saved}")

        assert p50 >= 10.0, f"Median latency too low ({p50:.1f} ms) -> likely not measuring human response."
        assert p50 <= 2000.0, f"Median latency too high ({p50:.1f} ms) -> likely no real response captured."
        assert 50.0 <= p50 <= 800.0, (
            f"Median latency {p50:.1f} ms outside expected human range [50,800]. "
            "This may indicate the response definition needs tuning (delta threshold), "
            "or that the trigger effect write did not take effect."
        )
    finally:
        try:
            _apply_l2_profile(ds, TRIGGER_OFF, (0,))
        except Exception:
            pass
        try:
            ds.close()
        except Exception:
            pass

