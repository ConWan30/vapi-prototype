/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAPI DualShock Agent — Gaming-Adapted Three-Layer Cognitive Architecture
 *
 * Direct port of the Pebble Tracker VAPI agent (agent.h) adapted for
 * gaming anti-cheat on a DualSense Edge controller with ESP32-S3 compute module.
 *
 * Architecture alignment with Pebble VAPI:
 *   - Same three-layer structure (Reflexive / Deliberative / Strategic)
 *   - Same PoAC record format (228 bytes, ECDSA-P256)
 *   - Same economic evaluator API
 *   - Same world_model_hash commitment pattern
 *
 * Gaming-specific changes:
 *   - L1 polls inputs at 1 kHz, generates PoAC at 2-10 Hz
 *   - L2 runs anti-cheat trend analysis every 5 seconds
 *   - L3 syncs to companion app via BLE every 60 seconds
 *   - World model = player skill profile (not environmental baselines)
 *   - TinyML = 8-class cheat detector (not activity classifier)
 *
 * Target: ESP32-S3 (Xtensa LX7 dual-core @ 240 MHz, 512 KB SRAM)
 */

#ifndef DUALSHOCK_AGENT_H
#define DUALSHOCK_AGENT_H

#include <stdint.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "poac.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ──────────────────────────────────────────────────────────────────
 * Gaming State Machine
 * Replaces Pebble's BOOT/IDLE/ACTIVE/ALERT/PSM/OTA/SWARM states
 * with gaming-specific states.
 * ────────────────────────────────────────────────────────────────── */
typedef enum {
    DS_STATE_BOOT,              /* Initializing, self-test, key load */
    DS_STATE_IDLE,              /* Paired but no game session active */
    DS_STATE_SESSION,           /* Active gameplay session (normal PoAC rate) */
    DS_STATE_TOURNAMENT,        /* Tournament mode (high-frequency PoAC) */
    DS_STATE_CHEAT_ALERT,       /* Cheat detected, elevated monitoring */
    DS_STATE_CALIBRATION,       /* IMU/stick calibration in progress */
    DS_STATE_LOW_BATTERY,       /* Battery critical, reduced operation */
} ds_agent_state_t;

/* ──────────────────────────────────────────────────────────────────
 * Gaming Action Codes (extend POAC_ACTION_* from poac.h)
 * Range 0x10-0x1F reserved for gaming-specific actions.
 * ────────────────────────────────────────────────────────────────── */
#define DS_ACTION_SESSION_START   0x10
#define DS_ACTION_SESSION_END     0x11
#define DS_ACTION_CHEAT_ALERT     0x12
#define DS_ACTION_SKILL_PROOF     0x13
#define DS_ACTION_TOURNAMENT_FRAME 0x14
#define DS_ACTION_CALIBRATION     0x15

/* ──────────────────────────────────────────────────────────────────
 * Gaming Inference Result Codes (extend POAC_INFER_* from poac.h)
 * Range 0x20-0x2F reserved for gaming-specific classifications.
 * ────────────────────────────────────────────────────────────────── */
#define DS_INFER_PLAY_NOMINAL     0x20  /* Normal human gameplay */
#define DS_INFER_PLAY_SKILLED     0x21  /* High-skill, within human bounds */
#define DS_INFER_CHEAT_REACTION   0x22  /* Impossible reaction time (<150ms sustained) */
#define DS_INFER_CHEAT_MACRO      0x23  /* Macro/turbo pattern (σ < 1ms) */
#define DS_INFER_CHEAT_AIMBOT     0x24  /* Aimbot-like stick movement */
#define DS_INFER_CHEAT_RECOIL     0x25  /* Perfect recoil compensation */
#define DS_INFER_CHEAT_IMU_MISS   0x26  /* Stick input without controller motion */
#define DS_INFER_CHEAT_INJECTION  0x27  /* Fabricated input (no IMU, impossible timing) */
#define DS_INFER_SKILL_COMBO      0x28  /* Perfect combo execution (bounty) */
#define DS_INFER_SKILL_SPEEDRUN   0x29  /* Speedrun split timestamp */

/* ──────────────────────────────────────────────────────────────────
 * Gaming Input Snapshot
 * Replaces Pebble's perception_t. This is the "sensor data" that
 * gets committed into the PoAC sensor_commitment field via SHA-256.
 * 50 bytes, deterministic big-endian serialization.
 * ────────────────────────────────────────────────────────────────── */
typedef struct __attribute__((packed)) {
    uint8_t  buttons[3];         /* 18 buttons packed into 3 bytes */
    int16_t  left_stick_x;       /* [-32768, 32767] */
    int16_t  left_stick_y;
    int16_t  right_stick_x;
    int16_t  right_stick_y;
    uint8_t  l2_trigger;         /* [0, 255] */
    uint8_t  r2_trigger;
    float    gyro_x, gyro_y, gyro_z;     /* rad/s */
    float    accel_x, accel_y, accel_z;   /* g */
    uint16_t touch0_x, touch0_y;          /* [0, 1919] × [0, 941] */
    uint16_t touch1_x, touch1_y;
    uint8_t  touch_active;                /* bit0: t0, bit1: t1 */
    uint16_t battery_mv;
    uint32_t frame_counter;               /* monotonic input frame number */
    uint32_t inter_frame_us;              /* µs since last frame */
} ds_input_snapshot_t;

_Static_assert(sizeof(ds_input_snapshot_t) == 50,
               "Input snapshot must be exactly 50 bytes for PoAC commitment");

/* ──────────────────────────────────────────────────────────────────
 * Gaming World Model — Player Skill Profile
 * Replaces Pebble's world_model_t (environmental baselines).
 * Hashed into world_model_hash before each PoAC generation,
 * capturing the decision context for forensic reconstruction.
 * ────────────────────────────────────────────────────────────────── */
#define DS_WORLD_MODEL_HISTORY  64  /* More history for skill profiling */

typedef struct {
    float    avg_reaction_ms;       /* Average reaction time this window */
    float    stick_precision;       /* Stick control precision score [0, 1] */
    float    timing_variance;       /* Button timing variance (ms²) */
    float    imu_correlation;       /* IMU-input correlation score [0, 1] */
    uint8_t  cheat_flags;           /* Bitfield of triggered cheat types */
    int64_t  timestamp_ms;
} ds_observation_summary_t;

typedef struct {
    ds_observation_summary_t history[DS_WORLD_MODEL_HISTORY];
    uint8_t  head;                  /* Circular buffer write index */
    uint8_t  count;                 /* Valid entries */

    /* Running baselines (EMA, α=0.05 for slow drift) */
    float    reaction_baseline;     /* Baseline reaction time (ms) */
    float    precision_baseline;    /* Baseline stick precision */
    float    consistency_baseline;  /* Baseline timing variance */
    float    imu_corr_baseline;     /* Baseline IMU correlation */

    /* Session aggregates */
    float    session_skill_rating;  /* Composite [0, 1000] */
    uint32_t total_frames;          /* Total input frames since boot */
    uint32_t total_sessions;        /* Total game sessions */
    uint32_t total_cheat_flags;     /* Cumulative cheat detections */
    uint32_t total_poac_generated;  /* Total PoAC records */
} ds_world_model_t;

/* ──────────────────────────────────────────────────────────────────
 * Agent Configuration — Gaming Tunable Parameters
 * Mirrors Pebble's agent_config_t structure with gaming-specific
 * defaults and additions.
 * ────────────────────────────────────────────────────────────────── */
typedef struct {
    /* Layer 1: Gaming Reflexive */
    uint32_t input_poll_interval_us;   /* Input poll rate (default: 1000 = 1 kHz) */
    uint32_t poac_interval_ms;         /* PoAC generation rate (default: 500 = 2 Hz) */
    uint32_t tournament_poac_ms;       /* Tournament PoAC rate (default: 100 = 10 Hz) */
    uint8_t  cheat_threshold;          /* Confidence to trigger alert [0-255] (default: 180) */

    /* Layer 2: Anti-Cheat Deliberative */
    uint32_t anticheat_interval_ms;    /* Trend analysis period (default: 5000 = 5s) */
    uint8_t  battery_critical_pct;     /* Below this → low-battery state (default: 10) */
    uint8_t  battery_low_pct;          /* Below this → reduce PoAC rate (default: 20) */
    uint8_t  cheat_resolve_count;      /* Consecutive clean windows to clear alert (default: 10) */

    /* Layer 3: Economic Strategic */
    uint32_t ble_sync_interval_ms;     /* BLE sync to companion app (default: 60000 = 60s) */
    bool     bounty_enabled;           /* Enable autonomous bounty evaluation */
    uint8_t  min_battery_for_bounty;   /* Don't accept bounties below this % (default: 20) */
} ds_agent_config_t;

/* Default configuration */
#define DS_AGENT_CONFIG_DEFAULTS { \
    .input_poll_interval_us = 1000,    \
    .poac_interval_ms       = 500,     \
    .tournament_poac_ms     = 100,     \
    .cheat_threshold        = 180,     \
    .anticheat_interval_ms  = 5000,    \
    .battery_critical_pct   = 10,      \
    .battery_low_pct        = 20,      \
    .cheat_resolve_count    = 10,      \
    .ble_sync_interval_ms   = 60000,   \
    .bounty_enabled         = true,    \
    .min_battery_for_bounty = 20,      \
}

/* ──────────────────────────────────────────────────────────────────
 * Public API — Same pattern as Pebble's agent.h
 * ────────────────────────────────────────────────────────────────── */

/**
 * Initialize the DualShock agent subsystem.
 * Must be called after poac_init().
 */
int ds_agent_init(const ds_agent_config_t *config);

/**
 * Start the agent (begins autonomous gaming operation).
 * Generates a BOOT PoAC record and transitions to IDLE state.
 */
int ds_agent_start(void);

/**
 * Stop the agent gracefully.
 * Persists state and stops all threads.
 */
int ds_agent_stop(void);

/**
 * Start a game session. Transitions IDLE → SESSION.
 * Generates a SESSION_START PoAC record.
 */
int ds_agent_start_session(void);

/**
 * End a game session. Transitions SESSION/TOURNAMENT → IDLE.
 * Generates a SESSION_END PoAC record.
 */
int ds_agent_end_session(void);

/**
 * Enable tournament mode. Transitions SESSION → TOURNAMENT.
 * Increases PoAC rate to 10 Hz for competitive play.
 */
int ds_agent_tournament_mode(bool enable);

/**
 * Get current agent state.
 */
ds_agent_state_t ds_agent_get_state(void);

/**
 * Get a copy of the current gaming world model.
 */
int ds_agent_get_world_model(ds_world_model_t *out);

/**
 * Update agent configuration at runtime.
 */
int ds_agent_set_config(const ds_agent_config_t *config);

/**
 * Force immediate BLE sync (drain PoAC queue to companion app).
 */
int ds_agent_force_sync(void);

/**
 * Trigger haptic feedback.
 */
typedef enum {
    DS_HAPTIC_CHEAT_ALERT,      /* 3× short bursts */
    DS_HAPTIC_BOUNTY_ACCEPTED,  /* 1× long rumble */
    DS_HAPTIC_SKILL_VERIFIED,   /* 2× short pulses */
    DS_HAPTIC_CHAIN_BREAK,      /* Continuous vibration */
    DS_HAPTIC_TOURNAMENT_PULSE, /* Periodic soft pulse (heartbeat) */
} ds_haptic_pattern_t;

int ds_agent_haptic(ds_haptic_pattern_t pattern);

/* Callbacks — same pattern as Pebble */
typedef void (*ds_state_cb_t)(ds_agent_state_t old_state, ds_agent_state_t new_state);
void ds_agent_register_state_callback(ds_state_cb_t cb);

typedef void (*ds_poac_cb_t)(const poac_record_t *record);
void ds_agent_register_poac_callback(ds_poac_cb_t cb);

typedef void (*ds_cheat_cb_t)(uint8_t cheat_type, uint8_t confidence);
void ds_agent_register_cheat_callback(ds_cheat_cb_t cb);

#ifdef __cplusplus
}
#endif

#endif /* DUALSHOCK_AGENT_H */
