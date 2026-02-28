/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Economic Evaluator — Autonomous Bounty Assessment and Battery Economics
 *
 * Implements the Economic Layer of the VAPI Economic Personhood Stack:
 *   - Discovers active bounties via CoAP subscription
 *   - Evaluates utility: P(success) * reward - energy_cost - opportunity_cost
 *   - Makes accept/decline decisions under physical constraints
 *   - Tracks active commitments and generates economic PoAC records
 *
 * Every economic decision (accept, decline, claim) produces a PoAC record,
 * ensuring full auditability of the agent's financial reasoning.
 *
 * Target: IoTeX Pebble Tracker (nRF9160, Zephyr RTOS)
 */

#ifndef ECONOMIC_H
#define ECONOMIC_H

#include <stdint.h>
#include <stdbool.h>
#include <zephyr/kernel.h>
#include "perception.h"
#include "poac.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Maximum bounties tracked simultaneously */
#define ECON_MAX_ACTIVE_BOUNTIES  4

/* Maximum bounties in discovery queue */
#define ECON_MAX_DISCOVERED       8

/* Bounty status */
typedef enum {
    BOUNTY_STATUS_DISCOVERED,   /* Seen on-chain, not yet evaluated */
    BOUNTY_STATUS_EVALUATING,   /* Cost-benefit analysis in progress */
    BOUNTY_STATUS_ACCEPTED,     /* Agent committed to this bounty */
    BOUNTY_STATUS_FULFILLING,   /* Actively collecting data for it */
    BOUNTY_STATUS_CLAIMING,     /* Submitting proof for reward */
    BOUNTY_STATUS_DECLINED,     /* Rejected (cost > benefit) */
    BOUNTY_STATUS_COMPLETED,    /* Reward received */
    BOUNTY_STATUS_EXPIRED,      /* Deadline passed */
} bounty_status_t;

/* Sensor requirement flags (bitfield) */
#define BOUNTY_REQUIRES_VOC       (1 << 0)
#define BOUNTY_REQUIRES_TEMP      (1 << 1)
#define BOUNTY_REQUIRES_HUMIDITY  (1 << 2)
#define BOUNTY_REQUIRES_PRESSURE  (1 << 3)
#define BOUNTY_REQUIRES_MOTION    (1 << 4)
#define BOUNTY_REQUIRES_LIGHT     (1 << 5)
#define BOUNTY_REQUIRES_GPS       (1 << 6)

/**
 * On-chain bounty descriptor.
 *
 * Parsed from CoAP/MQTT bounty feed originating from IoTeX smart contract events.
 */
typedef struct {
    uint32_t bounty_id;              /* On-chain bounty identifier */
    uint32_t reward_iotx_micro;      /* Reward in micro-IOTX (1 IOTX = 1,000,000) */
    uint16_t sensor_requirements;    /* Bitfield of BOUNTY_REQUIRES_* flags */
    uint16_t min_samples;            /* Minimum PoAC submissions required */
    uint32_t sample_interval_s;      /* Required sampling interval in seconds */
    uint32_t duration_s;             /* Bounty duration from acceptance */
    int64_t  deadline_ms;            /* Absolute deadline (Unix ms) */

    /* Geographic zone (simple bounding box) */
    double   zone_lat_min;
    double   zone_lat_max;
    double   zone_lon_min;
    double   zone_lon_max;

    /* Threshold triggers (0 = no threshold) */
    float    voc_threshold;          /* VOC resistance below this = event */
    float    temp_threshold_hi;      /* Temperature above this = event */
    float    temp_threshold_lo;      /* Temperature below this = event */
} bounty_descriptor_t;

/**
 * Cost-benefit evaluation result.
 *
 * Produced by the utility function and recorded in PoAC for auditability.
 */
typedef struct {
    uint32_t bounty_id;
    float    p_success;              /* Probability of successful fulfillment [0,1] */
    float    reward_value;           /* Expected reward (p_success * reward) */
    float    energy_cost;            /* Estimated battery drain [0,100] percentage points */
    float    opportunity_cost;       /* Value of displaced alternative actions */
    float    net_utility;            /* reward_value - energy_cost - opportunity_cost */
    bool     accepted;               /* Decision: accept or decline */
    uint8_t  decline_reason;         /* 0=accepted, 1=low battery, 2=wrong zone,
                                        3=missing sensor, 4=negative utility,
                                        5=at capacity */
} evaluation_result_t;

/**
 * Active bounty tracking entry.
 */
typedef struct {
    bounty_descriptor_t desc;
    bounty_status_t     status;
    evaluation_result_t eval;
    uint16_t            samples_submitted;  /* PoACs submitted so far */
    int64_t             accepted_at_ms;     /* When the agent accepted */
    int64_t             last_sample_ms;     /* Last PoAC submission time */
} active_bounty_t;

/**
 * Energy profile for the device — calibrated at boot.
 *
 * Used to estimate battery cost of bounty fulfillment.
 */
typedef struct {
    float mah_per_sensor_read;    /* mAh consumed per full sensor capture */
    float mah_per_cellular_tx;    /* mAh consumed per NB-IoT uplink */
    float mah_per_gps_fix;        /* mAh consumed per GPS acquisition */
    float mah_per_crypto_op;      /* mAh consumed per PoAC sign operation */
    float battery_capacity_mah;   /* Total battery capacity */
    float mah_per_pct;            /* Derived: capacity / 100 */
} energy_profile_t;

/**
 * Initialize the economic evaluator.
 *
 * Starts the bounty discovery CoAP subscription thread and
 * loads energy profile from device calibration data.
 *
 * @param coap_endpoint  Bounty feed endpoint (e.g., "coap://bounty.iotex.io/feed").
 *                       NULL to disable network discovery (manual bounties only).
 * @return 0 on success, negative errno on failure.
 */
int economic_init(const char *coap_endpoint);

/**
 * Evaluate a bounty against current device state.
 *
 * Computes the full utility function and returns the decision.
 * Generates a PoAC record (BOUNTY_ACCEPT or BOUNTY_DECLINE).
 *
 * @param bounty       Bounty descriptor to evaluate.
 * @param current      Current perception snapshot (for location/battery check).
 * @param out_result   Output evaluation result.
 * @return 0 on success, negative errno on failure.
 */
int economic_evaluate_bounty(const bounty_descriptor_t *bounty,
                             const perception_t *current,
                             evaluation_result_t *out_result);

/**
 * Get the list of currently active (accepted) bounties.
 *
 * @param out_bounties  Output array.
 * @param max_count     Size of output array.
 * @param out_count     Number of active bounties written.
 * @return 0 on success.
 */
int economic_get_active_bounties(active_bounty_t *out_bounties,
                                 size_t max_count, size_t *out_count);

/**
 * Submit a PoAC record as bounty fulfillment evidence.
 *
 * Links the PoAC to the active bounty and increments the submission counter.
 * If min_samples is reached, triggers a claim transaction.
 *
 * @param bounty_id  The bounty this PoAC fulfills.
 * @param record     The signed PoAC record.
 * @return 0 on success, -ENOENT if bounty not active, -ETIME if expired.
 */
int economic_submit_evidence(uint32_t bounty_id, const poac_record_t *record);

/**
 * Get cumulative economic statistics.
 */
typedef struct {
    uint32_t bounties_evaluated;
    uint32_t bounties_accepted;
    uint32_t bounties_declined;
    uint32_t bounties_completed;
    uint32_t total_reward_micro;     /* Cumulative micro-IOTX earned */
    float    total_energy_spent_pct; /* Cumulative battery % spent on bounties */
    float    roi;                    /* Return on energy investment */
} economic_stats_t;

int economic_get_stats(economic_stats_t *out);

/**
 * Manually inject a bounty for evaluation (e.g., from app or debug).
 *
 * Bypasses the CoAP discovery feed.
 */
int economic_inject_bounty(const bounty_descriptor_t *bounty);

/**
 * Compute survival horizon: estimated hours until battery critical.
 *
 * Uses energy profile and current consumption pattern.
 *
 * @param current_battery_pct  Current battery level.
 * @param critical_pct         Critical threshold.
 * @return Estimated hours remaining, or -1 on error.
 */
float economic_survival_horizon(uint8_t current_battery_pct,
                                uint8_t critical_pct);

/**
 * Optimally allocate battery budget across discovered bounties.
 *
 * Uses a greedy knapsack approximation: sorts candidates by utility density
 * (net_utility / energy_cost), selects greedily under the available battery
 * budget. Handles preemption of low-value active bounties when higher-value
 * candidates are available.
 *
 * O(n log n) time, O(n) stack space where n <= ECON_MAX_DISCOVERED.
 * No heap allocation.
 *
 * @param current     Current perception snapshot (for location/battery).
 * @param budget_pct  Available battery budget for bounties (e.g., battery_pct - critical_threshold).
 * @param out_accepted  Output array of accepted bounty IDs (caller allocates, max ECON_MAX_ACTIVE_BOUNTIES).
 * @param out_count     Output: number of bounties accepted.
 * @return 0 on success, negative errno on failure.
 */
int economic_optimize_bounties(const perception_t *current,
                               float budget_pct,
                               uint32_t *out_accepted,
                               size_t *out_count);

#ifdef __cplusplus
}
#endif

#endif /* ECONOMIC_H */
