/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAPI DualShock Agent — Gaming Three-Layer Cognitive Architecture
 *
 * Direct port of Pebble Tracker agent.c adapted for gaming anti-cheat.
 * Same PoAC record format, same chain integrity, same economic model.
 *
 * Layer 1 (Gaming Reflexive):    1 kHz input poll → TinyML → PoAC (2-10 Hz)
 * Layer 2 (Anti-Cheat Deliberative): Skill profiling, trend analysis (5 s)
 * Layer 3 (Economic Strategic):   BLE sync, bounty management (60 s)
 */

#include "dualshock_agent.h"
#include "tinyml_anticheat.h"
#include "poac.h"
/* #include "economic.h" */  /* Same API as Pebble — uncomment when ported */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"

#include <string.h>
#include <math.h>

static const char *TAG = "ds_agent";

/* ══════════════════════════════════════════════════════════════════
 * Internal State
 * ══════════════════════════════════════════════════════════════════ */

static ds_agent_state_t     s_state = DS_STATE_BOOT;
static ds_agent_config_t    s_config;
static ds_world_model_t     s_world_model;
static SemaphoreHandle_t    s_state_mutex;
static SemaphoreHandle_t    s_wm_mutex;

/* Thread handles */
static TaskHandle_t         s_l1_task = NULL;
static TaskHandle_t         s_l2_task = NULL;
static TaskHandle_t         s_l3_task = NULL;
static volatile bool        s_running = false;

/* Input ring buffer for feature extraction */
#define INPUT_RING_SIZE  256
static ds_input_snapshot_t  s_input_ring[INPUT_RING_SIZE];
static volatile uint32_t    s_input_head = 0;

/* PoAC record queue (drained by L3 → BLE) */
#define POAC_QUEUE_SIZE  32
static poac_record_t        s_poac_queue[POAC_QUEUE_SIZE];
static volatile uint8_t     s_poac_queue_head = 0;
static volatile uint8_t     s_poac_queue_tail = 0;
static SemaphoreHandle_t    s_poac_queue_mutex;

/* Callbacks */
static ds_state_cb_t        s_state_cb = NULL;
static ds_poac_cb_t         s_poac_cb = NULL;
static ds_cheat_cb_t        s_cheat_cb = NULL;

/* Anti-cheat state */
static uint8_t              s_consecutive_clean = 0;

/* EMA smoothing factor for world model baselines */
#define WM_EMA_ALPHA  0.05f

/* ══════════════════════════════════════════════════════════════════
 * Forward Declarations (Hardware Abstraction — implement per platform)
 * ══════════════════════════════════════════════════════════════════ */

/* SPI poll of stock controller MCU — returns raw input snapshot */
extern int ds_input_poll(ds_input_snapshot_t *out);

/* BLE: send PoAC record to companion app */
extern int ble_send_poac(const poac_record_t *record);

/* BLE: send world model to companion app */
extern int ble_send_world_model(const ds_world_model_t *wm);

/* Haptic motor control */
extern int haptic_play(ds_haptic_pattern_t pattern);

/* LED control (light bar) */
extern int led_set_color(uint8_t r, uint8_t g, uint8_t b);

/* Battery ADC read (returns mV) */
extern uint16_t battery_read_mv(void);

/* ══════════════════════════════════════════════════════════════════
 * Utility: State Transition
 * ══════════════════════════════════════════════════════════════════ */

static void set_state(ds_agent_state_t new_state)
{
    xSemaphoreTake(s_state_mutex, portMAX_DELAY);
    ds_agent_state_t old = s_state;
    if (old != new_state) {
        s_state = new_state;
        ESP_LOGI(TAG, "State: %d → %d", old, new_state);
        if (s_state_cb) {
            s_state_cb(old, new_state);
        }
    }
    xSemaphoreGive(s_state_mutex);
}

/* ══════════════════════════════════════════════════════════════════
 * Utility: World Model Hash
 * Deterministic serialization → SHA-256, same pattern as Pebble.
 * Hash is computed BEFORE updating the world model, so it captures
 * the decision context at the time of inference.
 * ══════════════════════════════════════════════════════════════════ */

static int wm_compute_hash(uint8_t out_hash[POAC_HASH_SIZE])
{
    /*
     * Serialization order (deterministic, big-endian floats):
     *   reaction_baseline (4B)
     *   precision_baseline (4B)
     *   consistency_baseline (4B)
     *   imu_corr_baseline (4B)
     *   session_skill_rating (4B)
     *   total_frames (4B)
     *   total_sessions (4B)
     *   total_cheat_flags (4B)
     *   count (1B)
     *   history[0..count-1] (each: 4+4+4+4+1+8 = 25 bytes)
     *
     * Max: 33 + 64*25 = 1633 bytes
     */
    uint8_t buf[1636];
    size_t pos = 0;

    xSemaphoreTake(s_wm_mutex, portMAX_DELAY);

    /* Baselines and aggregates */
    memcpy(&buf[pos], &s_world_model.reaction_baseline, 4);   pos += 4;
    memcpy(&buf[pos], &s_world_model.precision_baseline, 4);  pos += 4;
    memcpy(&buf[pos], &s_world_model.consistency_baseline, 4); pos += 4;
    memcpy(&buf[pos], &s_world_model.imu_corr_baseline, 4);   pos += 4;
    memcpy(&buf[pos], &s_world_model.session_skill_rating, 4); pos += 4;
    memcpy(&buf[pos], &s_world_model.total_frames, 4);        pos += 4;
    memcpy(&buf[pos], &s_world_model.total_sessions, 4);      pos += 4;
    memcpy(&buf[pos], &s_world_model.total_cheat_flags, 4);   pos += 4;
    buf[pos++] = s_world_model.count;

    /* History entries */
    for (int i = 0; i < s_world_model.count && i < DS_WORLD_MODEL_HISTORY; i++) {
        int idx = (s_world_model.head - s_world_model.count + i +
                   DS_WORLD_MODEL_HISTORY) % DS_WORLD_MODEL_HISTORY;
        ds_observation_summary_t *obs = &s_world_model.history[idx];
        memcpy(&buf[pos], &obs->avg_reaction_ms, 4);    pos += 4;
        memcpy(&buf[pos], &obs->stick_precision, 4);     pos += 4;
        memcpy(&buf[pos], &obs->timing_variance, 4);     pos += 4;
        memcpy(&buf[pos], &obs->imu_correlation, 4);     pos += 4;
        buf[pos++] = obs->cheat_flags;
        memcpy(&buf[pos], &obs->timestamp_ms, 8);        pos += 8;
    }

    xSemaphoreGive(s_wm_mutex);

    /* SHA-256 commitment (uses ESP32 hardware SHA accelerator) */
    return poac_commit_sensors(buf, pos, out_hash);
}

/* ══════════════════════════════════════════════════════════════════
 * Utility: Enqueue PoAC Record
 * ══════════════════════════════════════════════════════════════════ */

static void poac_enqueue(const poac_record_t *record)
{
    xSemaphoreTake(s_poac_queue_mutex, portMAX_DELAY);
    memcpy(&s_poac_queue[s_poac_queue_head], record, sizeof(poac_record_t));
    s_poac_queue_head = (s_poac_queue_head + 1) % POAC_QUEUE_SIZE;
    if (s_poac_queue_head == s_poac_queue_tail) {
        /* Queue full — drop oldest */
        s_poac_queue_tail = (s_poac_queue_tail + 1) % POAC_QUEUE_SIZE;
    }
    xSemaphoreGive(s_poac_queue_mutex);

    /* Notify callback if registered */
    if (s_poac_cb) {
        s_poac_cb(record);
    }
}

/* ══════════════════════════════════════════════════════════════════
 * Utility: Battery Percentage
 * ══════════════════════════════════════════════════════════════════ */

static uint8_t battery_pct_from_mv(uint16_t mv)
{
    /* Li-ion: 3.0V = 0%, 4.2V = 100%, linear approximation */
    if (mv <= 3000) return 0;
    if (mv >= 4200) return 100;
    return (uint8_t)(((uint32_t)(mv - 3000) * 100) / 1200);
}

/* ══════════════════════════════════════════════════════════════════
 * LAYER 1: Gaming Reflexive Thread
 *
 * Ported from Pebble agent.c L1 (lines 432-583).
 * Core loop: Poll inputs → Extract features → TinyML → PoAC
 * ══════════════════════════════════════════════════════════════════ */

static void l1_gaming_reflexive_task(void *arg)
{
    ESP_LOGI(TAG, "L1 Gaming Reflexive started (core 1)");

    ds_input_snapshot_t current = {0};
    ds_input_snapshot_t previous = {0};
    ac_result_t inference = {0};
    uint32_t frame_count = 0;
    int64_t last_poac_time_us = 0;

    while (s_running) {
        int64_t cycle_start = esp_timer_get_time();

        /* ── 1. Poll inputs from stock controller MCU via SPI ── */
        int err = ds_input_poll(&current);
        if (err != 0) {
            vTaskDelay(pdMS_TO_TICKS(1));
            continue;
        }

        /* ── 2. Push into ring buffer ── */
        uint32_t ring_idx = s_input_head % INPUT_RING_SIZE;
        memcpy(&s_input_ring[ring_idx], &current, sizeof(current));
        s_input_head++;

        /* ── 3. Feed feature extraction pipeline (every frame) ── */
        ac_push_frame(&current, (frame_count > 0) ? &previous : NULL);
        frame_count++;

        /* ── 4. Run TinyML inference at configured rate ── */
        /*    Default: every 100 frames (100 ms at 1 kHz) = 10 Hz */
        bool run_inference = (frame_count % (AC_WINDOW_SIZE) == 0);
        if (run_inference) {
            err = ac_classify(&inference);
            if (err == 0) {
                /* Check for cheat detection */
                bool cheat_detected = (inference.class_id >= DS_INFER_CHEAT_REACTION &&
                                       inference.class_id <= DS_INFER_CHEAT_INJECTION);

                if (cheat_detected && inference.confidence >= s_config.cheat_threshold) {
                    /* Cheat alert! */
                    if (s_state == DS_STATE_SESSION || s_state == DS_STATE_TOURNAMENT) {
                        set_state(DS_STATE_CHEAT_ALERT);
                        haptic_play(DS_HAPTIC_CHEAT_ALERT);
                        led_set_color(255, 0, 0);  /* RED light bar */
                    }
                    s_consecutive_clean = 0;

                    if (s_cheat_cb) {
                        s_cheat_cb(inference.class_id, inference.confidence);
                    }

                    ESP_LOGW(TAG, "CHEAT DETECTED: type=0x%02x conf=%d",
                             inference.class_id, inference.confidence);
                } else {
                    /* Clean window */
                    if (s_state == DS_STATE_CHEAT_ALERT) {
                        s_consecutive_clean++;
                        if (s_consecutive_clean >= s_config.cheat_resolve_count) {
                            set_state(DS_STATE_SESSION);
                            led_set_color(0, 255, 0);  /* GREEN light bar */
                            s_consecutive_clean = 0;
                        }
                    }
                }
            }
        }

        /* ── 5. Generate PoAC at configured rate ── */
        int64_t now_us = esp_timer_get_time();
        uint32_t poac_interval_us;
        if (s_state == DS_STATE_TOURNAMENT) {
            poac_interval_us = s_config.tournament_poac_ms * 1000;
        } else {
            poac_interval_us = s_config.poac_interval_ms * 1000;
        }

        bool generate_poac = (s_state >= DS_STATE_SESSION) &&
                              ((now_us - last_poac_time_us) >= poac_interval_us);

        if (generate_poac) {
            /* 5a. Sensor commitment: SHA-256 of 50-byte input snapshot */
            uint8_t sensor_hash[POAC_HASH_SIZE];
            poac_commit_sensors((const uint8_t *)&current,
                                sizeof(ds_input_snapshot_t), sensor_hash);

            /* 5b. World model hash: BEFORE updating (captures decision context) */
            uint8_t wm_hash[POAC_HASH_SIZE];
            wm_compute_hash(wm_hash);

            /* 5c. Determine action code */
            uint8_t action = DS_ACTION_SESSION_START;
            if (s_state == DS_STATE_CHEAT_ALERT) {
                action = DS_ACTION_CHEAT_ALERT;
            } else if (s_state == DS_STATE_TOURNAMENT) {
                action = DS_ACTION_TOURNAMENT_FRAME;
            } else {
                action = POAC_ACTION_REPORT;
            }

            /* 5d. Battery */
            uint16_t batt_mv = battery_read_mv();
            uint8_t batt_pct = battery_pct_from_mv(batt_mv);

            /* 5e. Generate + sign PoAC record (same API as Pebble) */
            poac_record_t record;
            int64_t timestamp_ms = now_us / 1000;
            uint32_t bounty_id = POAC_NO_BOUNTY;
            /* TODO: get active bounty from economic evaluator */

            err = poac_generate(
                sensor_hash,
                wm_hash,
                inference.class_id,
                action,
                inference.confidence,
                batt_pct,
                timestamp_ms,
                0.0,  /* latitude — no GPS on controller */
                0.0,  /* longitude */
                bounty_id,
                &record
            );

            if (err == 0) {
                poac_enqueue(&record);
                last_poac_time_us = now_us;

                xSemaphoreTake(s_wm_mutex, portMAX_DELAY);
                s_world_model.total_poac_generated++;
                xSemaphoreGive(s_wm_mutex);
            }

            /* 5f. Update world model AFTER hashing (same sequencing as Pebble) */
            /* Deferred to L2 for gaming — L1 only increments frame counter */
            xSemaphoreTake(s_wm_mutex, portMAX_DELAY);
            s_world_model.total_frames = frame_count;
            xSemaphoreGive(s_wm_mutex);
        }

        /* ── 6. Save previous snapshot for delta computation ── */
        memcpy(&previous, &current, sizeof(current));

        /* ── 7. Sleep until next poll ── */
        int64_t elapsed_us = esp_timer_get_time() - cycle_start;
        int64_t remaining = s_config.input_poll_interval_us - elapsed_us;
        if (remaining > 0) {
            /* Sub-millisecond: use busy-wait for <100 µs, vTaskDelay otherwise */
            if (remaining < 100) {
                while ((esp_timer_get_time() - cycle_start) <
                       s_config.input_poll_interval_us) {
                    /* spin */
                }
            } else {
                vTaskDelay(1);  /* Minimum 1 tick ≈ 1 ms at 1 kHz FreeRTOS */
            }
        }
    }

    ESP_LOGI(TAG, "L1 Gaming Reflexive stopped");
    vTaskDelete(NULL);
}

/* ══════════════════════════════════════════════════════════════════
 * LAYER 2: Anti-Cheat Deliberative Thread
 *
 * Ported from Pebble agent.c L2 (lines 593-807).
 * Replaces environmental trend analysis with player skill profiling
 * and anti-cheat pattern analysis.
 * ══════════════════════════════════════════════════════════════════ */

static void l2_anticheat_deliberative_task(void *arg)
{
    ESP_LOGI(TAG, "L2 Anti-Cheat Deliberative started");

    while (s_running) {
        vTaskDelay(pdMS_TO_TICKS(s_config.anticheat_interval_ms));

        if (s_state < DS_STATE_SESSION) {
            continue;  /* No analysis when not in a session */
        }

        /* ── 1. Battery management (same as Pebble L2) ── */
        uint16_t batt_mv = battery_read_mv();
        uint8_t batt_pct = battery_pct_from_mv(batt_mv);

        if (batt_pct <= s_config.battery_critical_pct) {
            if (s_state != DS_STATE_LOW_BATTERY) {
                set_state(DS_STATE_LOW_BATTERY);
                ESP_LOGW(TAG, "Battery critical: %d%%", batt_pct);

                /* Generate PSM_ENTER PoAC — same as Pebble */
                uint8_t sensor_hash[POAC_HASH_SIZE] = {0};
                uint8_t wm_hash[POAC_HASH_SIZE];
                wm_compute_hash(wm_hash);
                poac_record_t record;
                poac_generate(sensor_hash, wm_hash,
                              DS_INFER_PLAY_NOMINAL,
                              POAC_ACTION_PSM_ENTER,
                              0, batt_pct,
                              esp_timer_get_time() / 1000,
                              0.0, 0.0, POAC_NO_BOUNTY, &record);
                poac_enqueue(&record);
            }
            continue;
        }

        /* ── 2. Update player skill profile (world model) ── */
        /*
         * Compute summary statistics from recent TinyML inference windows.
         * This replaces Pebble's VOC/temp trend analysis with gaming metrics.
         */
        xSemaphoreTake(s_wm_mutex, portMAX_DELAY);

        /* TODO: Compute from actual inference history.
         * Placeholder: push a summary observation. */
        ds_observation_summary_t obs = {0};
        obs.timestamp_ms = esp_timer_get_time() / 1000;
        obs.avg_reaction_ms = s_world_model.reaction_baseline; /* placeholder */
        obs.stick_precision = s_world_model.precision_baseline;
        obs.timing_variance = s_world_model.consistency_baseline;
        obs.imu_correlation = s_world_model.imu_corr_baseline;
        obs.cheat_flags = 0;

        /* Push into circular buffer */
        s_world_model.history[s_world_model.head] = obs;
        s_world_model.head = (s_world_model.head + 1) % DS_WORLD_MODEL_HISTORY;
        if (s_world_model.count < DS_WORLD_MODEL_HISTORY) {
            s_world_model.count++;
        }

        /* EMA baseline update (α = 0.05) — same pattern as Pebble */
        if (s_world_model.count > 1) {
            s_world_model.reaction_baseline =
                WM_EMA_ALPHA * obs.avg_reaction_ms +
                (1.0f - WM_EMA_ALPHA) * s_world_model.reaction_baseline;
            s_world_model.precision_baseline =
                WM_EMA_ALPHA * obs.stick_precision +
                (1.0f - WM_EMA_ALPHA) * s_world_model.precision_baseline;
        } else {
            /* First observation: initialize baselines */
            s_world_model.reaction_baseline = obs.avg_reaction_ms;
            s_world_model.precision_baseline = obs.stick_precision;
        }

        xSemaphoreGive(s_wm_mutex);

        /* ── 3. Bounty evaluation (same as Pebble L2) ── */
        /* TODO: Port economic_optimize_bounties() call here.
         * Uses identical API — only energy profile constants differ. */
    }

    ESP_LOGI(TAG, "L2 Anti-Cheat Deliberative stopped");
    vTaskDelete(NULL);
}

/* ══════════════════════════════════════════════════════════════════
 * LAYER 3: Economic Strategic Thread
 *
 * Ported from Pebble agent.c L3 (lines 823-979).
 * Replaces cellular sync with BLE sync to companion app.
 * Includes the Autonomy Guard (same as Pebble).
 * ══════════════════════════════════════════════════════════════════ */

static void l3_economic_strategic_task(void *arg)
{
    ESP_LOGI(TAG, "L3 Economic Strategic started");

    while (s_running) {
        vTaskDelay(pdMS_TO_TICKS(s_config.ble_sync_interval_ms));

        /* ── 1. BLE sync: drain PoAC queue to companion app ── */
        xSemaphoreTake(s_poac_queue_mutex, portMAX_DELAY);
        uint8_t count = 0;
        while (s_poac_queue_tail != s_poac_queue_head && count < POAC_QUEUE_SIZE) {
            poac_record_t *record = &s_poac_queue[s_poac_queue_tail];
            int err = ble_send_poac(record);
            if (err != 0) {
                ESP_LOGW(TAG, "BLE send failed, will retry");
                break;
            }
            s_poac_queue_tail = (s_poac_queue_tail + 1) % POAC_QUEUE_SIZE;
            count++;
        }
        xSemaphoreGive(s_poac_queue_mutex);

        ESP_LOGI(TAG, "L3 synced %d PoAC records via BLE", count);

        /* ── 2. Send world model snapshot ── */
        ds_world_model_t wm_copy;
        xSemaphoreTake(s_wm_mutex, portMAX_DELAY);
        memcpy(&wm_copy, &s_world_model, sizeof(wm_copy));
        xSemaphoreGive(s_wm_mutex);

        ble_send_world_model(&wm_copy);

        /* ── 3. Autonomy Guard (same as Pebble L3) ── */
        /*
         * Trust rules for companion app commands:
         *   - REJECT any command that would disable PoAC generation
         *   - REJECT config changes if battery critical
         *   - REJECT sense intervals outside [100 µs, 10 s]
         *   - LOG all rejected commands for forensic audit
         *
         * This ensures the controller never cedes cognitive authority
         * to the companion app, preserving anti-cheat integrity.
         */

        /* TODO: Process incoming BLE commands from companion app.
         * Apply autonomy guard rules before executing. */
    }

    ESP_LOGI(TAG, "L3 Economic Strategic stopped");
    vTaskDelete(NULL);
}

/* ══════════════════════════════════════════════════════════════════
 * Public API Implementation
 * ══════════════════════════════════════════════════════════════════ */

int ds_agent_init(const ds_agent_config_t *config)
{
    if (config) {
        memcpy(&s_config, config, sizeof(s_config));
    } else {
        ds_agent_config_t defaults = DS_AGENT_CONFIG_DEFAULTS;
        memcpy(&s_config, &defaults, sizeof(s_config));
    }

    memset(&s_world_model, 0, sizeof(s_world_model));

    s_state_mutex = xSemaphoreCreateMutex();
    s_wm_mutex = xSemaphoreCreateMutex();
    s_poac_queue_mutex = xSemaphoreCreateMutex();

    if (!s_state_mutex || !s_wm_mutex || !s_poac_queue_mutex) {
        ESP_LOGE(TAG, "Failed to create mutexes");
        return -1;
    }

    ESP_LOGI(TAG, "Agent initialized (poll=%d µs, poac=%d ms, tourney=%d ms)",
             s_config.input_poll_interval_us,
             s_config.poac_interval_ms,
             s_config.tournament_poac_ms);

    return 0;
}

int ds_agent_start(void)
{
    if (s_running) return -1;
    s_running = true;

    /* Generate BOOT PoAC record — same as Pebble */
    uint8_t sensor_hash[POAC_HASH_SIZE] = {0};
    poac_record_t boot_record;
    poac_generate(sensor_hash, NULL,
                  DS_INFER_PLAY_NOMINAL,
                  POAC_ACTION_BOOT,
                  0, battery_pct_from_mv(battery_read_mv()),
                  esp_timer_get_time() / 1000,
                  0.0, 0.0, POAC_NO_BOUNTY, &boot_record);
    poac_enqueue(&boot_record);

    /* Start three-layer threads — same architecture as Pebble.
     * L1 pinned to core 1 for real-time input polling.
     * L2 and L3 run on core 0 alongside BLE stack. */
    xTaskCreatePinnedToCore(l1_gaming_reflexive_task, "vapi_L1_gaming",
                            8192, NULL, 20, &s_l1_task, 1);
    xTaskCreatePinnedToCore(l2_anticheat_deliberative_task, "vapi_L2_anticheat",
                            6144, NULL, 15, &s_l2_task, 0);
    xTaskCreatePinnedToCore(l3_economic_strategic_task, "vapi_L3_economic",
                            6144, NULL, 10, &s_l3_task, 0);

    set_state(DS_STATE_IDLE);
    led_set_color(0, 0, 255);  /* BLUE: idle, waiting for session */

    ESP_LOGI(TAG, "Agent started — 3 layers active");
    return 0;
}

int ds_agent_stop(void)
{
    s_running = false;
    /* Threads will self-terminate on next loop iteration */

    /* Persist PoAC chain state (counter + chain head) */
    poac_persist_state();

    set_state(DS_STATE_BOOT);
    ESP_LOGI(TAG, "Agent stopped");
    return 0;
}

int ds_agent_start_session(void)
{
    if (s_state != DS_STATE_IDLE && s_state != DS_STATE_LOW_BATTERY) {
        return -1;
    }

    ac_reset();  /* Reset TinyML feature window */

    /* Generate SESSION_START PoAC */
    uint8_t sensor_hash[POAC_HASH_SIZE] = {0};
    uint8_t wm_hash[POAC_HASH_SIZE];
    wm_compute_hash(wm_hash);
    poac_record_t record;
    poac_generate(sensor_hash, wm_hash,
                  DS_INFER_PLAY_NOMINAL, DS_ACTION_SESSION_START,
                  0, battery_pct_from_mv(battery_read_mv()),
                  esp_timer_get_time() / 1000,
                  0.0, 0.0, POAC_NO_BOUNTY, &record);
    poac_enqueue(&record);

    xSemaphoreTake(s_wm_mutex, portMAX_DELAY);
    s_world_model.total_sessions++;
    xSemaphoreGive(s_wm_mutex);

    set_state(DS_STATE_SESSION);
    led_set_color(0, 255, 0);  /* GREEN: session active, clean */
    haptic_play(DS_HAPTIC_BOUNTY_ACCEPTED);  /* Feedback: session started */

    ESP_LOGI(TAG, "Game session started");
    return 0;
}

int ds_agent_end_session(void)
{
    if (s_state < DS_STATE_SESSION) return -1;

    /* Generate SESSION_END PoAC */
    uint8_t sensor_hash[POAC_HASH_SIZE] = {0};
    uint8_t wm_hash[POAC_HASH_SIZE];
    wm_compute_hash(wm_hash);
    poac_record_t record;
    poac_generate(sensor_hash, wm_hash,
                  DS_INFER_PLAY_NOMINAL, DS_ACTION_SESSION_END,
                  0, battery_pct_from_mv(battery_read_mv()),
                  esp_timer_get_time() / 1000,
                  0.0, 0.0, POAC_NO_BOUNTY, &record);
    poac_enqueue(&record);

    set_state(DS_STATE_IDLE);
    led_set_color(0, 0, 255);  /* BLUE: idle */

    ESP_LOGI(TAG, "Game session ended");
    return 0;
}

int ds_agent_tournament_mode(bool enable)
{
    if (enable) {
        if (s_state != DS_STATE_SESSION) return -1;
        set_state(DS_STATE_TOURNAMENT);
        led_set_color(255, 165, 0);  /* ORANGE: tournament mode */
        haptic_play(DS_HAPTIC_TOURNAMENT_PULSE);
        ESP_LOGI(TAG, "Tournament mode ENABLED (10 Hz PoAC)");
    } else {
        if (s_state != DS_STATE_TOURNAMENT) return -1;
        set_state(DS_STATE_SESSION);
        led_set_color(0, 255, 0);  /* GREEN: normal session */
        ESP_LOGI(TAG, "Tournament mode DISABLED (2 Hz PoAC)");
    }
    return 0;
}

ds_agent_state_t ds_agent_get_state(void)
{
    return s_state;
}

int ds_agent_get_world_model(ds_world_model_t *out)
{
    if (!out) return -1;
    xSemaphoreTake(s_wm_mutex, portMAX_DELAY);
    memcpy(out, &s_world_model, sizeof(*out));
    xSemaphoreGive(s_wm_mutex);
    return 0;
}

int ds_agent_set_config(const ds_agent_config_t *config)
{
    if (!config) return -1;

    /* Autonomy guard: reject dangerous configs */
    if (config->input_poll_interval_us < 100 ||
        config->input_poll_interval_us > 10000000) {
        ESP_LOGW(TAG, "Rejected config: poll interval out of range");
        return -1;
    }
    if (config->poac_interval_ms < 50 || config->poac_interval_ms > 60000) {
        ESP_LOGW(TAG, "Rejected config: PoAC interval out of range");
        return -1;
    }

    memcpy(&s_config, config, sizeof(s_config));
    ESP_LOGI(TAG, "Config updated");
    return 0;
}

int ds_agent_force_sync(void)
{
    if (s_l3_task) {
        xTaskNotifyGive(s_l3_task);
    }
    return 0;
}

int ds_agent_haptic(ds_haptic_pattern_t pattern)
{
    return haptic_play(pattern);
}

void ds_agent_register_state_callback(ds_state_cb_t cb) { s_state_cb = cb; }
void ds_agent_register_poac_callback(ds_poac_cb_t cb)   { s_poac_cb = cb; }
void ds_agent_register_cheat_callback(ds_cheat_cb_t cb)  { s_cheat_cb = cb; }
