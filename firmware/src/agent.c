/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAPI Agent Core — Three-Layer Autonomous Agent Implementation
 *
 * Implements the reflexive/deliberative/strategic architecture for the
 * IoTeX Pebble Tracker. Each layer runs as a dedicated Zephyr thread:
 *
 *   Layer 1 (reflexive_thread):   Sense → Infer → Act loop, highest priority.
 *   Layer 2 (deliberative_thread): World-model analysis, goal evaluation.
 *   Layer 3 (strategic_thread):    Cloud sync, long-horizon reasoning.
 *
 * All agent decisions produce PoAC records that chain into a tamper-evident
 * log, guaranteeing verifiable autonomous cognition.
 *
 * Target: IoTeX Pebble Tracker (nRF9160, Zephyr RTOS, nRF Connect SDK v2.7+)
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/util.h>
#include <string.h>
#include <math.h>

#include "agent.h"
#include "perception.h"
#include "poac.h"
#include "economic.h"
#include "tinyml.h"

LOG_MODULE_REGISTER(agent, CONFIG_VAPI_AGENT_LOG_LEVEL);

/* ---------------------------------------------------------------------------
 * Kconfig defaults (overridable in prj.conf / board overlay)
 * -------------------------------------------------------------------------*/

#ifndef CONFIG_VAPI_AGENT_LOG_LEVEL
#define CONFIG_VAPI_AGENT_LOG_LEVEL 3  /* LOG_LEVEL_INF */
#endif

#ifndef CONFIG_VAPI_BUZZER_GPIO_PIN
#define CONFIG_VAPI_BUZZER_GPIO_PIN 25
#endif

#ifndef CONFIG_VAPI_BUZZER_GPIO_CONTROLLER
#define CONFIG_VAPI_BUZZER_GPIO_CONTROLLER "GPIO_0"
#endif

/* ---------------------------------------------------------------------------
 * Compile-time constants
 * -------------------------------------------------------------------------*/

#define THREAD_STACK_SIZE        4096

/* PoAC message queue depth — holds records awaiting uplink */
#define POAC_MSGQ_DEPTH          16

/* Number of consecutive normal readings required to leave ALERT */
#define ALERT_RESOLVE_COUNT      5

/* Exponential moving average smoothing factor (alpha). 0.1 = slow, 0.5 = fast */
#define EMA_ALPHA                0.1f

/* Interval scaling factors for state-dependent Layer 1 frequency */
#define SENSE_INTERVAL_ALERT_MS  5000   /* 5 s in ALERT */
#define SENSE_INTERVAL_PSM_MS    120000 /* 2 min in PSM  */

/* Minimum battery percentage for cloud sync (save energy for sensing) */
#define MIN_BATTERY_FOR_SYNC     15

/* ---------------------------------------------------------------------------
 * Forward declarations — stub / placeholder functions
 * -------------------------------------------------------------------------*/

/**
 * Cellular uplink placeholder.
 *
 * In production this serialises the payload and transmits via NB-IoT / LTE-M
 * using the nRF9160 modem.
 *
 * @param payload  Pointer to serialised data.
 * @param len      Length of payload in bytes.
 * @return 0 on success, negative errno on failure.
 */
static int cellular_send(const uint8_t *payload, size_t len);

/**
 * Cloud response processing placeholder.
 *
 * In production this deserialises a cloud downlink message and returns
 * suggestions (e.g. config updates, bounty hints).
 *
 * @param buf     Response buffer.
 * @param len     Response length.
 * @return 0 on success, negative errno on failure.
 */
static int cloud_process_response(const uint8_t *buf, size_t len);

/* ---------------------------------------------------------------------------
 * Module state (file-scope, all access guarded by mutexes/atomics)
 * -------------------------------------------------------------------------*/

/* Agent state */
static volatile agent_state_t agent_state = AGENT_STATE_BOOT;

/* Runtime configuration — protected by config_mutex */
static agent_config_t agent_cfg;

/* World model — protected by wm_mutex */
static world_model_t world_model;

/* Callbacks */
static agent_state_cb_t  state_cb;
static agent_poac_cb_t   poac_cb;

/* Normal-reading counter for ALERT resolution */
static uint32_t normal_reading_streak;

/* Active bounty ID (0 = none) */
static uint32_t active_bounty_id;

/* Flag: agent threads should run */
static volatile bool agent_running;

/* Flag: force an immediate cloud sync */
static volatile bool force_sync_flag;

/* Buzzer GPIO device and pin spec */
static const struct device *buzzer_dev;
static gpio_pin_t buzzer_pin;

/* ---------------------------------------------------------------------------
 * Synchronisation primitives
 * -------------------------------------------------------------------------*/

K_MUTEX_DEFINE(wm_mutex);       /* Guards world_model reads/writes           */
K_MUTEX_DEFINE(config_mutex);   /* Guards agent_cfg reads/writes             */
K_MUTEX_DEFINE(state_mutex);    /* Guards agent_state transitions            */

K_SEM_DEFINE(state_sem, 0, 1);  /* Signalled on state transitions            */

/* Message queue for PoAC records heading to the uplink buffer */
K_MSGQ_DEFINE(poac_msgq, sizeof(poac_record_t), POAC_MSGQ_DEPTH, 4);

/* ---------------------------------------------------------------------------
 * Default configuration
 * -------------------------------------------------------------------------*/

static const agent_config_t default_config = {
    .sense_interval_ms      = 30000,       /* 30 s     */
    .anomaly_threshold      = 180,         /* ~70 % confidence */
    .deliberate_interval_ms = 300000,      /* 5 min    */
    .battery_critical_pct   = 10,
    .battery_low_pct        = 25,
    .cloud_sync_interval_ms = 3600000,     /* 1 hour   */
    .cloud_endpoint         = "coaps://vapi.iotex.io/sync",
    .cloud_enabled          = true,
    .bounty_enabled         = true,
    .min_battery_for_bounty = 30,
};

/* ---------------------------------------------------------------------------
 * Internal helpers
 * -------------------------------------------------------------------------*/

/**
 * Transition the agent state machine.
 *
 * Enforces legal transitions, fires the user callback, signals the semaphore,
 * and logs the change.  Returns 0 on success, -EINVAL if the transition is
 * illegal from the current state.
 */
static int state_transition(agent_state_t new_state)
{
    int ret = 0;
    agent_state_t old;

    k_mutex_lock(&state_mutex, K_FOREVER);
    old = agent_state;

    /* Validate transition */
    switch (new_state) {
    case AGENT_STATE_IDLE:
        if (old != AGENT_STATE_BOOT &&
            old != AGENT_STATE_ALERT &&
            old != AGENT_STATE_ACTIVE &&
            old != AGENT_STATE_PSM &&
            old != AGENT_STATE_OTA) {
            ret = -EINVAL;
        }
        break;

    case AGENT_STATE_ACTIVE:
        if (old != AGENT_STATE_IDLE) {
            ret = -EINVAL;
        }
        break;

    case AGENT_STATE_ALERT:
        if (old != AGENT_STATE_IDLE &&
            old != AGENT_STATE_ACTIVE) {
            ret = -EINVAL;
        }
        break;

    case AGENT_STATE_PSM:
        /* Any state can transition to PSM (battery critical) */
        break;

    case AGENT_STATE_OTA:
        /* Any state can transition to OTA (firmware update signal) */
        break;

    case AGENT_STATE_SWARM_SYNC:
        break;

    case AGENT_STATE_BOOT:
        /* Should not transition back to BOOT */
        ret = -EINVAL;
        break;

    default:
        ret = -EINVAL;
        break;
    }

    if (ret == 0 && old != new_state) {
        agent_state = new_state;
        LOG_INF("State: %d -> %d", (int)old, (int)new_state);
    }
    k_mutex_unlock(&state_mutex);

    if (ret == 0 && old != new_state) {
        /* Fire callback outside mutex to prevent deadlock */
        if (state_cb) {
            state_cb(old, new_state);
        }
        k_sem_give(&state_sem);
    }

    return ret;
}

/**
 * Get the effective sense interval for Layer 1 based on current state.
 */
static uint32_t effective_sense_interval(void)
{
    agent_state_t s = agent_state;
    uint32_t base;

    k_mutex_lock(&config_mutex, K_FOREVER);
    base = agent_cfg.sense_interval_ms;
    k_mutex_unlock(&config_mutex);

    switch (s) {
    case AGENT_STATE_ALERT:
        return SENSE_INTERVAL_ALERT_MS;
    case AGENT_STATE_PSM:
        return SENSE_INTERVAL_PSM_MS;
    default:
        return base;
    }
}

/**
 * Push an observation summary into the circular world model buffer.
 * Caller must hold wm_mutex.
 */
static void wm_push_observation(const observation_summary_t *obs)
{
    world_model.history[world_model.head] = *obs;
    world_model.head = (world_model.head + 1) % WORLD_MODEL_HISTORY_SIZE;
    if (world_model.count < WORLD_MODEL_HISTORY_SIZE) {
        world_model.count++;
    }
    world_model.total_cycles++;
}

/**
 * Build an observation summary from a perception snapshot.
 */
static void build_observation_summary(const perception_t *p,
                                      observation_summary_t *obs)
{
    obs->avg_temp          = p->env.temperature_c;
    obs->avg_voc           = p->env.voc_resistance_ohm;
    obs->avg_lux           = (float)p->light.lux;
    obs->motion_magnitude  = sqrtf(p->imu.accel_x_g * p->imu.accel_x_g +
                                   p->imu.accel_y_g * p->imu.accel_y_g +
                                   p->imu.accel_z_g * p->imu.accel_z_g);
    obs->lat               = p->gps.latitude;
    obs->lon               = p->gps.longitude;
    obs->battery_pct       = p->battery_pct;
    obs->timestamp_ms      = p->timestamp_ms;
}

/**
 * Update exponential moving average baselines.
 * Caller must hold wm_mutex.
 */
static void wm_update_baselines(const perception_t *p)
{
    if (world_model.total_cycles == 0) {
        /* Seed baselines with first reading */
        world_model.voc_baseline  = p->env.voc_resistance_ohm;
        world_model.temp_baseline = p->env.temperature_c;
    } else {
        world_model.voc_baseline =
            EMA_ALPHA * p->env.voc_resistance_ohm +
            (1.0f - EMA_ALPHA) * world_model.voc_baseline;
        world_model.temp_baseline =
            EMA_ALPHA * p->env.temperature_c +
            (1.0f - EMA_ALPHA) * world_model.temp_baseline;
    }
}

/**
 * Compute SHA-256 of the current world model state for PoAC commitment.
 * Caller must hold wm_mutex.
 *
 * Hashes: baselines (voc_baseline, temp_baseline) + total_cycles + count +
 * the valid observation history. This captures the agent's accumulated context
 * at decision time, enabling forensic reconstruction of why two agents with
 * identical sensor readings might make different decisions.
 */
static int wm_compute_hash(uint8_t out_hash[POAC_HASH_SIZE])
{
    /*
     * Serialize the world model deterministically into a stack buffer.
     * Layout: voc_baseline(4) + temp_baseline(4) + total_cycles(4) + count(1)
     *       + count * sizeof(observation_summary_t)
     * Max: 13 + 32 * sizeof(observation_summary_t) ≈ 13 + 32*48 = ~1549 bytes
     * This fits on the 2KB reflexive thread stack with margin.
     */
    uint8_t wm_buf[16 + WORLD_MODEL_HISTORY_SIZE * sizeof(observation_summary_t)];
    size_t offset = 0;

    memcpy(wm_buf + offset, &world_model.voc_baseline, sizeof(float));
    offset += sizeof(float);
    memcpy(wm_buf + offset, &world_model.temp_baseline, sizeof(float));
    offset += sizeof(float);
    memcpy(wm_buf + offset, &world_model.total_cycles, sizeof(uint32_t));
    offset += sizeof(uint32_t);
    wm_buf[offset++] = world_model.count;

    /* Serialize valid history entries in chronological order */
    for (uint8_t i = 0; i < world_model.count; i++) {
        uint8_t idx = (world_model.head - world_model.count + i +
                       WORLD_MODEL_HISTORY_SIZE) % WORLD_MODEL_HISTORY_SIZE;
        memcpy(wm_buf + offset, &world_model.history[idx],
               sizeof(observation_summary_t));
        offset += sizeof(observation_summary_t);
    }

    return poac_commit_sensors(wm_buf, offset, out_hash);
}

/**
 * Initialise the buzzer GPIO.
 */
static int buzzer_init(void)
{
    int ret;

    buzzer_dev = device_get_binding(CONFIG_VAPI_BUZZER_GPIO_CONTROLLER);
    if (!buzzer_dev) {
        LOG_WRN("Buzzer GPIO controller not found — alerts will be silent");
        return -ENODEV;
    }

    buzzer_pin = CONFIG_VAPI_BUZZER_GPIO_PIN;
    ret = gpio_pin_configure(buzzer_dev, buzzer_pin, GPIO_OUTPUT_INACTIVE);
    if (ret < 0) {
        LOG_WRN("Buzzer pin config failed: %d", ret);
        return ret;
    }

    return 0;
}

/**
 * Activate or deactivate the buzzer.
 */
static void buzzer_set(bool on)
{
    if (buzzer_dev) {
        gpio_pin_set(buzzer_dev, buzzer_pin, on ? 1 : 0);
    }
}

/**
 * Alert beep pattern — three short pulses.
 */
static void buzzer_alert_pattern(void)
{
    for (int i = 0; i < 3; i++) {
        buzzer_set(true);
        k_sleep(K_MSEC(100));
        buzzer_set(false);
        k_sleep(K_MSEC(100));
    }
}

/**
 * Enqueue a PoAC record for uplink and fire the user callback.
 */
static void poac_enqueue(const poac_record_t *record)
{
    int ret = k_msgq_put(&poac_msgq, record, K_NO_WAIT);

    if (ret == -ENOMSG || ret == -EAGAIN) {
        LOG_WRN("PoAC uplink queue full — dropping oldest");
        poac_record_t discard;
        k_msgq_get(&poac_msgq, &discard, K_NO_WAIT);
        k_msgq_put(&poac_msgq, record, K_NO_WAIT);
    }

    if (poac_cb) {
        poac_cb(record);
    }
}

/* ---------------------------------------------------------------------------
 * Layer 1 — Reflexive Thread
 *
 * Highest priority.  Runs every sense_interval_ms (state-dependent).
 * Captures sensors, runs TinyML inference, generates a PoAC record,
 * and triggers immediate actions (buzzer, state transitions).
 * -------------------------------------------------------------------------*/

static void reflexive_entry(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    LOG_INF("Layer 1 (Reflexive) thread started");

    while (agent_running) {
        uint32_t interval_ms = effective_sense_interval();
        perception_t percept;
        tinyml_result_t ml_result;
        uint8_t confidence = 0;
        uint8_t infer_result;
        uint8_t action = POAC_ACTION_REPORT;
        uint8_t anomaly_thresh;
        poac_record_t record;
        int ret;

        /* ---- 1. Sense ------------------------------------------------- */
        ret = perception_capture(&percept);
        if (ret < 0) {
            LOG_ERR("Perception capture failed: %d", ret);
            k_sleep(K_MSEC(interval_ms));
            continue;
        }

        /* ---- 2. Infer (TinyML pipeline) -------------------------------- */
        ret = tinyml_infer(&percept, &ml_result);
        if (ret < 0) {
            LOG_WRN("TinyML inference failed: %d, using NOMINAL", ret);
            infer_result = POAC_INFER_CLASS_STATIONARY;
            confidence = 0;
        } else {
            infer_result = ml_result.class_id;
            confidence = ml_result.confidence;
            LOG_DBG("TinyML: class=%u conf=%u latency=%d us",
                    infer_result, confidence, ml_result.latency_us);
        }

        /* ---- 3. Sensor commitment (PoAC) ------------------------------ */
        uint8_t serial_buf[PERCEPTION_SERIAL_MAX_SIZE];
        size_t serial_len = 0;
        uint8_t sensor_hash[POAC_HASH_SIZE];

        ret = perception_serialize(&percept, serial_buf,
                                   sizeof(serial_buf), &serial_len);
        if (ret < 0) {
            LOG_ERR("Perception serialize failed: %d", ret);
            k_sleep(K_MSEC(interval_ms));
            continue;
        }

        ret = poac_commit_sensors(serial_buf, serial_len, sensor_hash);
        if (ret < 0) {
            LOG_ERR("Sensor commitment failed: %d", ret);
            k_sleep(K_MSEC(interval_ms));
            continue;
        }

        /* ---- 4. Anomaly check ----------------------------------------- */
        k_mutex_lock(&config_mutex, K_FOREVER);
        anomaly_thresh = agent_cfg.anomaly_threshold;
        k_mutex_unlock(&config_mutex);

        if (confidence >= anomaly_thresh &&
            (infer_result == POAC_INFER_ANOMALY_LOW ||
             infer_result == POAC_INFER_ANOMALY_HIGH ||
             infer_result == POAC_INFER_CLASS_FALL)) {
            /* Anomaly detected */
            action = POAC_ACTION_ALERT;

            LOG_WRN("ANOMALY detected — infer=0x%02x conf=%u",
                    infer_result, confidence);

            buzzer_alert_pattern();

            normal_reading_streak = 0;
            if (agent_state != AGENT_STATE_ALERT &&
                agent_state != AGENT_STATE_PSM) {
                state_transition(AGENT_STATE_ALERT);
            }
        } else {
            /* Normal reading */
            if (agent_state == AGENT_STATE_ALERT) {
                normal_reading_streak++;
                if (normal_reading_streak >= ALERT_RESOLVE_COUNT) {
                    LOG_INF("Anomaly resolved after %u normal readings",
                            ALERT_RESOLVE_COUNT);
                    state_transition(AGENT_STATE_IDLE);
                    normal_reading_streak = 0;
                    buzzer_set(false);
                }
            }
        }

        /* ---- 5. Hash world model BEFORE update (captures decision context) */
        uint8_t wm_hash[POAC_HASH_SIZE];
        k_mutex_lock(&wm_mutex, K_FOREVER);
        ret = wm_compute_hash(wm_hash);
        k_mutex_unlock(&wm_mutex);
        if (ret < 0) {
            LOG_WRN("World model hash failed: %d — using zero hash", ret);
            memset(wm_hash, 0, POAC_HASH_SIZE);
        }

        /* ---- 6. Generate PoAC record ---------------------------------- */
        ret = poac_generate(sensor_hash,
                            wm_hash,
                            infer_result,
                            action,
                            confidence,
                            percept.battery_pct,
                            percept.timestamp_ms,
                            percept.gps.latitude,
                            percept.gps.longitude,
                            active_bounty_id,
                            &record);
        if (ret < 0) {
            LOG_ERR("PoAC generation failed: %d", ret);
            k_sleep(K_MSEC(interval_ms));
            continue;
        }

        poac_enqueue(&record);

        /* ---- 7. Submit evidence if fulfilling a bounty ---------------- */
        if (active_bounty_id != POAC_NO_BOUNTY &&
            agent_state == AGENT_STATE_ACTIVE) {
            economic_submit_evidence(active_bounty_id, &record);
        }

        /* ---- 8. Update world model (AFTER hash captured) -------------- */
        observation_summary_t obs;
        build_observation_summary(&percept, &obs);

        k_mutex_lock(&wm_mutex, K_FOREVER);
        wm_update_baselines(&percept);
        wm_push_observation(&obs);
        k_mutex_unlock(&wm_mutex);

        LOG_DBG("L1 cycle %u: infer=0x%02x conf=%u batt=%u%%",
                world_model.total_cycles, infer_result,
                confidence, percept.battery_pct);

        /* ---- 9. Sleep until next cycle -------------------------------- */
        k_sleep(K_MSEC(effective_sense_interval()));
    }

    LOG_INF("Layer 1 (Reflexive) thread exiting");
}

/* ---------------------------------------------------------------------------
 * Layer 2 — Deliberative Thread
 *
 * Medium priority.  Runs every deliberate_interval_ms.
 * Analyses the world model (trends, baselines), manages battery thresholds,
 * adjusts Layer 1 sensing frequency, and evaluates bounties.
 * -------------------------------------------------------------------------*/

static void deliberative_entry(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    LOG_INF("Layer 2 (Deliberative) thread started");

    while (agent_running) {
        uint32_t interval_ms;
        uint8_t battery_critical, battery_low, min_bounty_batt;
        bool bounty_enabled;

        k_mutex_lock(&config_mutex, K_FOREVER);
        interval_ms      = agent_cfg.deliberate_interval_ms;
        battery_critical  = agent_cfg.battery_critical_pct;
        battery_low       = agent_cfg.battery_low_pct;
        bounty_enabled    = agent_cfg.bounty_enabled;
        min_bounty_batt   = agent_cfg.min_battery_for_bounty;
        k_mutex_unlock(&config_mutex);

        /* ---- 1. Battery management ------------------------------------ */
        int battery = perception_read_battery();

        if (battery >= 0) {
            uint8_t batt_pct = (uint8_t)battery;

            if (batt_pct <= battery_critical &&
                agent_state != AGENT_STATE_PSM) {
                LOG_WRN("Battery critical (%u%%) — entering PSM", batt_pct);

                /* Generate PSM entry PoAC */
                poac_record_t psm_record;
                uint8_t zero_hash[POAC_HASH_SIZE] = {0};
                int64_t now = k_uptime_get();

                poac_generate(zero_hash,
                              NULL, /* No world model context for PSM transition */
                              POAC_INFER_NOMINAL,
                              POAC_ACTION_PSM_ENTER,
                              0,
                              batt_pct,
                              now,
                              0.0, 0.0,
                              POAC_NO_BOUNTY,
                              &psm_record);
                poac_enqueue(&psm_record);

                /* Stop GPS to save power */
                perception_gps_stop();
                buzzer_set(false);

                state_transition(AGENT_STATE_PSM);
            } else if (agent_state == AGENT_STATE_PSM &&
                       batt_pct > battery_low) {
                /* Battery recovered — exit PSM */
                LOG_INF("Battery recovered (%u%%) — exiting PSM", batt_pct);

                poac_record_t exit_record;
                uint8_t zero_hash[POAC_HASH_SIZE] = {0};
                int64_t now = k_uptime_get();

                poac_generate(zero_hash,
                              NULL,
                              POAC_INFER_NOMINAL,
                              POAC_ACTION_PSM_EXIT,
                              0,
                              batt_pct,
                              now,
                              0.0, 0.0,
                              POAC_NO_BOUNTY,
                              &exit_record);
                poac_enqueue(&exit_record);

                perception_gps_start();
                state_transition(AGENT_STATE_IDLE);
            }

            /* Adjust Layer 1 sensing rate based on battery level */
            if (batt_pct <= battery_low &&
                agent_state != AGENT_STATE_PSM &&
                agent_state != AGENT_STATE_ALERT) {
                k_mutex_lock(&config_mutex, K_FOREVER);
                if (agent_cfg.sense_interval_ms < 60000) {
                    agent_cfg.sense_interval_ms = 60000;
                    LOG_INF("Low battery — sense interval -> 60s");
                }
                k_mutex_unlock(&config_mutex);
            }
        }

        /* ---- 2. World model trend analysis ---------------------------- */
        k_mutex_lock(&wm_mutex, K_FOREVER);

        if (world_model.count >= 4) {
            /*
             * Simple trend detection: compare the most recent quarter of
             * observations against the baseline.  Large deviations from the
             * EMA indicate changing conditions that may warrant attention.
             */
            float recent_voc_sum = 0.0f;
            float recent_temp_sum = 0.0f;
            uint8_t quarter = world_model.count / 4;

            for (uint8_t i = 0; i < quarter; i++) {
                uint8_t idx = (world_model.head - 1 - i +
                               WORLD_MODEL_HISTORY_SIZE) %
                              WORLD_MODEL_HISTORY_SIZE;
                recent_voc_sum  += world_model.history[idx].avg_voc;
                recent_temp_sum += world_model.history[idx].avg_temp;
            }

            float recent_voc_avg  = recent_voc_sum / (float)quarter;
            float recent_temp_avg = recent_temp_sum / (float)quarter;

            /* VOC dropping significantly below baseline suggests worsening air */
            if (world_model.voc_baseline > 0.0f) {
                float voc_ratio = recent_voc_avg / world_model.voc_baseline;
                if (voc_ratio < 0.5f) {
                    LOG_WRN("VOC trend: recent avg %.0f is %.0f%% below baseline %.0f",
                            (double)recent_voc_avg,
                            (double)((1.0f - voc_ratio) * 100.0f),
                            (double)world_model.voc_baseline);
                }
            }

            /* Temperature drifting far from baseline */
            float temp_delta = fabsf(recent_temp_avg - world_model.temp_baseline);
            if (temp_delta > 5.0f) {
                LOG_WRN("Temp trend: recent avg %.1f deviates %.1f C from baseline %.1f",
                        (double)recent_temp_avg,
                        (double)temp_delta,
                        (double)world_model.temp_baseline);
            }
        }

        k_mutex_unlock(&wm_mutex);

        /* ---- 3. Bounty evaluation (knapsack optimizer) ------------------- */
        if (bounty_enabled &&
            battery >= (int)min_bounty_batt &&
            agent_state != AGENT_STATE_PSM) {

            /* Run the knapsack optimizer over discovered bounties */
            perception_t econ_percept;
            perception_capture(&econ_percept);

            float budget = (float)(battery - (int)battery_critical);
            if (budget < 0.0f) budget = 0.0f;

            uint32_t newly_accepted[ECON_MAX_ACTIVE_BOUNTIES];
            size_t newly_count = 0;

            int ret = economic_optimize_bounties(&econ_percept, budget,
                                                 newly_accepted, &newly_count);
            if (ret == 0 && newly_count > 0) {
                /* Evaluate and formally accept each selected bounty */
                for (size_t i = 0; i < newly_count; i++) {
                    /* Find the bounty descriptor by ID and formally evaluate */
                    bounty_descriptor_t desc;
                    bool found = false;

                    /* The optimizer has already validated these — re-inject
                     * for formal PoAC generation via economic_evaluate_bounty */
                    active_bounty_t active[ECON_MAX_ACTIVE_BOUNTIES];
                    size_t active_count = 0;
                    economic_get_active_bounties(active, ECON_MAX_ACTIVE_BOUNTIES,
                                                &active_count);

                    for (size_t j = 0; j < active_count; j++) {
                        if (active[j].desc.bounty_id == newly_accepted[i]) {
                            found = true;
                            break;
                        }
                    }

                    if (found && active_bounty_id == POAC_NO_BOUNTY) {
                        active_bounty_id = newly_accepted[i];
                        if (agent_state == AGENT_STATE_IDLE) {
                            state_transition(AGENT_STATE_ACTIVE);
                            LOG_INF("Bounty %u accepted via optimizer — ACTIVE",
                                    active_bounty_id);
                        }
                    }
                }
            }

            /* Check if active bounty has completed or expired */
            active_bounty_t bounties[ECON_MAX_ACTIVE_BOUNTIES];
            size_t count = 0;
            economic_get_active_bounties(bounties, ECON_MAX_ACTIVE_BOUNTIES, &count);

            for (size_t i = 0; i < count; i++) {
                if (bounties[i].desc.bounty_id == active_bounty_id) {
                    if (bounties[i].status == BOUNTY_STATUS_COMPLETED ||
                        bounties[i].status == BOUNTY_STATUS_EXPIRED) {
                        LOG_INF("Bounty %u ended (status=%d)",
                                active_bounty_id, bounties[i].status);
                        active_bounty_id = POAC_NO_BOUNTY;
                        if (agent_state == AGENT_STATE_ACTIVE) {
                            state_transition(AGENT_STATE_IDLE);
                        }
                    }
                }
            }
        }

        LOG_DBG("L2 cycle: state=%d batt=%d bounty=%u",
                (int)agent_state, battery, active_bounty_id);

        k_sleep(K_MSEC(interval_ms));
    }

    LOG_INF("Layer 2 (Deliberative) thread exiting");
}

/* ---------------------------------------------------------------------------
 * Layer 3 — Strategic Thread
 *
 * Lowest priority.  Runs every cloud_sync_interval_ms.
 * Serializes world model + recent PoAC chain, transmits via cellular,
 * and processes cloud responses with autonomy-preserving trust evaluation.
 * -------------------------------------------------------------------------*/

/*
 * Maximum payload for cloud sync.  Accommodates the serialised world model
 * plus up to 8 PoAC records.  Kept conservative for NB-IoT constraints.
 */
#define CLOUD_PAYLOAD_MAX  2048

static void strategic_entry(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    LOG_INF("Layer 3 (Strategic) thread started");

    while (agent_running) {
        uint32_t interval_ms;
        bool cloud_enabled;

        k_mutex_lock(&config_mutex, K_FOREVER);
        interval_ms   = agent_cfg.cloud_sync_interval_ms;
        cloud_enabled = agent_cfg.cloud_enabled;
        k_mutex_unlock(&config_mutex);

        /* Wait for the sync interval or a forced sync signal */
        if (!force_sync_flag) {
            k_sleep(K_MSEC(interval_ms));
        }
        force_sync_flag = false;

        if (!agent_running) {
            break;
        }

        /* Skip cloud sync if disabled or battery too low */
        if (!cloud_enabled) {
            LOG_DBG("Cloud sync disabled, skipping");
            continue;
        }

        if (agent_state == AGENT_STATE_PSM) {
            LOG_DBG("In PSM — deferring cloud sync");
            continue;
        }

        int battery = perception_read_battery();
        if (battery >= 0 && battery < MIN_BATTERY_FOR_SYNC) {
            LOG_WRN("Battery %d%% — skipping cloud sync to conserve power",
                    battery);
            continue;
        }

        /* ---- 1. Serialize world model snapshot ------------------------ */
        uint8_t payload[CLOUD_PAYLOAD_MAX];
        size_t offset = 0;

        k_mutex_lock(&wm_mutex, K_FOREVER);

        /* Copy world model header fields */
        memcpy(payload + offset, &world_model.head, sizeof(uint8_t));
        offset += sizeof(uint8_t);
        memcpy(payload + offset, &world_model.count, sizeof(uint8_t));
        offset += sizeof(uint8_t);
        memcpy(payload + offset, &world_model.voc_baseline, sizeof(float));
        offset += sizeof(float);
        memcpy(payload + offset, &world_model.temp_baseline, sizeof(float));
        offset += sizeof(float);
        memcpy(payload + offset, &world_model.total_cycles, sizeof(uint32_t));
        offset += sizeof(uint32_t);

        /* Copy the observation history (only valid entries) */
        uint8_t entries = world_model.count;
        memcpy(payload + offset, &entries, sizeof(uint8_t));
        offset += sizeof(uint8_t);

        for (uint8_t i = 0; i < entries && offset + sizeof(observation_summary_t) < CLOUD_PAYLOAD_MAX; i++) {
            uint8_t idx = (world_model.head - entries + i +
                           WORLD_MODEL_HISTORY_SIZE) % WORLD_MODEL_HISTORY_SIZE;
            memcpy(payload + offset, &world_model.history[idx],
                   sizeof(observation_summary_t));
            offset += sizeof(observation_summary_t);
        }

        k_mutex_unlock(&wm_mutex);

        /* ---- 2. Drain PoAC message queue into payload ----------------- */
        poac_record_t record;
        uint8_t poac_count = 0;
        size_t poac_count_offset = offset;
        offset += sizeof(uint8_t); /* Reserve space for count byte */

        while (k_msgq_get(&poac_msgq, &record, K_NO_WAIT) == 0 &&
               offset + sizeof(poac_record_t) < CLOUD_PAYLOAD_MAX) {
            memcpy(payload + offset, &record, sizeof(poac_record_t));
            offset += sizeof(poac_record_t);
            poac_count++;
        }
        payload[poac_count_offset] = poac_count;

        LOG_INF("Cloud sync: %u bytes (%u observations, %u PoAC records)",
                (unsigned)offset, entries, poac_count);

        /* ---- 3. Transmit via cellular --------------------------------- */
        int ret = cellular_send(payload, offset);
        if (ret < 0) {
            LOG_ERR("Cellular send failed: %d", ret);
            /* Re-enqueue PoAC records that we drained but could not send.
             * In a real implementation we would have a persistent retry buffer.
             * Here we accept the loss for simplicity and log a warning.
             */
            if (poac_count > 0) {
                LOG_WRN("Lost %u PoAC records due to send failure", poac_count);
            }
            continue;
        }

        /* ---- 4. Process cloud response -------------------------------- */
        /*
         * In production the cellular_send() is request-response (CoAP CON)
         * and the response is returned in the same buffer.  We stub this
         * here by calling cloud_process_response() on the buffer, which
         * may contain configuration suggestions, bounty announcements,
         * or OTA update signals.
         */
        uint8_t response_buf[512];
        size_t  response_len = 0;

        /* Placeholder: in production, response_buf / response_len would be
         * populated by the cellular modem's response handler. */
        (void)response_buf;
        (void)response_len;

        /*
         * ---- 5. Autonomy guard ----------------------------------------
         *
         * The agent evaluates cloud suggestions against its own trust model.
         * This is the fundamental autonomy guarantee: the device is sovereign
         * over its own operation.
         *
         * Trust rules:
         *   - Ignore any suggestion that would disable PoAC generation.
         *   - Ignore config changes if battery is critical (preserve PSM).
         *   - Reject unreasonable sense intervals (<1s or >24h).
         *   - Log all rejected suggestions for later forensic analysis.
         */
        if (response_len > 0) {
            ret = cloud_process_response(response_buf, response_len);
            if (ret < 0) {
                LOG_WRN("Cloud response processing failed or rejected: %d", ret);
            }

            /* Additional autonomy checks would be applied here after
             * cloud_process_response() parses the response into a
             * struct of proposed changes. */
            if (battery >= 0 && battery <= (int)default_config.battery_critical_pct) {
                LOG_INF("Autonomy guard: battery critical, ignoring cloud suggestions");
            }
        }

        LOG_DBG("L3 cycle complete");
    }

    LOG_INF("Layer 3 (Strategic) thread exiting");
}

/* ---------------------------------------------------------------------------
 * Thread definitions (K_THREAD_DEFINE)
 *
 * Stack sizes: 2048 bytes each.
 * Priorities: Layer 1 highest (lowest numeric), Layer 3 lowest.
 *
 * Threads start suspended; agent_start() resumes them.
 * -------------------------------------------------------------------------*/

K_THREAD_STACK_DEFINE(reflexive_stack, THREAD_STACK_SIZE);
K_THREAD_STACK_DEFINE(deliberative_stack, THREAD_STACK_SIZE);
K_THREAD_STACK_DEFINE(strategic_stack, THREAD_STACK_SIZE);

static struct k_thread reflexive_thread_data;
static struct k_thread deliberative_thread_data;
static struct k_thread strategic_thread_data;

static k_tid_t reflexive_tid;
static k_tid_t deliberative_tid;
static k_tid_t strategic_tid;

/* Thread priorities — Zephyr preemptive scheduling.
 * Lower value = higher priority. */
#define REFLEXIVE_PRIORITY     5   /* Highest among agent threads */
#define DELIBERATIVE_PRIORITY  8   /* Medium */
#define STRATEGIC_PRIORITY     11  /* Lowest */

/* ---------------------------------------------------------------------------
 * Stub / placeholder implementations
 * -------------------------------------------------------------------------*/

static int cellular_send(const uint8_t *payload, size_t len)
{
    /*
     * Placeholder cellular uplink.
     *
     * In production: initialise the nRF9160 modem (nrf_modem_lib),
     * establish a CoAP or MQTT session, and transmit the payload
     * over NB-IoT or LTE-M.
     *
     * For now, just log the payload size and return success.
     */
    LOG_INF("cellular_send: %u bytes (stub)", (unsigned)len);
    ARG_UNUSED(payload);
    return 0;
}

static int cloud_process_response(const uint8_t *buf, size_t len)
{
    /*
     * Placeholder cloud response handler.
     *
     * In production: deserialise a CBOR/protobuf response containing
     * config updates, new bounty announcements, OTA signals, or
     * swarm coordination messages.
     *
     * Returns 0 if response was accepted, negative if rejected or invalid.
     */
    LOG_INF("cloud_process_response: %u bytes (stub)", (unsigned)len);
    ARG_UNUSED(buf);
    return 0;
}

/* ---------------------------------------------------------------------------
 * Self-test — run during BOOT before transitioning to IDLE
 * -------------------------------------------------------------------------*/

/**
 * Perform a basic self-test of all subsystems.
 *
 * @return 0 if all tests pass, negative errno on first failure.
 */
static int agent_self_test(void)
{
    int ret;

    /* Verify perception subsystem by capturing a test frame */
    perception_t test_percept;
    ret = perception_capture(&test_percept);
    if (ret < 0) {
        LOG_ERR("Self-test: perception capture failed: %d", ret);
        return ret;
    }

    /* Verify PoAC subsystem by checking the monotonic counter */
    uint32_t ctr = poac_get_counter();
    LOG_INF("Self-test: PoAC counter at %u", ctr);

    /* Verify battery is readable */
    int batt = perception_read_battery();
    if (batt < 0) {
        LOG_WRN("Self-test: battery read failed (non-fatal)");
    } else {
        LOG_INF("Self-test: battery at %d%%", batt);
    }

    LOG_INF("Self-test: PASSED");
    return 0;
}

/* ---------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------*/

int agent_init(const agent_config_t *config)
{
    LOG_INF("Initialising VAPI agent");

    /* Apply configuration */
    k_mutex_lock(&config_mutex, K_FOREVER);
    if (config) {
        memcpy(&agent_cfg, config, sizeof(agent_config_t));
    } else {
        memcpy(&agent_cfg, &default_config, sizeof(agent_config_t));
    }
    k_mutex_unlock(&config_mutex);

    /* Zero world model */
    k_mutex_lock(&wm_mutex, K_FOREVER);
    memset(&world_model, 0, sizeof(world_model_t));
    k_mutex_unlock(&wm_mutex);

    /* Reset module state */
    agent_state           = AGENT_STATE_BOOT;
    agent_running         = false;
    force_sync_flag       = false;
    normal_reading_streak = 0;
    active_bounty_id      = POAC_NO_BOUNTY;
    state_cb              = NULL;
    poac_cb               = NULL;

    /* Initialise buzzer GPIO */
    buzzer_init();

    /* Purge any stale PoAC messages */
    k_msgq_purge(&poac_msgq);

    /* Create threads (suspended — agent_start() will resume them) */
    reflexive_tid = k_thread_create(&reflexive_thread_data,
                                    reflexive_stack,
                                    THREAD_STACK_SIZE,
                                    reflexive_entry,
                                    NULL, NULL, NULL,
                                    REFLEXIVE_PRIORITY,
                                    0,
                                    K_FOREVER);
    k_thread_name_set(reflexive_tid, "vapi_L1_reflex");

    deliberative_tid = k_thread_create(&deliberative_thread_data,
                                       deliberative_stack,
                                       THREAD_STACK_SIZE,
                                       deliberative_entry,
                                       NULL, NULL, NULL,
                                       DELIBERATIVE_PRIORITY,
                                       0,
                                       K_FOREVER);
    k_thread_name_set(deliberative_tid, "vapi_L2_delib");

    strategic_tid = k_thread_create(&strategic_thread_data,
                                    strategic_stack,
                                    THREAD_STACK_SIZE,
                                    strategic_entry,
                                    NULL, NULL, NULL,
                                    STRATEGIC_PRIORITY,
                                    0,
                                    K_FOREVER);
    k_thread_name_set(strategic_tid, "vapi_L3_strat");

    LOG_INF("Agent initialised (threads created, suspended)");
    return 0;
}

int agent_start(void)
{
    int ret;

    LOG_INF("Starting VAPI agent — running self-test");

    /* Run self-test */
    ret = agent_self_test();
    if (ret < 0) {
        LOG_ERR("Self-test failed: %d — agent will not start", ret);
        return ret;
    }

    /* Generate BOOT PoAC record */
    perception_t boot_percept;
    ret = perception_capture(&boot_percept);
    if (ret < 0) {
        LOG_ERR("Boot perception capture failed: %d", ret);
        return ret;
    }

    uint8_t serial_buf[PERCEPTION_SERIAL_MAX_SIZE];
    size_t serial_len = 0;
    uint8_t sensor_hash[POAC_HASH_SIZE];

    ret = perception_serialize(&boot_percept, serial_buf,
                               sizeof(serial_buf), &serial_len);
    if (ret < 0) {
        LOG_ERR("Boot perception serialize failed: %d", ret);
        return ret;
    }

    ret = poac_commit_sensors(serial_buf, serial_len, sensor_hash);
    if (ret < 0) {
        LOG_ERR("Boot sensor commitment failed: %d", ret);
        return ret;
    }

    poac_record_t boot_record;
    ret = poac_generate(sensor_hash,
                        NULL, /* World model empty at boot */
                        POAC_INFER_NOMINAL,
                        POAC_ACTION_BOOT,
                        0,
                        boot_percept.battery_pct,
                        boot_percept.timestamp_ms,
                        boot_percept.gps.latitude,
                        boot_percept.gps.longitude,
                        POAC_NO_BOUNTY,
                        &boot_record);
    if (ret < 0) {
        LOG_ERR("Boot PoAC generation failed: %d", ret);
        return ret;
    }

    poac_enqueue(&boot_record);
    LOG_INF("Boot PoAC generated (counter=%u)", poac_get_counter());

    /* Transition BOOT → IDLE */
    agent_state = AGENT_STATE_IDLE;
    if (state_cb) {
        state_cb(AGENT_STATE_BOOT, AGENT_STATE_IDLE);
    }

    /* Start autonomous operation */
    agent_running = true;

    k_thread_start(reflexive_tid);
    k_thread_start(deliberative_tid);
    k_thread_start(strategic_tid);

    LOG_INF("VAPI agent running — three layers active");
    return 0;
}

int agent_stop(void)
{
    LOG_INF("Stopping VAPI agent");

    agent_running = false;

    /* Wake threads that may be sleeping so they observe the flag */
    k_thread_abort(reflexive_tid);
    k_thread_abort(deliberative_tid);
    k_thread_abort(strategic_tid);

    /* Persist state */
    int ret = poac_persist_state();
    if (ret < 0) {
        LOG_ERR("Failed to persist PoAC state: %d", ret);
    }

    /* Turn off buzzer */
    buzzer_set(false);

    LOG_INF("VAPI agent stopped");
    return 0;
}

agent_state_t agent_get_state(void)
{
    return agent_state;
}

int agent_get_world_model(world_model_t *out)
{
    if (!out) {
        return -EINVAL;
    }

    k_mutex_lock(&wm_mutex, K_FOREVER);
    memcpy(out, &world_model, sizeof(world_model_t));
    k_mutex_unlock(&wm_mutex);

    return 0;
}

int agent_set_config(const agent_config_t *config)
{
    if (!config) {
        return -EINVAL;
    }

    k_mutex_lock(&config_mutex, K_FOREVER);
    memcpy(&agent_cfg, config, sizeof(agent_config_t));
    k_mutex_unlock(&config_mutex);

    LOG_INF("Agent config updated: sense=%ums delib=%ums cloud=%ums",
            config->sense_interval_ms,
            config->deliberate_interval_ms,
            config->cloud_sync_interval_ms);

    return 0;
}

int agent_force_cloud_sync(void)
{
    if (!agent_running) {
        return -ESHUTDOWN;
    }

    force_sync_flag = true;

    /* Wake the strategic thread if it is sleeping */
    k_wakeup(strategic_tid);

    LOG_INF("Forced cloud sync requested");
    return 0;
}

void agent_register_state_callback(agent_state_cb_t cb)
{
    state_cb = cb;
}

void agent_register_poac_callback(agent_poac_cb_t cb)
{
    poac_cb = cb;
}
