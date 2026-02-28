/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * CoAP Bounty Feed — Zephyr CoAP OBSERVE Client Implementation
 *
 * Subscribes to the IoTeX bounty marketplace via CoAP OBSERVE and
 * parses CBOR-encoded bounty descriptors for the economic evaluator.
 *
 * Thread model:
 *   - Single dedicated thread at priority 12 (below agent threads)
 *   - 2KB stack (CoAP + CBOR parsing fits comfortably)
 *   - Rate limits bounty injection to max 1 per 10 seconds
 *   - Reconnects with exponential backoff on failure
 *
 * CBOR wire format (per bounty):
 *   {
 *     "id":       uint32,        // bounty_id
 *     "reward":   uint32,        // reward_iotx_micro
 *     "sensors":  uint16,        // sensor_requirements bitfield
 *     "samples":  uint16,        // min_samples
 *     "interval": uint32,        // sample_interval_s
 *     "duration": uint32,        // duration_s
 *     "deadline": int64,         // deadline_ms
 *     "lat_min":  float,         // zone_lat_min
 *     "lat_max":  float,         // zone_lat_max
 *     "lon_min":  float,         // zone_lon_min
 *     "lon_max":  float,         // zone_lon_max
 *     "voc_thr":  float,         // voc_threshold (0 = none)
 *     "temp_hi":  float,         // temp_threshold_hi (0 = none)
 *     "temp_lo":  float,         // temp_threshold_lo (0 = none)
 *   }
 *
 * Target: nRF9160 (Zephyr RTOS, nRF Connect SDK v2.7+)
 */

#include "coap_bounty_feed.h"
#include "economic.h"

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/net/socket.h>
#include <zephyr/net/coap.h>
#include <zcbor_decode.h>
#include <zcbor_common.h>

#include <string.h>
#include <errno.h>

LOG_MODULE_REGISTER(coap_feed, CONFIG_VAPI_LOG_LEVEL);

/* ══════════════════════════════════════════════════════════════════
 * Internal State
 * ══════════════════════════════════════════════════════════════════ */

static coap_feed_state_t s_state = COAP_FEED_IDLE;
static coap_feed_stats_t s_stats = {0};

/* Thread */
static K_THREAD_STACK_DEFINE(feed_stack, COAP_BOUNTY_STACK_SIZE);
static struct k_thread feed_thread;
static k_tid_t feed_tid = NULL;
static volatile bool s_running = false;

/* Endpoint (borrowed pointer — must remain valid) */
static const char *s_endpoint = NULL;

/* Socket */
static int s_sock = -1;

/* CoAP message buffers */
static uint8_t s_tx_buf[256];
static uint8_t s_rx_buf[COAP_BOUNTY_MAX_PAYLOAD + 128]; /* payload + CoAP headers */

/* Rate limiting */
static int64_t s_last_inject_ms = 0;

/* CoAP token for OBSERVE */
static uint8_t s_token[8];
static uint8_t s_token_len = 0;

/* ══════════════════════════════════════════════════════════════════
 * CBOR Parsing — Extract bounty_descriptor_t from CBOR map
 * ══════════════════════════════════════════════════════════════════ */

/**
 * Parse a single CBOR map into a bounty_descriptor_t.
 *
 * Uses zcbor (Zephyr's built-in CBOR library) for safe parsing.
 * Unknown keys are silently skipped for forward compatibility.
 *
 * @return 0 on success, negative errno on parse error.
 */
static int parse_bounty_cbor(const uint8_t *data, size_t len,
                              bounty_descriptor_t *out)
{
    zcbor_state_t zs[3]; /* Decoder state (3 levels: array → map → values) */
    zcbor_new_decode_state(zs, ARRAY_SIZE(zs), data, len, 1, NULL, 0);

    memset(out, 0, sizeof(*out));

    /* Expect a CBOR map */
    bool ok = true;
    size_t map_count;
    ok = ok && zcbor_map_start_decode(zs);

    /* Iterate map entries */
    while (ok && !zcbor_map_end_decode(zs)) {
        /* Key: text string */
        struct zcbor_string key;
        if (!zcbor_tstr_decode(zs, &key)) {
            /* Skip non-string key */
            zcbor_any_skip(zs, NULL);
            zcbor_any_skip(zs, NULL);
            continue;
        }

        /* Match key to bounty_descriptor_t field */
        #define KEY_IS(s) (key.len == sizeof(s)-1 && memcmp(key.value, s, key.len) == 0)

        if (KEY_IS("id")) {
            uint32_t v;
            if (zcbor_uint32_decode(zs, &v)) out->bounty_id = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("reward")) {
            uint32_t v;
            if (zcbor_uint32_decode(zs, &v)) out->reward_iotx_micro = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("sensors")) {
            uint32_t v;
            if (zcbor_uint32_decode(zs, &v)) out->sensor_requirements = (uint16_t)v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("samples")) {
            uint32_t v;
            if (zcbor_uint32_decode(zs, &v)) out->min_samples = (uint16_t)v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("interval")) {
            uint32_t v;
            if (zcbor_uint32_decode(zs, &v)) out->sample_interval_s = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("duration")) {
            uint32_t v;
            if (zcbor_uint32_decode(zs, &v)) out->duration_s = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("deadline")) {
            int64_t v;
            if (zcbor_int64_decode(zs, &v)) out->deadline_ms = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("lat_min")) {
            double v;
            if (zcbor_float64_decode(zs, &v)) out->zone_lat_min = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("lat_max")) {
            double v;
            if (zcbor_float64_decode(zs, &v)) out->zone_lat_max = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("lon_min")) {
            double v;
            if (zcbor_float64_decode(zs, &v)) out->zone_lon_min = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("lon_max")) {
            double v;
            if (zcbor_float64_decode(zs, &v)) out->zone_lon_max = v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("voc_thr")) {
            double v;
            if (zcbor_float64_decode(zs, &v)) out->voc_threshold = (float)v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("temp_hi")) {
            double v;
            if (zcbor_float64_decode(zs, &v)) out->temp_threshold_hi = (float)v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else if (KEY_IS("temp_lo")) {
            double v;
            if (zcbor_float64_decode(zs, &v)) out->temp_threshold_lo = (float)v;
            else { s_stats.parse_errors++; return -EINVAL; }
        } else {
            /* Unknown key — skip value for forward compatibility */
            zcbor_any_skip(zs, NULL);
        }

        #undef KEY_IS
    }

    s_stats.bounties_parsed++;
    return 0;
}

/* ══════════════════════════════════════════════════════════════════
 * CoAP OBSERVE Registration
 * ══════════════════════════════════════════════════════════════════ */

/**
 * Build and send a CoAP OBSERVE GET request for /bounties/active.
 *
 * Uses CON (Confirmable) for reliability with Zephyr's built-in
 * retransmission.
 *
 * @return 0 on success, negative errno on failure.
 */
static int send_observe_request(void)
{
    struct coap_packet request;

    /* Generate random token */
    s_token_len = sizeof(s_token);
    sys_rand_get(s_token, s_token_len);

    int rc = coap_packet_init(&request, s_tx_buf, sizeof(s_tx_buf),
                               COAP_VERSION_1,
                               COAP_TYPE_CON,
                               s_token_len, s_token,
                               COAP_METHOD_GET,
                               coap_next_id());
    if (rc < 0) {
        LOG_ERR("Failed to init CoAP packet: %d", rc);
        return rc;
    }

    /* Add OBSERVE option (register = 0) */
    rc = coap_append_option_int(&request, COAP_OPTION_OBSERVE, 0);
    if (rc < 0) {
        LOG_ERR("Failed to add OBSERVE option: %d", rc);
        return rc;
    }

    /* URI-Path: /bounties/active (two segments) */
    rc = coap_packet_append_option(&request, COAP_OPTION_URI_PATH,
                                    "bounties", 8);
    if (rc < 0) return rc;

    rc = coap_packet_append_option(&request, COAP_OPTION_URI_PATH,
                                    "active", 6);
    if (rc < 0) return rc;

    /* Content-Format: application/cbor (60) */
    rc = coap_append_option_int(&request, COAP_OPTION_ACCEPT,
                                 60 /* application/cbor */);
    if (rc < 0) return rc;

    /* Send */
    ssize_t sent = send(s_sock, request.data, request.offset, 0);
    if (sent < 0) {
        LOG_ERR("CoAP send failed: %d", errno);
        s_stats.connection_failures++;
        return -errno;
    }

    LOG_INF("OBSERVE request sent (%d bytes, token=%02x%02x...)",
            (int)sent, s_token[0], s_token[1]);
    return 0;
}

/* ══════════════════════════════════════════════════════════════════
 * Response Handling
 * ══════════════════════════════════════════════════════════════════ */

/**
 * Process a CoAP response containing CBOR-encoded bounty array.
 *
 * Expected format: CBOR array of bounty maps.
 * Each map is parsed into bounty_descriptor_t and injected into
 * the economic evaluator (rate-limited).
 */
static void handle_response(const uint8_t *payload, size_t payload_len)
{
    s_stats.notifications_received++;

    if (payload_len == 0) {
        LOG_DBG("Empty OBSERVE notification (no bounties)");
        return;
    }

    /* Decode outer CBOR array */
    zcbor_state_t zs[4];
    zcbor_new_decode_state(zs, ARRAY_SIZE(zs), payload, payload_len, 10, NULL, 0);

    if (!zcbor_list_start_decode(zs)) {
        LOG_WRN("Failed to decode CBOR array");
        s_stats.parse_errors++;
        return;
    }

    while (!zcbor_list_end_decode(zs)) {
        /* Save decoder position for individual bounty */
        size_t remaining = zs->payload_end - zs->payload;
        const uint8_t *bounty_start = zs->payload;

        bounty_descriptor_t bounty;
        int rc = parse_bounty_cbor(zs->payload, remaining, &bounty);

        if (rc != 0) {
            LOG_WRN("Failed to parse bounty: %d", rc);
            /* Try to skip this entry and continue */
            zcbor_any_skip(zs, NULL);
            continue;
        }

        /* Skip the map we just parsed in the outer array */
        zcbor_any_skip(zs, NULL);

        /* Rate limiting: max 1 injection per COAP_BOUNTY_RATE_LIMIT_S */
        int64_t now_ms = k_uptime_get();
        int64_t elapsed_s = (now_ms - s_last_inject_ms) / 1000;

        if (elapsed_s < COAP_BOUNTY_RATE_LIMIT_S) {
            s_stats.rate_limited++;
            LOG_DBG("Rate limited: bounty %u skipped (%llds since last)",
                    bounty.bounty_id, (long long)elapsed_s);
            continue;
        }

        /* Inject into economic evaluator */
        rc = economic_inject_bounty(&bounty);
        if (rc == 0) {
            s_stats.bounties_injected++;
            s_last_inject_ms = now_ms;
            LOG_INF("Injected bounty %u (reward=%u µIOTX, samples=%u)",
                    bounty.bounty_id, bounty.reward_iotx_micro,
                    bounty.min_samples);
        } else {
            LOG_WRN("Failed to inject bounty %u: %d", bounty.bounty_id, rc);
        }
    }
}

/* ══════════════════════════════════════════════════════════════════
 * Feed Thread
 * ══════════════════════════════════════════════════════════════════ */

static void feed_thread_fn(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1); ARG_UNUSED(p2); ARG_UNUSED(p3);

    LOG_INF("CoAP bounty feed thread started (endpoint=%s)", s_endpoint);

    uint32_t backoff_ms = 1000;
    uint32_t consecutive_failures = 0;

    while (s_running) {
        /* ── 1. DNS resolve + socket create ── */
        s_state = COAP_FEED_RESOLVING;

        struct sockaddr_in addr4 = {0};
        addr4.sin_family = AF_INET;
        addr4.sin_port = htons(COAP_BOUNTY_PORT);

        /* Simple hostname resolution via DNS */
        struct zsock_addrinfo hints = {
            .ai_family = AF_INET,
            .ai_socktype = SOCK_DGRAM,
        };
        struct zsock_addrinfo *result = NULL;

        int rc = zsock_getaddrinfo(s_endpoint, "5683", &hints, &result);
        if (rc != 0 || result == NULL) {
            LOG_ERR("DNS resolve failed for %s: %d", s_endpoint, rc);
            s_stats.connection_failures++;
            consecutive_failures++;
            goto backoff;
        }

        memcpy(&addr4, result->ai_addr, sizeof(addr4));
        zsock_freeaddrinfo(result);

        /* Create UDP socket */
        s_sock = zsock_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (s_sock < 0) {
            LOG_ERR("Socket create failed: %d", errno);
            s_stats.connection_failures++;
            consecutive_failures++;
            goto backoff;
        }

        /* Set receive timeout */
        struct timeval tv = {
            .tv_sec = COAP_BOUNTY_OBSERVE_TIMEOUT_S,
            .tv_usec = 0,
        };
        zsock_setsockopt(s_sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

        /* Connect (for send/recv convenience) */
        rc = zsock_connect(s_sock, (struct sockaddr *)&addr4, sizeof(addr4));
        if (rc < 0) {
            LOG_ERR("Socket connect failed: %d", errno);
            s_stats.connection_failures++;
            zsock_close(s_sock);
            s_sock = -1;
            consecutive_failures++;
            goto backoff;
        }

        /* ── 2. Register OBSERVE ── */
        s_state = COAP_FEED_OBSERVING;
        consecutive_failures = 0;
        backoff_ms = 1000; /* Reset backoff on success */

        rc = send_observe_request();
        if (rc < 0) {
            zsock_close(s_sock);
            s_sock = -1;
            consecutive_failures++;
            goto backoff;
        }

        /* ── 3. Receive loop (OBSERVE notifications) ── */
        while (s_running && s_state == COAP_FEED_OBSERVING) {
            ssize_t received = zsock_recv(s_sock, s_rx_buf, sizeof(s_rx_buf), 0);

            if (received < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    /* Timeout — re-register OBSERVE */
                    LOG_INF("OBSERVE timeout, re-registering");
                    rc = send_observe_request();
                    if (rc < 0) {
                        break; /* Will reconnect */
                    }
                    continue;
                }
                LOG_ERR("recv failed: %d", errno);
                s_stats.connection_failures++;
                break;
            }

            if (received == 0) {
                continue;
            }

            /* Parse CoAP response */
            struct coap_packet response;
            rc = coap_packet_parse(&response, s_rx_buf, received, NULL, 0);
            if (rc < 0) {
                LOG_WRN("Failed to parse CoAP response: %d", rc);
                s_stats.parse_errors++;
                continue;
            }

            /* Verify token matches our OBSERVE */
            uint8_t resp_token[8];
            uint8_t resp_token_len = coap_header_get_token(&response, resp_token);
            if (resp_token_len != s_token_len ||
                memcmp(resp_token, s_token, s_token_len) != 0) {
                LOG_DBG("Token mismatch, ignoring");
                continue;
            }

            /* Check response code */
            uint8_t code = coap_header_get_code(&response);
            if (code != COAP_RESPONSE_CODE_CONTENT) {
                LOG_WRN("Non-content response: %d.%02d",
                        code >> 5, code & 0x1F);
                if (code == COAP_RESPONSE_CODE_NOT_FOUND) {
                    /* Resource gone — back off and retry */
                    break;
                }
                continue;
            }

            /* Extract payload */
            uint16_t payload_len;
            const uint8_t *payload = coap_packet_get_payload(&response,
                                                              &payload_len);
            if (payload && payload_len > 0) {
                handle_response(payload, payload_len);
            }

            /* Send ACK for CON responses */
            uint8_t type = coap_header_get_type(&response);
            if (type == COAP_TYPE_CON) {
                struct coap_packet ack;
                uint8_t ack_buf[32];
                coap_packet_init(&ack, ack_buf, sizeof(ack_buf),
                                 COAP_VERSION_1,
                                 COAP_TYPE_ACK,
                                 resp_token_len, resp_token,
                                 0, /* empty code for ACK */
                                 coap_header_get_id(&response));
                send(s_sock, ack.data, ack.offset, 0);
            }
        }

        /* Clean up socket */
        if (s_sock >= 0) {
            zsock_close(s_sock);
            s_sock = -1;
        }

backoff:
        if (!s_running) break;

        s_state = COAP_FEED_BACKOFF;

        /* Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s cap */
        if (consecutive_failures > COAP_BOUNTY_MAX_FAILURES) {
            backoff_ms = 30000;
        } else if (consecutive_failures > 0) {
            backoff_ms = 1000 << (consecutive_failures - 1);
            if (backoff_ms > 30000) backoff_ms = 30000;
        }

        LOG_INF("Backoff %u ms before reconnect (failures=%u)",
                backoff_ms, consecutive_failures);
        k_sleep(K_MSEC(backoff_ms));
    }

    s_state = COAP_FEED_STOPPED;
    LOG_INF("CoAP bounty feed thread stopped");
}

/* ══════════════════════════════════════════════════════════════════
 * Public API
 * ══════════════════════════════════════════════════════════════════ */

int coap_bounty_feed_start(const char *endpoint)
{
    if (!endpoint) {
        return -EINVAL;
    }

    if (s_running) {
        LOG_WRN("Feed already running");
        return -EALREADY;
    }

    s_endpoint = endpoint;
    s_running = true;
    memset(&s_stats, 0, sizeof(s_stats));

    feed_tid = k_thread_create(&feed_thread, feed_stack,
                                K_THREAD_STACK_SIZEOF(feed_stack),
                                feed_thread_fn, NULL, NULL, NULL,
                                COAP_BOUNTY_THREAD_PRIO, 0, K_NO_WAIT);

    k_thread_name_set(feed_tid, "coap_bounty");

    LOG_INF("CoAP bounty feed started (endpoint=%s)", endpoint);
    return 0;
}

void coap_bounty_feed_stop(void)
{
    if (!s_running) return;

    s_running = false;

    /* Close socket to unblock recv */
    if (s_sock >= 0) {
        zsock_close(s_sock);
        s_sock = -1;
    }

    /* Wait for thread to exit */
    if (feed_tid) {
        k_thread_join(&feed_thread, K_SECONDS(5));
        feed_tid = NULL;
    }

    s_state = COAP_FEED_STOPPED;
    LOG_INF("CoAP bounty feed stopped");
}

coap_feed_state_t coap_bounty_feed_get_state(void)
{
    return s_state;
}

void coap_bounty_feed_get_stats(coap_feed_stats_t *out)
{
    if (out) {
        memcpy(out, &s_stats, sizeof(coap_feed_stats_t));
    }
}
