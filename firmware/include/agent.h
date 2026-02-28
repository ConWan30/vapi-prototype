/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAPI Agent Core — Three-Layer Autonomous Agent Architecture
 *
 * Layer 1 (Reflexive):   Real-time sensor → TinyML → immediate action (<100ms)
 * Layer 2 (Deliberative): Periodic goal evaluation, planning, battery mgmt (30s-5min)
 * Layer 3 (Strategic):    Async cloud sync for long-horizon planning (1-6 hours)
 *
 * Each layer runs as a separate Zephyr thread. All decisions produce PoAC records.
 *
 * Target: IoTeX Pebble Tracker (nRF9160, Zephyr RTOS)
 */

#ifndef AGENT_H
#define AGENT_H

#include <stdint.h>
#include <stdbool.h>
#include <zephyr/kernel.h>
#include "perception.h"
#include "poac.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Agent state machine states */
typedef enum {
    AGENT_STATE_BOOT,          /* Initializing, self-test */
    AGENT_STATE_IDLE,          /* Nominal sensing, low duty cycle */
    AGENT_STATE_ACTIVE,        /* Active monitoring (bounty in progress) */
    AGENT_STATE_ALERT,         /* Anomaly detected, high-frequency sensing */
    AGENT_STATE_PSM,           /* Power save mode (deep sleep between cycles) */
    AGENT_STATE_OTA,           /* Receiving over-the-air update */
    AGENT_STATE_SWARM_SYNC,    /* Swarm coordination active */
} agent_state_t;

/* Agent configuration — tunable at runtime */
typedef struct {
    /* Layer 1: Reflexive */
    uint32_t sense_interval_ms;     /* Base sensing interval (default: 30000) */
    uint8_t  anomaly_threshold;     /* Confidence threshold for anomaly alert [0-255] */

    /* Layer 2: Deliberative */
    uint32_t deliberate_interval_ms; /* Goal evaluation period (default: 300000 = 5min) */
    uint8_t  battery_critical_pct;   /* Below this → force PSM (default: 10) */
    uint8_t  battery_low_pct;        /* Below this → reduce sensing (default: 25) */

    /* Layer 3: Strategic */
    uint32_t cloud_sync_interval_ms; /* Cloud upload period (default: 3600000 = 1hr) */
    char     cloud_endpoint[128];    /* CoAP/MQTT endpoint for strategic sync */
    bool     cloud_enabled;          /* Enable/disable cloud sync */

    /* Economic */
    bool     bounty_enabled;         /* Enable autonomous bounty evaluation */
    uint8_t  min_battery_for_bounty; /* Don't accept bounties below this % */
} agent_config_t;

/**
 * Compressed world model — recent observation history for deliberation.
 *
 * Circular buffer of summarized observations (~2 KB total).
 * Used by Layer 2 for temporal reasoning (trend detection, baseline comparison).
 */
#define WORLD_MODEL_HISTORY_SIZE  32  /* Number of observation summaries kept */

typedef struct {
    float    avg_temp;
    float    avg_voc;
    float    avg_lux;
    float    motion_magnitude;  /* RMS of accel vector */
    double   lat, lon;
    uint8_t  battery_pct;
    int64_t  timestamp_ms;
} observation_summary_t;

typedef struct {
    observation_summary_t history[WORLD_MODEL_HISTORY_SIZE];
    uint8_t  head;            /* Circular buffer write index */
    uint8_t  count;           /* Number of valid entries */
    float    voc_baseline;    /* Running VOC baseline for anomaly detection */
    float    temp_baseline;   /* Running temperature baseline */
    uint32_t total_cycles;    /* Total agent cycles since boot */
} world_model_t;

/**
 * Initialize the agent subsystem.
 *
 * Starts all three layer threads with default configuration.
 * Must be called after poac_init() and perception_init().
 *
 * @param config  Agent configuration. NULL for defaults.
 * @return 0 on success, negative errno on failure.
 */
int agent_init(const agent_config_t *config);

/**
 * Start the agent (begins autonomous operation).
 *
 * Generates a BOOT PoAC record and transitions to IDLE state.
 */
int agent_start(void);

/**
 * Stop the agent gracefully.
 *
 * Persists state (PoAC chain, world model, counter) and stops threads.
 */
int agent_stop(void);

/**
 * Get current agent state.
 */
agent_state_t agent_get_state(void);

/**
 * Get a copy of the current world model (for diagnostics / cloud sync).
 */
int agent_get_world_model(world_model_t *out);

/**
 * Update agent configuration at runtime.
 *
 * Thread-safe. Takes effect on the next cycle of each layer.
 */
int agent_set_config(const agent_config_t *config);

/**
 * Force a cloud sync (Layer 3) immediately.
 *
 * Useful when the user triggers a manual upload via button/app.
 */
int agent_force_cloud_sync(void);

/**
 * Register a callback for agent state changes.
 *
 * @param cb  Callback function (called from agent thread context).
 */
typedef void (*agent_state_cb_t)(agent_state_t old_state, agent_state_t new_state);
void agent_register_state_callback(agent_state_cb_t cb);

/**
 * Register a callback for completed PoAC records.
 *
 * Called after each record is generated and signed. Use this to
 * buffer records for cellular uplink.
 *
 * @param cb  Callback function.
 */
typedef void (*agent_poac_cb_t)(const poac_record_t *record);
void agent_register_poac_callback(agent_poac_cb_t cb);

#ifdef __cplusplus
}
#endif

#endif /* AGENT_H */
