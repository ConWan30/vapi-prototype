"""
VAPI Phase 8 — Backend Cheat Behavioral Classifier (Layer 3)

IMPORTANT: This model is trained on simulated behavioral data generated from
analytical models of wallhack/aimbot usage patterns. Real-world accuracy
on human adversarial input requires validation with labeled gameplay data.
The heuristic fallback classifier provides interpretable rules while
the neural model is under development.

Design:
    Standalone 3-class behavioral backend cheat detector that complements
    the primary 6-class AntiCheatClassifier in dualshock_emulator.py.

    Run AFTER primary classification; overrides only NOMINAL/SKILLED results
    when a backend cheat behavioral pattern is detected with high confidence.

3 Classes:
    Class 0: CLEAN — no backend cheat signatures (output: no override)
    Class 1: WALLHACK_PREAIM (0x29) — pre-aim behavioral fingerprint
    Class 2: AIMBOT_BEHAVIORAL (0x2A) — lock-on aim behavioral fingerprint

Why without game state:
    Wallhack users physically move stick toward occluded enemies, producing
    smooth tracking velocity that STOPS precisely when the enemy becomes visible
    (aim already on target). This creates a detectable stop-start velocity pattern.

    Aimbot-assisted players show micro-correction patterns: small stick correction
    AFTER a large rapid movement (aimbot snaps, human barely corrects). This
    produces characteristic jerk + micro-velocity tail signatures.

Architecture:
    9 temporal features → 32 → 16 → 3 (softmax)
    INT8 quantized TFLite export for firmware deployment (optional)
    Heuristic fallback is always available without TFLite runtime
"""

import logging
import math
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 8 inference codes (mirrors dualshock_integration.py declarations)
# ---------------------------------------------------------------------------
INFER_NOMINAL            = 0x20
INFER_SKILLED            = 0x21
INFER_WALLHACK_PREAIM    = 0x29  # Phase 8: Behavioral wallhack pre-aim
INFER_AIMBOT_BEHAVIORAL  = 0x2A  # Phase 8: Behavioral aimbot lock-on

# Codes that are safe to override (primary clean results only)
_OVERRIDABLE_CODES = {INFER_NOMINAL, INFER_SKILLED}


# ---------------------------------------------------------------------------
# Temporal Feature Window
# ---------------------------------------------------------------------------
@dataclass
class TemporalFeatureWindow:
    """
    Multi-second behavioral feature window for backend cheat detection.

    Computed from a buffer of FeatureFrame objects spanning approximately
    5 seconds (~37–300 frames at 8 ms each).
    """
    # --- Velocity stop patterns (wallhack indicator) ---
    velocity_stop_count: float     # Count of sudden velocity-to-zero transitions
    velocity_stop_sharpness: float # Avg |dv/dt| at stop transitions (higher = more abrupt)
    tracking_duration_avg: float   # Avg run length of sustained tracking motion (frames)

    # --- Post-snap micro-correction (aimbot indicator) ---
    jerk_micro_tail_ratio: float   # Ratio: micro-velocity frames after high-jerk event
    snap_correction_lag_ms: float  # Time between high-jerk snap and first micro-correction
    aim_settling_variance: float   # Variance of stick velocity magnitude after rapid movement

    # --- Pre-aim pattern (wallhack indicator) ---
    direction_anticipation: float  # Stick movement before a direction-change event

    # --- General temporal context ---
    window_frames: int             # Total frames in this window
    window_duration_ms: float      # Total window duration in milliseconds

    def to_vector(self) -> List[float]:
        """Return ordered feature vector for model input."""
        return [
            self.velocity_stop_count,
            self.velocity_stop_sharpness,
            self.tracking_duration_avg,
            self.jerk_micro_tail_ratio,
            self.snap_correction_lag_ms,
            self.aim_settling_variance,
            self.direction_anticipation,
            float(self.window_frames),
            self.window_duration_ms,
        ]


# ---------------------------------------------------------------------------
# BackendCheatClassifier
# ---------------------------------------------------------------------------
class BackendCheatClassifier:
    """
    3-class behavioral backend cheat detector.

    Complements the primary 6-class AntiCheatClassifier. Run after primary
    classification; overrides only NOMINAL/SKILLED results when a backend
    cheat behavioral pattern is detected with high confidence (>= 180/255).

    Minimum window before classification: WINDOW_MIN_FRAMES = 37 (~3 seconds)
    Classification interval: every CLASSIFY_INTERVAL_FRAMES = 15 (~1.2 seconds)
    """

    WINDOW_MIN_FRAMES       = 37    # ~3 seconds at 8 ms per frame
    CLASSIFY_INTERVAL_FRAMES = 15   # Classify every ~1.2 seconds
    CONFIDENCE_THRESHOLD    = 180   # [0–255] minimum to report a cheat
    FRAME_BUFFER_MAX        = 300   # ~2.5 minutes of history at 120 Hz

    # Heuristic thresholds
    _WALLHACK_STOP_COUNT_MIN  = 3
    _WALLHACK_SHARPNESS_MIN   = 15.0
    _WALLHACK_TRACK_DURATION  = 8.0   # frames
    _AIMBOT_JERK_RATIO_MIN    = 0.6
    _AIMBOT_LAG_MAX_MS        = 50.0
    _AIMBOT_SETTLE_VAR_MAX    = 0.01

    def __init__(self):
        self._frame_buffer: deque = deque(maxlen=self.FRAME_BUFFER_MAX)
        self._frame_count: int = 0
        self._tflite_model = None
        self._tflite_interpreter = None

    def load_model(self, model_path: str) -> bool:
        """
        Attempt to load a TFLite model from disk.

        Args:
            model_path: Path to .tflite file (e.g., backend_cheat_model.tflite)

        Returns:
            True if loaded successfully, False otherwise. Falls back to heuristic.
        """
        if not model_path:
            return False
        try:
            import tflite_runtime.interpreter as tflite  # type: ignore
            self._tflite_interpreter = tflite.Interpreter(model_path=model_path)
            self._tflite_interpreter.allocate_tensors()
            log.info("BackendCheatClassifier: TFLite model loaded from %s", model_path)
            return True
        except ImportError:
            log.debug("tflite_runtime not available — using heuristic fallback")
        except Exception as exc:
            log.warning("Failed to load TFLite model: %s", exc)
        return False

    def push_frame(self, frame) -> None:
        """
        Add a FeatureFrame to the rolling buffer.

        Args:
            frame: FeatureFrame object (from dualshock_emulator.AntiCheatClassifier)
        """
        self._frame_buffer.append(frame)
        self._frame_count += 1

    def extract_temporal_features(self) -> Optional[TemporalFeatureWindow]:
        """
        Compute temporal feature window from the accumulated frame buffer.

        Returns:
            TemporalFeatureWindow if >= WINDOW_MIN_FRAMES accumulated, else None.
        """
        frames = list(self._frame_buffer)
        n = len(frames)
        if n < self.WINDOW_MIN_FRAMES:
            return None

        dt_ms = 8.0  # nominal frame interval

        # Extract per-frame stick velocity magnitudes
        velocities: List[float] = []
        for f in frames:
            vx = getattr(f, "stick_velocity_x", 0.0)
            vy = getattr(f, "stick_velocity_y", 0.0)
            velocities.append(math.sqrt(vx ** 2 + vy ** 2))

        # --- Velocity stop analysis ---
        stop_threshold = 0.02   # velocity below this = "stopped"
        moving_threshold = 0.05  # velocity above this = "tracking"

        stop_count = 0
        stop_sharpness_sum = 0.0
        tracking_runs: List[int] = []
        in_tracking = False
        current_run = 0

        for i in range(1, n):
            dv = abs(velocities[i] - velocities[i - 1])
            # Transition from moving to stopped
            if velocities[i - 1] > moving_threshold and velocities[i] < stop_threshold:
                stop_count += 1
                stop_sharpness_sum += dv / (dt_ms / 1000.0)  # dv/dt in s^-1

            # Track run lengths
            if velocities[i] > moving_threshold:
                if not in_tracking:
                    in_tracking = True
                    current_run = 1
                else:
                    current_run += 1
            else:
                if in_tracking:
                    tracking_runs.append(current_run)
                    in_tracking = False
                    current_run = 0
        if in_tracking and current_run > 0:
            tracking_runs.append(current_run)

        tracking_duration_avg = (
            sum(tracking_runs) / len(tracking_runs) if tracking_runs else 0.0
        )
        velocity_stop_sharpness = (
            stop_sharpness_sum / stop_count if stop_count > 0 else 0.0
        )

        # --- Jerk analysis (aimbot) ---
        jerks: List[float] = []
        for f in frames:
            jerks.append(abs(getattr(f, "jerk_magnitude", 0.0)))

        jerk_threshold = getattr(
            frames[0], "jerk_threshold", 2.0
        ) if frames else 2.0

        high_jerk_count = sum(1 for j in jerks if j > jerk_threshold)
        micro_tail_frames = 0

        for i, j in enumerate(jerks):
            if j > jerk_threshold and i + 1 < n:
                # Look ahead for micro-correction (velocity 0.01–0.05)
                for k in range(i + 1, min(i + 10, n)):
                    if 0.01 < velocities[k] < 0.05:
                        micro_tail_frames += 1

        jerk_micro_tail_ratio = (
            micro_tail_frames / (high_jerk_count * 10)
            if high_jerk_count > 0 else 0.0
        )
        jerk_micro_tail_ratio = min(1.0, jerk_micro_tail_ratio)

        # --- Snap correction lag ---
        snap_lags: List[float] = []
        for i, j in enumerate(jerks):
            if j > jerk_threshold and i + 1 < n:
                for k in range(i + 1, min(i + 20, n)):
                    if 0.005 < velocities[k] < 0.08:
                        snap_lags.append((k - i) * dt_ms)
                        break

        snap_correction_lag_ms = (
            sum(snap_lags) / len(snap_lags) if snap_lags else 999.0
        )

        # --- Aim settling variance (after rapid movement) ---
        post_snap_velocities: List[float] = []
        for i, j in enumerate(jerks):
            if j > jerk_threshold:
                start = i + 3
                end = min(start + 10, n)
                post_snap_velocities.extend(velocities[start:end])

        if post_snap_velocities:
            mean_psv = sum(post_snap_velocities) / len(post_snap_velocities)
            aim_settling_variance = sum(
                (v - mean_psv) ** 2 for v in post_snap_velocities
            ) / len(post_snap_velocities)
        else:
            aim_settling_variance = 1.0  # High variance = no aimbot signal

        # --- Direction anticipation (wallhack pre-aim) ---
        anticipation_sum = 0.0
        for f in frames:
            anticipation_sum += abs(getattr(f, "direction_change_anticipation", 0.0))
        direction_anticipation = anticipation_sum / n

        return TemporalFeatureWindow(
            velocity_stop_count=float(stop_count),
            velocity_stop_sharpness=velocity_stop_sharpness,
            tracking_duration_avg=tracking_duration_avg,
            jerk_micro_tail_ratio=jerk_micro_tail_ratio,
            snap_correction_lag_ms=snap_correction_lag_ms,
            aim_settling_variance=aim_settling_variance,
            direction_anticipation=direction_anticipation,
            window_frames=n,
            window_duration_ms=n * dt_ms,
        )

    def classify_session(self, frames: list) -> Optional[Tuple[int, int]]:
        """
        Run classification over the accumulated frame buffer.

        Pushes all provided frames into the buffer, then extracts temporal
        features and classifies. Uses TFLite model if available, otherwise
        falls back to heuristic rules.

        Args:
            frames: List of FeatureFrame objects from the current session interval.

        Returns:
            (inference_code, confidence) or None if clean/insufficient data.
        """
        for f in frames:
            self.push_frame(f)

        w = self.extract_temporal_features()
        if w is None:
            return None

        # TFLite path
        if self._tflite_interpreter is not None:
            try:
                return self._tflite_classify(w)
            except Exception as exc:
                log.debug("TFLite classify failed, using heuristic: %s", exc)

        # Heuristic fallback
        return self._heuristic_classify(w)

    def _tflite_classify(self, w: TemporalFeatureWindow) -> Optional[Tuple[int, int]]:
        """Run TFLite model inference on temporal feature vector."""
        import numpy as np  # type: ignore

        interp = self._tflite_interpreter
        input_details  = interp.get_input_details()
        output_details = interp.get_output_details()

        features = np.array([w.to_vector()], dtype=np.float32)
        interp.set_tensor(input_details[0]["index"], features)
        interp.invoke()

        output = interp.get_tensor(output_details[0]["index"])[0]
        class_idx = int(np.argmax(output))
        prob = float(output[class_idx])

        if class_idx == 0:
            return None  # CLEAN

        confidence = max(self.CONFIDENCE_THRESHOLD, int(prob * 255))
        confidence = min(255, confidence)

        if confidence < self.CONFIDENCE_THRESHOLD:
            return None

        code = INFER_WALLHACK_PREAIM if class_idx == 1 else INFER_AIMBOT_BEHAVIORAL
        return (code, confidence)

    def _heuristic_classify(
        self, w: TemporalFeatureWindow
    ) -> Optional[Tuple[int, int]]:
        """
        Heuristic rule-based classifier (interpretable fallback).

        WALLHACK_PREAIM:
            velocity_stop_count > 3
            AND velocity_stop_sharpness > 15.0
            AND tracking_duration_avg > 8 frames

        AIMBOT_BEHAVIORAL:
            jerk_micro_tail_ratio > 0.6
            AND snap_correction_lag_ms < 50 ms
            AND aim_settling_variance < 0.01
        """
        # --- Aimbot check first (higher specificity) ---
        if (
            w.jerk_micro_tail_ratio > self._AIMBOT_JERK_RATIO_MIN
            and w.snap_correction_lag_ms < self._AIMBOT_LAG_MAX_MS
            and w.aim_settling_variance < self._AIMBOT_SETTLE_VAR_MAX
        ):
            return (INFER_AIMBOT_BEHAVIORAL, 185)

        # --- Wallhack check ---
        if (
            w.velocity_stop_count > self._WALLHACK_STOP_COUNT_MIN
            and w.velocity_stop_sharpness > self._WALLHACK_SHARPNESS_MIN
            and w.tracking_duration_avg > self._WALLHACK_TRACK_DURATION
        ):
            return (INFER_WALLHACK_PREAIM, 190)

        return None

    def reset(self) -> None:
        """Clear accumulated frame buffer and state."""
        self._frame_buffer.clear()
        self._frame_count = 0


# ---------------------------------------------------------------------------
# Synthetic training data generation
# ---------------------------------------------------------------------------
def generate_training_data(
    n_per_class: int = 3000,
    seed: int = 42,
) -> Tuple[List[List[float]], List[int]]:
    """
    Generate synthetic training data for the 3-class backend model.

    IMPORTANT: All data is synthetically generated from analytical models.
    Real-world validation with labeled gameplay data is required before
    deployment in production attestation contexts.

    Classes:
        0 (CLEAN): Random walk velocity, natural stop variance (Gaussian),
                   no systematic pre-aim patterns.

        1 (WALLHACK_PREAIM): Smooth tracking (low velocity variance) +
                   abrupt stops (high stop sharpness) at regular intervals +
                   pre-aim direction anticipation.

        2 (AIMBOT_BEHAVIORAL): High jerk events followed by micro-correction
                   tails, near-zero settling variance after snaps.

    Returns:
        (features, labels) where features is a list of 9-element vectors
        and labels is a list of class indices.
    """
    import random
    rng = random.Random(seed)

    features: List[List[float]] = []
    labels:   List[int]         = []

    def _gauss(mu: float, sigma: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, rng.gauss(mu, sigma)))

    for _ in range(n_per_class):
        # CLEAN (class 0) — natural human input patterns
        w = TemporalFeatureWindow(
            velocity_stop_count     = _gauss(1.2, 0.8,  0.0, 3.0),
            velocity_stop_sharpness = _gauss(4.0, 2.0,  0.0, 14.9),
            tracking_duration_avg   = _gauss(5.0, 2.0,  1.0, 7.9),
            jerk_micro_tail_ratio   = _gauss(0.2, 0.1,  0.0, 0.59),
            snap_correction_lag_ms  = _gauss(150, 50,   50,  500),
            aim_settling_variance   = _gauss(0.05, 0.02, 0.01, 0.2),
            direction_anticipation  = _gauss(0.01, 0.005, 0.0, 0.05),
            window_frames           = rng.randint(37, 300),
            window_duration_ms      = 0.0,  # not used by model
        )
        w = TemporalFeatureWindow(**{**w.__dict__, "window_duration_ms": w.window_frames * 8.0})
        features.append(w.to_vector())
        labels.append(0)

    for _ in range(n_per_class):
        # WALLHACK_PREAIM (class 1) — stop-start tracking pattern
        stop_count = _gauss(5.0, 1.5, 3.1, 12.0)
        w = TemporalFeatureWindow(
            velocity_stop_count     = stop_count,
            velocity_stop_sharpness = _gauss(20.0, 5.0,  15.1, 60.0),
            tracking_duration_avg   = _gauss(12.0, 3.0,   8.1, 30.0),
            jerk_micro_tail_ratio   = _gauss(0.2,  0.1,   0.0, 0.59),
            snap_correction_lag_ms  = _gauss(200,  60,    50,  500),
            aim_settling_variance   = _gauss(0.04, 0.02,  0.01, 0.15),
            direction_anticipation  = _gauss(0.08, 0.03,  0.05, 0.3),
            window_frames           = rng.randint(37, 300),
            window_duration_ms      = 0.0,
        )
        w = TemporalFeatureWindow(**{**w.__dict__, "window_duration_ms": w.window_frames * 8.0})
        features.append(w.to_vector())
        labels.append(1)

    for _ in range(n_per_class):
        # AIMBOT_BEHAVIORAL (class 2) — snap + micro-correction pattern
        w = TemporalFeatureWindow(
            velocity_stop_count     = _gauss(1.0, 0.5,  0.0, 3.0),
            velocity_stop_sharpness = _gauss(4.0, 2.0,  0.0, 14.0),
            tracking_duration_avg   = _gauss(3.0, 1.0,  1.0, 7.0),
            jerk_micro_tail_ratio   = _gauss(0.75, 0.1,  0.61, 1.0),
            snap_correction_lag_ms  = _gauss(25,   10,   5,   49.9),
            aim_settling_variance   = _gauss(0.004, 0.002, 0.0, 0.0099),
            direction_anticipation  = _gauss(0.02, 0.01, 0.0, 0.05),
            window_frames           = rng.randint(37, 300),
            window_duration_ms      = 0.0,
        )
        w = TemporalFeatureWindow(**{**w.__dict__, "window_duration_ms": w.window_frames * 8.0})
        features.append(w.to_vector())
        labels.append(2)

    # Shuffle
    combined = list(zip(features, labels))
    rng.shuffle(combined)
    features, labels = zip(*combined)
    return list(features), list(labels)


def train_backend_model(save_path: str = "backend_cheat_model.tflite") -> bool:
    """
    Full training pipeline. Generates synthetic data, trains model, exports INT8 TFLite.

    Architecture: 9 features -> Dense(32, relu) -> Dense(16, relu) -> Dense(3, softmax)

    IMPORTANT: Requires TensorFlow. Install with: pip install tensorflow

    Returns:
        True if training and export succeeded, False otherwise.
    """
    try:
        import numpy as np                    # type: ignore
        import tensorflow as tf               # type: ignore
    except ImportError:
        log.error(
            "TensorFlow not available. Install with: pip install tensorflow\n"
            "The heuristic classifier is always available without TensorFlow."
        )
        return False

    log.info("Generating synthetic training data...")
    X_list, y_list = generate_training_data(n_per_class=3000)
    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int32)

    # Normalize features (per-feature min-max)
    X_min = X.min(axis=0)
    X_max = X.max(axis=0)
    X_range = X_max - X_min
    X_range[X_range == 0] = 1.0  # avoid divide-by-zero
    X_norm = (X - X_min) / X_range

    # Train/val split (80/20)
    n = len(X_norm)
    split = int(n * 0.8)
    X_train, X_val = X_norm[:split], X_norm[split:]
    y_train, y_val = y[:split], y[split:]

    # Build model
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(9,)),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(16, activation="relu"),
        tf.keras.layers.Dense(3,  activation="softmax"),
    ])
    model.compile(
        optimizer="adam",
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )

    log.info("Training backend cheat model (synthetic data)...")
    model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=30,
        batch_size=64,
        verbose=1,
    )

    # Convert to INT8 TFLite
    def representative_dataset():
        for i in range(min(500, len(X_train))):
            yield [X_train[i:i+1]]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type  = tf.int8
    converter.inference_output_type = tf.int8

    try:
        tflite_model = converter.convert()
        with open(save_path, "wb") as f:
            f.write(tflite_model)
        log.info(
            "Backend cheat model saved to %s (%d bytes)",
            save_path, len(tflite_model),
        )
        return True
    except Exception as exc:
        log.warning("INT8 quantization failed: %s — saving float32 model instead", exc)
        converter2 = tf.lite.TFLiteConverter.from_keras_model(model)
        tflite_model = converter2.convert()
        with open(save_path, "wb") as f:
            f.write(tflite_model)
        log.info("Float32 TFLite model saved to %s", save_path)
        return True


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="VAPI Phase 8 Backend Cheat Classifier"
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="Train and export the TFLite model",
    )
    parser.add_argument(
        "--output",
        default="backend_cheat_model.tflite",
        help="Output path for TFLite model (default: backend_cheat_model.tflite)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run heuristic classifier on synthetic samples",
    )
    args = parser.parse_args()

    if args.train:
        success = train_backend_model(save_path=args.output)
        sys.exit(0 if success else 1)

    if args.demo:
        clf = BackendCheatClassifier()

        # Simulate synthetic aimbot session frames using a mock FeatureFrame
        class _MockFrame:
            stick_velocity_x = 0.0
            stick_velocity_y = 0.0
            jerk_magnitude = 0.0
            jerk_threshold = 2.0
            direction_change_anticipation = 0.0

        print("\n--- BackendCheatClassifier Demo ---")

        # CLEAN session
        frames = []
        import random
        rng = random.Random(0)
        for _ in range(50):
            f = _MockFrame()
            f.stick_velocity_x = rng.gauss(0, 0.1)
            f.stick_velocity_y = rng.gauss(0, 0.1)
            f.jerk_magnitude = abs(rng.gauss(0.5, 0.3))
            frames.append(f)

        clf.reset()
        result = clf.classify_session(frames)
        print(f"CLEAN session result:  {result} (expected: None)")

        # AIMBOT session — high jerk + micro-correction
        frames_aim = []
        for i in range(50):
            f = _MockFrame()
            if i % 10 == 0:
                f.jerk_magnitude = 3.5   # snap
                f.stick_velocity_x = 0.8
            elif i % 10 in (1, 2):
                f.stick_velocity_x = 0.03  # micro-correction
                f.jerk_magnitude = 0.1
            else:
                f.stick_velocity_x = rng.gauss(0, 0.01)
                f.jerk_magnitude = 0.2
            f.stick_velocity_y = rng.gauss(0, 0.01)
            frames_aim.append(f)

        clf.reset()
        result_aim = clf.classify_session(frames_aim)
        code_name = "AIMBOT_BEHAVIORAL" if result_aim and result_aim[0] == 0x2A else str(result_aim)
        print(f"AIMBOT session result: {result_aim} ({code_name})")
