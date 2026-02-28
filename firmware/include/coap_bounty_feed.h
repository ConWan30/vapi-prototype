/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * CoAP Bounty Feed — Zephyr CoAP Client for Bounty Discovery
 *
 * Subscribes to the IoTeX bounty feed via CoAP OBSERVE on /bounties/active.
 * Parses CBOR-encoded bounty descriptors and injects them into the
 * economic evaluator for autonomous cost-benefit assessment.
 *
 * Wire protocol:
 *   CoAP OBSERVE GET coap://[endpoint]/bounties/active
 *   Response payload: CBOR array of bounty_descriptor_t
 *
 * Reliability:
 *   - CON messages with Zephyr's built-in retransmit
 *   - Re-OBSERVE on timeout or RST
 *   - Rate limiting: max 1 bounty evaluation per 10 seconds
 *   - Thread: 2KB stack, priority 12 (below agent threads)
 *
 * Target: nRF9160 (Zephyr RTOS, nRF Connect SDK v2.7+)
 */

#ifndef COAP_BOUNTY_FEED_H
#define COAP_BOUNTY_FEED_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ──────────────────────────────────────────────────────────────────
 * Constants
 * ────────────────────────────────────────────────────────────────── */

/** CoAP default port. */
#define COAP_BOUNTY_PORT          5683

/** Maximum CBOR payload size for bounty feed response. */
#define COAP_BOUNTY_MAX_PAYLOAD   1024

/** Rate limit: minimum seconds between bounty evaluations. */
#define COAP_BOUNTY_RATE_LIMIT_S  10

/** Thread stack size in bytes. */
#define COAP_BOUNTY_STACK_SIZE    2048

/** Thread priority (lower number = higher priority). */
#define COAP_BOUNTY_THREAD_PRIO   12

/** OBSERVE re-registration interval in seconds (if no notifications). */
#define COAP_BOUNTY_OBSERVE_TIMEOUT_S  300

/** Maximum consecutive failures before exponential backoff. */
#define COAP_BOUNTY_MAX_FAILURES  5

/* ──────────────────────────────────────────────────────────────────
 * Feed State
 * ────────────────────────────────────────────────────────────────── */

typedef enum {
    COAP_FEED_IDLE,          /**< Not started */
    COAP_FEED_RESOLVING,     /**< DNS resolution in progress */
    COAP_FEED_OBSERVING,     /**< OBSERVE subscription active */
    COAP_FEED_BACKOFF,       /**< Waiting after failure */
    COAP_FEED_STOPPED,       /**< Explicitly stopped */
} coap_feed_state_t;

/* ──────────────────────────────────────────────────────────────────
 * Feed Statistics
 * ────────────────────────────────────────────────────────────────── */

typedef struct {
    uint32_t notifications_received;  /**< Total OBSERVE notifications */
    uint32_t bounties_parsed;         /**< Successfully parsed bounty descriptors */
    uint32_t bounties_injected;       /**< Injected into economic evaluator */
    uint32_t parse_errors;            /**< CBOR parse failures */
    uint32_t connection_failures;     /**< Socket/CoAP failures */
    uint32_t rate_limited;            /**< Bounties skipped due to rate limit */
} coap_feed_stats_t;

/* ──────────────────────────────────────────────────────────────────
 * Public API
 * ────────────────────────────────────────────────────────────────── */

/**
 * Start the CoAP bounty feed observation thread.
 *
 * Spawns a dedicated Zephyr thread that connects to the specified
 * CoAP endpoint, registers an OBSERVE on /bounties/active, and
 * parses incoming CBOR bounty descriptors.
 *
 * @param endpoint  CoAP endpoint URI (e.g., "coap://bounty.iotex.io").
 *                  Must remain valid for the lifetime of the thread.
 * @return 0 on success, negative errno on failure.
 */
int coap_bounty_feed_start(const char *endpoint);

/**
 * Stop the CoAP bounty feed thread.
 *
 * Deregisters the OBSERVE, closes the socket, and terminates
 * the thread. Safe to call even if not started.
 */
void coap_bounty_feed_stop(void);

/**
 * Get current feed state.
 */
coap_feed_state_t coap_bounty_feed_get_state(void);

/**
 * Get feed statistics.
 */
void coap_bounty_feed_get_stats(coap_feed_stats_t *out);

#ifdef __cplusplus
}
#endif

#endif /* COAP_BOUNTY_FEED_H */
