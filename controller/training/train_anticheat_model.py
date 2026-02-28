#!/usr/bin/env python3
"""
VAPI Anti-Cheat TinyML Model — Training Pipeline
==================================================

Generates synthetic gaming input data matching the 30-feature space from
the DualSense Edge emulator, trains a compact dense neural network for
6-class anti-cheat classification, quantizes to INT8 via TFLite, and
exports as a C header array for firmware inclusion.

Classes (matching firmware inference codes):
  0x20  NOMINAL      — Normal human gameplay
  0x21  SKILLED      — High-skill within human bounds
  0x23  CHEAT:MACRO  — Macro/turbo pattern (press variance < 1ms²)
  0x24  CHEAT:AIMBOT — Ballistic stick snap (jerk > 2.0)
  0x26  CHEAT:IMU_MISS — Stick input without controller motion
  0x27  CHEAT:INJECTION — Fabricated input (no IMU noise)

Input features (12 most discriminative from the 30-feature frame):
  0: press_variance    — Button press timing variance (ms²)
  1: imu_noise         — Gyroscope magnitude (rad/s)
  2: imu_corr          — IMU-stick cross-correlation [0,1]
  3: jerk_r            — Right stick jerk (3rd derivative)
  4: stick_r_vel       — Right stick velocity magnitude
  5: reaction_ms       — Reaction time proxy (ms)
  6: gyro_mag          — Total gyro magnitude
  7: accel_mag         — Total accel magnitude
  8: press_rate        — Button press rate (presses/window)
  9: hold_asymmetry    — Button hold time asymmetry [0,1]
  10: stick_l_vel      — Left stick velocity magnitude
  11: stick_r_acc      — Right stick acceleration

Target: ESP32-S3 (Xtensa LX7) or nRF9160 (Cortex-M33)
  - <20 KB flash for model weights
  - <4 KB RAM for inference arena
  - <5 ms inference time
  - >95% accuracy on synthetic test set

Usage:
    pip install -r requirements.txt
    python train_anticheat_model.py
"""

import os
import struct
import textwrap
import datetime

import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix

# ─── Constants ───────────────────────────────────────────────────────────────

NUM_FEATURES = 12
NUM_CLASSES = 6
SAMPLES_PER_CLASS = 5000
TOTAL_SAMPLES = NUM_CLASSES * SAMPLES_PER_CLASS

CLASS_NAMES = [
    "NOMINAL", "SKILLED", "CHEAT:MACRO",
    "CHEAT:AIMBOT", "CHEAT:IMU_MISS", "CHEAT:INJECTION",
]
CLASS_CODES = [0x20, 0x21, 0x23, 0x24, 0x26, 0x27]

# Feature indices
F_PRESS_VAR = 0
F_IMU_NOISE = 1
F_IMU_CORR = 2
F_JERK_R = 3
F_STICK_R_VEL = 4
F_REACTION_MS = 5
F_GYRO_MAG = 6
F_ACCEL_MAG = 7
F_PRESS_RATE = 8
F_HOLD_ASYM = 9
F_STICK_L_VEL = 10
F_STICK_R_ACC = 11

# Thresholds from firmware (tinyml_anticheat.c / dualshock_emulator.py)
THRESH_MACRO_VAR = 1.0       # ms² — below → macro
THRESH_IMU_NOISE = 0.001     # rad/s — below → injection
THRESH_IMU_CORR = 0.15       # — below + stick move → IMU mismatch
THRESH_AIMBOT_JERK = 2.0     # — above → aimbot snap
THRESH_REACTION = 150.0      # ms — below sustained → inhuman

# ─── Synthetic Data Generation ───────────────────────────────────────────────

def _clip(val, lo, hi):
    return max(lo, min(hi, val))

def generate_nominal(n: int, rng: np.random.Generator) -> np.ndarray:
    """Normal human gameplay — all features in natural ranges."""
    data = np.zeros((n, NUM_FEATURES), dtype=np.float32)
    data[:, F_PRESS_VAR]   = rng.exponential(50.0, n).clip(2.0, 500.0)
    data[:, F_IMU_NOISE]   = rng.normal(0.05, 0.03, n).clip(0.005, 0.3)
    data[:, F_IMU_CORR]    = rng.normal(0.6, 0.15, n).clip(0.2, 1.0)
    data[:, F_JERK_R]      = rng.exponential(0.3, n).clip(0.0, 1.5)
    data[:, F_STICK_R_VEL] = rng.exponential(0.1, n).clip(0.0, 0.8)
    data[:, F_REACTION_MS] = rng.normal(250, 50, n).clip(160, 600)
    data[:, F_GYRO_MAG]    = rng.normal(0.08, 0.04, n).clip(0.01, 0.5)
    data[:, F_ACCEL_MAG]   = rng.normal(1.0, 0.1, n).clip(0.7, 1.5)
    data[:, F_PRESS_RATE]  = rng.poisson(5, n).clip(0, 30).astype(np.float32)
    data[:, F_HOLD_ASYM]   = rng.beta(5, 5, n).clip(0.1, 0.9)
    data[:, F_STICK_L_VEL] = rng.exponential(0.08, n).clip(0.0, 0.6)
    data[:, F_STICK_R_ACC] = rng.normal(0.0, 0.05, n).clip(-0.3, 0.3)
    return data

def generate_skilled(n: int, rng: np.random.Generator) -> np.ndarray:
    """High-skill human — faster reactions, tighter control, but still human."""
    data = np.zeros((n, NUM_FEATURES), dtype=np.float32)
    data[:, F_PRESS_VAR]   = rng.exponential(15.0, n).clip(1.5, 100.0)
    data[:, F_IMU_NOISE]   = rng.normal(0.06, 0.02, n).clip(0.01, 0.2)
    data[:, F_IMU_CORR]    = rng.normal(0.75, 0.1, n).clip(0.4, 1.0)
    data[:, F_JERK_R]      = rng.exponential(0.5, n).clip(0.0, 1.8)
    data[:, F_STICK_R_VEL] = rng.exponential(0.15, n).clip(0.0, 1.0)
    data[:, F_REACTION_MS] = rng.normal(180, 25, n).clip(155, 350)
    data[:, F_GYRO_MAG]    = rng.normal(0.1, 0.04, n).clip(0.02, 0.4)
    data[:, F_ACCEL_MAG]   = rng.normal(1.05, 0.12, n).clip(0.8, 1.6)
    data[:, F_PRESS_RATE]  = rng.poisson(10, n).clip(0, 40).astype(np.float32)
    data[:, F_HOLD_ASYM]   = rng.beta(8, 8, n).clip(0.2, 0.8)
    data[:, F_STICK_L_VEL] = rng.exponential(0.12, n).clip(0.0, 0.8)
    data[:, F_STICK_R_ACC] = rng.normal(0.0, 0.08, n).clip(-0.4, 0.4)
    return data

def generate_macro(n: int, rng: np.random.Generator) -> np.ndarray:
    """Macro/turbo cheat — near-zero press timing variance."""
    data = generate_nominal(n, rng)
    # Key discriminator: impossibly low press variance
    data[:, F_PRESS_VAR]   = rng.exponential(0.15, n).clip(0.0001, 0.8)
    data[:, F_PRESS_RATE]  = rng.poisson(25, n).clip(15, 60).astype(np.float32)
    data[:, F_HOLD_ASYM]   = rng.uniform(0.48, 0.52, n)  # Perfect symmetry
    return data

def generate_aimbot(n: int, rng: np.random.Generator) -> np.ndarray:
    """Aimbot cheat — ballistic stick snaps with extreme jerk."""
    data = generate_skilled(n, rng)
    # Key discriminator: impossible jerk values
    data[:, F_JERK_R]      = rng.exponential(3.0, n).clip(2.1, 20.0)
    data[:, F_STICK_R_VEL] = rng.exponential(0.5, n).clip(0.2, 3.0)
    data[:, F_STICK_R_ACC] = rng.normal(0.0, 0.3, n).clip(-1.0, 1.0)
    data[:, F_REACTION_MS] = rng.normal(100, 20, n).clip(50, 145)
    return data

def generate_imu_miss(n: int, rng: np.random.Generator) -> np.ndarray:
    """IMU mismatch — stick moving but controller stationary (XIM/Cronus)."""
    data = generate_nominal(n, rng)
    # Key discriminator: low IMU-stick correlation + stick has velocity
    data[:, F_IMU_CORR]    = rng.uniform(0.0, 0.12, n)
    data[:, F_IMU_NOISE]   = rng.exponential(0.002, n).clip(0.0005, 0.01)
    data[:, F_GYRO_MAG]    = rng.exponential(0.002, n).clip(0.0001, 0.01)
    data[:, F_STICK_R_VEL] = rng.exponential(0.15, n).clip(0.05, 1.0)
    data[:, F_STICK_L_VEL] = rng.exponential(0.1, n).clip(0.03, 0.8)
    return data

def generate_injection(n: int, rng: np.random.Generator) -> np.ndarray:
    """Input injection — fabricated inputs with no IMU signal whatsoever."""
    data = generate_nominal(n, rng)
    # Key discriminator: zero IMU noise
    data[:, F_IMU_NOISE]   = rng.exponential(0.00005, n).clip(0.0, 0.0008)
    data[:, F_GYRO_MAG]    = rng.exponential(0.00005, n).clip(0.0, 0.0005)
    data[:, F_ACCEL_MAG]   = rng.uniform(0.98, 1.02, n)  # Perfect gravity only
    data[:, F_IMU_CORR]    = rng.uniform(0.0, 0.05, n)   # No correlation
    return data

def generate_dataset(seed: int = 42):
    """Generate full synthetic dataset with labels."""
    rng = np.random.default_rng(seed)
    generators = [
        generate_nominal, generate_skilled, generate_macro,
        generate_aimbot, generate_imu_miss, generate_injection,
    ]
    X_parts, y_parts = [], []
    for cls_idx, gen_fn in enumerate(generators):
        X_parts.append(gen_fn(SAMPLES_PER_CLASS, rng))
        y_parts.append(np.full(SAMPLES_PER_CLASS, cls_idx, dtype=np.int32))

    X = np.vstack(X_parts)
    y = np.concatenate(y_parts)

    # Shuffle
    perm = rng.permutation(len(X))
    return X[perm], y[perm]


# ─── Feature Normalization ───────────────────────────────────────────────────

def compute_normalization(X_train):
    """Compute per-feature mean and std for z-score normalization."""
    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0)
    std[std < 1e-7] = 1.0  # Prevent division by zero
    return mean, std

def normalize(X, mean, std):
    return ((X - mean) / std).astype(np.float32)


# ─── Model Training ─────────────────────────────────────────────────────────

def build_and_train(X_train, y_train, X_val, y_val):
    """Build and train a compact dense neural network."""
    import tensorflow as tf

    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(NUM_FEATURES,)),
        tf.keras.layers.Dense(64, activation='relu'),
        tf.keras.layers.BatchNormalization(),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(32, activation='relu'),
        tf.keras.layers.BatchNormalization(),
        tf.keras.layers.Dropout(0.1),
        tf.keras.layers.Dense(NUM_CLASSES, activation='softmax'),
    ])

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss='sparse_categorical_crossentropy',
        metrics=['accuracy'],
    )

    model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=50,
        batch_size=128,
        verbose=1,
        callbacks=[
            tf.keras.callbacks.EarlyStopping(
                patience=8, restore_best_weights=True, monitor='val_accuracy'
            ),
            tf.keras.callbacks.ReduceLROnPlateau(
                factor=0.5, patience=4, monitor='val_loss'
            ),
        ],
    )
    return model


# ─── TFLite Export ───────────────────────────────────────────────────────────

def export_tflite(model, X_cal, output_path: str):
    """Quantize to INT8 and export as TFLite flatbuffer."""
    import tensorflow as tf

    def representative_dataset():
        for i in range(min(500, len(X_cal))):
            yield [X_cal[i:i+1].astype(np.float32)]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8

    tflite_model = converter.convert()

    with open(output_path, 'wb') as f:
        f.write(tflite_model)

    return tflite_model


def export_c_header(tflite_bytes: bytes, output_path: str):
    """Export TFLite model as a C array header for firmware inclusion."""
    hex_values = ', '.join(f'0x{b:02x}' for b in tflite_bytes)
    # Wrap to 12 values per line
    values = [f'0x{b:02x}' for b in tflite_bytes]
    lines = []
    for i in range(0, len(values), 12):
        lines.append('    ' + ', '.join(values[i:i+12]))

    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    header = f"""\
/*
 * VAPI Anti-Cheat TinyML Model — INT8 Quantized TFLite
 *
 * Auto-generated by train_anticheat_model.py on {now}
 * DO NOT EDIT — regenerate by running the training pipeline.
 *
 * Classes: NOMINAL(0x20), SKILLED(0x21), MACRO(0x23),
 *          AIMBOT(0x24), IMU_MISS(0x26), INJECTION(0x27)
 * Features: 12 (press_var, imu_noise, imu_corr, jerk_r, stick_r_vel,
 *           reaction_ms, gyro_mag, accel_mag, press_rate, hold_asym,
 *           stick_l_vel, stick_r_acc)
 * Size: {len(tflite_bytes)} bytes
 */

#ifndef ANTICHEAT_MODEL_H
#define ANTICHEAT_MODEL_H

#include <stdint.h>

#define ANTICHEAT_MODEL_SIZE {len(tflite_bytes)}
#define ANTICHEAT_NUM_FEATURES 12
#define ANTICHEAT_NUM_CLASSES 6

/* Class index → firmware inference code mapping */
static const uint8_t anticheat_class_to_infer[] = {{
    0x20, /* 0: NOMINAL */
    0x21, /* 1: SKILLED */
    0x23, /* 2: CHEAT:MACRO */
    0x24, /* 3: CHEAT:AIMBOT */
    0x26, /* 4: CHEAT:IMU_MISS */
    0x27, /* 5: CHEAT:INJECTION */
}};

alignas(16) static const uint8_t anticheat_model_data[ANTICHEAT_MODEL_SIZE] = {{
{chr(10).join(lines)}
}};

#endif /* ANTICHEAT_MODEL_H */
"""
    with open(output_path, 'w') as f:
        f.write(header)


# ─── Evaluation ──────────────────────────────────────────────────────────────

def evaluate_tflite(tflite_path: str, X_test, y_test):
    """Run TFLite INT8 model on test set and report accuracy."""
    import tensorflow as tf

    interpreter = tf.lite.Interpreter(model_path=tflite_path)
    interpreter.allocate_tensors()

    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]

    # Get quantization params
    input_scale = input_details['quantization'][0]
    input_zp = input_details['quantization'][1]
    output_scale = output_details['quantization'][0]
    output_zp = output_details['quantization'][1]

    predictions = []
    for i in range(len(X_test)):
        # Quantize input
        x_q = np.round(X_test[i] / input_scale + input_zp).astype(np.int8)
        interpreter.set_tensor(input_details['index'], x_q.reshape(1, -1))
        interpreter.invoke()
        out_q = interpreter.get_tensor(output_details['index'])[0]
        # Dequantize and argmax
        out_f = (out_q.astype(np.float32) - output_zp) * output_scale
        predictions.append(np.argmax(out_f))

    predictions = np.array(predictions)
    accuracy = np.mean(predictions == y_test)

    print(f"\n{'='*70}")
    print(f"  VAPI Anti-Cheat TinyML Model — INT8 Evaluation")
    print(f"{'='*70}")
    print(f"  Test samples:  {len(y_test)}")
    print(f"  Accuracy:      {accuracy:.4f} ({accuracy*100:.1f}%)")
    print(f"{'='*70}\n")

    print(classification_report(
        y_test, predictions, target_names=CLASS_NAMES, digits=3
    ))

    print("\nConfusion Matrix:")
    cm = confusion_matrix(y_test, predictions)
    header = "            " + "  ".join(f"{c[:6]:>6}" for c in CLASS_NAMES)
    print(header)
    for i, row in enumerate(cm):
        print(f"  {CLASS_NAMES[i]:>12}  " + "  ".join(f"{v:>6}" for v in row))

    return accuracy


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    output_dir = os.path.dirname(os.path.abspath(__file__))
    tflite_path = os.path.join(output_dir, 'anticheat_model.tflite')
    header_path = os.path.join(output_dir, 'anticheat_model.h')

    print("="*70)
    print("  VAPI Anti-Cheat TinyML Training Pipeline")
    print("="*70)

    # 1. Generate synthetic data
    print("\n[1/6] Generating synthetic dataset...")
    X, y = generate_dataset(seed=42)
    print(f"  Total samples: {len(X)} ({SAMPLES_PER_CLASS} per class)")
    print(f"  Features: {NUM_FEATURES}")
    print(f"  Classes: {NUM_CLASSES} ({', '.join(CLASS_NAMES)})")

    # 2. Split
    print("\n[2/6] Splitting train/val/test (70/15/15)...")
    X_train, X_temp, y_train, y_temp = train_test_split(
        X, y, test_size=0.30, random_state=42, stratify=y
    )
    X_val, X_test, y_val, y_test = train_test_split(
        X_temp, y_temp, test_size=0.50, random_state=42, stratify=y_temp
    )
    print(f"  Train: {len(X_train)}, Val: {len(X_val)}, Test: {len(X_test)}")

    # 3. Normalize
    print("\n[3/6] Computing normalization parameters...")
    mean, std = compute_normalization(X_train)
    X_train_n = normalize(X_train, mean, std)
    X_val_n = normalize(X_val, mean, std)
    X_test_n = normalize(X_test, mean, std)
    print(f"  Feature means: {mean}")
    print(f"  Feature stds:  {std}")

    # 4. Train
    print("\n[4/6] Training model...")
    model = build_and_train(X_train_n, y_train, X_val_n, y_val)

    # Float model accuracy
    _, float_acc = model.evaluate(X_test_n, y_test, verbose=0)
    print(f"\n  Float32 test accuracy: {float_acc:.4f}")

    # 5. Export TFLite INT8
    print("\n[5/6] Exporting TFLite INT8...")
    tflite_bytes = export_tflite(model, X_train_n, tflite_path)
    print(f"  Model size: {len(tflite_bytes)} bytes ({len(tflite_bytes)/1024:.1f} KB)")
    print(f"  Saved to: {tflite_path}")

    # Export C header
    export_c_header(tflite_bytes, header_path)
    print(f"  C header: {header_path}")

    # 6. Evaluate INT8 model
    print("\n[6/6] Evaluating INT8 quantized model...")
    int8_acc = evaluate_tflite(tflite_path, X_test_n, y_test)

    # Summary
    print(f"\n{'='*70}")
    print(f"  Training Complete")
    print(f"{'='*70}")
    print(f"  Float32 accuracy:  {float_acc*100:.1f}%")
    print(f"  INT8 accuracy:     {int8_acc*100:.1f}%")
    print(f"  Model size:        {len(tflite_bytes)} bytes")
    print(f"  Target met:        {'YES' if int8_acc > 0.95 else 'NO'} (>95%)")
    print(f"  Files generated:")
    print(f"    {tflite_path}")
    print(f"    {header_path}")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
