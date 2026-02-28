/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * TinyML Inference Engine — Heuristic Fallback + Edge Impulse Integration
 *
 * Two operational modes:
 *
 *   1. Heuristic fallback (default, always available):
 *      - IMU: statistical classification from accelerometer windows
 *        (variance, peak magnitude, average magnitude)
 *      - Environmental: threshold-based VOC/temperature anomaly detection
 *      - Combined: environmental anomalies override motion classification
 *
 *   2. Edge Impulse model (when CONFIG_VAPI_EDGE_IMPULSE=y):
 *      - Loads a pre-trained, INT8-quantized neural network
 *      - Runs inference via TFLite-Micro on Cortex-M33 @ 64 MHz
 *      - Environmental overlay still applies on top of model output
 *
 * The heuristic fallback is designed to produce reasonable results that
 * exercise the full PoAC pipeline end-to-end, so the system works
 * immediately on first flash before any model training.
 *
 * Target: IoTeX Pebble Tracker (nRF9160, Cortex-M33, 256 KB RAM)
 * Budget: < 80 KB flash, < 32 KB inference RAM, < 50 ms per inference
 */

#include "tinyml.h"

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <string.h>
#include <math.h>
#include <errno.h>

LOG_MODULE_REGISTER(tinyml, CONFIG_VAPI_LOG_LEVEL);

/* --------------------------------------------------------------------------
 * Conditional Edge Impulse SDK inclusion
 *
 * When CONFIG_VAPI_EDGE_IMPULSE is defined, the Edge Impulse C++ library
 * is linked into the build. The run_classifier() function is called via
 * an extern "C" wrapper (see models/edge_impulse/ei_wrapper.cpp).
 * -------------------------------------------------------------------------- */

#ifdef CONFIG_VAPI_EDGE_IMPULSE

/*
 * Edge Impulse C wrapper — defined in models/edge_impulse/ei_wrapper.cpp.
 * This avoids pulling C++ headers into our C translation unit.
 */
extern int ei_wrapper_classify(const float *features, size_t feature_count,
                               float *out_probs, size_t max_classes,
                               size_t *out_num_classes, int *out_latency_us);
extern int ei_wrapper_get_model_size(size_t *out_flash, size_t *out_arena);
extern const char *ei_wrapper_get_model_name(void);

#endif /* CONFIG_VAPI_EDGE_IMPULSE */

/* --------------------------------------------------------------------------
 * Heuristic model identity
 *
 * Used for PoAC model attestation when no Edge Impulse model is loaded.
 * SHA-256 of this block becomes the model_manifest_hash, ensuring the
 * verifier can distinguish "heuristic v1" from any trained model.
 * -------------------------------------------------------------------------- */

static const uint8_t heuristic_model_weights[] = {
    /* Magic: "VAPI" */
    0x56, 0x41, 0x50, 0x49,
    /* Type: heuristic classifier */
    0x48, 0x45, 0x55, 0x52,
    /* Version: 1.0.0 */
    0x01, 0x00, 0x00, 0x00,
    /* Architecture ID length (24) + "heuristic_motion_env_v1\0" */
    0x18, 0x00, 0x00, 0x00,
    0x68, 0x65, 0x75, 0x72, 0x69, 0x73, 0x74, 0x69,  /* "heuristi" */
    0x63, 0x5F, 0x6D, 0x6F, 0x74, 0x69, 0x6F, 0x6E,  /* "c_motion" */
    0x5F, 0x65, 0x6E, 0x76, 0x5F, 0x76, 0x31, 0x00,  /* "_env_v1\0" */
    /* Thresholds (IEEE 754 single-precision, big-endian):
     * fall_threshold  = 4.0g
     * voc_critical    = 10000.0 ohm
     * voc_warning     = 50000.0 ohm
     * stationary_max  = 0.3g
     * walking_max     = 2.0g
     */
    0x40, 0x80, 0x00, 0x00,  /* 4.0f    */
    0x46, 0x1C, 0x40, 0x00,  /* 10000.0f */
    0x47, 0x43, 0x50, 0x00,  /* 50000.0f */
    0x3E, 0x99, 0x99, 0x9A,  /* 0.3f    */
    0x40, 0x00, 0x00, 0x00,  /* 2.0f    */
};

#define HEURISTIC_MODEL_SIZE sizeof(heuristic_model_weights)

/* --------------------------------------------------------------------------
 * Module state
 * -------------------------------------------------------------------------- */

/* IMU feature window — circular buffer of accelerometer samples.
 * Stored as [x0, y0, z0, x1, y1, z1, ...] in chronological order.
 * When window_ready is true, the buffer contains a full window. */
static float feature_window[TINYML_FEATURE_COUNT];
static size_t window_write_idx;           /* Next sample slot (0..WINDOW_SIZE-1) */
static size_t window_samples_collected;   /* Total samples pushed since last reset */
static bool   window_ready;              /* True when >= WINDOW_SIZE samples collected */

/* Cached result from last successful inference */
static tinyml_result_t last_result;

/* Whether the Edge Impulse model is loaded and operational */
static bool ei_model_active;

/* Initialization flag */
static bool initialized;

/* --------------------------------------------------------------------------
 * Heuristic motion classifier
 *
 * Operates on the full feature window (100 samples * 3 axes = 300 floats).
 * Computes statistical features from the accelerometer data:
 *   - Average magnitude: distinguishes stationary from moving
 *   - Peak magnitude: detects falls (sudden high-g events)
 *   - Magnitude variance: distinguishes rhythmic walking from smooth vehicle
 *   - Jerk (delta between consecutive magnitudes): impact detection
 *
 * Classification thresholds are tuned for a wrist/belt-mounted Pebble Tracker.
 * -------------------------------------------------------------------------- */

/**
 * Classify motion from the IMU feature window.
 *
 * @param features  Array of TINYML_FEATURE_COUNT floats [x,y,z,x,y,z,...].
 * @param n_samples Number of complete samples (TINYML_WINDOW_SIZE).
 * @param out       Output classification result (class_id, confidence, probs).
 */
static void heuristic_classify_motion(const float *features, size_t n_samples,
                                      tinyml_result_t *out)
{
    float sum_mag = 0.0f;
    float max_mag = 0.0f;
    float prev_mag = 0.0f;
    float sum_jerk = 0.0f;

    /* Pass 1: compute magnitudes, peak, and jerk */
    for (size_t i = 0; i < n_samples; i++) {
        float x = features[i * TINYML_AXES + 0];
        float y = features[i * TINYML_AXES + 1];
        float z = features[i * TINYML_AXES + 2];
        float mag = sqrtf(x * x + y * y + z * z);

        sum_mag += mag;
        if (mag > max_mag) {
            max_mag = mag;
        }
        if (i > 0) {
            float jerk = fabsf(mag - prev_mag);
            sum_jerk += jerk;
        }
        prev_mag = mag;
    }

    float avg_mag = sum_mag / (float)n_samples;
    float avg_jerk = sum_jerk / (float)(n_samples - 1);

    /* Pass 2: compute variance of magnitude */
    float sum_sq_diff = 0.0f;
    for (size_t i = 0; i < n_samples; i++) {
        float x = features[i * TINYML_AXES + 0];
        float y = features[i * TINYML_AXES + 1];
        float z = features[i * TINYML_AXES + 2];
        float mag = sqrtf(x * x + y * y + z * z);
        float diff = mag - avg_mag;
        sum_sq_diff += diff * diff;
    }
    float variance = sum_sq_diff / (float)n_samples;

    /* Initialize probabilities to zero */
    memset(out->probabilities, 0, sizeof(out->probabilities));

    /* Classification decision tree */

    /* 1. Fall detection: sudden high-g spike with high jerk */
    if (max_mag > 4.0f && avg_jerk > 0.5f) {
        out->class_id = POAC_INFER_CLASS_FALL;
        out->confidence = 200;
        out->probabilities[0] = 0.05f;  /* stationary */
        out->probabilities[1] = 0.02f;  /* walking    */
        out->probabilities[2] = 0.05f;  /* vehicle    */
        out->probabilities[3] = 0.78f;  /* fall       */
        out->probabilities[4] = 0.10f;  /* anomaly    */
        return;
    }

    /* 2. Fall detection: extreme peak even without sustained jerk */
    if (max_mag > 6.0f) {
        out->class_id = POAC_INFER_CLASS_FALL;
        out->confidence = 230;
        out->probabilities[3] = 0.90f;
        out->probabilities[4] = 0.05f;
        return;
    }

    /* 3. Vehicle: sustained motion, low variance (smooth ride) */
    if (avg_mag > 1.2f && variance < 0.3f && avg_jerk < 0.3f) {
        out->class_id = POAC_INFER_CLASS_VEHICLE;
        out->confidence = 170;
        out->probabilities[0] = 0.05f;
        out->probabilities[1] = 0.15f;
        out->probabilities[2] = 0.67f;
        out->probabilities[3] = 0.03f;
        out->probabilities[4] = 0.10f;
        return;
    }

    /* 4. Walking: moderate motion with rhythmic variance */
    if (avg_mag > 0.3f && avg_mag < 2.5f && variance > 0.05f) {
        float walk_conf = 0.50f + (variance > 0.2f ? 0.21f : variance);
        if (walk_conf > 0.95f) walk_conf = 0.95f;

        out->class_id = POAC_INFER_CLASS_WALKING;
        out->confidence = (uint8_t)(walk_conf * 255.0f);
        out->probabilities[0] = 0.10f;
        out->probabilities[1] = walk_conf;
        out->probabilities[2] = 0.05f;
        out->probabilities[3] = 0.02f;
        out->probabilities[4] = 1.0f - walk_conf - 0.17f;
        return;
    }

    /* 5. Stationary: very low motion */
    if (avg_mag < 0.3f || (avg_mag < 1.2f && variance < 0.01f)) {
        float stat_conf = 1.0f - (avg_mag / 1.2f);
        if (stat_conf < 0.5f) stat_conf = 0.5f;
        if (stat_conf > 0.95f) stat_conf = 0.95f;

        out->class_id = POAC_INFER_CLASS_STATIONARY;
        out->confidence = (uint8_t)(stat_conf * 255.0f);
        out->probabilities[0] = stat_conf;
        out->probabilities[1] = 0.05f;
        out->probabilities[2] = 0.02f;
        out->probabilities[3] = 0.01f;
        out->probabilities[4] = 1.0f - stat_conf - 0.08f;
        return;
    }

    /* 6. Default: nominal (unclear activity) */
    out->class_id = POAC_INFER_NOMINAL;
    out->confidence = 128;
    out->probabilities[0] = 0.25f;
    out->probabilities[1] = 0.25f;
    out->probabilities[2] = 0.20f;
    out->probabilities[3] = 0.10f;
    out->probabilities[4] = 0.20f;
}

/**
 * Evaluate environmental anomalies from a perception snapshot.
 *
 * If an environmental anomaly is detected with higher confidence than the
 * motion classification, the anomaly overrides the motion result.
 *
 * @param percept  Current sensor snapshot.
 * @param result   Motion classification result (may be overridden).
 * @return true if an environmental anomaly was detected and overrides motion.
 */
static bool evaluate_environmental_anomaly(const perception_t *percept,
                                           tinyml_result_t *result)
{
    /* Skip if BME680 didn't produce a valid VOC reading */
    if (percept->env.voc_resistance_ohm <= 0.0f) {
        return false;
    }

    /* Critical VOC anomaly: resistance drops far below typical clean-air range.
     * Clean air: 100k-500k ohm.  Polluted: 10k-50k ohm.  Dangerous: < 10k ohm.
     */
    if (percept->env.voc_resistance_ohm < 10000.0f) {
        /* Only override if this is more confident than existing classification */
        uint8_t anomaly_conf = 190;
        if (anomaly_conf > result->confidence ||
            result->class_id == POAC_INFER_NOMINAL ||
            result->class_id == POAC_INFER_CLASS_STATIONARY) {
            result->class_id = POAC_INFER_ANOMALY_HIGH;
            result->confidence = anomaly_conf;
            LOG_WRN("VOC CRITICAL: resistance=%.0f ohm — high-confidence anomaly",
                    (double)percept->env.voc_resistance_ohm);
            return true;
        }
    }

    /* Warning VOC anomaly: moderate air quality degradation */
    if (percept->env.voc_resistance_ohm < 50000.0f) {
        uint8_t anomaly_conf = 100;
        if (result->class_id == POAC_INFER_NOMINAL ||
            result->class_id == POAC_INFER_CLASS_STATIONARY) {
            result->class_id = POAC_INFER_ANOMALY_LOW;
            result->confidence = anomaly_conf;
            LOG_INF("VOC WARNING: resistance=%.0f ohm — low-confidence anomaly",
                    (double)percept->env.voc_resistance_ohm);
            return true;
        }
    }

    /* Temperature anomaly: extreme readings suggest sensor malfunction or
     * genuinely dangerous conditions (fire, freezer exposure, etc.) */
    if (percept->env.temperature_c > 60.0f || percept->env.temperature_c < -20.0f) {
        uint8_t anomaly_conf = 180;
        if (anomaly_conf > result->confidence) {
            result->class_id = POAC_INFER_ANOMALY_HIGH;
            result->confidence = anomaly_conf;
            LOG_WRN("TEMP CRITICAL: %.1f C — high-confidence anomaly",
                    (double)percept->env.temperature_c);
            return true;
        }
    }

    return false;
}

/**
 * Single-sample heuristic for when the feature window is not yet full.
 *
 * Uses only the current accelerometer reading (no window history).
 * Lower confidence than window-based classification.
 */
static void heuristic_single_sample(const perception_t *percept,
                                    tinyml_result_t *out)
{
    float mag = sqrtf(percept->imu.accel_x_g * percept->imu.accel_x_g +
                      percept->imu.accel_y_g * percept->imu.accel_y_g +
                      percept->imu.accel_z_g * percept->imu.accel_z_g);

    memset(out->probabilities, 0, sizeof(out->probabilities));

    if (mag > 4.0f) {
        out->class_id = POAC_INFER_CLASS_FALL;
        out->confidence = 160;  /* Lower than window-based */
        out->probabilities[3] = 0.63f;
    } else if (mag < 0.2f) {
        out->class_id = POAC_INFER_CLASS_STATIONARY;
        out->confidence = 180;
        out->probabilities[0] = 0.71f;
    } else if (mag < 2.0f) {
        out->class_id = POAC_INFER_CLASS_WALKING;
        out->confidence = 140;
        out->probabilities[1] = 0.55f;
    } else {
        out->class_id = POAC_INFER_CLASS_VEHICLE;
        out->confidence = 130;
        out->probabilities[2] = 0.51f;
    }

    out->latency_us = 0;
}

/* --------------------------------------------------------------------------
 * Linearized feature buffer for Edge Impulse
 *
 * Edge Impulse expects features in chronological order starting from
 * index 0. Our circular buffer may have the write pointer in the middle.
 * This function linearizes the buffer for the classifier.
 * -------------------------------------------------------------------------- */

static void linearize_feature_window(float *out_linear)
{
    /*
     * The circular buffer writes at window_write_idx. The oldest sample
     * is at window_write_idx (it was the next to be overwritten).
     * We need to read from [write_idx .. end, 0 .. write_idx-1].
     */
    size_t oldest = window_write_idx;
    size_t first_chunk = (TINYML_WINDOW_SIZE - oldest) * TINYML_AXES;
    size_t second_chunk = oldest * TINYML_AXES;

    memcpy(out_linear, &feature_window[oldest * TINYML_AXES],
           first_chunk * sizeof(float));
    if (second_chunk > 0) {
        memcpy(&out_linear[first_chunk], feature_window,
               second_chunk * sizeof(float));
    }
}

/* --------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------- */

int tinyml_init(void)
{
    /* Reset feature window */
    memset(feature_window, 0, sizeof(feature_window));
    window_write_idx = 0;
    window_samples_collected = 0;
    window_ready = false;

    /* Reset cached result */
    memset(&last_result, 0, sizeof(last_result));
    last_result.class_id = POAC_INFER_NOMINAL;

    ei_model_active = false;

#ifdef CONFIG_VAPI_EDGE_IMPULSE
    /* Verify Edge Impulse model is present and compatible */
    size_t flash_size, arena_size;
    int ret = ei_wrapper_get_model_size(&flash_size, &arena_size);
    if (ret == 0) {
        ei_model_active = true;
        LOG_INF("Edge Impulse model loaded: %s (flash=%zuB, arena=%zuB)",
                ei_wrapper_get_model_name(), flash_size, arena_size);

        if (arena_size > 32768) {
            LOG_WRN("Model arena (%zu) exceeds 32KB budget — may OOM", arena_size);
        }
    } else {
        LOG_WRN("Edge Impulse model init failed: %d — heuristic fallback active", ret);
    }
#endif

    initialized = true;

    LOG_INF("TinyML engine initialized (mode=%s, window=%u samples @ %u axes)",
            ei_model_active ? "Edge Impulse" : "heuristic",
            TINYML_WINDOW_SIZE, TINYML_AXES);

    return 0;
}

void tinyml_push_sample(float accel_x, float accel_y, float accel_z)
{
    size_t base = window_write_idx * TINYML_AXES;

    feature_window[base + 0] = accel_x;
    feature_window[base + 1] = accel_y;
    feature_window[base + 2] = accel_z;

    window_write_idx = (window_write_idx + 1) % TINYML_WINDOW_SIZE;

    if (window_samples_collected < TINYML_WINDOW_SIZE) {
        window_samples_collected++;
    }

    if (window_samples_collected >= TINYML_WINDOW_SIZE) {
        window_ready = true;
    }
}

int tinyml_classify(tinyml_result_t *out)
{
    if (out == NULL) {
        return -EINVAL;
    }

    if (!initialized) {
        return -ENODEV;
    }

    /* If window not full, return cached result */
    if (!window_ready) {
        *out = last_result;
        return -EAGAIN;
    }

    int32_t start_cycles = k_cycle_get_32();

    /* Linearize the circular buffer for inference */
    float linear_features[TINYML_FEATURE_COUNT];
    linearize_feature_window(linear_features);

#ifdef CONFIG_VAPI_EDGE_IMPULSE
    if (ei_model_active) {
        /* Edge Impulse inference path */
        float probs[5] = {0};
        size_t num_classes = 0;
        int latency_us = 0;

        int ret = ei_wrapper_classify(linear_features, TINYML_FEATURE_COUNT,
                                       probs, 5, &num_classes, &latency_us);
        if (ret != 0) {
            LOG_ERR("EI classify failed: %d — falling back to heuristic", ret);
            goto heuristic_fallback;
        }

        /* Map EI probabilities to our result format.
         * Expected class order: [stationary, walking, vehicle, fall, anomaly] */
        memcpy(out->probabilities, probs, sizeof(out->probabilities));

        /* Find the winning class */
        float max_prob = 0.0f;
        size_t max_idx = 0;
        for (size_t i = 0; i < num_classes && i < 5; i++) {
            if (probs[i] > max_prob) {
                max_prob = probs[i];
                max_idx = i;
            }
        }

        /* Map class index to POAC_INFER_* code */
        static const uint8_t class_map[5] = {
            POAC_INFER_CLASS_STATIONARY,  /* 0 */
            POAC_INFER_CLASS_WALKING,     /* 1 */
            POAC_INFER_CLASS_VEHICLE,     /* 2 */
            POAC_INFER_CLASS_FALL,        /* 3 */
            POAC_INFER_ANOMALY_HIGH,      /* 4 */
        };

        out->class_id = (max_idx < 5) ? class_map[max_idx] : POAC_INFER_NOMINAL;
        out->confidence = (uint8_t)(max_prob * 255.0f);
        out->latency_us = latency_us;

        last_result = *out;
        return 0;
    }

heuristic_fallback:
#endif /* CONFIG_VAPI_EDGE_IMPULSE */

    /* Heuristic classification from window statistics */
    heuristic_classify_motion(linear_features, TINYML_WINDOW_SIZE, out);

    int32_t end_cycles = k_cycle_get_32();
    out->latency_us = k_cyc_to_us_floor32(end_cycles - start_cycles);

    last_result = *out;
    return 0;
}

int tinyml_infer(const perception_t *percept, tinyml_result_t *out)
{
    if (percept == NULL || out == NULL) {
        return -EINVAL;
    }

    if (!initialized) {
        return -ENODEV;
    }

    /* Push the current IMU sample into the feature window.
     *
     * In production with a real model, samples should be pushed at the
     * model's expected sample rate (e.g., 50 Hz via a timer interrupt).
     * Here in the reflexive loop (30s cadence), we push one sample per
     * cycle. This means the window fills after 100 cycles (~50 minutes).
     *
     * For immediate results on first boot, the single-sample heuristic
     * is used until the window is full.
     */
    tinyml_push_sample(percept->imu.accel_x_g,
                       percept->imu.accel_y_g,
                       percept->imu.accel_z_g);

    /* Run window-based classification (or use single-sample fallback) */
    int ret = tinyml_classify(out);

    if (ret == -EAGAIN) {
        /* Window not ready — use single-sample heuristic */
        heuristic_single_sample(percept, out);
        ret = 0;
    } else if (ret != 0) {
        LOG_ERR("Classification failed: %d", ret);
        return ret;
    }

    /* Environmental anomaly overlay — may override motion classification
     * if a serious environmental condition is detected. */
    evaluate_environmental_anomaly(percept, out);

    LOG_DBG("Infer: class=0x%02x conf=%u latency=%d us (window=%s)",
            out->class_id, out->confidence, out->latency_us,
            window_ready ? "full" : "filling");

    return 0;
}

int tinyml_get_model_info(tinyml_model_info_t *out)
{
    if (out == NULL) {
        return -EINVAL;
    }

#ifdef CONFIG_VAPI_EDGE_IMPULSE
    if (ei_model_active) {
        out->model_name = ei_wrapper_get_model_name();
        out->version = 1;
        out->window_size_ms = 2000;  /* 100 samples @ 50 Hz */
        out->num_classes = 5;
        ei_wrapper_get_model_size(&out->model_size_bytes, &out->arena_size_bytes);
        return 0;
    }
#endif

    out->model_name = "heuristic_motion_env_v1";
    out->version = 1;
    out->window_size_ms = 2000;  /* 100 samples @ 50 Hz equivalent */
    out->num_classes = 5;
    out->model_size_bytes = HEURISTIC_MODEL_SIZE;
    out->arena_size_bytes = sizeof(feature_window) + sizeof(float) * TINYML_FEATURE_COUNT;

    return 0;
}

int tinyml_get_weights(const uint8_t **out_weights, size_t *out_len)
{
    if (out_weights == NULL || out_len == NULL) {
        return -EINVAL;
    }

#ifdef CONFIG_VAPI_EDGE_IMPULSE
    if (ei_model_active) {
        /*
         * For Edge Impulse models, the model binary is in the .rodata section.
         * The ei_wrapper exposes a pointer to the model data for attestation.
         * If the wrapper doesn't provide this, fall back to the heuristic ID.
         */
        /* TODO: Add ei_wrapper_get_model_data() when EI SDK is integrated */
    }
#endif

    /* Heuristic model: return the identity block */
    *out_weights = heuristic_model_weights;
    *out_len = HEURISTIC_MODEL_SIZE;

    return 0;
}

bool tinyml_has_model(void)
{
    return ei_model_active;
}
