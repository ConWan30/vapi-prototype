#!/usr/bin/env python3
"""
Generate adversarial sessions by programmatically transforming real DualShock Edge captures.

Six attack types (35 sessions total):
  A: replay         (5)  — identical stream, timestamps +3600 s
  B: injection      (5)  — all IMU zeroed, sticks+triggers from real data
  C: macro          (5)  — constant 50 ms R2 intervals, real analog data
  D: transplant     (5)  — stick+IMU from session X, trigger timing from session Y
  E: warmup        (10)  — linear bot→human interpolation sequence
  F: quant_masked   (5)  — 60 Hz-quantized R2 presses + Gaussian jitter, real IMU

Usage:
    python scripts/generate_adversarial_from_real.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUMAN_DIR    = PROJECT_ROOT / "sessions" / "human"
ADV_DIR      = PROJECT_ROOT / "sessions" / "adversarial"
ADV_DIR.mkdir(parents=True, exist_ok=True)

# Truncate each generated session to this many reports (30 s at 1000 Hz).
REPORTS_PER_SESSION: int = 30_000

# DualSense USB button bit layout (buttons_1 byte, offset 9)
_R2_DIGITAL_BIT   = 3    # bit 3 of buttons_1
_L2_DIGITAL_BIT   = 2    # bit 2 of buttons_1
_PRESS_THRESH     = 64   # analog threshold — pressed
_RELEASE_THRESH   = 30   # analog threshold — released


# ---------------------------------------------------------------------------
# I/O helpers
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


def _base_meta(src: Path, attack_type: str, **kw) -> dict:
    return {
        "device_vid":        "0x054C",
        "device_pid":        "0x0DF2",
        "device_name":       "DualShock Edge CFI-ZCP1 [ADVERSARIAL-REAL]",
        "capture_timestamp": "ADVERSARIAL",
        "source_session":    src.name,
        "generator":         "scripts/generate_adversarial_from_real.py",
        "polling_rate_hz":   1000.0,
        "attack_type":       attack_type,
        **kw,
    }


# ---------------------------------------------------------------------------
# Attack A — Replay
# ---------------------------------------------------------------------------

def gen_replay(src: Path, dst: Path, shift_ms: int = 3_600_000,
               dry_run: bool = False) -> None:
    """Identical report stream replayed 1 hour later (timestamps +3600 s)."""
    session = _load(src)
    reports = [
        {
            "timestamp_ms":      r["timestamp_ms"] + shift_ms,
            "features":          r["features"],
            "sensor_commitment": r.get("sensor_commitment", _commit(r["features"])),
        }
        for r in session["reports"]
    ]
    meta = _base_meta(
        src, "replay",
        timestamp_shift_ms=shift_ms,
        expected_l2_detection=False,
        expected_l4_detection=False,
        expected_l5_detection=False,
        detection_layer="chain",
        note=(
            "Replay attack: exact session stream replayed 1 hour later. "
            "Detected at PoAC chain level (duplicate record hash / timestamp). "
            "PITL L4 should NOT fire (same biometric identity). "
            "L5 should NOT fire (same human press timing). "
            "0% PITL detection is the CORRECT expected result here."
        ),
    )
    _save(dst, meta, reports, dry_run)


# ---------------------------------------------------------------------------
# Attack B — IMU-stripped injection
# ---------------------------------------------------------------------------

def gen_injection(src: Path, dst: Path, dry_run: bool = False) -> None:
    """Zero all IMU fields; keep sticks, triggers, buttons from real data."""
    session = _load(src)
    reports = []
    for r in session["reports"]:
        feats = dict(r["features"])
        feats["gyro_x"] = feats["gyro_y"] = feats["gyro_z"] = 0
        feats["accel_x"] = feats["accel_y"] = feats["accel_z"] = 0
        reports.append({
            "timestamp_ms":      r["timestamp_ms"],
            "features":          feats,
            "sensor_commitment": _commit(feats),
        })
    meta = _base_meta(
        src, "injection",
        imu_zeroed=True,
        expected_l2_detection=True,
        expected_l4_detection=True,
        expected_l5_detection=False,
        note=(
            "Software injection: sticks/triggers replicate real gameplay; "
            "all IMU zeroed — software cannot read hardware IMU. "
            "L2 should fire 0x28 (gyro std=0 with active sticks/triggers). "
            "L4 biometric distance also elevated (zero-tremor fingerprint)."
        ),
    )
    _save(dst, meta, reports, dry_run)


# ---------------------------------------------------------------------------
# Attack C — Perfect-timing macro overlay
# ---------------------------------------------------------------------------

def gen_macro(src: Path, dst: Path,
              interval_reports: int = 50,
              hold_duration: int = 10,
              dry_run: bool = False) -> None:
    """Replace R2 button timing with constant 50 ms (1000 Hz) intervals."""
    session = _load(src)
    reports = []
    for i, r in enumerate(session["reports"]):
        feats = dict(r["features"])
        b1 = int(feats.get("buttons_1") or 0)
        b1 &= ~(1 << _R2_DIGITAL_BIT)            # clear R2 digital
        cycle_pos = i % interval_reports
        in_press  = cycle_pos < hold_duration
        if in_press:
            b1 |= (1 << _R2_DIGITAL_BIT)
        feats["buttons_1"]  = b1
        feats["r2_trigger"] = 220 if in_press else 0
        reports.append({
            "timestamp_ms":      r["timestamp_ms"],
            "features":          feats,
            "sensor_commitment": _commit(feats),
        })
    meta = _base_meta(
        src, "macro",
        press_interval_reports=interval_reports,
        hold_duration_reports=hold_duration,
        expected_l2_detection=False,
        expected_l4_detection=False,
        expected_l5_detection=True,
        note=(
            f"Macro overlay: R2 pressed every {interval_reports} reports (50 ms) "
            f"for {hold_duration} reports, real analog/IMU data kept. "
            "L5 should fire 0x2B: CV=0 (<0.08), entropy=0 bits (<1.0), "
            "quant=1.0 (50 ms = 3x16.67 ms tick, >0.55)."
        ),
    )
    _save(dst, meta, reports, dry_run)


# ---------------------------------------------------------------------------
# Attack D — Biometric transplant
# ---------------------------------------------------------------------------

def gen_transplant(src_imu: Path, src_trg: Path, dst: Path,
                   dry_run: bool = False) -> None:
    """Stick+IMU from src_imu; trigger timing + button bits from src_trg."""
    sess_a = _load(src_imu)
    sess_b = _load(src_trg)
    n = min(len(sess_a["reports"]), len(sess_b["reports"]))
    _TRIGGER_MASK = (1 << _L2_DIGITAL_BIT) | (1 << _R2_DIGITAL_BIT)
    reports = []
    for i in range(n):
        feats = dict(sess_a["reports"][i]["features"])
        fb    = sess_b["reports"][i]["features"]
        # Graft trigger analog values
        feats["l2_trigger"] = int(fb.get("l2_trigger") or 0)
        feats["r2_trigger"] = int(fb.get("r2_trigger") or 0)
        # Graft L2/R2 digital bits from session B; keep all other button bits from A
        b1_a = int(feats.get("buttons_1") or 0)
        b1_b = int(fb.get("buttons_1") or 0)
        feats["buttons_1"] = (b1_a & ~_TRIGGER_MASK) | (b1_b & _TRIGGER_MASK)
        reports.append({
            "timestamp_ms":      sess_a["reports"][i]["timestamp_ms"],
            "features":          feats,
            "sensor_commitment": _commit(feats),
        })
    meta = _base_meta(
        src_imu, "transplant",
        imu_source=src_imu.name,
        trigger_source=src_trg.name,
        expected_l2_detection=False,
        expected_l4_detection=True,
        expected_l5_detection=False,
        note=(
            f"Biometric transplant: stick+IMU from {src_imu.name}, "
            f"trigger timing from {src_trg.name}. "
            "Chimeric fingerprint: tremor/stick from person A, onset velocity "
            "from person B. L4 Mahalanobis distance should exceed threshold when "
            "source sessions are from different biometric populations. "
            "Note: single-person dataset may limit L4 sensitivity here."
        ),
    )
    _save(dst, meta, reports, dry_run)


# ---------------------------------------------------------------------------
# Attack E — Gradual warmup (bot → human interpolation)
# ---------------------------------------------------------------------------

def gen_warmup_session(src: Path, alpha: float, idx: int, dst: Path,
                       dry_run: bool = False) -> None:
    """
    Single warmup session in a 10-session sequence.
    alpha=0.0 → pure bot characteristics; alpha=1.0 → pure human (src data).
    Sessions 0-2 (alpha<0.3): bot-like  — L2, L4, L5 should fire.
    Sessions 3-6 (alpha<0.7): transitional.
    Sessions 7-9 (alpha>=0.7): near-human.
    """
    session = _load(src)
    rng     = random.Random(42 + idx * 17)

    # Bot IMU: near-zero noise around a gravity bias
    BOT_GYRO_STD  = 4.0   # LSB
    BOT_GYRO_BIAS = (8, -4, 2)         # (x, y, z)
    BOT_ACCEL     = (0, 0, 9630)       # pure 1 g downward
    PRESS_INTERVAL = 50                # reports at 1000 Hz
    PRESS_HOLD     = 10

    reports = []
    for i, r in enumerate(session["reports"]):
        fh   = r["features"]
        feats = dict(fh)

        # --- IMU: lerp bot → human ---
        for axis, bias in zip(("gyro_x", "gyro_y", "gyro_z"), BOT_GYRO_BIAS):
            bot_val   = bias + int(rng.gauss(0, BOT_GYRO_STD))
            human_val = int(fh.get(axis) or 0)
            feats[axis] = int(human_val * alpha + bot_val * (1.0 - alpha))
        for axis, bot_val in zip(("accel_x", "accel_y", "accel_z"), BOT_ACCEL):
            human_val  = int(fh.get(axis) or 0)
            feats[axis] = int(human_val * alpha + bot_val * (1.0 - alpha))

        # --- Button timing: constant bot → real human ---
        b1_human   = int(fh.get("buttons_1") or 0)
        cycle_pos  = i % PRESS_INTERVAL
        b1_bot     = (b1_human & ~(1 << _R2_DIGITAL_BIT))
        if cycle_pos < PRESS_HOLD:
            b1_bot |= (1 << _R2_DIGITAL_BIT)

        if alpha < 0.35:
            # Pure bot timing
            feats["buttons_1"]  = b1_bot
            feats["r2_trigger"] = 220 if cycle_pos < PRESS_HOLD else 0
        elif alpha < 0.65:
            # Probabilistic blend: use human bit with probability alpha
            feats["buttons_1"] = b1_human if rng.random() < alpha else b1_bot
        else:
            feats["buttons_1"] = b1_human

        reports.append({
            "timestamp_ms":      r["timestamp_ms"],
            "features":          feats,
            "sensor_commitment": _commit(feats),
        })

    label = ("bot_like" if alpha < 0.3 else
             "transitional" if alpha < 0.7 else "near_human")
    meta = _base_meta(
        src, "warmup",
        warmup_alpha=round(alpha, 3),
        warmup_session_index=idx + 1,
        warmup_total=10,
        warmup_label=label,
        expected_l2_detection=(alpha < 0.30),
        expected_l4_detection=(alpha < 0.50),
        expected_l5_detection=(alpha < 0.35),
        note=(
            f"Warmup session {idx+1}/10 (alpha={alpha:.2f}, label={label}). "
            "Sequence tests BehavioralArchaeologist warmup detection. "
            "Sessions 1-3: bot-like (near-zero IMU variance, constant timing). "
            "Sessions 4-7: transitional (mixed). "
            "Sessions 8-10: near-human (real data dominant)."
        ),
    )
    _save(dst, meta, reports, dry_run)


# ---------------------------------------------------------------------------
# Attack F — Quantization-masked bot
# ---------------------------------------------------------------------------

def gen_quant_masked(src: Path, dst: Path,
                     jitter_sigma: float = 2.0,
                     dry_run: bool = False) -> None:
    """
    60 Hz-locked R2 presses with Gaussian jitter (sigma=2 ms).
    Despite jitter, intervals cluster tightly around 16.67 ms multiples.
    Real IMU from source session is preserved.
    """
    session = _load(src)
    rng     = random.Random(77)
    TICK    = 16.667   # 60 Hz tick in reports (at 1000 Hz polling)
    HOLD    = 8        # press hold duration in reports

    # Pre-build press_active boolean array
    n = len(session["reports"])
    press_active = bytearray(n)   # 0 or 1
    t = TICK + rng.gauss(0, jitter_sigma)
    while True:
        ps = max(0, int(t))
        if ps >= n:
            break
        for j in range(ps, min(ps + HOLD, n)):
            press_active[j] = 1
        t += TICK + rng.gauss(0, jitter_sigma)

    reports = []
    for i, r in enumerate(session["reports"]):
        feats = dict(r["features"])
        b1    = int(feats.get("buttons_1") or 0)
        b1   &= ~(1 << _R2_DIGITAL_BIT)
        if press_active[i]:
            b1              |= (1 << _R2_DIGITAL_BIT)
            feats["r2_trigger"] = 220
        else:
            feats["r2_trigger"] = 0
        feats["buttons_1"] = b1
        reports.append({
            "timestamp_ms":      r["timestamp_ms"],
            "features":          feats,
            "sensor_commitment": _commit(feats),
        })
    meta = _base_meta(
        src, "tick_quantized",
        tick_interval_reports=TICK,
        jitter_sigma_reports=jitter_sigma,
        expected_l2_detection=False,
        expected_l4_detection=False,
        expected_l5_detection=True,
        note=(
            f"Quantization-masked bot: 60 Hz-quantized R2 presses "
            f"(sigma={jitter_sigma} ms jitter). "
            "CV ~0.12 (above 0.08 threshold — CV signal does NOT fire). "
            "Entropy ~0 bits (<1.0 — all intervals in one 50 ms bin — fires). "
            "Quant score >0.84 (>0.55 — fires). "
            "2/3 signals -> L5 0x2B fires despite jitter masking the CV signal."
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
    if len(sessions) < 35:
        print(f"ERROR: Need >= 35 hw_*.json sessions in {HUMAN_DIR}, "
              f"found {len(sessions)}")
        sys.exit(1)

    print(f"Found {len(sessions)} real hardware sessions in {HUMAN_DIR}")
    print(f"Output directory: {ADV_DIR}")
    if args.dry_run:
        print("[dry-run mode — no files will be written]\n")
    print()

    # ------------------------------------------------------------------
    # Attack A: Replay (5 variants — hw_006..hw_010)
    # ------------------------------------------------------------------
    print("=== Attack A: Replay (5 variants) ===")
    for i, src in enumerate(sessions[1:6]):
        gen_replay(src, ADV_DIR / f"replay_{i+1:03d}.json", dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Attack B: IMU-stripped injection (5 variants)
    # ------------------------------------------------------------------
    print("\n=== Attack B: IMU-stripped injection (5 variants) ===")
    b_idx = [5, 10, 14, 19, 24]
    for i, idx in enumerate(b_idx):
        gen_injection(sessions[idx], ADV_DIR / f"injection_{i+1:03d}.json",
                      dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Attack C: Macro overlay (5 variants)
    # ------------------------------------------------------------------
    print("\n=== Attack C: Perfect-timing macro overlay (5 variants) ===")
    c_idx = [10, 15, 19, 24, 28]
    for i, idx in enumerate(c_idx):
        gen_macro(sessions[idx], ADV_DIR / f"macro_{i+1:03d}.json",
                  dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Attack D: Biometric transplant (5 variants)
    # ------------------------------------------------------------------
    print("\n=== Attack D: Biometric transplant (5 variants) ===")
    d_pairs = [
        (sessions[15], sessions[29]),   # hw_020 imu + hw_034 triggers
        (sessions[20], sessions[34]),   # hw_025 imu + hw_039 triggers
        (sessions[25], sessions[38]),   # hw_030 imu + hw_043 triggers
        (sessions[30], sessions[5]),    # hw_035 imu + hw_010 triggers
        (sessions[1],  sessions[19]),   # hw_006 imu + hw_024 triggers
    ]
    for i, (si, st) in enumerate(d_pairs):
        gen_transplant(si, st, ADV_DIR / f"transplant_{i+1:03d}.json",
                       dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Attack E: Gradual warmup (10-session sequence)
    # ------------------------------------------------------------------
    print("\n=== Attack E: Gradual warmup (10-session sequence) ===")
    warmup_src = sessions[20]   # hw_025 as human target
    for i in range(10):
        alpha = i / 9.0
        gen_warmup_session(warmup_src, alpha, i,
                           ADV_DIR / f"warmup_{i+1:03d}.json",
                           dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Attack F: Quantization-masked bot (5 variants)
    # ------------------------------------------------------------------
    print("\n=== Attack F: Quantization-masked bot (5 variants) ===")
    f_idx = [29, 30, 31, 32, 33]
    for i, idx in enumerate(f_idx):
        gen_quant_masked(sessions[idx], ADV_DIR / f"quant_masked_{i+1:03d}.json",
                         dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    if not args.dry_run:
        counts = {
            "replay":        len(list(ADV_DIR.glob("replay_*.json"))),
            "injection":     len(list(ADV_DIR.glob("injection_*.json"))),
            "macro":         len(list(ADV_DIR.glob("macro_*.json"))),
            "transplant":    len(list(ADV_DIR.glob("transplant_*.json"))),
            "warmup":        len(list(ADV_DIR.glob("warmup_*.json"))),
            "tick_quantized":len(list(ADV_DIR.glob("quant_masked_*.json"))),
        }
        print("=== Generation complete ===")
        for k, v in counts.items():
            print(f"  {k}: {v} files")
        total_new = 5 + 5 + 5 + 5 + 10 + 5
        print(f"  New sessions this run: {total_new}")
        print(f"  Total adversarial sessions: "
              f"{len(list(ADV_DIR.glob('*.json')))}")
    else:
        print("=== Dry-run complete (no files written) ===")


if __name__ == "__main__":
    main()
