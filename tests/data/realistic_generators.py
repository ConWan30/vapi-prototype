"""
realistic_generators.py — Synthetic session generators for PITL detection benchmarking.

WARNING: ALL DATA PRODUCED BY THESE GENERATORS IS SYNTHETIC.
Any detection accuracy figures derived from this module must include the caveat
"on synthetic test patterns" at every occurrence. Real-hardware calibration
(via scripts/capture_session.py + scripts/threshold_calibrator.py) is required
before any performance claim can be made about real-world adversarial gameplay.

Generators
----------
generate_human_session(duration_s, skill_level)
    Realistic human input with lognormal reaction times, Bezier stick movement,
    ramp trigger pulls with micro-tremor, Brownian IMU noise, and fatigue drift.

generate_macro_session(duration_s, pattern)
    Perfect timing, zero variance, no IMU correlation — the classic software macro.

generate_aimbot_session(duration_s, aggression)
    Ballistic stick snaps with inhuman jerk, otherwise normal stats.

generate_injection_session(duration_s)
    Perfect stick data, zero IMU noise — the clearest hardware injection signal.

generate_warmup_attack_session(sessions_count)
    Gradually improving humanity_proxy across multiple sessions — slow-ramp evasion.

generate_replay_attack_session(original_session)
    Exact replay with shifted timestamps — anti-replay nullifier bypass simulation.
"""

import hashlib
import math
import random
import time

# ---------------------------------------------------------------------------
# Constants — all magic numbers documented with derivation or calibration note
# ---------------------------------------------------------------------------

# Human reaction time: lognormal(μ=5.7, σ=0.3) → median ~300ms, range 150–800ms
# Source: Donders (1868); modern gaming data: Baayen & Milin (2010)
_REACTION_TIME_MU    = 5.7   # ln(300ms) ≈ 5.7
_REACTION_TIME_SIGMA = 0.3   # ≈ 30% coefficient of variation

# IMU Brownian noise parameters — placeholder, requires real-hardware calibration
# via scripts/threshold_calibrator.py. Current values are conservative estimates.
_IMU_BROWNIAN_SIGMA  = 2.0   # LSB per step (gyro thermal drift)
_IMU_ACCEL_SIGMA     = 5.0   # LSB per step (accelerometer noise)

# Skill-level modifiers — maps human skill tier to timing tightness
# (lower = tighter / more consistent = harder to distinguish from macros)
_SKILL_MODIFIERS = {
    "bronze":   1.5,  # High variance, slow reaction
    "silver":   1.2,
    "gold":     1.0,  # Reference human
    "platinum": 0.85,
    "diamond":  0.7,  # Tight but still human — highest FP risk tier
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lognormal(mu: float, sigma: float, rng: random.Random) -> float:
    """Sample from lognormal distribution."""
    return math.exp(mu + sigma * rng.gauss(0.0, 1.0))


def _sigmoid(x: float) -> float:
    """Logistic sigmoid function."""
    return 1.0 / (1.0 + math.exp(-x))


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _brownian_step(prev: float, sigma: float, lo: float, hi: float, rng: random.Random) -> float:
    return _clamp(prev + rng.gauss(0.0, sigma), lo, hi)


def _make_record(ts_ms: int, lx: float, ly: float, rx: float, ry: float,
                 l2: float, r2: float, gx: float, gy: float, gz: float,
                 ax: float, ay: float, az: float, inter_event_ms: float,
                 humanity_proxy: float, session_type: str) -> dict:
    """Assemble a single synthetic InputSnapshot-like record."""
    return {
        "ts_ms":           ts_ms,
        "left_stick_x":    round(_clamp(lx, 0.0, 255.0)),
        "left_stick_y":    round(_clamp(ly, 0.0, 255.0)),
        "right_stick_x":   round(_clamp(rx, 0.0, 255.0)),
        "right_stick_y":   round(_clamp(ry, 0.0, 255.0)),
        "l2_trigger":      round(_clamp(l2, 0.0, 255.0)),
        "r2_trigger":      round(_clamp(r2, 0.0, 255.0)),
        "gyro_x":          round(gx),
        "gyro_y":          round(gy),
        "gyro_z":          round(gz),
        "accel_x":         round(ax),
        "accel_y":         round(ay),
        "accel_z":         round(az),
        "inter_event_ms":  round(inter_event_ms, 2),
        "humanity_proxy":  round(_clamp(humanity_proxy, 0.0, 1.0), 4),
        "session_type":    session_type,
    }


# ---------------------------------------------------------------------------
# Public generators
# ---------------------------------------------------------------------------

def generate_human_session(duration_s: int = 60, skill_level: str = "gold",
                            seed: int = None) -> dict:
    """
    Generate a realistic human gaming session.

    Parameters
    ----------
    duration_s : int
        Session duration in seconds. Default 60.
    skill_level : str
        One of: bronze, silver, gold, platinum, diamond. Controls timing variance.
        Higher skill = tighter timing, closer to bot-like consistency → higher FP risk.
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    dict
        {session_type, duration_s, skill_level, records, metadata}

    Notes
    -----
    Reaction time: lognormal(μ=5.7, σ=0.3×modifier) → ~300ms median for gold.
    Stick movement: linear interpolation with Gaussian jitter (σ proportional to speed).
    Trigger pull: ramp with grip-force-dependent onset velocity + micro-tremor (3-8 Hz).
    IMU: Brownian motion baseline + movement-correlated acceleration + thermal drift.
    Button timing: exponential inter-press with fatigue drift (increasing variance over time).
    """
    rng = random.Random(seed)
    modifier = _SKILL_MODIFIERS.get(skill_level, 1.0)

    records = []
    ts_ms = 0
    lx, ly = 128.0, 128.0   # Sticks centered (0–255, center 128)
    rx, ry = 128.0, 128.0
    l2, r2 = 0.0, 0.0       # Triggers released
    gx, gy, gz = 0.0, 0.0, 0.0
    ax, ay, az = 0.0, 0.0, 9800.0  # ~1g gravity on Z

    # Fatigue grows as session progresses — increases timing variance over time
    fatigue = 0.0

    end_ms = duration_s * 1000
    while ts_ms < end_ms:
        # Reaction time with fatigue drift
        effective_sigma = _REACTION_TIME_SIGMA * modifier * (1.0 + fatigue * 0.3)
        inter_event_ms = _clamp(_lognormal(_REACTION_TIME_MU, effective_sigma, rng),
                                 50.0, 1200.0)

        # Stick movement: Brownian walk toward a random target with jitter
        lx_target = rng.uniform(60.0, 196.0)
        ly_target = rng.uniform(60.0, 196.0)
        speed_factor = abs(lx - lx_target) / 128.0 + abs(ly - ly_target) / 128.0
        lx = _brownian_step(lx * 0.7 + lx_target * 0.3, 3.0 * speed_factor + 1.0, 0.0, 255.0, rng)
        ly = _brownian_step(ly * 0.7 + ly_target * 0.3, 3.0 * speed_factor + 1.0, 0.0, 255.0, rng)

        rx_target = rng.uniform(60.0, 196.0)
        ry_target = rng.uniform(60.0, 196.0)
        rx = _brownian_step(rx * 0.7 + rx_target * 0.3, 2.0 + 1.0, 0.0, 255.0, rng)
        ry = _brownian_step(ry * 0.7 + ry_target * 0.3, 2.0 + 1.0, 0.0, 255.0, rng)

        # Trigger: ramp up/down with micro-tremor (3-8 Hz physiological range)
        # Micro-tremor amplitude: ~2% of full range = ~5 LSB
        tremor_freq = rng.uniform(3.0, 8.0)
        tremor_amp = 5.0 * modifier  # Skilled players have less tremor
        tremor = tremor_amp * math.sin(2.0 * math.pi * tremor_freq * ts_ms / 1000.0)
        l2 = _clamp(l2 + rng.gauss(0.0, 8.0) + tremor, 0.0, 255.0)
        r2 = _clamp(r2 + rng.gauss(0.0, 8.0) + tremor, 0.0, 255.0)

        # IMU: Brownian + movement-correlated acceleration
        gx = _brownian_step(gx, _IMU_BROWNIAN_SIGMA, -2000.0, 2000.0, rng)
        gy = _brownian_step(gy, _IMU_BROWNIAN_SIGMA, -2000.0, 2000.0, rng)
        gz = _brownian_step(gz, _IMU_BROWNIAN_SIGMA, -2000.0, 2000.0, rng)
        movement = speed_factor * 200.0
        ax = _brownian_step(ax, _IMU_ACCEL_SIGMA + movement, -8000.0, 8000.0, rng)
        ay = _brownian_step(ay, _IMU_ACCEL_SIGMA + movement, -8000.0, 8000.0, rng)
        az = _brownian_step(az, _IMU_ACCEL_SIGMA + movement, 7000.0, 12000.0, rng)

        # Humanity proxy: high for humans (0.7–0.95 depending on skill tier)
        # Lower skill = higher variance → higher humanity score paradoxically
        # because the classifier sees natural human irregularity
        humanity_base = 0.85 - (1.0 - modifier) * 0.1
        humanity_proxy = _clamp(humanity_base + rng.gauss(0.0, 0.05), 0.6, 0.98)

        records.append(_make_record(
            ts_ms, lx, ly, rx, ry, l2, r2, gx, gy, gz, ax, ay, az,
            inter_event_ms, humanity_proxy, "human"
        ))

        ts_ms += int(inter_event_ms)
        # Fatigue accumulation: slow drift over session duration
        fatigue = min(1.0, fatigue + 0.001)

    return {
        "session_type":  "human",
        "duration_s":    duration_s,
        "skill_level":   skill_level,
        "records":       records,
        "metadata": {
            "generator":       "generate_human_session",
            "seed":            seed,
            "record_count":    len(records),
            "note":            "SYNTHETIC — real-hardware calibration required",
        },
    }


def generate_macro_session(duration_s: int = 60, pattern: str = "button_spam",
                            seed: int = None) -> dict:
    """
    Generate a software macro session with perfect timing and zero variance.

    Characteristics:
    - Inter-event timing: constant or perfectly periodic (CV ≈ 0)
    - Stick movement: instantaneous snaps to exact pixel coordinates
    - No IMU correlation: controller doesn't move regardless of input
    - L5 TemporalRhythmOracle will catch this via low CV + low entropy

    Parameters
    ----------
    duration_s : int
        Session duration in seconds.
    pattern : str
        "button_spam" — rapid button presses at fixed 50ms interval
        "stick_rotation" — perfect circular stick motion
    seed : int, optional
        Unused (macros are deterministic by definition).

    Returns
    -------
    dict
        {session_type, duration_s, pattern, records, metadata}
    """
    records = []
    ts_ms = 0
    # Constant inter-event interval — the clearest macro signal
    # L5 CV threshold: < 0.08 is flagged as suspicious
    INTERVAL_MS = 50   # 20Hz constant rate — CV=0.0 (caught immediately by L5)

    end_ms = duration_s * 1000
    angle = 0.0

    while ts_ms < end_ms:
        if pattern == "stick_rotation":
            # Perfect circle — inhuman geometric precision
            lx = 128.0 + 100.0 * math.cos(angle)
            ly = 128.0 + 100.0 * math.sin(angle)
            rx, ry = 128.0, 128.0
            angle += 0.1  # Constant angular velocity
        else:
            # Stationary sticks, periodic trigger/button presses
            lx, ly = 128.0, 128.0
            rx, ry = 128.0, 128.0

        l2 = 255.0 if (ts_ms // INTERVAL_MS) % 2 == 0 else 0.0
        r2 = 0.0

        # Zero IMU — controller doesn't physically move during macro execution
        gx, gy, gz = 0.0, 0.0, 0.0
        ax, ay, az = 0.0, 0.0, 9800.0

        # Humanity proxy: very low (bot-like regularity)
        humanity_proxy = 0.05

        records.append(_make_record(
            ts_ms, lx, ly, rx, ry, l2, r2, gx, gy, gz, ax, ay, az,
            float(INTERVAL_MS), humanity_proxy, "macro"
        ))
        ts_ms += INTERVAL_MS

    return {
        "session_type": "macro",
        "duration_s":   duration_s,
        "pattern":      pattern,
        "records":      records,
        "metadata": {
            "generator":    "generate_macro_session",
            "interval_ms":  INTERVAL_MS,
            "record_count": len(records),
            "note":         "SYNTHETIC — L5 CV < 0.08 will flag this",
        },
    }


def generate_aimbot_session(duration_s: int = 60, aggression: float = 0.8,
                             seed: int = None) -> dict:
    """
    Generate an aimbot session: ballistic stick snaps with inhuman jerk.

    Characteristics:
    - Right stick: instantaneous snaps to target angles (zero transition time)
    - Left stick: normal human movement (aimbot doesn't affect movement)
    - Trigger: perfect timing relative to snap completion
    - IMU: normal human correlation (controller IS being held)
    - L3 behavioral ML will flag via right-stick jerk distribution

    Parameters
    ----------
    duration_s : int
        Session duration in seconds.
    aggression : float
        0.0 = subtle (slow aimbot with slight human-like noise)
        1.0 = maximum (instant perfect snaps, zero variance)
    seed : int, optional
        Random seed.
    """
    rng = random.Random(seed)
    records = []
    ts_ms = 0

    lx, ly = 128.0, 128.0
    gx, gy, gz = 0.0, 0.0, 0.0
    ax, ay, az = 0.0, 0.0, 9800.0

    end_ms = duration_s * 1000

    while ts_ms < end_ms:
        # Left stick: normal human reaction time
        inter_event_ms = _clamp(_lognormal(_REACTION_TIME_MU, _REACTION_TIME_SIGMA, rng),
                                 50.0, 600.0)

        lx = _brownian_step(lx * 0.7 + rng.uniform(80.0, 180.0) * 0.3, 3.0, 0.0, 255.0, rng)
        ly = _brownian_step(ly * 0.7 + rng.uniform(80.0, 180.0) * 0.3, 3.0, 0.0, 255.0, rng)

        # Right stick: aimbot snap — instantaneous jump to target
        # Zero transition noise when aggression=1.0; slight noise at lower aggression
        rx_target = rng.uniform(0.0, 255.0)
        ry_target = rng.uniform(0.0, 255.0)
        noise_scale = (1.0 - aggression) * 10.0
        rx = _clamp(rx_target + rng.gauss(0.0, noise_scale), 0.0, 255.0)
        ry = _clamp(ry_target + rng.gauss(0.0, noise_scale), 0.0, 255.0)

        # Trigger: fires exactly when aim snaps (inhuman synchronization)
        l2 = 0.0
        r2 = 255.0 * aggression  # Perfect pull at full aggression

        # IMU: human-correlated (controller IS in hands, physical movement present)
        gx = _brownian_step(gx, _IMU_BROWNIAN_SIGMA * 2.0, -2000.0, 2000.0, rng)
        gy = _brownian_step(gy, _IMU_BROWNIAN_SIGMA * 2.0, -2000.0, 2000.0, rng)
        gz = _brownian_step(gz, _IMU_BROWNIAN_SIGMA, -2000.0, 2000.0, rng)
        ax = _brownian_step(ax, _IMU_ACCEL_SIGMA * 2.0, -8000.0, 8000.0, rng)
        ay = _brownian_step(ay, _IMU_ACCEL_SIGMA * 2.0, -8000.0, 8000.0, rng)
        az = _brownian_step(az, _IMU_ACCEL_SIGMA, 7000.0, 12000.0, rng)

        # Humanity proxy: moderate — human timing but inhuman aim precision
        humanity_proxy = _clamp(0.45 + (1.0 - aggression) * 0.3 + rng.gauss(0.0, 0.05), 0.1, 0.8)

        records.append(_make_record(
            ts_ms, lx, ly, rx, ry, l2, r2, gx, gy, gz, ax, ay, az,
            inter_event_ms, humanity_proxy, "aimbot"
        ))
        ts_ms += int(inter_event_ms)

    return {
        "session_type": "aimbot",
        "duration_s":   duration_s,
        "aggression":   aggression,
        "records":      records,
        "metadata": {
            "generator":    "generate_aimbot_session",
            "seed":         seed,
            "record_count": len(records),
            "note":         "SYNTHETIC — L3 ML flags via right-stick jerk distribution",
        },
    }


def generate_injection_session(duration_s: int = 60, seed: int = None) -> dict:
    """
    Generate a driver injection session: perfect stick data, zero IMU noise.

    This is the clearest hardware injection signal: a device that produces
    perfect HID reports but has zero IMU correlation. On a real controller
    held by a human, IMU will always show some noise. Zero IMU + perfect sticks
    indicates HID injection bypassing the physical controller entirely.

    L4 BIOMETRIC_ANOMALY (0x30) will flag this via Mahalanobis distance when
    the IMU features are all near-zero while stick features show active play.
    """
    rng = random.Random(seed)
    records = []
    ts_ms = 0
    end_ms = duration_s * 1000

    while ts_ms < end_ms:
        # Injected stick data: smooth, plausible, "human-looking" trajectories
        inter_event_ms = _clamp(_lognormal(_REACTION_TIME_MU, _REACTION_TIME_SIGMA, rng),
                                 80.0, 400.0)
        lx = 128.0 + 60.0 * math.sin(ts_ms / 800.0)
        ly = 128.0 + 60.0 * math.cos(ts_ms / 700.0)
        rx = 128.0 + 80.0 * math.sin(ts_ms / 300.0)
        ry = 128.0 + 80.0 * math.cos(ts_ms / 350.0)
        l2 = _clamp(127.5 + 127.5 * math.sin(ts_ms / 500.0), 0.0, 255.0)
        r2 = 0.0

        # DEAD GIVEAWAY: zero IMU — injected reports don't include real sensor data
        gx, gy, gz = 0.0, 0.0, 0.0
        ax, ay, az = 0.0, 0.0, 0.0  # Even gravity absent — clear injection signal

        humanity_proxy = 0.1  # Very low — IMU mismatch dominates

        records.append(_make_record(
            ts_ms, lx, ly, rx, ry, l2, r2, gx, gy, gz, ax, ay, az,
            inter_event_ms, humanity_proxy, "injection"
        ))
        ts_ms += int(inter_event_ms)

    return {
        "session_type": "injection",
        "duration_s":   duration_s,
        "records":      records,
        "metadata": {
            "generator":    "generate_injection_session",
            "seed":         seed,
            "record_count": len(records),
            "note":         "SYNTHETIC — zero IMU with active sticks = injection signal",
        },
    }


def generate_warmup_attack_session(sessions_count: int = 10, seed: int = None) -> list:
    """
    Generate a multi-session warmup attack: gradually improving humanity_proxy.

    The attacker starts with obvious bot behavior and slowly transitions toward
    human-like behavior to warm up the detection system. BehavioralArchaeologist
    (Phase 26) detects this via correlated positive slopes in drift_trend and
    humanity_trend — normal humans show stable or noisy slopes, not correlated
    upward trends.

    Returns a LIST of sessions, not a single session.

    Parameters
    ----------
    sessions_count : int
        Number of sessions in the attack sequence. Default 10.
    seed : int, optional
        Random seed.
    """
    rng = random.Random(seed)
    sessions = []

    for i in range(sessions_count):
        # Humanity proxy gradually increases from 0.1 (bot-like) to 0.8 (human-like)
        # This is the warmup attack pattern that BehavioralArchaeologist detects
        progress = i / max(1, sessions_count - 1)  # 0.0 → 1.0
        humanity_target = 0.1 + 0.7 * progress

        # Timing variance also improves (lower = more bot-like → higher = more human)
        variance_target = 0.05 + 0.25 * progress

        session_records = []
        ts_ms = 0
        end_ms = 60_000  # 60 seconds per session

        while ts_ms < end_ms:
            # Timing: starts perfectly regular (bot), becomes lognormal (human)
            if variance_target < 0.1:
                inter_event_ms = 50.0 + rng.gauss(0.0, variance_target * 50.0)
            else:
                inter_event_ms = _clamp(
                    _lognormal(_REACTION_TIME_MU, variance_target, rng),
                    50.0, 800.0
                )

            lx = 128.0 + rng.gauss(0.0, 30.0 * progress)
            ly = 128.0 + rng.gauss(0.0, 30.0 * progress)
            rx = 128.0 + rng.gauss(0.0, 30.0 * progress)
            ry = 128.0 + rng.gauss(0.0, 30.0 * progress)
            l2 = max(0.0, rng.gauss(50.0, 50.0 * progress))
            r2 = 0.0

            # IMU: starts dead (injection), gains noise as attack "warms up"
            imu_scale = progress * _IMU_BROWNIAN_SIGMA * 3.0
            gx = rng.gauss(0.0, imu_scale)
            gy = rng.gauss(0.0, imu_scale)
            gz = rng.gauss(0.0, imu_scale)
            ax = rng.gauss(0.0, imu_scale)
            ay = rng.gauss(0.0, imu_scale)
            az = rng.gauss(9800.0, imu_scale + 10.0)

            humanity_proxy = _clamp(
                humanity_target + rng.gauss(0.0, 0.05),
                0.0, 1.0
            )

            session_records.append(_make_record(
                ts_ms, lx, ly, rx, ry, l2, r2, gx, gy, gz, ax, ay, az,
                inter_event_ms, humanity_proxy, "warmup_attack"
            ))
            ts_ms += int(max(10.0, inter_event_ms))

        sessions.append({
            "session_type":    "warmup_attack",
            "session_index":   i,
            "sessions_count":  sessions_count,
            "progress":        round(progress, 3),
            "humanity_target": round(humanity_target, 3),
            "records":         session_records,
            "metadata": {
                "generator":    "generate_warmup_attack_session",
                "seed":         seed,
                "record_count": len(session_records),
                "note":         "SYNTHETIC — BehavioralArchaeologist detects correlated slope",
            },
        })

    return sessions


def generate_replay_attack_session(original_session: dict, time_shift_ms: int = 3600_000,
                                    seed: int = None) -> dict:
    """
    Generate a replay attack: exact copy of a session with shifted timestamps.

    The attacker captures a legitimate gaming session and replays it later.
    The PoAC chain uses nullifier hashes to prevent exact replay (same
    record_hash blocked by used_nullifiers on-chain). This generator shifts
    timestamps to simulate a "near-replay" that bypasses naive timestamp checks.

    The nullifier anti-replay in PITLSessionRegistry.sol is the primary defense.
    The sensor commitment in the PoAC body will differ if the timestamp is
    included in the commitment — this is the key design decision documented here.

    Parameters
    ----------
    original_session : dict
        A session dict as returned by any other generator.
    time_shift_ms : int
        Millisecond offset to shift all timestamps (default 1 hour = 3,600,000ms).
    seed : int, optional
        Unused (replay is deterministic by definition).

    Returns
    -------
    dict
        A new session with identical input data but shifted timestamps.
        session_type will be "replay_attack".
    """
    import copy
    shifted_records = []
    for record in original_session.get("records", []):
        r = copy.copy(record)
        r["ts_ms"] = record["ts_ms"] + time_shift_ms
        r["session_type"] = "replay_attack"
        shifted_records.append(r)

    return {
        "session_type":   "replay_attack",
        "duration_s":     original_session.get("duration_s", 0),
        "original_type":  original_session.get("session_type", "unknown"),
        "time_shift_ms":  time_shift_ms,
        "records":        shifted_records,
        "metadata": {
            "generator":    "generate_replay_attack_session",
            "seed":         seed,
            "record_count": len(shifted_records),
            "note":         "SYNTHETIC — nullifier anti-replay in PITLSessionRegistry blocks this",
        },
    }
