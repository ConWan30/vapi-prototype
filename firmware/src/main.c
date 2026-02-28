/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAPI — Verified Autonomous Physical Intelligence
 * Main Application Entry Point for IoTeX Pebble Tracker
 *
 * Initializes all subsystems and starts the autonomous agent.
 * Boot sequence:
 *   1. Initialize PSA Crypto (CryptoCell-310)
 *   2. Initialize PoAC module (key generation/load, counter restore)
 *   3. Initialize perception layer (sensor drivers, GPS)
 *   4. Initialize economic evaluator (bounty discovery)
 *   5. Attest the TinyML model (compute model manifest hash)
 *   6. Start the agent (generates BOOT PoAC, begins autonomous operation)
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/drivers/gpio.h>
#include <nrf_modem_lib.h>
#include <modem/lte_lc.h>

#include "poac.h"
#include "perception.h"
#include "agent.h"
#include "economic.h"
#include "tinyml.h"

LOG_MODULE_REGISTER(vapi_main, CONFIG_VAPI_LOG_LEVEL);

/* VAPI firmware version — update on each release */
#define VAPI_VERSION_MAJOR  0
#define VAPI_VERSION_MINOR  2
#define VAPI_VERSION_PATCH  0
#define VAPI_VERSION_STRING "0.2.0-rc1"

/* --------------------------------------------------------------------------
 * LED and buzzer output (Pebble Tracker GPIO)
 * -------------------------------------------------------------------------- */

/* LED node from devicetree — Pebble has a single status LED */
#define LED_NODE DT_ALIAS(led0)
#if DT_NODE_HAS_STATUS(LED_NODE, okay)
static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(LED_NODE, gpios);
#else
#error "LED device not found in devicetree"
#endif

static void led_blink(int count, int on_ms, int off_ms)
{
    for (int i = 0; i < count; i++) {
        gpio_pin_set_dt(&led, 1);
        k_msleep(on_ms);
        gpio_pin_set_dt(&led, 0);
        if (i < count - 1) {
            k_msleep(off_ms);
        }
    }
}

/* --------------------------------------------------------------------------
 * TinyML model stub
 *
 * In production, this would be an Edge Impulse or TFLite Micro model
 * loaded from flash. The model binary is hashed at boot for the PoAC
 * model_manifest_hash field.
 *
 * Placeholder: a static byte array representing model weights.
 * Replace with actual model binary linked via CMake.
 * -------------------------------------------------------------------------- */

/* Placeholder model weights — replace with actual Edge Impulse export */
static const uint8_t tinyml_model_weights[] = {
    /* This would be the quantized INT8 model binary (~40-80KB).
     * For the prototype, we use a small placeholder so the
     * attestation pipeline is exercised end-to-end. */
    0x56, 0x41, 0x50, 0x49,  /* "VAPI" magic */
    0x01, 0x00, 0x00, 0x00,  /* Version 1.0 */
    /* ... actual model weights would follow ... */
};

#define TINYML_MODEL_VERSION  1
#define TINYML_MODEL_ARCH_ID  "edge_impulse_activity_v1"

/* --------------------------------------------------------------------------
 * Cellular connectivity
 * -------------------------------------------------------------------------- */

static int cellular_init(void)
{
    LOG_INF("Initializing modem...");

    int err = nrf_modem_lib_init();
    if (err) {
        LOG_ERR("Modem library init failed: %d", err);
        return err;
    }

    LOG_INF("Connecting to LTE network...");
    err = lte_lc_init_and_connect();
    if (err) {
        LOG_ERR("LTE connect failed: %d", err);
        /* Non-fatal — agent can operate offline and buffer PoACs */
        return err;
    }

    LOG_INF("LTE connected");
    return 0;
}

/* --------------------------------------------------------------------------
 * PoAC uplink callback
 *
 * Called by the agent every time a new PoAC record is generated.
 * Buffers records and transmits them over cellular when connectivity
 * is available.
 * -------------------------------------------------------------------------- */

#define POAC_UPLINK_QUEUE_SIZE  16

static poac_record_t uplink_queue[POAC_UPLINK_QUEUE_SIZE];
static uint8_t uplink_head;
static uint8_t uplink_count;
static K_MUTEX_DEFINE(uplink_mutex);

static void on_poac_generated(const poac_record_t *record)
{
    k_mutex_lock(&uplink_mutex, K_FOREVER);

    if (uplink_count < POAC_UPLINK_QUEUE_SIZE) {
        uplink_queue[uplink_head] = *record;
        uplink_head = (uplink_head + 1) % POAC_UPLINK_QUEUE_SIZE;
        uplink_count++;
        LOG_DBG("PoAC #%u queued for uplink (%u buffered)",
                record->monotonic_ctr, uplink_count);
    } else {
        LOG_WRN("Uplink queue full — dropping PoAC #%u", record->monotonic_ctr);
    }

    k_mutex_unlock(&uplink_mutex);
}

/* --------------------------------------------------------------------------
 * Agent state change callback
 * -------------------------------------------------------------------------- */

static void on_state_change(agent_state_t old_state, agent_state_t new_state)
{
    LOG_INF("Agent state: %d -> %d", old_state, new_state);

    /* Visual feedback via LED */
    switch (new_state) {
    case AGENT_STATE_BOOT:
        led_blink(1, 500, 0);
        break;
    case AGENT_STATE_IDLE:
        led_blink(2, 100, 100);
        break;
    case AGENT_STATE_ALERT:
        led_blink(5, 50, 50);  /* Rapid flash for alert */
        break;
    case AGENT_STATE_PSM:
        gpio_pin_set_dt(&led, 0); /* LED off in power save */
        break;
    default:
        break;
    }
}

/* --------------------------------------------------------------------------
 * Main
 * -------------------------------------------------------------------------- */

int main(void)
{
    int err;

    LOG_INF("=== VAPI: Verified Autonomous Physical Intelligence ===");
    LOG_INF("Firmware v" VAPI_VERSION_STRING " built: " __DATE__ " " __TIME__);

    /* 1. Initialize LED */
    if (!gpio_is_ready_dt(&led)) {
        LOG_ERR("LED device not ready");
        return -ENODEV;
    }
    gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
    led_blink(3, 200, 100); /* Boot indicator: 3 quick flashes */

    /* 2. Initialize PoAC subsystem (PSA Crypto + key management) */
    LOG_INF("Initializing PoAC subsystem...");
    err = poac_init();
    if (err) {
        LOG_ERR("PoAC init failed: %d", err);
        return err;
    }

    /* Log device public key for on-chain registration */
    uint8_t pubkey[65];
    size_t pubkey_len;
    err = poac_get_device_pubkey(pubkey, &pubkey_len);
    if (err == 0) {
        LOG_INF("Device public key (first 8 bytes): "
                "%02x%02x%02x%02x%02x%02x%02x%02x...",
                pubkey[0], pubkey[1], pubkey[2], pubkey[3],
                pubkey[4], pubkey[5], pubkey[6], pubkey[7]);
    }

    /* 3. Attest the TinyML model */
    LOG_INF("Attesting TinyML model...");
    err = poac_attest_model(tinyml_model_weights, sizeof(tinyml_model_weights),
                            TINYML_MODEL_VERSION,
                            TINYML_MODEL_ARCH_ID, strlen(TINYML_MODEL_ARCH_ID));
    if (err) {
        LOG_ERR("Model attestation failed: %d", err);
        return err;
    }

    /* 4. Initialize TinyML inference engine */
    LOG_INF("Initializing TinyML engine...");
    err = tinyml_init();
    if (err) {
        LOG_WRN("TinyML init failed: %d (heuristic fallback active)", err);
    }

    /* 5. Initialize perception layer (sensors) */
    LOG_INF("Initializing sensors...");
    err = perception_init();
    if (err) {
        LOG_WRN("Perception init partial failure: %d (continuing)", err);
        /* Non-fatal — agent adapts to available sensors */
    }

    /* 6. Start GPS tracking */
    perception_gps_start();

    /* 7. Initialize cellular connectivity */
    err = cellular_init();
    if (err) {
        LOG_WRN("Cellular not available: %d (agent will buffer PoACs)", err);
    }

    /* 8. Initialize economic evaluator */
    err = economic_init(NULL); /* No CoAP endpoint yet — manual bounty injection */
    if (err) {
        LOG_WRN("Economic init failed: %d", err);
    }

    /* 9. Configure and start the agent */
    agent_config_t config = {
        .sense_interval_ms      = 30000,    /* 30 seconds */
        .anomaly_threshold      = 200,      /* ~78% confidence */
        .deliberate_interval_ms = 300000,   /* 5 minutes */
        .battery_critical_pct   = 10,
        .battery_low_pct        = 25,
        .cloud_sync_interval_ms = 3600000,  /* 1 hour */
        .cloud_enabled          = false,    /* Disabled until endpoint configured */
        .bounty_enabled         = true,
        .min_battery_for_bounty = 15,
    };

    LOG_INF("Starting VAPI agent...");
    err = agent_init(&config);
    if (err) {
        LOG_ERR("Agent init failed: %d", err);
        return err;
    }

    /* Register callbacks */
    agent_register_poac_callback(on_poac_generated);
    agent_register_state_callback(on_state_change);

    /* Launch! */
    err = agent_start();
    if (err) {
        LOG_ERR("Agent start failed: %d", err);
        return err;
    }

    LOG_INF("=== VAPI agent is now autonomous ===");
    LOG_INF("PoAC counter: %u", poac_get_counter());

    /*
     * Main thread now handles uplink duty.
     * Periodically flushes the PoAC uplink queue over cellular.
     */
    while (true) {
        k_sleep(K_SECONDS(60));

        /* Flush uplink queue */
        k_mutex_lock(&uplink_mutex, K_FOREVER);
        uint8_t to_send = uplink_count;
        k_mutex_unlock(&uplink_mutex);

        if (to_send > 0) {
            LOG_INF("Uplink: %u PoAC records pending", to_send);
            /*
             * TODO: Serialize records and send via MQTT/CoAP to
             * IoTeX W3bstream or the PoACVerifier contract bridge.
             *
             * For each record:
             *   1. Serialize to ~170 byte buffer
             *   2. Send via MQTT publish to "vapi/{device_id}/poac"
             *   3. On ACK, remove from queue
             */
        }

        /* Periodic status log */
        economic_stats_t econ_stats;
        economic_get_stats(&econ_stats);
        LOG_INF("Status: state=%d, counter=%u, bounties=%u/%u, reward=%u uIOTX",
                agent_get_state(), poac_get_counter(),
                econ_stats.bounties_completed, econ_stats.bounties_evaluated,
                econ_stats.total_reward_micro);
    }

    return 0;
}
