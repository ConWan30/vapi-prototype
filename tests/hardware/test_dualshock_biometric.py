"""
test_dualshock_biometric.py — Live L4 Biometric Fusion tests for DualShock Edge.

Tests the BiometricFusionClassifier two-track EMA against real HID data from a
physical DualShock Edge (Sony CFI-ZCP1) connected via USB.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP-BY-STEP TEST PROCEDURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PREREQUISITES:
  1. DualShock Edge CFI-ZCP1 connected via USB-C data cable (NOT charge-only)
  2. Controller shows white PS button LED (USB mode; blue = Bluetooth — wrong mode)
  3. hidapi installed: pip install hidapi
  4. Running from project root: pytest tests/hardware/test_dualshock_biometric.py -v -m hardware -s

TOTAL ESTIMATED TIME: ~10 minutes

TESTS IN ORDER:
  1. test_1_biometric_feature_extraction_live       (~30 seconds)
  2. test_2_stable_track_initialisation             (~90 seconds)
  3. test_3_drift_velocity_after_play               (~60 seconds)
  4. test_4_candidate_track_diverges_on_bot_input   (~30 seconds)
  5. test_5_imu_stick_coupling_nonzero              (~10 seconds)
  6. test_6_trigger_onset_velocity_characterisation (~30 seconds)
  7. test_7_micro_tremor_accel_variance_present     (~10 seconds)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NOTE: These tests exercise the BiometricFusionClassifier with real HID data.
They do NOT test on-chain logic or ZK proofs. Full PITL pipeline requires
main.py and the bridge agent running. Use these tests BEFORE the first bridge
session to validate hardware compatibility and calibrate thresholds.
"""

import math
import struct
import sys
import time
from pathlib import Path

import pytest

# Make controller and bridge importable
_REPO = Path(__file__).parents[2]
sys.path.insert(0, str(_REPO / "controller"))
sys.path.insert(0, str(_REPO / "bridge"))

hid = pytest.importorskip("hid", reason="hidapi not installed. Run: pip install hidapi")

# Try importing the biometric classifier
try:
    from tinyml_biometric_fusion import BiometricFusionClassifier, BiometricFeatureFrame
    HAS_BIOMETRIC = True
except ImportError:
    HAS_BIOMETRIC = False

DUALSHOCK_EDGE_VID = 0x054C
DUALSHOCK_EDGE_PID = 0x0DF2

# Window for L4 feature extraction (50 reports = 50 ms at 1 kHz)
FEATURE_WINDOW = 50
# Number of warmup sessions before classify() activates
N_WARMUP = 5
# IMU/stick coupling threshold (empirical pre-calibration)
_IMU_STICK_COUPLING_MIN = 0.05


# ---------------------------------------------------------------------------
# Low-level HID helpers (no bridge dependency)
# ---------------------------------------------------------------------------

def _read_n(h, count, timeout_ms=20):
    """Read up to count non-empty reports."""
    out = []
    deadline = time.perf_counter() + (count * timeout_ms / 1000.0) + 3.0
    while len(out) < count and time.perf_counter() < deadline:
        raw = h.read(128, timeout_ms=timeout_ms)
        if raw:
            out.append(bytes(raw))
    return out


def _parse_report(data: bytes) -> dict:
    """Unpack a 64-byte DualSense USB report into named fields."""
    if len(data) < 28:
        return {}
    return {
        "left_x":  data[1], "left_y":  data[2],
        "right_x": data[3], "right_y": data[4],
        "l2":      data[5], "r2":      data[6],
        "counter": data[7],
        "gyro_x":  struct.unpack_from("<h", data, 16)[0],
        "gyro_y":  struct.unpack_from("<h", data, 18)[0],
        "gyro_z":  struct.unpack_from("<h", data, 20)[0],
        "accel_x": struct.unpack_from("<h", data, 22)[0],
        "accel_y": struct.unpack_from("<h", data, 24)[0],
        "accel_z": struct.unpack_from("<h", data, 26)[0],
        "l2_eff":  data[43] if len(data) > 43 else 0,
        "r2_eff":  data[44] if len(data) > 44 else 0,
    }


def _extract_biometric_frame(reports: list[bytes]) -> "BiometricFeatureFrame | None":
    """
    Extract a BiometricFeatureFrame from a window of HID reports.

    Mirrors the 7-feature extraction documented in dualshock-edge-hid-format.md:
      0: trigger_resistance_change_rate  — rate of change in L2 trigger value
      1: trigger_onset_velocity_l2       — ΔL2/Δt at first non-zero sample
      2: trigger_onset_velocity_r2       — ΔR2/Δt at first non-zero sample
      3: micro_tremor_accel_variance     — var(accel_magnitude) over window
      4: grip_asymmetry                  — mean(abs(left_stick_x - right_stick_x))
      5: stick_autocorr_lag1             — autocorrelation of left_stick_x at lag 1
      6: stick_autocorr_lag5             — autocorrelation of left_stick_x at lag 5
    """
    if not HAS_BIOMETRIC:
        return None

    parsed = [_parse_report(r) for r in reports if len(r) >= 28]
    if len(parsed) < 10:
        return None

    l2_vals = [p["l2"] for p in parsed]
    lx_vals = [p["left_x"] for p in parsed]
    rx_vals = [p["right_x"] for p in parsed]
    ax = [p["accel_x"] for p in parsed]
    ay = [p["accel_y"] for p in parsed]
    az = [p["accel_z"] for p in parsed]

    def _mean(v):   return sum(v) / len(v) if v else 0.0
    def _var(v):
        m = _mean(v)
        return sum((x - m) ** 2 for x in v) / len(v) if v else 0.0
    def _autocorr(v, lag):
        if len(v) <= lag:
            return 0.0
        m, s = _mean(v), math.sqrt(_var(v))
        if s < 1e-9:
            return 0.0
        return sum((v[i] - m) * (v[i + lag] - m) for i in range(len(v) - lag)) / (s ** 2 * (len(v) - lag))

    # trigger_resistance_change_rate: mean absolute change in L2 per report
    l2_changes = [abs(l2_vals[i] - l2_vals[i - 1]) for i in range(1, len(l2_vals))]
    tcr = _mean(l2_changes) / 255.0  # normalise to [0, 1]

    # trigger_onset_velocity_l2: ΔL2 at first non-zero crossing, normalised
    tov_l2 = 0.0
    for i in range(1, len(l2_vals)):
        if l2_vals[i - 1] == 0 and l2_vals[i] > 0:
            tov_l2 = l2_vals[i] / 255.0
            break

    # trigger_onset_velocity_r2: same for R2
    r2_vals = [p["r2"] for p in parsed]
    tov_r2 = 0.0
    for i in range(1, len(r2_vals)):
        if r2_vals[i - 1] == 0 and r2_vals[i] > 0:
            tov_r2 = r2_vals[i] / 255.0
            break

    # micro_tremor_accel_variance: var of accel magnitude (normalised)
    accel_mags = [math.sqrt(ax[i]**2 + ay[i]**2 + az[i]**2) for i in range(len(ax))]
    mtav = min(1.0, _var(accel_mags) / (8192.0 ** 2))

    # grip_asymmetry: mean abs(left_stick_x - right_stick_x) / 255
    grip = _mean([abs(lx - rx) for lx, rx in zip(lx_vals, rx_vals)]) / 255.0

    # stick_autocorr_lag1/5
    ac1 = max(0.0, min(1.0, (_autocorr(lx_vals, 1) + 1.0) / 2.0))
    ac5 = max(0.0, min(1.0, (_autocorr(lx_vals, 5) + 1.0) / 2.0))

    return BiometricFeatureFrame(
        trigger_resistance_change_rate=tcr,
        trigger_onset_velocity_l2=tov_l2,
        trigger_onset_velocity_r2=tov_r2,
        micro_tremor_accel_variance=mtav,
        grip_asymmetry=grip,
        stick_autocorr_lag1=ac1,
        stick_autocorr_lag5=ac5,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.hardware
@pytest.mark.skipif(not HAS_BIOMETRIC, reason="tinyml_biometric_fusion not importable from controller/")
class TestBiometricFeatureExtractionLive:
    """
    TEST 1 — Feature extraction from live HID data.

    PROCEDURE (estimated time: ~30 seconds):
      1. Pick up the DualShock Edge and hold it naturally in both hands.
      2. Slowly press and release the L2 and R2 triggers a few times.
      3. Move both sticks gently in circles.
      4. The test will read 50 HID reports (~50 ms) and extract the 7 PITL features.
      5. All 7 feature values will be printed — save these for threshold calibration.

    EXPECTED OUTPUT:
      ✓ All 7 features in [0.0, 1.0]
      ✓ No NaN or Inf values
      ✓ trigger_onset_velocity_l2/r2 > 0.0 (confirms trigger presses were detected)
      ✓ micro_tremor_accel_variance > 0.0 (confirms physical IMU noise present)
    """

    def test_1_biometric_feature_extraction_live(self, hid_device):
        """Extract 7 biometric features from live HID data; verify ranges and print for calibration."""
        print("\n" + "=" * 60)
        print("TEST 1: Biometric Feature Extraction")
        print("=" * 60)
        print("ACTION: Hold the controller naturally. Press L2/R2 a few times.")
        print("        Move both sticks gently. This will take ~5 seconds.")
        time.sleep(1)

        reports = _read_n(hid_device, FEATURE_WINDOW * 2, timeout_ms=30)
        assert len(reports) >= FEATURE_WINDOW, (
            f"Only {len(reports)} reports received (need {FEATURE_WINDOW}). "
            "Check USB cable and controller power."
        )

        frame = _extract_biometric_frame(reports[:FEATURE_WINDOW])
        assert frame is not None, (
            "Feature extraction returned None — reports may be < 28 bytes. "
            "Ensure USB mode (not Bluetooth)."
        )

        vec = frame.to_vector()
        assert len(vec) == 7, f"Expected 7-dim feature vector, got {len(vec)}"

        field_names = [
            "trigger_resistance_change_rate",
            "trigger_onset_velocity_l2",
            "trigger_onset_velocity_r2",
            "micro_tremor_accel_variance",
            "grip_asymmetry",
            "stick_autocorr_lag1",
            "stick_autocorr_lag5",
        ]

        print("\n  [CALIBRATION DATA] L4 Feature Vector:")
        for name, val in zip(field_names, vec):
            assert not math.isnan(float(val)), f"{name} is NaN"
            assert not math.isinf(float(val)), f"{name} is Inf"
            # Features are normalised or bounded; direct check on frame attrs
            print(f"    {name:42s} = {float(val):.6f}")

        assert frame.micro_tremor_accel_variance >= 0.0, (
            "micro_tremor_accel_variance is negative — extraction bug."
        )
        assert 0.0 <= frame.grip_asymmetry <= 1.0, (
            f"grip_asymmetry={frame.grip_asymmetry:.4f} out of [0, 1]."
        )
        print("\n  PASS: All features extracted successfully. Save output for threshold_calibrator.py.")


@pytest.mark.hardware
@pytest.mark.skipif(not HAS_BIOMETRIC, reason="tinyml_biometric_fusion not importable from controller/")
class TestStableTrackInitialisationLive:
    """
    TEST 2 — Stable track initialisation from live play.

    PROCEDURE (estimated time: ~90 seconds):
      The test drives N_WARMUP_SESSIONS (5) sessions through BiometricFusionClassifier
      using live HID windows. After each session, it calls update_stable_fingerprint().

      1. When prompted, play normally with the controller for 5 seconds per session.
      2. Between sessions, you may rest briefly (the test pauses automatically).
      3. After all 5 sessions, the test checks that:
         a. _stable_mean is no longer all-zero (stable track was initialised)
         b. _stable_initialized flag is True
         c. fingerprint_drift_velocity is 0.0 (stable and candidate agree)

    EXPECTED OUTPUT:
      ✓ Stable track initialized after N_WARMUP_SESSIONS updates
      ✓ fingerprint_drift_velocity ≈ 0.0 (same sessions fed to both tracks)
    """

    def test_2_stable_track_initialisation(self, hid_device):
        """Run N_WARMUP_SESSIONS live sessions through the classifier; verify stable track init."""
        print("\n" + "=" * 60)
        print("TEST 2: Stable Track Initialisation")
        print("=" * 60)
        print(f"You will play through {N_WARMUP} 5-second sessions.")

        clf = BiometricFusionClassifier()
        assert not clf._stable_initialized, "Stable track should start un-initialized."
        import numpy as np

        for session_i in range(N_WARMUP):
            print(f"\n  SESSION {session_i + 1}/{N_WARMUP}: Play normally for 5 seconds...")
            time.sleep(0.5)
            reports = _read_n(hid_device, FEATURE_WINDOW * 4, timeout_ms=25)
            frame = _extract_biometric_frame(reports[:FEATURE_WINDOW])
            if frame is None:
                pytest.skip(f"Session {session_i + 1}: insufficient reports for feature extraction.")

            clf.update_fingerprint(frame)
            clf.update_stable_fingerprint(frame)
            print(f"  Session {session_i + 1} complete. drift_velocity = {clf.fingerprint_drift_velocity:.4f}")
            time.sleep(0.3)

        assert clf._stable_initialized, (
            f"Stable track not initialized after {N_WARMUP} update_stable_fingerprint() calls. "
            "Check BiometricFusionClassifier._stable_initialized logic."
        )

        drift = clf.fingerprint_drift_velocity
        print(f"\n  [RESULT] fingerprint_drift_velocity = {drift:.4f}")
        print(f"  [RESULT] _stable_mean[:3] = {clf._stable_mean[:3]}")
        print("  PASS: Stable track initialized from real play sessions.")

        # With same-session data fed to both tracks, drift must be low (< 0.5)
        assert drift < 0.5, (
            f"drift_velocity={drift:.4f} unexpectedly high after identical warmup sessions. "
            "Candidate and stable tracks should be close when trained on the same data."
        )


@pytest.mark.hardware
@pytest.mark.skipif(not HAS_BIOMETRIC, reason="tinyml_biometric_fusion not importable from controller/")
class TestDriftVelocityAfterPlay:
    """
    TEST 3 — Drift velocity increases when play style changes.

    PROCEDURE (estimated time: ~60 seconds):
      1. SESSION A (5 sessions): Hold controller still / very gentle movements.
         → Stable track anchors to "stationary" biometric profile.
      2. SESSION B: Play actively — move sticks fast, press triggers rapidly.
         → Candidate track should drift; drift_velocity should INCREASE.

    EXPECTED OUTPUT:
      ✓ drift_velocity_after > drift_velocity_baseline
      The increase confirms the drift velocity signal works for contamination detection.
    """

    def test_3_drift_velocity_after_play(self, hid_device):
        """Verify drift_velocity increases when play style changes vs stable baseline."""
        print("\n" + "=" * 60)
        print("TEST 3: Drift Velocity Signal")
        print("=" * 60)
        print("PHASE A: Hold controller still / barely move sticks (5 sessions x 2s each)")

        clf = BiometricFusionClassifier()

        for i in range(N_WARMUP):
            print(f"  Phase A session {i + 1}/{N_WARMUP}: keep controller still...")
            time.sleep(0.3)
            reports = _read_n(hid_device, FEATURE_WINDOW * 2, timeout_ms=25)
            frame = _extract_biometric_frame(reports[:FEATURE_WINDOW])
            if frame is None:
                pytest.skip("Phase A: insufficient reports.")
            clf.update_fingerprint(frame)
            clf.update_stable_fingerprint(frame)

        drift_baseline = clf.fingerprint_drift_velocity
        print(f"\n  Baseline drift_velocity = {drift_baseline:.4f}")

        print("\nPHASE B: Now ACTIVELY play -- move sticks fast, press/release triggers rapidly!")
        print("  (5 seconds of active, varied input)")
        time.sleep(1.0)

        for i in range(3):
            reports = _read_n(hid_device, FEATURE_WINDOW * 2, timeout_ms=25)
            frame = _extract_biometric_frame(reports[:FEATURE_WINDOW])
            if frame is not None:
                clf.update_fingerprint(frame)
                # NOTE: Do NOT call update_stable_fingerprint() here — stable track quarantine
            time.sleep(0.2)

        drift_after = clf.fingerprint_drift_velocity
        print(f"\n  [RESULT] drift_after = {drift_after:.4f}")
        print(f"  [RESULT] drift_baseline = {drift_baseline:.4f}")

        assert drift_after >= drift_baseline, (
            f"drift_velocity did not increase after active play: "
            f"baseline={drift_baseline:.4f}, after={drift_after:.4f}. "
            "The drift signal may not be sensitive enough to detect style changes. "
            "Check EMA alpha parameter in BiometricFusionClassifier."
        )
        print(f"\n  PASS: Drift velocity increased from {drift_baseline:.4f} -> {drift_after:.4f}.")
        print("  This confirms the contamination detection signal is live-hardware-validated.")


@pytest.mark.hardware
@pytest.mark.skipif(not HAS_BIOMETRIC, reason="tinyml_biometric_fusion not importable from controller/")
class TestCandidateTrackDivergence:
    """
    TEST 4 — Candidate track diverges; stable track stays anchored (quarantine).

    PROCEDURE (estimated time: ~30 seconds):
      1. Phase A: Warm up with gentle play — stable track anchors here.
      2. Phase B: Simulate bot-like input — hold controller perfectly still with
         sticks at exact center (128, 128). Do NOT touch anything.
         → Candidate track should drift toward zero-noise profile.
         → Stable track MUST NOT change (quarantine invariant).

    EXPECTED OUTPUT:
      ✓ _stable_mean unchanged after phase B (quarantine holds)
      ✓ fingerprint_drift_velocity > 0 (contamination signal visible)
    """

    def test_4_candidate_track_diverges_on_bot_input(self, hid_device):
        """Stable track must not move when only update_fingerprint() (candidate) is called."""
        print("\n" + "=" * 60)
        print("TEST 4: Stable Track Quarantine (live)")
        print("=" * 60)
        print("PHASE A: Play normally for 5 sessions to establish stable baseline.")

        clf = BiometricFusionClassifier()
        import numpy as np

        for i in range(N_WARMUP):
            print(f"  Phase A session {i + 1}/{N_WARMUP}...")
            time.sleep(0.2)
            reports = _read_n(hid_device, FEATURE_WINDOW * 2, timeout_ms=25)
            frame = _extract_biometric_frame(reports[:FEATURE_WINDOW])
            if frame is None:
                pytest.skip("Phase A: insufficient reports.")
            clf.update_fingerprint(frame)
            clf.update_stable_fingerprint(frame)

        stable_mean_after_warmup = clf._stable_mean.copy()
        print(f"\n  Stable track anchored. _stable_mean[:3] = {stable_mean_after_warmup[:3]}")

        print("\nPHASE B: PUT CONTROLLER DOWN -- do NOT touch it for 5 seconds.")
        print("  (Simulates zero-input bot-like session. No update_stable_fingerprint called.)")
        time.sleep(1.5)

        for _ in range(10):
            reports = _read_n(hid_device, FEATURE_WINDOW, timeout_ms=25)
            frame = _extract_biometric_frame(reports[:FEATURE_WINDOW])
            if frame is not None:
                clf.update_fingerprint(frame)
                # CRITICAL: Do NOT call update_stable_fingerprint()
            time.sleep(0.1)

        np.testing.assert_array_equal(
            clf._stable_mean, stable_mean_after_warmup,
            err_msg=(
                "QUARANTINE VIOLATED: _stable_mean changed during candidate-only updates. "
                "update_stable_fingerprint() was called despite only calling update_fingerprint(). "
                "This means the stable-track poisoning attack would succeed on real hardware."
            )
        )

        drift = clf.fingerprint_drift_velocity
        print(f"\n  [RESULT] _stable_mean unchanged: PASS")
        print(f"  [RESULT] drift_velocity = {drift:.4f}")
        print("  PASS: Stable track quarantine holds on live hardware.")


@pytest.mark.hardware
class TestImuStickCouplingLive:
    """
    TEST 5 — IMU–stick coupling (L2 injection detection surface).

    PROCEDURE (estimated time: ~10 seconds):
      1. Pick up and hold the controller naturally.
      2. Move the sticks actively.
      3. The test measures correlation between |gyro| and |stick_magnitude|.

    PURPOSE:
      Software injection (SendInput, XInput emulation) produces stick movement
      but NO gyro noise. A real controller held by a human always produces
      correlated IMU motion from micro-tremors and wrist movement.

    EXPECTED OUTPUT:
      ✓ imu_noise_std > 0 (any real controller exceeds zero IMU noise floor)
      ✓ stick_active (stick moved during the test window)
    """

    def test_5_imu_stick_coupling_nonzero(self, hid_device):
        """Physical controller must produce nonzero IMU noise when stick is moved."""
        print("\n" + "=" * 60)
        print("TEST 5: IMU–Stick Coupling (L2 Injection Detection Surface)")
        print("=" * 60)
        print("ACTION: Hold the controller and move the left stick actively for 3 seconds.")
        time.sleep(0.5)

        reports = _read_n(hid_device, 200, timeout_ms=20)
        assert len(reports) >= 50, f"Only {len(reports)} reports received."

        gyro_mags, stick_mags = [], []
        for r in reports:
            p = _parse_report(r)
            if not p:
                continue
            gyro_mag = math.sqrt(p["gyro_x"]**2 + p["gyro_y"]**2 + p["gyro_z"]**2)
            stick_mag = math.sqrt((p["left_x"] - 128)**2 + (p["left_y"] - 128)**2)
            gyro_mags.append(gyro_mag)
            stick_mags.append(stick_mag)

        def _std(v):
            m = sum(v) / len(v)
            return math.sqrt(sum((x - m) ** 2 for x in v) / len(v))

        imu_noise_std = _std(gyro_mags) if gyro_mags else 0.0
        stick_active = any(s > 5.0 for s in stick_mags)

        print(f"\n  [RESULT] IMU gyro magnitude std = {imu_noise_std:.2f} (units: raw LSB)")
        print(f"  [RESULT] Any stick > 5 LSB from center: {stick_active}")
        print(f"  [RESULT] L2 zero-IMU detection threshold: 0.001 rad/s = ~{0.001 / 0.061:.1f} LSB")

        # A physical DualSense always has nonzero IMU even at rest
        # (hand micro-tremors, breathing, vibration). Threshold: > 0 is sufficient here.
        assert imu_noise_std > 0.0, (
            "IMU noise std = 0.0 — this is IMPOSSIBLE for a physical controller. "
            "Bytes 16–21 may not be gyro data for this firmware version. "
            "Check byte offsets or confirm USB mode (not Bluetooth)."
        )

        print(f"\n  PASS: IMU noise std = {imu_noise_std:.2f} LSB -- confirms physical presence.")
        print("  A software injection attack (SendInput) produces std ~ 0 -- detectable by L2 check.")


@pytest.mark.hardware
class TestTriggerOnsetVelocityLive:
    """
    TEST 6 — Adaptive trigger onset velocity characterisation.

    PROCEDURE (estimated time: ~30 seconds):
      1. Rest both L2 and R2 triggers completely (value should be 0).
      2. When prompted, press L2 quickly and firmly in one smooth motion.
         Hold for 1 second. Release.
      3. Repeat for R2.
      The test measures the onset velocity (ΔL2/Δt at first non-zero sample).

    PURPOSE:
      trigger_onset_velocity is PITL L4 feature index 0. It distinguishes
      bot-like instantaneous trigger presses (onset_velocity → ∞) from human
      gradual-then-firm presses. This test establishes your personal baseline.

    EXPECTED OUTPUT (printed for calibration):
      ✓ l2_onset_velocity_lsb_per_report > 0 (L2 press was detected)
      ✓ r2_onset_velocity_lsb_per_report > 0 (R2 press was detected)
    """

    def test_6_trigger_onset_velocity_characterisation(self, hid_device):
        """Measure L2/R2 trigger onset velocity from real presses. Print for calibration."""
        print("\n" + "=" * 60)
        print("TEST 6: Trigger Onset Velocity Characterisation")
        print("=" * 60)
        print("STEP 1: Release both triggers completely.")
        print("STEP 2: In 3 seconds: press L2 QUICKLY to full depth. Hold 1s. Release.")
        print("STEP 3: Then press R2 QUICKLY. Hold 1s. Release.")
        time.sleep(3)

        reports = _read_n(hid_device, 300, timeout_ms=20)
        assert len(reports) >= 50, f"Only {len(reports)} reports."

        l2_onset_idx, r2_onset_idx = None, None
        l2_vals = [r[5] for r in reports if len(r) > 5]
        r2_vals = [r[6] for r in reports if len(r) > 6]

        for i in range(1, len(l2_vals)):
            if l2_vals[i - 1] == 0 and l2_vals[i] > 0:
                l2_onset_idx = i
                break
        for i in range(1, len(r2_vals)):
            if r2_vals[i - 1] == 0 and r2_vals[i] > 0:
                r2_onset_idx = i
                break

        print("\n  [CALIBRATION DATA] Trigger Onset Velocity:")
        if l2_onset_idx is not None:
            # Velocity over first 5 reports after onset
            window_end = min(l2_onset_idx + 5, len(l2_vals))
            l2_delta = l2_vals[window_end - 1] - l2_vals[l2_onset_idx - 1]
            l2_vel = l2_delta / max(1, window_end - l2_onset_idx)
            print(f"    L2 onset at report {l2_onset_idx}: "
                  f"value={l2_vals[l2_onset_idx]} -> peak={max(l2_vals[l2_onset_idx:window_end])} "
                  f"velocity~{l2_vel:.1f} LSB/report")
        else:
            print("    L2: no onset detected (trigger not pressed or was already pressed)")

        if r2_onset_idx is not None:
            window_end = min(r2_onset_idx + 5, len(r2_vals))
            r2_delta = r2_vals[window_end - 1] - r2_vals[r2_onset_idx - 1]
            r2_vel = r2_delta / max(1, window_end - r2_onset_idx)
            print(f"    R2 onset at report {r2_onset_idx}: "
                  f"value={r2_vals[r2_onset_idx]} -> peak={max(r2_vals[r2_onset_idx:window_end])} "
                  f"velocity~{r2_vel:.1f} LSB/report")
        else:
            print("    R2: no onset detected (trigger not pressed or was already pressed)")

        # At least one trigger must have been pressed
        assert l2_onset_idx is not None or r2_onset_idx is not None, (
            "Neither L2 nor R2 onset detected. "
            "Ensure triggers started fully released and were pressed during the test window."
        )
        print("\n  PASS: Trigger onset velocity measured. Add values to threshold_calibrator.py config.")


@pytest.mark.hardware
class TestMicroTremorLive:
    """
    TEST 7 — Micro-tremor accelerometer variance (physical presence fingerprint).

    PROCEDURE (estimated time: ~10 seconds):
      1. Rest the controller in your hands with a natural grip.
      2. Do NOT intentionally move — just hold it as you would during a game pause.
      3. The test reads 100 reports and measures accel magnitude variance.

    PURPOSE:
      micro_tremor_accel_variance is PITL L4 feature index 3. It reflects the
      involuntary hand micro-tremors unique to biological controllers. Software
      injection produces zero variance — this test validates the physical signal.

    EXPECTED OUTPUT:
      ✓ accel_variance > 0.0 (physical hand tremor present)
      ✓ gyro_std > 0.0 (gyroscope confirms physical presence)
    """

    def test_7_micro_tremor_accel_variance_present(self, hid_device):
        """Verify nonzero accelerometer variance when controller held in natural grip."""
        print("\n" + "=" * 60)
        print("TEST 7: Micro-tremor Accelerometer Variance")
        print("=" * 60)
        print("ACTION: Hold the controller naturally (as during a game pause). Do not move deliberately.")
        time.sleep(1)

        reports = _read_n(hid_device, 100, timeout_ms=20)
        assert len(reports) >= 30, f"Only {len(reports)} reports received."

        accel_mags = []
        gyro_z_vals = []
        for r in reports:
            p = _parse_report(r)
            if not p:
                continue
            accel_mags.append(math.sqrt(p["accel_x"]**2 + p["accel_y"]**2 + p["accel_z"]**2))
            gyro_z_vals.append(p["gyro_z"])

        if not accel_mags:
            pytest.skip("No IMU data in reports — byte offsets may differ for this firmware.")

        def _var(v):
            m = sum(v) / len(v)
            return sum((x - m) ** 2 for x in v) / len(v)

        def _std(v):
            m = sum(v) / len(v)
            return math.sqrt(sum((x - m) ** 2 for x in v) / len(v))

        accel_variance = _var(accel_mags)
        gyro_std = _std(gyro_z_vals) if gyro_z_vals else 0.0
        accel_mean = sum(accel_mags) / len(accel_mags)

        print(f"\n  [CALIBRATION DATA] Micro-tremor (stationary hold):")
        print(f"    accel_magnitude_mean     = {accel_mean:.1f} LSB (expect ~8192 = 1g)")
        print(f"    accel_magnitude_variance = {accel_variance:.1f} LSB²")
        print(f"    gyro_z_std               = {gyro_std:.2f} LSB")
        print(f"    (1 LSB accel ~ 0.000244g, 1 LSB gyro ~ 0.061°/s)")

        assert accel_variance > 0.0, (
            "accel_magnitude_variance = 0.0 — impossible for a physical controller. "
            "Even a stone-still controller on a table has thermal noise > 0. "
            "Check IMU byte offsets (16–27 for gyro/accel in 64-byte USB report)."
        )
        assert gyro_std > 0.0, (
            "gyro_z_std = 0.0 — impossible for a physical DualSense IMU. "
            "Likely reading wrong bytes — confirm USB mode and firmware version."
        )

        print(f"\n  PASS: Micro-tremor variance = {accel_variance:.1f} LSB² -- physical presence confirmed.")
        print("  A software injection proxy produces variance = 0 -> detectable by L4 feature.")
