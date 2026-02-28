/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAPI BLE Companion App Sync — ESP32-S3 NimBLE GATT Service
 *
 * Provides BLE connectivity between the DualShock controller's ESP32-S3
 * compute module and the VAPI companion mobile app. Streams PoAC records
 * as GATT notifications, exposes agent state as a readable characteristic,
 * and accepts session control commands via a writable characteristic.
 *
 * GATT Service Layout:
 *   Service UUID: VAPI0001-CAFE-BABE-DEAD-000000000001
 *   ├── PoAC Stream     (Notify)  — 228-byte PoAC record chunks
 *   ├── Agent State     (Read)    — Current ds_agent_state_t + stats
 *   └── Session Control (Write)   — Start/stop session, tournament, force sync
 *
 * Design:
 *   - Circular buffer of 16 unsent PoAC records (FIFO drain on connect)
 *   - MTU negotiation for 228B+ payloads (requests 256B MTU)
 *   - Exponential backoff reconnection (1s → 2s → 4s → ... → 30s cap)
 *   - Thread-safe: all buffer ops protected by FreeRTOS mutex
 *
 * Target: ESP32-S3 with ESP-IDF NimBLE stack
 */

#ifndef BLE_SYNC_H
#define BLE_SYNC_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ──────────────────────────────────────────────────────────────────
 * Constants
 * ────────────────────────────────────────────────────────────────── */

/** Maximum unsent PoAC records buffered for BLE drain. */
#define BLE_POAC_BUFFER_SIZE    16

/** Requested MTU (must fit 228-byte PoAC record + 3 ATT overhead). */
#define BLE_REQUESTED_MTU       256

/** Maximum reconnection backoff in milliseconds. */
#define BLE_MAX_BACKOFF_MS      30000

/** Initial reconnection delay in milliseconds. */
#define BLE_INITIAL_BACKOFF_MS  1000

/* ──────────────────────────────────────────────────────────────────
 * Session Control Commands (written by companion app)
 * ────────────────────────────────────────────────────────────────── */

#define BLE_CMD_SESSION_START       0x01
#define BLE_CMD_SESSION_STOP        0x02
#define BLE_CMD_TOURNAMENT_ON       0x03
#define BLE_CMD_TOURNAMENT_OFF      0x04
#define BLE_CMD_FORCE_SYNC          0x05
#define BLE_CMD_SET_SENSE_INTERVAL  0x10  /* Payload: uint16_t ms (LE) */

/* ──────────────────────────────────────────────────────────────────
 * BLE Connection State
 * ────────────────────────────────────────────────────────────────── */

typedef enum {
    BLE_STATE_IDLE,          /**< Not advertising, not connected */
    BLE_STATE_ADVERTISING,   /**< Advertising, waiting for connection */
    BLE_STATE_CONNECTED,     /**< Connected, ready to stream */
    BLE_STATE_DRAINING,      /**< Connected, draining PoAC buffer */
    BLE_STATE_DISCONNECTED,  /**< Was connected, now in backoff */
} ble_sync_state_t;

/* ──────────────────────────────────────────────────────────────────
 * BLE Sync Statistics (exposed via Agent State characteristic)
 * ────────────────────────────────────────────────────────────────── */

typedef struct {
    uint32_t records_sent;      /**< Total PoAC records sent via BLE */
    uint32_t records_dropped;   /**< Records dropped due to full buffer */
    uint32_t reconnect_count;   /**< Total reconnection attempts */
    uint16_t current_mtu;       /**< Negotiated MTU size */
    uint8_t  buffer_occupancy;  /**< Current records in buffer [0-16] */
} ble_sync_stats_t;

/* ──────────────────────────────────────────────────────────────────
 * Public API
 * ────────────────────────────────────────────────────────────────── */

/**
 * Initialize BLE sync subsystem.
 *
 * Configures NimBLE GATT service, registers characteristics, and
 * begins advertising. Must be called after NimBLE host is synced.
 *
 * @return 0 on success, negative errno on failure.
 */
int ble_sync_init(void);

/**
 * Enqueue a PoAC record for BLE transmission.
 *
 * Thread-safe. If the buffer is full, the oldest record is dropped
 * and stats.records_dropped is incremented.
 *
 * @param record  Pointer to the 228-byte serialized PoAC record.
 * @param len     Record length (must be POAC_RECORD_WIRE_SIZE = 228).
 * @return 0 on success, -ENOMEM if dropped due to full buffer.
 */
int ble_sync_enqueue(const uint8_t *record, uint16_t len);

/**
 * Force drain: immediately notify all buffered records.
 *
 * Called by ds_agent_force_sync(). If not connected, triggers
 * immediate advertising restart.
 *
 * @return Number of records drained, or negative errno.
 */
int ble_sync_force_drain(void);

/**
 * Get current BLE sync state.
 */
ble_sync_state_t ble_sync_get_state(void);

/**
 * Get BLE sync statistics.
 */
void ble_sync_get_stats(ble_sync_stats_t *out);

/**
 * Shutdown BLE sync subsystem.
 *
 * Stops advertising, disconnects, and frees resources.
 */
void ble_sync_deinit(void);

/* ──────────────────────────────────────────────────────────────────
 * Callback — companion app command received
 * ────────────────────────────────────────────────────────────────── */

/**
 * Callback type for session control commands from the companion app.
 *
 * @param cmd      Command byte (BLE_CMD_*).
 * @param payload  Optional payload bytes (NULL if no payload).
 * @param len      Payload length (0 if no payload).
 */
typedef void (*ble_cmd_callback_t)(uint8_t cmd, const uint8_t *payload, uint16_t len);

/**
 * Register a callback for incoming BLE commands.
 * The agent should register this during init to handle session control.
 */
void ble_sync_register_cmd_callback(ble_cmd_callback_t cb);

#ifdef __cplusplus
}
#endif

#endif /* BLE_SYNC_H */
