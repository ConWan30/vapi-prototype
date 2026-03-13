#!/usr/bin/env python3
"""
Phase 48: Professional/white-box adversarial session generator.

Three new attack classes targeting a threshold-aware adversary who has read the
VAPI whitepaper and knows all published biometric thresholds (N=74 calibration):

  G: randomized_bot   (5) — Gaussian IMU at human variance + real button timing
  H: threshold_aware  (5) — fully synthetic; all thresholds independently tuned
  I: spectral_mimicry (5) — spectral-shaped accel matching human entropy ~4.8 bits

Key finding: Even with human-calibrated marginal variance, the multivariate
Mahalanobis (L4) catches independent noise. You cannot spoof 9 correlated features
by tuning them independently.

Usage:
    python scripts/generate_professional_adversarial.py [--dry-run]

Requires:
    numpy (already a project dependency)

Output (15 new sessions):
    sessions/adversarial/randomized_bot_001-005.json
    sessions/adversarial/threshold_aware_001-005.json
    sessions/adversarial/spectral_mimicry_001-005.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUMAN_DIR    = PROJECT_ROOT / "sessions" / "human"
ADV_DIR      = PROJECT_ROOT / "sessions" / "adversarial"
ADV_DIR.mkdir(parents=True, exist_ok=True)

REPORTS_PER_SESSION: int = 30_000   # 30 s at 1000 Hz

# Human calibration constants (Phase 46, N=74 sessions, DualShock Edge, 2026-03-13)
HUMAN_GYRO_STD         = 333.0    # LSB — used to calibrate Attack G/H IMU noise
HUMAN_ACCEL_TREMOR_STD = 528.0    # LSB — sqrt(micro_tremor_accel_variance=278239)
HUMAN_GRAVITY_LSB      = 9630     # LSB — accel_z at rest (1 g downward on device)

# DualSense button bit layout (buttons_1 byte)
_R2_DIGITAL_BIT  = 3   # bit 3 of buttons_1


# ---------------------------------------------------------------------------
# I/O helpers (mirrors generate_adversarial_from_real.py exactly)
# ---------------------------------------------------------------------------

def _load(path: Path, max_reports: int = REPORTS_PER_SESSION) -> dict:
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    session["reports"] = session["reports"][:max_reports]
    return session


def _save(path: Path, meta: dict, reports: list, dry_run: bool = False) -> None:
    meta["report_count"] = len(reports)
    if dry_run:
        print(f"  [dry-run] would write {path.name} ({len(reports)} reports)")
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"metadata": meta, "reports": reports}, f, separators=(",", ":"))
    print(f"  Saved {path.name} ({len(reports)} reports)")


def _commit(features: dict) -> str:
    return hashlib.sha256(
        json.dumps(features, sort_keys=True).encode()
    ).hexdigest()


def _pro_meta(attack_type: str, src_name: str | None, **kw) -> dict:
    meta: dict = {
        "device_vid":        "0x054C",
        "device_pid":        "0x0DF2",
        "device_name":       "DualShock Edge CFI-ZCP1 [PHASE48-PRO]",
        "capture_timestamp": "ADVERSARIAL",
        "generator":         "scripts/generate_professional_adversarial.py",
        "phase":             48,
        "attack_type":       attack_type,
        "polling_rate_hz":   1000.0,
    }
    if src_name is not None:
        meta["source_session"] = src_name
    meta.update(kw)
    return meta


# ---------------------------------------------------------------------------
# Attack G — Randomized IMU Bot
# ---------------------------------------------------------------------------

def gen_randomized_bot(src: Path, dst: Path,
                       seed: int = 42, dry_run: bool = False) -> None:
    """
    Attack G: Hardware intermediary that injects Gaussian IMU noise at human-calibrated
    variance while replaying a real session's button timing.

    Adversary model: Has read the whitepaper. Knows human gyro_std ≈ 333 LSB and
    micro_tremor_accel_variance ≈ 278,239 LSB². Injects N(0, σ²) per axis.

    Expected detection:
    - L2: PASS (gyro_std >> 20 LSB — passes variance check)
    - L4: FIRE (Mahalanobis catches: independent Gaussian entropy ~8 bits vs human 4.8 bits;
                stick_autocorr unaffected; other correlation features wrong)
    - L5: PASS (button timing from real session → human IBI distribution)
    - L2B: FIRE (random IMU not causally preceded by button presses)
    """
    session = _load(src)
    rng = np.random.default_rng(seed=seed)
    n = len(session["reports"])

    # Pre-generate all IMU noise vectors at once (efficient)
    gyro_noise   = rng.normal(0, HUMAN_GYRO_STD,         size=(n, 3))
    accel_noise  = rng.normal(0, HUMAN_ACCEL_TREMOR_STD, size=(n, 2))
    accel_z_jit  = rng.normal(0, 100.0,                  size=n)

    reports = []
    for i, r in enumerate(session["reports"]):
        feats = dict(r["features"])
        # Replace IMU with calibrated Gaussian noise
        feats["gyro_x"]  = round(float(gyro_noise[i, 0]), 2)
        feats["gyro_y"]  = round(float(gyro_noise[i, 1]), 2)
        feats["gyro_z"]  = round(float(gyro_noise[i, 2]), 2)
        feats["accel_x"] = round(float(accel_noise[i, 0]), 2)
        feats["accel_y"] = round(float(accel_noise[i, 1]), 2)
        feats["accel_z"] = round(float(HUMAN_GRAVITY_LSB + accel_z_jit[i]), 2)
        # Buttons/sticks/triggers untouched — real human timing
        reports.append({
            "timestamp_ms":      r["timestamp_ms"],
            "features":          feats,
            "sensor_commitment": _commit(feats),
        })

    meta = _pro_meta(
        "randomized_bot", src.name,
        seed=seed,
        imu_gyro_std_lsb=HUMAN_GYRO_STD,
        imu_accel_tremor_std_lsb=HUMAN_ACCEL_TREMOR_STD,
        buttons_from_real=True,
        expected_l2_detection=False,
        expected_l4_detection=True,
        expected_l5_detection=False,
        expected_l2b_detection=True,
        note=(
            "Attack G: Hardware intermediary with human-calibrated Gaussian IMU. "
            "gyro_std = 333 LSB (human mean), accel_var = 278239 LSB² (human mean). "
            "Button timing unchanged from real session (human IBI pattern). "
            "L4 FIRES: independent Gaussian accel_magnitude_spectral_entropy ~8 bits "
            "vs human 4.8 bits (>> anomaly threshold). "
            "L2B FIRES: no causal IMU precursor before button presses. "
            "Key finding: human-like marginal variance != human biometric profile."
        ),
    )
    _save(dst, meta, reports, dry_run)


# ---------------------------------------------------------------------------
# Attack H — Threshold-Aware Synthesized Bot
# ---------------------------------------------------------------------------

def gen_threshold_aware(dst: Path,
                        session_idx: int = 0,
                        seed: int = 100,
                        dry_run: bool = False) -> None:
    """
    Attack H: Fully synthetic bot designed by an attacker who has read all VAPI
    thresholds and attempts to pass every individual PITL check.

    Threshold-aware tuning applied:
    - gyro_std = 333 LSB        → above L2 threshold (> 20 LSB) ✓
    - accel_var = 278,239 LSB²  → above L4 feature floor ✓
    - R2 timing: Gamma(k=2,θ=75ms) → CV ≈ 0.71 >> 0.08 L5 threshold ✓
    - R2 entropy: Gamma produces multiple bins → > 1.0 bits ✓

    What the attacker cannot fix:
    - accel_magnitude_spectral_entropy ≈ 8.0 bits (Gaussian → flat spectrum)
      vs human 4.8 bits (>>anomaly threshold at 8.836)
    - stick_autocorr_lag1 ≈ 0 (random walk sticks) vs human ~0.85
    - grip_asymmetry = 0 (only R2 pressed) vs human mean ~0.12
    - L2B fires: no physical coupling between IMU and button presses

    Expected detection:
    - L2: PASS
    - L4: FIRE (entropy + autocorr + grip_asymmetry deviations)
    - L5: PASS (Gamma IBI designed to pass)
    - L2B: FIRE (no causal IMU precursor)
    """
    rng = np.random.default_rng(seed=seed + session_idx * 17)
    N   = REPORTS_PER_SESSION

    # --- Sticks: Gaussian random walk (center ± 1 LSB) ---
    stick_lx = np.clip(
        128 + np.cumsum(rng.integers(-1, 2, size=N)),
        0, 255
    ).astype(int)
    stick_ly = np.clip(
        128 + np.cumsum(rng.integers(-1, 2, size=N)),
        0, 255
    ).astype(int)
    # Right stick: dead zone (center, as in NCAA CFB 26)
    stick_rx = np.full(N, 128, dtype=int)
    stick_ry = np.full(N, 128, dtype=int)

    # --- IMU: Gaussian at human-calibrated variance ---
    gyro_x  = rng.normal(0, HUMAN_GYRO_STD,         N)
    gyro_y  = rng.normal(0, HUMAN_GYRO_STD,         N)
    gyro_z  = rng.normal(0, HUMAN_GYRO_STD,         N)
    accel_x = rng.normal(0, HUMAN_ACCEL_TREMOR_STD, N)
    accel_y = rng.normal(0, HUMAN_ACCEL_TREMOR_STD, N)
    accel_z = rng.normal(HUMAN_GRAVITY_LSB, 100.0,   N)

    # --- R2 timing: Gamma(k=2, θ=75ms) IBIs → CV ≈ 0.71 ---
    # Build press array: cumulative IBI-based press schedule
    press_active = np.zeros(N, dtype=bool)
    t = 0.0
    HOLD = 30  # press hold in reports (30ms)
    while True:
        ibi = float(rng.gamma(shape=2.0, scale=75.0))
        t += ibi
        ps = int(t)
        if ps >= N:
            break
        pe = min(ps + HOLD, N)
        press_active[ps:pe] = True
        # Track press start for IBI measurement

    r2_trigger  = np.where(press_active, 200, 0)
    buttons_1   = np.where(press_active, 1 << _R2_DIGITAL_BIT, 0)

    reports = []
    for i in range(N):
        feats = {
            "report_id":       1,
            "report_length":   27,
            "left_stick_x":    int(stick_lx[i]),
            "left_stick_y":    int(stick_ly[i]),
            "right_stick_x":   int(stick_rx[i]),
            "right_stick_y":   int(stick_ry[i]),
            "l2_trigger":      0,
            "r2_trigger":      int(r2_trigger[i]),
            "gyro_x":          round(float(gyro_x[i]), 2),
            "gyro_y":          round(float(gyro_y[i]), 2),
            "gyro_z":          round(float(gyro_z[i]), 2),
            "accel_x":         round(float(accel_x[i]), 2),
            "accel_y":         round(float(accel_y[i]), 2),
            "accel_z":         round(float(accel_z[i]), 2),
            "buttons_1":       int(buttons_1[i]),
        }
        reports.append({
            "timestamp_ms":      i,
            "features":          feats,
            "sensor_commitment": _commit(feats),
        })

    meta = _pro_meta(
        "threshold_aware", None,
        session_index=session_idx + 1,
        seed=seed + session_idx * 17,
        imu_gyro_std_lsb=HUMAN_GYRO_STD,
        imu_accel_tremor_std_lsb=HUMAN_ACCEL_TREMOR_STD,
        r2_timing_distribution="Gamma(k=2,theta=75ms)",
        r2_timing_cv_expected=0.707,
        press_hold_reports=HOLD,
        expected_l2_detection=False,
        expected_l4_detection=True,
        expected_l5_detection=False,
        expected_l2b_detection=True,
        note=(
            "Attack H: Threshold-aware fully synthetic bot. Adversary has read all "
            "published VAPI thresholds and tuned each signal to pass individually. "
            "Passes: L2 (gyro_std=333 >> 20), L5 (Gamma IBI CV=0.71 >> 0.08). "
            "Fails: L4 fires because (1) accel entropy ~8 bits (Gaussian flat spectrum) "
            "vs human 4.8 bits; (2) stick_autocorr ~0 (random walk) vs human ~0.85; "
            "(3) grip_asymmetry = 0 (R2-only) vs human ~0.12. "
            "L2B fires: no causal IMU-button coupling. "
            "Key finding: Cannot independently spoof 9 correlated L4 features."
        ),
    )
    _save(dst, meta, reports, dry_run)


# ---------------------------------------------------------------------------
# Attack I — Spectral Entropy Mimicry
# ---------------------------------------------------------------------------

def _compute_entropy(mag: np.ndarray) -> float:
    """Shannon entropy of DC-removed magnitude power spectrum (mirrors VAPI live path)."""
    if len(mag) < 1024:
        return 0.0
    if float(np.var(mag)) < 4.0:
        return 0.0
    mag_dc = mag - float(np.mean(mag))
    power  = np.abs(np.fft.rfft(mag_dc)) ** 2
    total  = float(np.sum(power))
    if total < 1e-12:
        return 0.0
    p = power / total
    p = p[p > 1e-12]
    return float(-np.sum(p * np.log2(p)))


def _shape_noise_to_psd(target_psd: np.ndarray, n: int,
                        rng: np.random.Generator) -> np.ndarray:
    """
    Generate noise with PSD matching target_psd (length must be n//2+1 bins).
    Uses spectral shaping in frequency domain: multiply white noise FFT by sqrt(target_psd).
    """
    eps   = rng.standard_normal(n)
    eps_f = np.fft.rfft(eps)
    # Scale: sqrt(target_psd) shapes the amplitude spectrum
    amp_target = np.sqrt(np.maximum(target_psd * n / 2.0, 0.0))
    eps_f_shaped = eps_f * amp_target
    result = np.fft.irfft(eps_f_shaped, n=n)
    return result


def gen_spectral_mimicry(src: Path, dst: Path,
                         seed: int = 200, dry_run: bool = False) -> None:
    """
    Attack I: Advanced attacker targeting accel_magnitude_spectral_entropy (Phase 46).
    Has measured human entropy ~4.8 bits and designs spectrally-shaped noise to match.

    Method: Extract PSD from real session's accel magnitude → shape white noise to
    match that PSD → replace session's accel axes with shaped noise.

    This is the hardest attack to detect via spectral entropy alone because the
    entropy of the shaped noise CAN land in the human range [3.5–6.0 bits].

    However, L4 still FIRES because:
    - Other features (stick_autocorr, tremor_peak_hz, grip_asymmetry) deviate
    - The Mahalanobis captures multivariate correlation structure
    - VAPI's defense is 9-feature multivariate, not single-entropy gating

    Expected detection:
    - L2: PASS
    - L4: FIRE (multivariate — autocorr/tremor features deviate even with matched entropy)
    - L5: PASS (timing from real session)
    - L2B: borderline (gyro kept from real session → some coupling preserved)
    """
    session = _load(src)
    rng     = np.random.default_rng(seed=seed)
    n       = len(session["reports"])

    # Extract real accel magnitude for PSD reference
    ax_r = np.array([float(r["features"].get("accel_x") or 0) for r in session["reports"]])
    ay_r = np.array([float(r["features"].get("accel_y") or 0) for r in session["reports"]])
    az_r = np.array([float(r["features"].get("accel_z") or HUMAN_GRAVITY_LSB) for r in session["reports"]])
    mag_real = np.sqrt(ax_r**2 + ay_r**2 + az_r**2)

    # Compute target PSD from DC-removed real magnitude
    mag_dc_real = mag_real[:REPORTS_PER_SESSION] - float(np.mean(mag_real[:REPORTS_PER_SESSION]))
    power_real  = np.abs(np.fft.rfft(mag_dc_real)) ** 2
    total_real  = float(np.sum(power_real))
    if total_real < 1e-12:
        # Fallback: flat PSD if source has no variance
        n_bins = len(power_real)
        target_psd = np.ones(n_bins) / n_bins
    else:
        target_psd = power_real / total_real

    # Generate spectrally-shaped noise per accel axis
    # Each axis gets an independent realization shaped to the same PSD
    shaped_x = _shape_noise_to_psd(target_psd, n, rng)
    shaped_y = _shape_noise_to_psd(target_psd, n, rng)
    shaped_z = _shape_noise_to_psd(target_psd, n, rng)

    # Scale shaped noise to match real signal variance (~HUMAN_ACCEL_TREMOR_STD)
    for arr in (shaped_x, shaped_y, shaped_z):
        std = float(np.std(arr))
        if std > 1e-10:
            arr *= HUMAN_ACCEL_TREMOR_STD / std

    # Verify entropy of resulting magnitude (for metadata reporting)
    mag_synth = np.sqrt(
        shaped_x[:1024]**2 +
        shaped_y[:1024]**2 +
        (HUMAN_GRAVITY_LSB + shaped_z[:1024])**2
    )
    entropy_check = _compute_entropy(mag_synth)

    reports = []
    for i, r in enumerate(session["reports"]):
        feats = dict(r["features"])
        # Replace accel; keep gyro (maintains some L2B coupling from real session)
        feats["accel_x"] = round(float(shaped_x[i]), 2)
        feats["accel_y"] = round(float(shaped_y[i]), 2)
        feats["accel_z"] = round(float(HUMAN_GRAVITY_LSB + shaped_z[i]), 2)
        reports.append({
            "timestamp_ms":      r["timestamp_ms"],
            "features":          feats,
            "sensor_commitment": _commit(feats),
        })

    meta = _pro_meta(
        "spectral_mimicry", src.name,
        seed=seed,
        psd_source=src.name,
        entropy_first1024_bits=round(entropy_check, 4),
        human_entropy_target_bits=4.8,
        gyro_kept=True,
        buttons_kept=True,
        expected_l2_detection=False,
        expected_l4_detection=True,
        expected_l5_detection=False,
        expected_l2b_detection_borderline=True,
        note=(
            "Attack I: Spectral entropy mimicry targeting accel_magnitude_spectral_entropy "
            "(Phase 46 feature). PSD extracted from real source session; white noise shaped "
            "to match. Resulting entropy on first 1024 frames: "
            f"{entropy_check:.3f} bits (human mean: 4.8 bits, std: 1.303). "
            "Gyro kept from real session (preserves some L2B coupling). "
            "L4 FIRES: even with entropy in range, multivariate Mahalanobis catches "
            "correlation structure mismatch (other 8 features deviate). "
            "Key finding: spectral entropy robustness confirmed at multivariate level."
        ),
    )
    _save(dst, meta, reports, dry_run)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print filenames without writing any files")
    args = parser.parse_args()

    sessions = sorted(HUMAN_DIR.glob("hw_*.json"))
    if len(sessions) < 36:
        print(f"ERROR: Need >= 36 hw_*.json sessions in {HUMAN_DIR}, "
              f"found {len(sessions)}")
        sys.exit(1)

    print(f"Found {len(sessions)} real hardware sessions in {HUMAN_DIR}")
    print(f"Output directory: {ADV_DIR}")
    if args.dry_run:
        print("[dry-run mode — no files will be written]\n")
    print()

    # Source session indices for Attacks G and I (5 P1 sessions, known stable)
    # hw_005=0, hw_006=1, ... hw_020=15, hw_025=20, hw_030=25, hw_035=30, hw_040=35
    G_SOURCES = [sessions[15], sessions[20], sessions[25], sessions[30], sessions[35]]

    # ------------------------------------------------------------------
    # Attack G: Randomized IMU Bot (5 variants)
    # ------------------------------------------------------------------
    print("=== Attack G: Randomized IMU Bot (5 variants) ===")
    for i, src in enumerate(G_SOURCES):
        gen_randomized_bot(
            src, ADV_DIR / f"randomized_bot_{i+1:03d}.json",
            seed=42 + i * 7,
            dry_run=args.dry_run,
        )

    # ------------------------------------------------------------------
    # Attack H: Threshold-Aware Synthesized Bot (5 variants)
    # ------------------------------------------------------------------
    print("\n=== Attack H: Threshold-Aware Synthesized Bot (5 variants) ===")
    for i in range(5):
        gen_threshold_aware(
            ADV_DIR / f"threshold_aware_{i+1:03d}.json",
            session_idx=i,
            seed=100,
            dry_run=args.dry_run,
        )

    # ------------------------------------------------------------------
    # Attack I: Spectral Entropy Mimicry (5 variants)
    # ------------------------------------------------------------------
    print("\n=== Attack I: Spectral Entropy Mimicry (5 variants) ===")
    for i, src in enumerate(G_SOURCES):
        gen_spectral_mimicry(
            src, ADV_DIR / f"spectral_mimicry_{i+1:03d}.json",
            seed=200 + i * 11,
            dry_run=args.dry_run,
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    if not args.dry_run:
        counts = {
            "randomized_bot":  len(list(ADV_DIR.glob("randomized_bot_*.json"))),
            "threshold_aware": len(list(ADV_DIR.glob("threshold_aware_*.json"))),
            "spectral_mimicry":len(list(ADV_DIR.glob("spectral_mimicry_*.json"))),
        }
        print("=== Phase 48 generation complete ===")
        for k, v in counts.items():
            print(f"  {k}: {v} files")
        print(f"  New professional sessions this run: {sum(counts.values())}")
        print(f"  Total adversarial sessions: {len(list(ADV_DIR.glob('*.json')))}")
        print()
        print("Expected detection (run scripts/run_adversarial_validation.py to confirm):")
        print("  randomized_bot:   L2=PASS, L4=FIRE, L5=PASS, L2B=FIRE")
        print("  threshold_aware:  L2=PASS, L4=FIRE, L5=PASS, L2B=FIRE")
        print("  spectral_mimicry: L2=PASS, L4=FIRE, L5=PASS, L2B=borderline")
    else:
        print("=== Dry-run complete (no files written) ===")


if __name__ == "__main__":
    main()
