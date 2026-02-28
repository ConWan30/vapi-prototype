/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * TinyML Anti-Cheat Engine — Gaming-Specific Cheat Detection for VAPI DualShock
 *
 * Direct adaptation of the Pebble Tracker's tinyml.h interface for gaming
 * anti-cheat classification. Same API pattern, different model and features.
 *
 * Pebble alignment:
 *   - Same tinyml_result_t output structure (class_id, confidence, probabilities)
 *   - Same tinyml_model_info_t metadata structure
 *   - Same get_weights() API for PoAC model attestation (model_manifest_hash)
 *   - Inference result codes extend POAC_INFER_* range (0x20-0x2F)
 *
 * Gaming model:
 *   - Input: 100-frame window × 30 features = 3000 values (INT8 quantized)
 *   - Architecture: Conv1D stack → GlobalAvgPool → Dense → Softmax
 *   - Output: 8 classes (2 clean + 6 cheat types)
 *   - Constraints: <60 KB flash, <24 KB inference RAM, <5 ms on ESP32-S3
 *
 * Target: ESP32-S3 (Xtensa LX7 @ 240 MHz) with TFLite Micro + ESP-NN
 */

#ifndef TINYML_ANTICHEAT_H
#define TINYML_ANTICHEAT_H

#include <stdint.h>
#include <stdbool.h>
#include "dualshock_agent.h"
#include "poac.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ──────────────────────────────────────────────────────────────────
 * Feature Window Configuration
 * ────────────────────────────────────────────────────────────────── */
#define AC_WINDOW_SIZE         100   /* Frames per classification window */
#define AC_FEATURES_PER_FRAME  30    /* Features extracted per input frame */
#define AC_FEATURE_COUNT       (AC_WINDOW_SIZE * AC_FEATURES_PER_FRAME) /* 3000 */
#define AC_NUM_CLASSES         8     /* Classification output classes */

/* Model constraints */
#define AC_MODEL_MAX_FLASH     (60 * 1024)  /* 60 KB max model size */
#define AC_MODEL_MAX_ARENA     (24 * 1024)  /* 24 KB max inference RAM */

/* ──────────────────────────────────────────────────────────────────
 * Per-Frame Feature Vector
 * Extracted from raw ds_input_snapshot_t by the feature extraction
 * pipeline. These are the 30 features fed to the TinyML model.
 * ────────────────────────────────────────────────────────────────── */
typedef struct {
    /* Stick features (8) */
    float stick_lx_norm;            /* Left stick X normalized [-1, 1] */
    float stick_ly_norm;            /* Left stick Y normalized */
    float stick_rx_norm;            /* Right stick X normalized */
    float stick_ry_norm;            /* Right stick Y normalized */
    float stick_l_velocity;         /* Left stick velocity (Δ/Δt) */
    float stick_r_velocity;         /* Right stick velocity */
    float stick_l_acceleration;     /* Left stick acceleration (ΔΔ/Δt²) */
    float stick_r_acceleration;     /* Right stick acceleration */

    /* Trigger features (2) */
    float trigger_l2_norm;          /* L2 normalized [0, 1] */
    float trigger_r2_norm;          /* R2 normalized [0, 1] */

    /* Button features (5) */
    float button_state_packed;      /* 18 buttons → float encoding */
    float inter_press_interval_ms;  /* ms since last button state change */
    float inter_press_variance;     /* Running σ² of press intervals */
    float button_press_rate;        /* Presses per second (rolling 1s) */
    float button_hold_asymmetry;    /* Ratio of press/release durations */

    /* IMU features (7) */
    float gyro_x, gyro_y, gyro_z;
    float accel_magnitude;          /* sqrt(ax²+ay²+az²) */
    float gyro_magnitude;           /* sqrt(gx²+gy²+gz²) */
    float imu_stick_correlation;    /* Cross-correlation(stick_vel, gyro) */
    float imu_noise_floor;          /* High-freq gyro variance (micro-tremor) */

    /* Touch features (3) */
    float touch_x_norm;             /* Primary touch X [0, 1] or -1 */
    float touch_y_norm;             /* Primary touch Y [0, 1] or -1 */
    float touch_entropy;            /* Shannon entropy of recent positions */

    /* Timing features (3) */
    float frame_dt_ms;              /* Inter-frame time in ms */
    float reaction_proxy_ms;        /* Time from idle→active transition */
    float direction_change_count;   /* Stick direction reversals this window */

    /* Derived (2) */
    float stick_jerk_l;             /* Third derivative of left stick */
    float stick_jerk_r;             /* Third derivative of right stick */
} ac_feature_frame_t;

_Static_assert(sizeof(ac_feature_frame_t) == 30 * sizeof(float),
               "Feature frame must be exactly 30 floats");

/* ──────────────────────────────────────────────────────────────────
 * Inference Result — Same structure as Pebble's tinyml_result_t
 * ────────────────────────────────────────────────────────────────── */
typedef struct {
    uint8_t  class_id;              /* DS_INFER_* code (0x20-0x29) */
    uint8_t  confidence;            /* [0-255] mapped from max probability */
    float    probabilities[AC_NUM_CLASSES]; /* Raw class probabilities */
    int32_t  latency_us;            /* Inference time in microseconds */
} ac_result_t;

/* Class index mapping for probabilities[] array */
#define AC_CLASS_NOMINAL    0
#define AC_CLASS_SKILLED    1
#define AC_CLASS_REACT      2
#define AC_CLASS_MACRO      3
#define AC_CLASS_AIMBOT     4
#define AC_CLASS_RECOIL     5
#define AC_CLASS_IMU_MISS   6
#define AC_CLASS_INJECTION  7

/* ──────────────────────────────────────────────────────────────────
 * Model Metadata — Same structure as Pebble's tinyml_model_info_t
 * ────────────────────────────────────────────────────────────────── */
typedef struct {
    const char *model_name;         /* e.g., "anticheat_v1" */
    uint32_t    version;
    uint32_t    window_size_ms;     /* 100 frames @ 1 kHz = 100 ms */
    uint32_t    num_classes;        /* 8 */
    uint32_t    model_size_bytes;   /* Flash footprint */
    uint32_t    arena_size_bytes;   /* RAM needed for inference */
} ac_model_info_t;

/* ──────────────────────────────────────────────────────────────────
 * Public API
 * Same function naming pattern as Pebble's tinyml.h
 * ────────────────────────────────────────────────────────────────── */

/**
 * Initialize the anti-cheat TinyML engine.
 * Loads the TFLite model, allocates inference arena.
 * Falls back to heuristic detection if no model is available.
 */
int ac_init(void);

/**
 * Extract features from a raw input snapshot and push into the
 * feature window. Call this at 1 kHz (every input poll).
 *
 * Internally tracks previous frames for velocity/acceleration
 * computation and running statistics.
 *
 * @param snapshot  Current raw input from SPI poll.
 * @param prev      Previous snapshot (for delta computation). NULL on first call.
 */
void ac_push_frame(const ds_input_snapshot_t *snapshot,
                   const ds_input_snapshot_t *prev);

/**
 * Run anti-cheat classification on the current feature window.
 * Call at the configured inference rate (default: 10 Hz).
 *
 * @param out  Output classification result.
 * @return 0 on success, -EAGAIN if window not full, negative errno on failure.
 */
int ac_classify(ac_result_t *out);

/**
 * Convenience: extract features + classify in one call.
 * Used by the agent's reflexive layer.
 *
 * @param snapshot  Current input snapshot.
 * @param out       Output classification result.
 * @return 0 on success, negative errno on failure.
 */
int ac_infer(const ds_input_snapshot_t *snapshot, ac_result_t *out);

/**
 * Get model metadata (for PoAC model attestation).
 */
int ac_get_model_info(ac_model_info_t *out);

/**
 * Get pointer to model weights for PoAC model_manifest_hash.
 * Same API as Pebble's tinyml_get_weights().
 */
int ac_get_weights(const uint8_t **out_weights, size_t *out_len);

/**
 * Check if a real TFLite model is loaded (vs. heuristic fallback).
 */
bool ac_has_model(void);

/**
 * Reset the feature window and running statistics.
 * Call on session start or after calibration.
 */
void ac_reset(void);

/**
 * Load a new model at runtime (OTA model update from companion app).
 *
 * @param model_data  TFLite flatbuffer bytes.
 * @param model_len   Length in bytes (must be <= AC_MODEL_MAX_FLASH).
 * @return 0 on success, -EINVAL if model invalid, -ENOMEM if too large.
 */
int ac_load_model(const uint8_t *model_data, size_t model_len);

#ifdef __cplusplus
}
#endif

#endif /* TINYML_ANTICHEAT_H */
