/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * TinyML Inference Wrapper — Edge Impulse Integration for VAPI Agent
 *
 * Provides a C-callable interface to Edge Impulse's C++ inference SDK.
 * The model classifies activity from IMU data (accelerometer windows)
 * into: stationary, walking, vehicle, fall, or anomaly.
 *
 * Model training workflow:
 *   1. Collect IMU data via Edge Impulse data forwarder or upload CSVs
 *   2. Train a classification model in Edge Impulse Studio
 *   3. Export as "C++ library" and place in models/edge_impulse/
 *   4. Uncomment the Edge Impulse sources in CMakeLists.txt
 *
 * Until a trained model is available, the heuristic fallback provides
 * reasonable classifications from raw sensor thresholds.
 *
 * Target: IoTeX Pebble Tracker (nRF9160, Cortex-M33 @ 64 MHz)
 * Model constraints: INT8 quantized, <80 KB flash, <32 KB inference RAM
 */

#ifndef TINYML_H
#define TINYML_H

#include <stdint.h>
#include <stdbool.h>
#include "perception.h"
#include "poac.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Feature window size for IMU-based classification.
 * Edge Impulse default for activity recognition: 2 seconds @ 50 Hz = 100 samples.
 * Each sample: 3 accel axes (floats) = 12 bytes. Window = 1200 bytes.
 */
#define TINYML_WINDOW_SIZE     100
#define TINYML_AXES            3     /* accel X, Y, Z */
#define TINYML_FEATURE_COUNT   (TINYML_WINDOW_SIZE * TINYML_AXES)  /* 300 floats */

/* Inference result — maps to POAC_INFER_* codes */
typedef struct {
    uint8_t  class_id;       /* POAC_INFER_CLASS_* or POAC_INFER_ANOMALY_* */
    uint8_t  confidence;     /* [0-255] mapped from model probability */
    float    probabilities[5]; /* Raw class probabilities: [stationary, walking, vehicle, fall, anomaly] */
    int32_t  latency_us;     /* Inference time in microseconds */
} tinyml_result_t;

/* Model metadata */
typedef struct {
    const char *model_name;
    uint32_t    version;
    uint32_t    window_size_ms;
    uint32_t    num_classes;
    uint32_t    model_size_bytes;   /* Flash footprint */
    uint32_t    arena_size_bytes;   /* RAM needed for inference */
} tinyml_model_info_t;

/**
 * Initialize the TinyML inference engine.
 *
 * If Edge Impulse SDK is linked: initializes the EI classifier.
 * If not: activates the heuristic fallback (no-op, always available).
 *
 * @return 0 on success, negative errno on failure.
 */
int tinyml_init(void);

/**
 * Push an IMU sample into the feature window.
 *
 * Call this at the model's expected sample rate (e.g., 50 Hz).
 * When the window is full, the next call to tinyml_classify() will
 * use the complete window.
 *
 * @param accel_x  Acceleration X in g.
 * @param accel_y  Acceleration Y in g.
 * @param accel_z  Acceleration Z in g.
 */
void tinyml_push_sample(float accel_x, float accel_y, float accel_z);

/**
 * Run inference on the current feature window.
 *
 * If the window is not yet full, returns the last result (or NOMINAL
 * if no inference has run yet).
 *
 * @param out  Output inference result.
 * @return 0 on success, -EAGAIN if window not ready, negative errno on failure.
 */
int tinyml_classify(tinyml_result_t *out);

/**
 * Run inference directly from a perception snapshot.
 *
 * Convenience function that extracts IMU data from the perception struct,
 * runs classification, and also evaluates environmental anomalies (VOC, temp).
 * This is the primary entry point called by the agent's reflexive layer.
 *
 * @param percept  Current sensor snapshot.
 * @param out      Output inference result.
 * @return 0 on success, negative errno on failure.
 */
int tinyml_infer(const perception_t *percept, tinyml_result_t *out);

/**
 * Get model metadata (for attestation and logging).
 */
int tinyml_get_model_info(tinyml_model_info_t *out);

/**
 * Get pointer to model weights for PoAC attestation.
 *
 * @param out_weights  Pointer to model weight data (read-only).
 * @param out_len      Length of weight data in bytes.
 * @return 0 on success.
 */
int tinyml_get_weights(const uint8_t **out_weights, size_t *out_len);

/**
 * Check if a real Edge Impulse model is loaded (vs. heuristic fallback).
 */
bool tinyml_has_model(void);

#ifdef __cplusplus
}
#endif

#endif /* TINYML_H */
