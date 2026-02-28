/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Economic Evaluator — Autonomous Bounty Assessment and Battery Economics
 *
 * Implements cost-benefit analysis for on-chain bounties under real physical
 * constraints. Every economic decision generates a PoAC record for auditability.
 */

#include "economic.h"
#include "coap_bounty_feed.h"
#include "poac.h"
#include "perception.h"

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <string.h>
#include <math.h>

LOG_MODULE_REGISTER(economic, CONFIG_VAPI_LOG_LEVEL);

/* --------------------------------------------------------------------------
 * Internal state
 * -------------------------------------------------------------------------- */

static K_MUTEX_DEFINE(econ_mutex);

/* Active bounties the agent has committed to */
static active_bounty_t active_bounties[ECON_MAX_ACTIVE_BOUNTIES];
static size_t active_bounty_count;

/* Discovered but not-yet-evaluated bounties */
static bounty_descriptor_t discovered_queue[ECON_MAX_DISCOVERED];
static size_t discovered_count;

/* Device energy profile — calibrated at init */
static energy_profile_t energy_profile;

/* Cumulative statistics */
static economic_stats_t stats;

/* CoAP endpoint for bounty feed (NULL = disabled) */
static const char *bounty_endpoint;

/* --------------------------------------------------------------------------
 * Energy profile calibration
 * -------------------------------------------------------------------------- */

/**
 * Default energy profile for the IoTeX Pebble Tracker.
 *
 * Values derived from nRF9160 power profiling (Nordic PPK2 measurements):
 *   - Sensor read (BME680 + ICM-42605 + TSL2572): ~0.015 mAh per capture
 *   - NB-IoT uplink (170 bytes, ~2s active): ~0.08 mAh per transmission
 *   - GPS cold fix (~30s): ~0.25 mAh; warm fix (~5s): ~0.04 mAh
 *   - CryptoCell SHA-256 + ECDSA sign: ~0.001 mAh per operation
 *   - Battery: 1000 mAh (typical Pebble Li-Po)
 */
static void energy_profile_load_defaults(energy_profile_t *ep)
{
    ep->mah_per_sensor_read = 0.015f;
    ep->mah_per_cellular_tx = 0.08f;
    ep->mah_per_gps_fix     = 0.04f;   /* Warm fix assumed after initial lock */
    ep->mah_per_crypto_op   = 0.001f;
    ep->battery_capacity_mah = 1000.0f;
    ep->mah_per_pct = ep->battery_capacity_mah / 100.0f;
}

/* --------------------------------------------------------------------------
 * Utility function — the core economic reasoning
 * -------------------------------------------------------------------------- */

/**
 * Estimate battery percentage consumed by fulfilling a bounty.
 *
 * cost = num_samples * (sensor_read + crypto + gps_if_needed + cellular_tx)
 * converted to battery percentage.
 */
static float estimate_energy_cost(const bounty_descriptor_t *b)
{
    float per_sample = energy_profile.mah_per_sensor_read
                     + energy_profile.mah_per_crypto_op
                     + energy_profile.mah_per_cellular_tx;

    /* GPS required per sample if bounty needs location verification */
    if (b->sensor_requirements & BOUNTY_REQUIRES_GPS) {
        per_sample += energy_profile.mah_per_gps_fix;
    }

    float total_mah = per_sample * (float)b->min_samples;
    return (total_mah / energy_profile.mah_per_pct);
}

/**
 * Estimate probability of successful fulfillment.
 *
 * Factors:
 *   - Sensor capability match (binary: do we have the required sensors?)
 *   - Location proximity (are we in the bounty zone?)
 *   - Time feasibility (can we collect min_samples before deadline?)
 */
static float estimate_success_probability(const bounty_descriptor_t *b,
                                          const perception_t *current)
{
    float p = 1.0f;

    /* Sensor capability — the Pebble has all sensors, but check GPS fix */
    if ((b->sensor_requirements & BOUNTY_REQUIRES_GPS) && !current->gps_valid) {
        p *= 0.5f; /* Reduce confidence if no current GPS fix */
    }

    /* Geographic check */
    if (current->gps_valid) {
        bool in_zone = (current->gps.latitude  >= b->zone_lat_min &&
                        current->gps.latitude  <= b->zone_lat_max &&
                        current->gps.longitude >= b->zone_lon_min &&
                        current->gps.longitude <= b->zone_lon_max);
        if (!in_zone) {
            p *= 0.1f; /* Very low probability if not in zone */
        }
    } else {
        p *= 0.3f; /* Unknown location — moderate penalty */
    }

    /* Time feasibility */
    int64_t now = current->timestamp_ms;
    int64_t remaining_ms = b->deadline_ms - now;
    int64_t required_ms = (int64_t)b->min_samples * (int64_t)b->sample_interval_s * 1000LL;

    if (remaining_ms <= 0) {
        return 0.0f; /* Expired */
    }
    if (required_ms > remaining_ms) {
        p *= (float)remaining_ms / (float)required_ms; /* Partial feasibility */
    }

    return p;
}

/**
 * Compute opportunity cost: value of the best alternative we'd displace.
 *
 * Simple heuristic: if we're already at capacity, the opportunity cost is
 * the lowest-utility active bounty's net value. Otherwise, zero.
 */
static float compute_opportunity_cost(void)
{
    if (active_bounty_count < ECON_MAX_ACTIVE_BOUNTIES) {
        return 0.0f; /* Capacity available, no displacement needed */
    }

    /* Find the lowest-utility active bounty */
    float min_utility = 1e9f;
    for (size_t i = 0; i < active_bounty_count; i++) {
        if (active_bounties[i].eval.net_utility < min_utility) {
            min_utility = active_bounties[i].eval.net_utility;
        }
    }
    return (min_utility > 0.0f) ? min_utility : 0.0f;
}

/* --------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------- */

int economic_init(const char *coap_endpoint)
{
    k_mutex_lock(&econ_mutex, K_FOREVER);

    memset(active_bounties, 0, sizeof(active_bounties));
    active_bounty_count = 0;
    discovered_count = 0;
    memset(&stats, 0, sizeof(stats));

    energy_profile_load_defaults(&energy_profile);
    bounty_endpoint = coap_endpoint;

    k_mutex_unlock(&econ_mutex);

    LOG_INF("Economic evaluator initialized (endpoint=%s)",
            coap_endpoint ? coap_endpoint : "disabled");

    /* Start CoAP observation thread for bounty feed discovery.
     * If endpoint is NULL, bounties are injected manually via
     * economic_inject_bounty() or from the deliberative layer. */
    if (coap_endpoint) {
        int feed_rc = coap_bounty_feed_start(coap_endpoint);
        if (feed_rc != 0) {
            LOG_WRN("CoAP bounty feed failed to start: %d "
                     "(manual injection still available)", feed_rc);
        }
    }

    return 0;
}

int economic_evaluate_bounty(const bounty_descriptor_t *bounty,
                             const perception_t *current,
                             evaluation_result_t *out_result)
{
    if (!bounty || !current || !out_result) {
        return -EINVAL;
    }

    k_mutex_lock(&econ_mutex, K_FOREVER);

    evaluation_result_t eval = {
        .bounty_id = bounty->bounty_id,
        .accepted  = false,
        .decline_reason = 0,
    };

    /* Step 1: Check hard constraints first (cheap, before math) */

    /* Battery gate */
    if (current->battery_pct < 15) {
        eval.decline_reason = 1; /* Low battery */
        eval.p_success = 0.0f;
        eval.net_utility = -1.0f;
        goto decision;
    }

    /* Capacity gate */
    if (active_bounty_count >= ECON_MAX_ACTIVE_BOUNTIES) {
        /* Check if this bounty could displace the weakest active one */
        /* For now, simple rejection at capacity */
        eval.decline_reason = 5;
        eval.p_success = 0.0f;
        eval.net_utility = -1.0f;
        goto decision;
    }

    /* Step 2: Compute utility function */
    eval.p_success = estimate_success_probability(bounty, current);
    eval.reward_value = eval.p_success *
                        ((float)bounty->reward_iotx_micro / 1000000.0f);
    eval.energy_cost = estimate_energy_cost(bounty);
    eval.opportunity_cost = compute_opportunity_cost();
    eval.net_utility = eval.reward_value - eval.energy_cost - eval.opportunity_cost;

    /* Step 3: Decision */
    if (eval.p_success < 0.1f) {
        eval.decline_reason = (current->gps_valid &&
            (current->gps.latitude < bounty->zone_lat_min ||
             current->gps.latitude > bounty->zone_lat_max ||
             current->gps.longitude < bounty->zone_lon_min ||
             current->gps.longitude > bounty->zone_lon_max)) ? 2 : 3;
    } else if (eval.net_utility <= 0.0f) {
        eval.decline_reason = 4; /* Negative utility */
    } else {
        eval.accepted = true;
        eval.decline_reason = 0;
    }

decision:
    *out_result = eval;

    /* Record the decision */
    stats.bounties_evaluated++;

    if (eval.accepted) {
        /* Add to active bounties */
        active_bounty_t *slot = &active_bounties[active_bounty_count];
        memset(slot, 0, sizeof(*slot));
        slot->desc = *bounty;
        slot->status = BOUNTY_STATUS_ACCEPTED;
        slot->eval = eval;
        slot->accepted_at_ms = current->timestamp_ms;
        active_bounty_count++;
        stats.bounties_accepted++;

        LOG_INF("Bounty #%u ACCEPTED (utility=%.3f, p=%.2f, cost=%.2f%%)",
                bounty->bounty_id, eval.net_utility,
                eval.p_success, eval.energy_cost);
    } else {
        stats.bounties_declined++;
        LOG_INF("Bounty #%u DECLINED (reason=%u, utility=%.3f)",
                bounty->bounty_id, eval.decline_reason, eval.net_utility);
    }

    k_mutex_unlock(&econ_mutex);

    /*
     * Generate PoAC for this economic decision.
     * The action_code encodes accept/decline; the bounty_id links it on-chain.
     */
    uint8_t sensor_hash[POAC_HASH_SIZE];
    uint8_t serial_buf[PERCEPTION_SERIAL_MAX_SIZE];
    size_t serial_len;

    if (perception_serialize(current, serial_buf, sizeof(serial_buf), &serial_len) == 0) {
        poac_commit_sensors(serial_buf, serial_len, sensor_hash);

        poac_record_t record;
        poac_generate(
            sensor_hash,
            NULL, /* World model hash not available in economic context */
            POAC_INFER_NOMINAL,
            eval.accepted ? POAC_ACTION_BOUNTY_ACCEPT : POAC_ACTION_BOUNTY_DECLINE,
            (uint8_t)(eval.p_success * 255.0f),
            current->battery_pct,
            current->timestamp_ms,
            current->gps.latitude,
            current->gps.longitude,
            bounty->bounty_id,
            &record
        );
        /* Record is signed and chained — caller can transmit it */
    }

    return 0;
}

int economic_get_active_bounties(active_bounty_t *out_bounties,
                                 size_t max_count, size_t *out_count)
{
    k_mutex_lock(&econ_mutex, K_FOREVER);

    size_t n = (active_bounty_count < max_count) ? active_bounty_count : max_count;
    memcpy(out_bounties, active_bounties, n * sizeof(active_bounty_t));
    *out_count = n;

    k_mutex_unlock(&econ_mutex);
    return 0;
}

int economic_submit_evidence(uint32_t bounty_id, const poac_record_t *record)
{
    if (!record) {
        return -EINVAL;
    }

    k_mutex_lock(&econ_mutex, K_FOREVER);

    /* Find the active bounty */
    active_bounty_t *bounty = NULL;
    for (size_t i = 0; i < active_bounty_count; i++) {
        if (active_bounties[i].desc.bounty_id == bounty_id) {
            bounty = &active_bounties[i];
            break;
        }
    }

    if (!bounty) {
        k_mutex_unlock(&econ_mutex);
        LOG_WRN("Evidence for unknown bounty #%u", bounty_id);
        return -ENOENT;
    }

    /* Check expiry */
    if (record->timestamp_ms > bounty->desc.deadline_ms) {
        bounty->status = BOUNTY_STATUS_EXPIRED;
        k_mutex_unlock(&econ_mutex);
        LOG_WRN("Bounty #%u expired", bounty_id);
        return -ETIME;
    }

    /* Check sample interval */
    int64_t interval_ms = (int64_t)bounty->desc.sample_interval_s * 1000LL;
    if (bounty->samples_submitted > 0 &&
        (record->timestamp_ms - bounty->last_sample_ms) < interval_ms) {
        k_mutex_unlock(&econ_mutex);
        LOG_DBG("Bounty #%u: sample too soon, skipping", bounty_id);
        return -EAGAIN;
    }

    bounty->samples_submitted++;
    bounty->last_sample_ms = record->timestamp_ms;
    bounty->status = BOUNTY_STATUS_FULFILLING;

    LOG_INF("Bounty #%u: evidence %u/%u submitted",
            bounty_id, bounty->samples_submitted, bounty->desc.min_samples);

    /* Check if fulfillment complete */
    if (bounty->samples_submitted >= bounty->desc.min_samples) {
        bounty->status = BOUNTY_STATUS_CLAIMING;
        stats.bounties_completed++;
        stats.total_reward_micro += bounty->desc.reward_iotx_micro;
        LOG_INF("Bounty #%u: FULFILLED — claiming reward (%u micro-IOTX)",
                bounty_id, bounty->desc.reward_iotx_micro);

        /*
         * TODO: Trigger on-chain claim transaction via cellular.
         * This would call BountyMarket.claimReward(bounty_id, poac_hashes[])
         * through the MQTT/CoAP bridge to IoTeX.
         */
    }

    k_mutex_unlock(&econ_mutex);
    return 0;
}

int economic_get_stats(economic_stats_t *out)
{
    k_mutex_lock(&econ_mutex, K_FOREVER);

    *out = stats;

    /* Compute ROI */
    if (stats.total_energy_spent_pct > 0.0f) {
        float reward_normalized = (float)stats.total_reward_micro / 1000000.0f;
        out->roi = reward_normalized / stats.total_energy_spent_pct;
    } else {
        out->roi = 0.0f;
    }

    k_mutex_unlock(&econ_mutex);
    return 0;
}

int economic_inject_bounty(const bounty_descriptor_t *bounty)
{
    if (!bounty) {
        return -EINVAL;
    }

    k_mutex_lock(&econ_mutex, K_FOREVER);

    if (discovered_count >= ECON_MAX_DISCOVERED) {
        k_mutex_unlock(&econ_mutex);
        LOG_WRN("Bounty discovery queue full, dropping #%u", bounty->bounty_id);
        return -ENOMEM;
    }

    discovered_queue[discovered_count++] = *bounty;

    k_mutex_unlock(&econ_mutex);

    LOG_INF("Bounty #%u injected into discovery queue (%zu pending)",
            bounty->bounty_id, discovered_count);
    return 0;
}

float economic_survival_horizon(uint8_t current_battery_pct, uint8_t critical_pct)
{
    if (current_battery_pct <= critical_pct) {
        return 0.0f;
    }

    float available_pct = (float)(current_battery_pct - critical_pct);
    float available_mah = available_pct * energy_profile.mah_per_pct;

    /*
     * Estimate consumption rate from active bounties + baseline.
     * Baseline idle: ~0.05 mA average (PSM + periodic wake).
     * Active sensing: depends on duty cycle.
     */
    float hourly_mah = 0.05f; /* Baseline idle consumption */

    k_mutex_lock(&econ_mutex, K_FOREVER);

    for (size_t i = 0; i < active_bounty_count; i++) {
        if (active_bounties[i].status == BOUNTY_STATUS_FULFILLING) {
            const bounty_descriptor_t *b = &active_bounties[i].desc;
            float per_sample = energy_profile.mah_per_sensor_read
                             + energy_profile.mah_per_crypto_op
                             + energy_profile.mah_per_cellular_tx;
            if (b->sensor_requirements & BOUNTY_REQUIRES_GPS) {
                per_sample += energy_profile.mah_per_gps_fix;
            }
            float samples_per_hour = 3600.0f / (float)b->sample_interval_s;
            hourly_mah += per_sample * samples_per_hour;
        }
    }

    k_mutex_unlock(&econ_mutex);

    if (hourly_mah <= 0.0f) {
        return 999.0f; /* Effectively infinite at zero consumption */
    }

    return available_mah / hourly_mah;
}

/* --------------------------------------------------------------------------
 * Greedy knapsack bounty optimizer
 *
 * Problem: Given N discovered bounties, each with a utility value and energy
 * cost, select a subset that maximizes total utility subject to:
 *   - Total energy cost <= budget_pct (available battery)
 *   - At most ECON_MAX_ACTIVE_BOUNTIES selected
 *
 * Approach: Greedy approximation sorted by utility density (value/cost).
 * This is optimal for the fractional knapsack and provides a (1 - 1/e)
 * approximation for the 0/1 case. For N <= 8 items, the greedy solution
 * is near-optimal in practice.
 *
 * Preemption: If all active slots are full, we check whether any discovered
 * bounty has higher utility density than the weakest active bounty. If so,
 * the weakest is released and replaced.
 * -------------------------------------------------------------------------- */

/* Candidate for sorting — stack-allocated, no heap */
typedef struct {
    size_t  queue_idx;      /* Index into discovered_queue */
    float   net_utility;    /* From evaluation */
    float   energy_cost;    /* Battery % consumed */
    float   density;        /* net_utility / energy_cost */
    bool    feasible;       /* Passes hard constraints */
} knapsack_candidate_t;

/* Simple insertion sort — N <= 8, so O(N^2) is perfectly fine and avoids qsort */
static void sort_candidates_by_density(knapsack_candidate_t *arr, size_t n)
{
    for (size_t i = 1; i < n; i++) {
        knapsack_candidate_t key = arr[i];
        size_t j = i;
        while (j > 0 && arr[j - 1].density < key.density) {
            arr[j] = arr[j - 1];
            j--;
        }
        arr[j] = key;
    }
}

int economic_optimize_bounties(const perception_t *current,
                               float budget_pct,
                               uint32_t *out_accepted,
                               size_t *out_count)
{
    if (!current || !out_accepted || !out_count) {
        return -EINVAL;
    }

    *out_count = 0;

    k_mutex_lock(&econ_mutex, K_FOREVER);

    if (discovered_count == 0) {
        k_mutex_unlock(&econ_mutex);
        LOG_DBG("No discovered bounties to optimize");
        return 0;
    }

    /* Phase 1: Evaluate all discovered bounties and build candidate list */
    knapsack_candidate_t candidates[ECON_MAX_DISCOVERED];
    size_t num_candidates = 0;

    for (size_t i = 0; i < discovered_count; i++) {
        knapsack_candidate_t c;
        c.queue_idx = i;

        /* Quick feasibility checks */
        const bounty_descriptor_t *b = &discovered_queue[i];

        /* Expired? */
        if (b->deadline_ms > 0 && current->timestamp_ms > b->deadline_ms) {
            continue;
        }

        /* Already active? */
        bool already_active = false;
        for (size_t j = 0; j < active_bounty_count; j++) {
            if (active_bounties[j].desc.bounty_id == b->bounty_id) {
                already_active = true;
                break;
            }
        }
        if (already_active) {
            continue;
        }

        c.energy_cost = estimate_energy_cost(b);
        c.feasible = true;

        /* Geographic check */
        if (current->gps_valid) {
            if (current->gps.latitude < b->zone_lat_min ||
                current->gps.latitude > b->zone_lat_max ||
                current->gps.longitude < b->zone_lon_min ||
                current->gps.longitude > b->zone_lon_max) {
                c.feasible = false;
            }
        }

        /* Energy check */
        if (c.energy_cost > budget_pct) {
            c.feasible = false;
        }

        if (!c.feasible) {
            continue;
        }

        /* Compute utility */
        float p_success = estimate_success_probability(b, current);
        float reward = p_success * ((float)b->reward_iotx_micro / 1000000.0f);
        c.net_utility = reward - c.energy_cost;

        if (c.net_utility <= 0.0f) {
            continue; /* Negative utility — skip */
        }

        c.density = c.net_utility / (c.energy_cost > 0.001f ? c.energy_cost : 0.001f);
        candidates[num_candidates++] = c;
    }

    if (num_candidates == 0) {
        k_mutex_unlock(&econ_mutex);
        LOG_DBG("No feasible bounty candidates after evaluation");
        return 0;
    }

    /* Phase 2: Sort by utility density (highest first) */
    sort_candidates_by_density(candidates, num_candidates);

    LOG_INF("Knapsack optimizer: %zu candidates, budget=%.1f%%",
            num_candidates, (double)budget_pct);

    /* Phase 3: Greedy selection */
    float remaining_budget = budget_pct;
    size_t slots_available = ECON_MAX_ACTIVE_BOUNTIES - active_bounty_count;
    size_t accepted = 0;

    /* Phase 3a: Check if preemption is beneficial */
    if (slots_available == 0 && num_candidates > 0) {
        /* Find weakest active bounty by utility density */
        float weakest_density = 1e9f;
        size_t weakest_idx = 0;

        for (size_t i = 0; i < active_bounty_count; i++) {
            float active_density = active_bounties[i].eval.net_utility /
                (active_bounties[i].eval.energy_cost > 0.001f ?
                 active_bounties[i].eval.energy_cost : 0.001f);
            if (active_density < weakest_density) {
                weakest_density = active_density;
                weakest_idx = i;
            }
        }

        /* Preempt if best candidate is significantly better (>50% higher density) */
        if (candidates[0].density > weakest_density * 1.5f) {
            LOG_INF("Preempting bounty #%u (density=%.3f) for #%u (density=%.3f)",
                    active_bounties[weakest_idx].desc.bounty_id,
                    (double)weakest_density,
                    discovered_queue[candidates[0].queue_idx].bounty_id,
                    (double)candidates[0].density);

            /* Reclaim energy budget from preempted bounty */
            remaining_budget += active_bounties[weakest_idx].eval.energy_cost;

            /* Remove preempted bounty by swapping with last */
            active_bounties[weakest_idx] = active_bounties[active_bounty_count - 1];
            active_bounty_count--;
            slots_available = 1;
        }
    }

    /* Phase 3b: Greedy fill */
    for (size_t i = 0; i < num_candidates && accepted < slots_available; i++) {
        knapsack_candidate_t *c = &candidates[i];

        if (c->energy_cost > remaining_budget) {
            continue; /* Doesn't fit in remaining budget */
        }

        const bounty_descriptor_t *b = &discovered_queue[c->queue_idx];
        out_accepted[accepted] = b->bounty_id;
        remaining_budget -= c->energy_cost;
        accepted++;

        LOG_INF("  Selected bounty #%u (density=%.3f, cost=%.2f%%, utility=%.3f)",
                b->bounty_id, (double)c->density,
                (double)c->energy_cost, (double)c->net_utility);
    }

    *out_count = accepted;

    /* Phase 4: Clear processed bounties from discovery queue */
    discovered_count = 0; /* Simplest approach: drain the queue after optimization */

    k_mutex_unlock(&econ_mutex);

    LOG_INF("Knapsack result: %zu bounties selected, %.1f%% budget remaining",
            accepted, (double)remaining_budget);

    return 0;
}
