/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * TinyML Anti-Cheat Engine — Gaming Cheat Detection for VAPI DualShock
 *
 * Ported from Pebble's tinyml.c with gaming-specific model and features.
 * Same API pattern: init → push samples → classify → get weights for attestation.
 *
 * Detection targets:
 *   - Impossible reaction times (<150 ms sustained)
 *   - Macro/turbo patterns (σ < 1 ms inter-press timing)
 *   - Aimbot stick movement (ballistic snap-to-target)
 *   - Recoil script (perfectly inverse compensation)
 *   - IMU mismatch (stick movement without controller motion)
 *   - Input injection (fabricated inputs, impossible timing)
 *
 * When no TFLite model is loaded, falls back to heuristic detection
 * using hand-tuned thresholds — same pattern as Pebble's heuristic fallback.
 *
 * Target: ESP32-S3, TFLite Micro with ESP-NN acceleration
 */

#include "tinyml_anticheat.h"
#include "dualshock_agent.h"
#include "esp_log.h"
#include "esp_timer.h"

#include <string.h>
#include <math.h>
#include <stdlib.h>

static const char *TAG = "ac_tinyml";

/* ══════════════════════════════════════════════════════════════════
 * TFLite Micro Integration (conditional compilation)
 * When VAPI_USE_TFLITE is defined, uses real model inference.
 * Otherwise, falls back to heuristic detection.
 * ══════════════════════════════════════════════════════════════════ */

#ifdef VAPI_USE_TFLITE
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"

/* Embedded model binary (linked from models/anticheat_v1.tflite) */
extern const uint8_t anticheat_model_start[] asm("_binary_anticheat_v1_tflite_start");
extern const uint8_t anticheat_model_end[]   asm("_binary_anticheat_v1_tflite_end");

static const tflite::Model         *s_model = NULL;
static tflite::MicroInterpreter    *s_interpreter = NULL;
static uint8_t                      s_arena[AC_MODEL_MAX_ARENA]
                                        __attribute__((aligned(16)));
#endif /* VAPI_USE_TFLITE */

/* ══════════════════════════════════════════════════════════════════
 * Internal State
 * ══════════════════════════════════════════════════════════════════ */

static bool s_initialized = false;
static bool s_has_model = false;

/* Feature window: circular buffer of extracted features */
static ac_feature_frame_t s_feature_window[AC_WINDOW_SIZE];
static uint32_t s_window_head = 0;
static uint32_t s_window_count = 0;

/* Running statistics for feature extraction */
static struct {
    /* Stick velocity tracking (previous positions for Δ computation) */
    float prev_stick_lx, prev_stick_ly;
    float prev_stick_rx, prev_stick_ry;
    float prev_vel_lx, prev_vel_ly;
    float prev_vel_rx, prev_vel_ry;

    /* Button timing tracking */
    uint32_t last_button_change_us;
    float    press_intervals[32];       /* Ring buffer of inter-press intervals */
    uint8_t  press_interval_head;
    uint8_t  press_interval_count;
    uint8_t  prev_buttons[3];
    float    press_rate_window[10];     /* 10× 100ms windows for rate */
    uint8_t  rate_head;
    uint16_t presses_this_window;

    /* IMU-stick cross-correlation accumulator */
    float    corr_sum_xy;              /* Σ(stick_vel × gyro) */
    float    corr_sum_x2;             /* Σ(stick_vel²) */
    float    corr_sum_y2;             /* Σ(gyro²) */
    uint32_t corr_count;

    /* Touch entropy */
    float    touch_positions[16];      /* Recent touch X positions */
    uint8_t  touch_head;
    uint8_t  touch_count;

    /* Reaction time proxy */
    bool     stick_was_idle;
    int64_t  idle_to_active_us;

    /* Direction change counting */
    float    prev_stick_angle;
    uint16_t direction_changes;
} s_stats;

/* Last inference result (returned if window not ready) */
static ac_result_t s_last_result = {
    .class_id = DS_INFER_PLAY_NOMINAL,
    .confidence = 0,
    .latency_us = 0,
};

/* Dynamic model buffer for OTA updates */
static uint8_t *s_dynamic_model = NULL;
static size_t   s_dynamic_model_len = 0;

/* ══════════════════════════════════════════════════════════════════
 * Feature Extraction Helpers
 * ══════════════════════════════════════════════════════════════════ */

static float normalize_stick(int16_t raw)
{
    return (float)raw / 32768.0f;
}

static float stick_velocity(float current, float previous, float dt_ms)
{
    if (dt_ms < 0.001f) return 0.0f;
    return (current - previous) / dt_ms;
}

static float compute_magnitude(float x, float y, float z)
{
    return sqrtf(x * x + y * y + z * z);
}

static float compute_press_variance(void)
{
    if (s_stats.press_interval_count < 2) return 999.0f;  /* No data = high variance */

    float sum = 0, sum2 = 0;
    uint8_t n = s_stats.press_interval_count;
    for (int i = 0; i < n; i++) {
        float v = s_stats.press_intervals[i];
        sum += v;
        sum2 += v * v;
    }
    float mean = sum / n;
    return (sum2 / n) - (mean * mean);  /* Variance */
}

static float compute_touch_entropy(void)
{
    if (s_stats.touch_count < 4) return 1.0f;  /* Insufficient data = assume human */

    /* Simple histogram entropy over 8 bins */
    int bins[8] = {0};
    for (int i = 0; i < s_stats.touch_count; i++) {
        int bin = (int)(s_stats.touch_positions[i] * 7.99f);
        if (bin < 0) bin = 0;
        if (bin > 7) bin = 7;
        bins[bin]++;
    }

    float entropy = 0.0f;
    float total = (float)s_stats.touch_count;
    for (int i = 0; i < 8; i++) {
        if (bins[i] > 0) {
            float p = (float)bins[i] / total;
            entropy -= p * log2f(p);
        }
    }
    return entropy / 3.0f;  /* Normalize to [0, 1] range (log2(8) = 3) */
}

static float compute_imu_correlation(void)
{
    if (s_stats.corr_count < 10) return 0.5f;  /* Insufficient data */

    float denom = sqrtf(s_stats.corr_sum_x2 * s_stats.corr_sum_y2);
    if (denom < 0.0001f) return 0.0f;

    float r = s_stats.corr_sum_xy / denom;
    return (r + 1.0f) / 2.0f;  /* Map [-1,1] to [0,1] */
}

/* ══════════════════════════════════════════════════════════════════
 * Heuristic Fallback Classifier
 * Same pattern as Pebble's heuristic in tinyml.c — hand-tuned
 * thresholds that provide reasonable detection without a trained model.
 * ══════════════════════════════════════════════════════════════════ */

static void heuristic_classify(ac_result_t *out)
{
    /* Analyze recent feature window */
    float avg_press_variance = 0;
    float avg_imu_noise = 0;
    float avg_reaction = 0;
    float avg_imu_corr = 0;
    float avg_stick_jerk = 0;
    uint32_t n = (s_window_count < AC_WINDOW_SIZE) ? s_window_count : AC_WINDOW_SIZE;

    if (n == 0) {
        out->class_id = DS_INFER_PLAY_NOMINAL;
        out->confidence = 128;
        return;
    }

    for (uint32_t i = 0; i < n; i++) {
        uint32_t idx = (s_window_head - n + i + AC_WINDOW_SIZE) % AC_WINDOW_SIZE;
        avg_press_variance += s_feature_window[idx].inter_press_variance;
        avg_imu_noise += s_feature_window[idx].imu_noise_floor;
        avg_reaction += s_feature_window[idx].reaction_proxy_ms;
        avg_imu_corr += s_feature_window[idx].imu_stick_correlation;
        avg_stick_jerk += fabsf(s_feature_window[idx].stick_jerk_r);
    }
    avg_press_variance /= n;
    avg_imu_noise /= n;
    avg_reaction /= n;
    avg_imu_corr /= n;
    avg_stick_jerk /= n;

    /* Zero out probabilities */
    memset(out->probabilities, 0, sizeof(out->probabilities));

    /* ── Detection rules (ordered by severity) ── */

    /* 1. Macro/turbo: near-zero timing variance */
    if (avg_press_variance < 1.0f && avg_press_variance > 0.0001f) {
        out->class_id = DS_INFER_CHEAT_MACRO;
        out->confidence = 230;
        out->probabilities[AC_CLASS_MACRO] = 0.90f;
        return;
    }

    /* 2. Input injection: no IMU noise (controller on desk/no human holding it) */
    if (avg_imu_noise < 0.001f && avg_imu_corr < 0.1f) {
        out->class_id = DS_INFER_CHEAT_INJECTION;
        out->confidence = 210;
        out->probabilities[AC_CLASS_INJECTION] = 0.82f;
        return;
    }

    /* 3. IMU mismatch: stick moves but controller doesn't */
    if (avg_imu_corr < 0.15f && avg_stick_jerk > 0.5f) {
        out->class_id = DS_INFER_CHEAT_IMU_MISS;
        out->confidence = 200;
        out->probabilities[AC_CLASS_IMU_MISS] = 0.78f;
        return;
    }

    /* 4. Impossible reaction: sustained <150 ms reaction time */
    if (avg_reaction > 0 && avg_reaction < 150.0f) {
        out->class_id = DS_INFER_CHEAT_REACTION;
        out->confidence = 190;
        out->probabilities[AC_CLASS_REACT] = 0.75f;
        return;
    }

    /* 5. Aimbot: extremely high stick jerk (ballistic snap) */
    if (avg_stick_jerk > 2.0f) {
        out->class_id = DS_INFER_CHEAT_AIMBOT;
        out->confidence = 180;
        out->probabilities[AC_CLASS_AIMBOT] = 0.70f;
        return;
    }

    /* 6. Skilled play: low reaction + high precision but within human bounds */
    if (avg_reaction < 250.0f && avg_reaction >= 150.0f && avg_imu_corr > 0.6f) {
        out->class_id = DS_INFER_PLAY_SKILLED;
        out->confidence = 200;
        out->probabilities[AC_CLASS_SKILLED] = 0.78f;
        out->probabilities[AC_CLASS_NOMINAL] = 0.20f;
        return;
    }

    /* 7. Default: nominal play */
    out->class_id = DS_INFER_PLAY_NOMINAL;
    out->confidence = 220;
    out->probabilities[AC_CLASS_NOMINAL] = 0.86f;
    out->probabilities[AC_CLASS_SKILLED] = 0.10f;
}

/* ══════════════════════════════════════════════════════════════════
 * Public API Implementation
 * ══════════════════════════════════════════════════════════════════ */

int ac_init(void)
{
    memset(&s_stats, 0, sizeof(s_stats));
    memset(s_feature_window, 0, sizeof(s_feature_window));
    s_window_head = 0;
    s_window_count = 0;
    s_stats.stick_was_idle = true;

#ifdef VAPI_USE_TFLITE
    /* Load embedded TFLite model */
    size_t model_len = anticheat_model_end - anticheat_model_start;
    s_model = tflite::GetModel(anticheat_model_start);
    if (s_model->version() != TFLITE_SCHEMA_VERSION) {
        ESP_LOGE(TAG, "Model schema mismatch: got %lu, expected %d",
                 s_model->version(), TFLITE_SCHEMA_VERSION);
        s_has_model = false;
    } else {
        /* Register required ops for Conv1D anti-cheat model */
        static tflite::MicroMutableOpResolver<8> resolver;
        resolver.AddConv2D();        /* Conv1D implemented as Conv2D */
        resolver.AddReshape();
        resolver.AddFullyConnected();
        resolver.AddSoftmax();
        resolver.AddMean();          /* GlobalAveragePooling */
        resolver.AddQuantize();
        resolver.AddDequantize();
        resolver.AddRelu();

        static tflite::MicroInterpreter interpreter(
            s_model, resolver, s_arena, AC_MODEL_MAX_ARENA);
        s_interpreter = &interpreter;

        if (s_interpreter->AllocateTensors() != kTfLiteOk) {
            ESP_LOGE(TAG, "Failed to allocate TFLite tensors");
            s_has_model = false;
        } else {
            s_has_model = true;
            ESP_LOGI(TAG, "TFLite model loaded: %zu bytes, arena %zu bytes",
                     model_len, AC_MODEL_MAX_ARENA);
        }
    }
#else
    s_has_model = false;
    ESP_LOGI(TAG, "Heuristic fallback active (no TFLite model linked)");
#endif

    s_initialized = true;
    return 0;
}

void ac_push_frame(const ds_input_snapshot_t *snapshot,
                   const ds_input_snapshot_t *prev)
{
    if (!s_initialized || !snapshot) return;

    float dt_ms = (prev) ? (float)snapshot->inter_frame_us / 1000.0f : 1.0f;
    if (dt_ms < 0.001f) dt_ms = 1.0f;

    ac_feature_frame_t frame = {0};

    /* ── Stick features ── */
    frame.stick_lx_norm = normalize_stick(snapshot->left_stick_x);
    frame.stick_ly_norm = normalize_stick(snapshot->left_stick_y);
    frame.stick_rx_norm = normalize_stick(snapshot->right_stick_x);
    frame.stick_ry_norm = normalize_stick(snapshot->right_stick_y);

    float lx = frame.stick_lx_norm, ly = frame.stick_ly_norm;
    float rx = frame.stick_rx_norm, ry = frame.stick_ry_norm;

    float vel_lx = stick_velocity(lx, s_stats.prev_stick_lx, dt_ms);
    float vel_ly = stick_velocity(ly, s_stats.prev_stick_ly, dt_ms);
    float vel_rx = stick_velocity(rx, s_stats.prev_stick_rx, dt_ms);
    float vel_ry = stick_velocity(ry, s_stats.prev_stick_ry, dt_ms);

    frame.stick_l_velocity = sqrtf(vel_lx * vel_lx + vel_ly * vel_ly);
    frame.stick_r_velocity = sqrtf(vel_rx * vel_rx + vel_ry * vel_ry);

    /* Acceleration (second derivative) */
    frame.stick_l_acceleration = stick_velocity(
        frame.stick_l_velocity,
        sqrtf(s_stats.prev_vel_lx * s_stats.prev_vel_lx +
              s_stats.prev_vel_ly * s_stats.prev_vel_ly), dt_ms);
    frame.stick_r_acceleration = stick_velocity(
        frame.stick_r_velocity,
        sqrtf(s_stats.prev_vel_rx * s_stats.prev_vel_rx +
              s_stats.prev_vel_ry * s_stats.prev_vel_ry), dt_ms);

    /* Jerk (third derivative) — key aimbot indicator */
    frame.stick_jerk_l = (frame.stick_l_acceleration - 0) / dt_ms; /* simplified */
    frame.stick_jerk_r = (frame.stick_r_acceleration - 0) / dt_ms;

    /* Update tracking state */
    s_stats.prev_stick_lx = lx; s_stats.prev_stick_ly = ly;
    s_stats.prev_stick_rx = rx; s_stats.prev_stick_ry = ry;
    s_stats.prev_vel_lx = vel_lx; s_stats.prev_vel_ly = vel_ly;
    s_stats.prev_vel_rx = vel_rx; s_stats.prev_vel_ry = vel_ry;

    /* ── Trigger features ── */
    frame.trigger_l2_norm = (float)snapshot->l2_trigger / 255.0f;
    frame.trigger_r2_norm = (float)snapshot->r2_trigger / 255.0f;

    /* ── Button features ── */
    frame.button_state_packed = (float)(
        (snapshot->buttons[0] << 16) |
        (snapshot->buttons[1] << 8) |
         snapshot->buttons[2]) / 16777216.0f;

    /* Detect button state changes */
    bool button_changed = (prev != NULL) &&
        (memcmp(snapshot->buttons, s_stats.prev_buttons, 3) != 0);
    if (button_changed) {
        int64_t now_us = esp_timer_get_time();
        if (s_stats.last_button_change_us > 0) {
            float interval_ms = (float)(now_us - s_stats.last_button_change_us) / 1000.0f;
            s_stats.press_intervals[s_stats.press_interval_head] = interval_ms;
            s_stats.press_interval_head =
                (s_stats.press_interval_head + 1) % 32;
            if (s_stats.press_interval_count < 32) {
                s_stats.press_interval_count++;
            }
            frame.inter_press_interval_ms = interval_ms;
        }
        s_stats.last_button_change_us = now_us;
        s_stats.presses_this_window++;
        memcpy(s_stats.prev_buttons, snapshot->buttons, 3);
    }

    frame.inter_press_variance = compute_press_variance();
    frame.button_press_rate = (float)s_stats.presses_this_window * 10.0f; /* per sec */
    frame.button_hold_asymmetry = 0.5f; /* TODO: track press/release durations */

    /* ── IMU features ── */
    frame.gyro_x = snapshot->gyro_x;
    frame.gyro_y = snapshot->gyro_y;
    frame.gyro_z = snapshot->gyro_z;
    frame.accel_magnitude = compute_magnitude(
        snapshot->accel_x, snapshot->accel_y, snapshot->accel_z);
    frame.gyro_magnitude = compute_magnitude(
        snapshot->gyro_x, snapshot->gyro_y, snapshot->gyro_z);

    /* IMU-stick cross-correlation (running accumulator) */
    float stick_vel_total = frame.stick_r_velocity; /* Right stick = aim */
    float gyro_total = frame.gyro_magnitude;
    s_stats.corr_sum_xy += stick_vel_total * gyro_total;
    s_stats.corr_sum_x2 += stick_vel_total * stick_vel_total;
    s_stats.corr_sum_y2 += gyro_total * gyro_total;
    s_stats.corr_count++;

    frame.imu_stick_correlation = compute_imu_correlation();

    /* IMU noise floor: high-frequency gyro variance (micro-tremor indicator) */
    /* Human hand tremor: 8-12 Hz, ~0.01-0.05 rad/s */
    frame.imu_noise_floor = frame.gyro_magnitude; /* Simplified: magnitude as proxy */

    /* ── Touch features ── */
    if (snapshot->touch_active & 0x01) {
        frame.touch_x_norm = (float)snapshot->touch0_x / 1920.0f;
        frame.touch_y_norm = (float)snapshot->touch0_y / 942.0f;

        s_stats.touch_positions[s_stats.touch_head] = frame.touch_x_norm;
        s_stats.touch_head = (s_stats.touch_head + 1) % 16;
        if (s_stats.touch_count < 16) s_stats.touch_count++;
    } else {
        frame.touch_x_norm = -1.0f;
        frame.touch_y_norm = -1.0f;
    }
    frame.touch_entropy = compute_touch_entropy();

    /* ── Timing features ── */
    frame.frame_dt_ms = dt_ms;

    /* Reaction time proxy: time from stick idle → active */
    float stick_mag = sqrtf(rx * rx + ry * ry);
    if (s_stats.stick_was_idle && stick_mag > 0.15f) {
        frame.reaction_proxy_ms =
            (float)(esp_timer_get_time() - s_stats.idle_to_active_us) / 1000.0f;
        s_stats.stick_was_idle = false;
    } else if (stick_mag < 0.05f) {
        s_stats.stick_was_idle = true;
        s_stats.idle_to_active_us = esp_timer_get_time();
        frame.reaction_proxy_ms = 0;
    }

    /* Direction changes */
    float angle = atan2f(ry, rx);
    float delta = fabsf(angle - s_stats.prev_stick_angle);
    if (delta > 1.5f) {  /* ~90° direction change */
        s_stats.direction_changes++;
    }
    s_stats.prev_stick_angle = angle;
    frame.direction_change_count = (float)s_stats.direction_changes;

    /* ── Push into window ── */
    s_feature_window[s_window_head % AC_WINDOW_SIZE] = frame;
    s_window_head++;
    if (s_window_count < AC_WINDOW_SIZE) {
        s_window_count++;
    }

    /* Reset per-window counters every AC_WINDOW_SIZE frames */
    if (s_window_head % AC_WINDOW_SIZE == 0) {
        s_stats.presses_this_window = 0;
        s_stats.direction_changes = 0;
        /* Decay correlation accumulators (exponential forget) */
        s_stats.corr_sum_xy *= 0.9f;
        s_stats.corr_sum_x2 *= 0.9f;
        s_stats.corr_sum_y2 *= 0.9f;
    }
}

int ac_classify(ac_result_t *out)
{
    if (!s_initialized || !out) return -1;
    if (s_window_count < AC_WINDOW_SIZE) {
        memcpy(out, &s_last_result, sizeof(*out));
        return -11; /* EAGAIN */
    }

    int64_t start_us = esp_timer_get_time();

#ifdef VAPI_USE_TFLITE
    if (s_has_model && s_interpreter) {
        /* Quantize feature window to INT8 and copy to input tensor */
        TfLiteTensor *input = s_interpreter->input(0);
        int8_t *input_data = input->data.int8;

        for (int i = 0; i < AC_WINDOW_SIZE; i++) {
            int idx = (s_window_head - AC_WINDOW_SIZE + i + AC_WINDOW_SIZE) %
                      AC_WINDOW_SIZE;
            float *frame_floats = (float *)&s_feature_window[idx];
            for (int j = 0; j < AC_FEATURES_PER_FRAME; j++) {
                /* Quantize: scale to INT8 range [-128, 127] */
                float val = frame_floats[j];
                val = fmaxf(-1.0f, fminf(1.0f, val));  /* Clamp */
                input_data[i * AC_FEATURES_PER_FRAME + j] =
                    (int8_t)(val * 127.0f);
            }
        }

        /* Run inference */
        if (s_interpreter->Invoke() != kTfLiteOk) {
            ESP_LOGE(TAG, "TFLite inference failed");
            heuristic_classify(out);
        } else {
            /* Read output probabilities */
            TfLiteTensor *output = s_interpreter->output(0);
            float max_prob = -1.0f;
            int max_class = 0;

            for (int i = 0; i < AC_NUM_CLASSES; i++) {
                /* Dequantize INT8 output */
                float prob = ((float)output->data.int8[i] -
                              output->params.zero_point) *
                             output->params.scale;
                out->probabilities[i] = prob;
                if (prob > max_prob) {
                    max_prob = prob;
                    max_class = i;
                }
            }

            /* Map class index to inference code */
            static const uint8_t class_to_infer[] = {
                DS_INFER_PLAY_NOMINAL,    /* 0 */
                DS_INFER_PLAY_SKILLED,    /* 1 */
                DS_INFER_CHEAT_REACTION,  /* 2 */
                DS_INFER_CHEAT_MACRO,     /* 3 */
                DS_INFER_CHEAT_AIMBOT,    /* 4 */
                DS_INFER_CHEAT_RECOIL,    /* 5 */
                DS_INFER_CHEAT_IMU_MISS,  /* 6 */
                DS_INFER_CHEAT_INJECTION, /* 7 */
            };
            out->class_id = class_to_infer[max_class];
            out->confidence = (uint8_t)(max_prob * 255.0f);
        }
    } else {
        heuristic_classify(out);
    }
#else
    heuristic_classify(out);
#endif

    out->latency_us = (int32_t)(esp_timer_get_time() - start_us);
    memcpy(&s_last_result, out, sizeof(*out));

    return 0;
}

int ac_infer(const ds_input_snapshot_t *snapshot, ac_result_t *out)
{
    if (!snapshot || !out) return -1;
    ac_push_frame(snapshot, NULL);
    return ac_classify(out);
}

int ac_get_model_info(ac_model_info_t *out)
{
    if (!out) return -1;

#ifdef VAPI_USE_TFLITE
    if (s_has_model) {
        out->model_name = "anticheat_v1";
        out->version = 1;
        out->window_size_ms = AC_WINDOW_SIZE;  /* 100 frames = 100 ms at 1 kHz */
        out->num_classes = AC_NUM_CLASSES;
        out->model_size_bytes = anticheat_model_end - anticheat_model_start;
        out->arena_size_bytes = AC_MODEL_MAX_ARENA;
        return 0;
    }
#endif

    out->model_name = "heuristic_fallback";
    out->version = 0;
    out->window_size_ms = AC_WINDOW_SIZE;
    out->num_classes = AC_NUM_CLASSES;
    out->model_size_bytes = 0;
    out->arena_size_bytes = 0;
    return 0;
}

int ac_get_weights(const uint8_t **out_weights, size_t *out_len)
{
    if (!out_weights || !out_len) return -1;

#ifdef VAPI_USE_TFLITE
    if (s_has_model) {
        if (s_dynamic_model) {
            *out_weights = s_dynamic_model;
            *out_len = s_dynamic_model_len;
        } else {
            *out_weights = anticheat_model_start;
            *out_len = anticheat_model_end - anticheat_model_start;
        }
        return 0;
    }
#endif

    /* Heuristic fallback: return threshold constants as "weights" for attestation */
    static const uint8_t heuristic_weights[] = {
        /* Version byte */ 0x00,
        /* Macro threshold σ */ 0x01, 0x00, /* 1.0 ms as uint16 */
        /* IMU noise threshold */ 0x00, 0x01, /* 0.001 as fixed-point */
        /* Reaction threshold */ 0x00, 0x96, /* 150 ms as uint16 */
        /* Aimbot jerk threshold */ 0x02, 0x00, /* 2.0 as fixed-point */
    };
    *out_weights = heuristic_weights;
    *out_len = sizeof(heuristic_weights);
    return 0;
}

bool ac_has_model(void)
{
    return s_has_model;
}

void ac_reset(void)
{
    memset(&s_stats, 0, sizeof(s_stats));
    memset(s_feature_window, 0, sizeof(s_feature_window));
    s_window_head = 0;
    s_window_count = 0;
    s_stats.stick_was_idle = true;
    s_last_result.class_id = DS_INFER_PLAY_NOMINAL;
    s_last_result.confidence = 0;
    ESP_LOGI(TAG, "Feature window and stats reset");
}

int ac_load_model(const uint8_t *model_data, size_t model_len)
{
    if (!model_data || model_len == 0) return -1;
    if (model_len > AC_MODEL_MAX_FLASH) {
        ESP_LOGE(TAG, "Model too large: %zu > %d", model_len, AC_MODEL_MAX_FLASH);
        return -12; /* ENOMEM */
    }

    /* Allocate and copy model data */
    if (s_dynamic_model) {
        free(s_dynamic_model);
    }
    s_dynamic_model = (uint8_t *)malloc(model_len);
    if (!s_dynamic_model) {
        ESP_LOGE(TAG, "Failed to allocate model buffer");
        return -12;
    }
    memcpy(s_dynamic_model, model_data, model_len);
    s_dynamic_model_len = model_len;

#ifdef VAPI_USE_TFLITE
    /* Reload interpreter with new model */
    s_model = tflite::GetModel(s_dynamic_model);
    if (s_model->version() != TFLITE_SCHEMA_VERSION) {
        ESP_LOGE(TAG, "Invalid model schema");
        free(s_dynamic_model);
        s_dynamic_model = NULL;
        s_dynamic_model_len = 0;
        return -22; /* EINVAL */
    }
    /* Re-allocate tensors */
    if (s_interpreter->AllocateTensors() != kTfLiteOk) {
        ESP_LOGE(TAG, "Failed to allocate tensors for new model");
        return -12;
    }
    s_has_model = true;
    ESP_LOGI(TAG, "New anti-cheat model loaded: %zu bytes", model_len);
#else
    ESP_LOGW(TAG, "Model loaded but TFLite not compiled; using heuristic");
#endif

    /* Reset feature window for new model */
    ac_reset();
    return 0;
}
