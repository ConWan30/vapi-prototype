/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAPI BLE Companion App Sync — ESP32-S3 NimBLE Implementation
 *
 * GATT service streaming PoAC records to the VAPI companion mobile app.
 * Uses circular buffer with automatic drain on connection, MTU negotiation
 * for 228-byte record payloads, and exponential backoff reconnection.
 *
 * Target: ESP32-S3 with ESP-IDF v5.x NimBLE stack
 */

#include "ble_sync.h"

#include <string.h>
#include <errno.h>

#include "esp_log.h"
#include "esp_nimble_hci.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "ble_sync";

/* ══════════════════════════════════════════════════════════════════
 * UUIDs
 *
 * Service:         VAPI0001-CAFE-BABE-DEAD-000000000001
 * PoAC Stream:     VAPI0001-CAFE-BABE-DEAD-000000000002  (Notify)
 * Agent State:     VAPI0001-CAFE-BABE-DEAD-000000000003  (Read)
 * Session Control: VAPI0001-CAFE-BABE-DEAD-000000000004  (Write)
 * ══════════════════════════════════════════════════════════════════ */

/* 128-bit UUID base: VA-PI-00-01-CA-FE-BA-BE-DE-AD-00-00-00-00-00-XX */
static const ble_uuid128_t svc_uuid = BLE_UUID128_INIT(
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0xAD, 0xDE,
    0xBE, 0xBA, 0xFE, 0xCA, 0x01, 0x00, 0x50, 0x56  /* "VP" prefix */
);

static const ble_uuid128_t chr_poac_uuid = BLE_UUID128_INIT(
    0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0xAD, 0xDE,
    0xBE, 0xBA, 0xFE, 0xCA, 0x01, 0x00, 0x50, 0x56
);

static const ble_uuid128_t chr_state_uuid = BLE_UUID128_INIT(
    0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0xAD, 0xDE,
    0xBE, 0xBA, 0xFE, 0xCA, 0x01, 0x00, 0x50, 0x56
);

static const ble_uuid128_t chr_ctrl_uuid = BLE_UUID128_INIT(
    0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0xAD, 0xDE,
    0xBE, 0xBA, 0xFE, 0xCA, 0x01, 0x00, 0x50, 0x56
);

/* ══════════════════════════════════════════════════════════════════
 * Record Wire Size
 * ══════════════════════════════════════════════════════════════════ */

#define POAC_RECORD_WIRE_SIZE  228  /* 164B body + 64B signature */

/* ══════════════════════════════════════════════════════════════════
 * State
 * ══════════════════════════════════════════════════════════════════ */

/* Circular buffer of serialized PoAC records */
static uint8_t  s_buffer[BLE_POAC_BUFFER_SIZE][POAC_RECORD_WIRE_SIZE];
static uint8_t  s_buf_head = 0;   /* Next write position */
static uint8_t  s_buf_tail = 0;   /* Next read position */
static uint8_t  s_buf_count = 0;  /* Current occupancy */

static SemaphoreHandle_t s_buf_mutex = NULL;

/* Connection state */
static ble_sync_state_t  s_state = BLE_STATE_IDLE;
static uint16_t          s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static uint16_t          s_poac_attr_handle = 0;
static bool              s_notify_enabled = false;
static uint16_t          s_negotiated_mtu = 23; /* Default ATT MTU */

/* Reconnection backoff */
static uint32_t s_backoff_ms = BLE_INITIAL_BACKOFF_MS;

/* Statistics */
static ble_sync_stats_t s_stats = {0};

/* Command callback */
static ble_cmd_callback_t s_cmd_callback = NULL;

/* Drain task handle */
static TaskHandle_t s_drain_task = NULL;

/* ══════════════════════════════════════════════════════════════════
 * Forward Declarations
 * ══════════════════════════════════════════════════════════════════ */

static int  gatt_chr_access_poac(uint16_t conn_handle, uint16_t attr_handle,
                                  struct ble_gatt_access_ctxt *ctxt, void *arg);
static int  gatt_chr_access_state(uint16_t conn_handle, uint16_t attr_handle,
                                   struct ble_gatt_access_ctxt *ctxt, void *arg);
static int  gatt_chr_access_ctrl(uint16_t conn_handle, uint16_t attr_handle,
                                  struct ble_gatt_access_ctxt *ctxt, void *arg);
static void gap_event_handler(struct ble_gap_event *event, void *arg);
static void start_advertising(void);
static void drain_task_fn(void *arg);

/* ══════════════════════════════════════════════════════════════════
 * GATT Service Definition
 * ══════════════════════════════════════════════════════════════════ */

static const struct ble_gatt_svc_def gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                /* PoAC Stream — Notify characteristic */
                .uuid       = &chr_poac_uuid.u,
                .access_cb  = gatt_chr_access_poac,
                .val_handle = &s_poac_attr_handle,
                .flags      = BLE_GATT_CHR_F_NOTIFY,
            },
            {
                /* Agent State — Read characteristic */
                .uuid       = &chr_state_uuid.u,
                .access_cb  = gatt_chr_access_state,
                .flags      = BLE_GATT_CHR_F_READ,
            },
            {
                /* Session Control — Write characteristic */
                .uuid       = &chr_ctrl_uuid.u,
                .access_cb  = gatt_chr_access_ctrl,
                .flags      = BLE_GATT_CHR_F_WRITE,
            },
            { 0 }, /* Sentinel */
        },
    },
    { 0 }, /* Sentinel */
};

/* ══════════════════════════════════════════════════════════════════
 * GATT Characteristic Callbacks
 * ══════════════════════════════════════════════════════════════════ */

/**
 * PoAC Stream — notify only, no direct read.
 * The companion app subscribes via CCCD to receive PoAC records.
 */
static int gatt_chr_access_poac(uint16_t conn_handle, uint16_t attr_handle,
                                 struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    /* Notify-only characteristic; direct reads return empty */
    return 0;
}

/**
 * Agent State — read returns a packed struct:
 *   [0]      state (uint8)
 *   [1-4]    records_sent (uint32 LE)
 *   [5-8]    records_dropped (uint32 LE)
 *   [9-12]   reconnect_count (uint32 LE)
 *   [13-14]  current_mtu (uint16 LE)
 *   [15]     buffer_occupancy (uint8)
 */
static int gatt_chr_access_state(uint16_t conn_handle, uint16_t attr_handle,
                                  struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }

    uint8_t buf[16];
    buf[0] = (uint8_t)s_state;

    /* Pack little-endian (mobile apps expect LE) */
    buf[1] = (s_stats.records_sent >>  0) & 0xFF;
    buf[2] = (s_stats.records_sent >>  8) & 0xFF;
    buf[3] = (s_stats.records_sent >> 16) & 0xFF;
    buf[4] = (s_stats.records_sent >> 24) & 0xFF;

    buf[5] = (s_stats.records_dropped >>  0) & 0xFF;
    buf[6] = (s_stats.records_dropped >>  8) & 0xFF;
    buf[7] = (s_stats.records_dropped >> 16) & 0xFF;
    buf[8] = (s_stats.records_dropped >> 24) & 0xFF;

    buf[9]  = (s_stats.reconnect_count >>  0) & 0xFF;
    buf[10] = (s_stats.reconnect_count >>  8) & 0xFF;
    buf[11] = (s_stats.reconnect_count >> 16) & 0xFF;
    buf[12] = (s_stats.reconnect_count >> 24) & 0xFF;

    buf[13] = (s_stats.current_mtu >> 0) & 0xFF;
    buf[14] = (s_stats.current_mtu >> 8) & 0xFF;

    buf[15] = s_stats.buffer_occupancy;

    int rc = os_mbuf_append(ctxt->om, buf, sizeof(buf));
    return (rc == 0) ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

/**
 * Session Control — write receives a command byte + optional payload.
 * All commands are validated by the Autonomy Guard before execution.
 */
static int gatt_chr_access_ctrl(uint16_t conn_handle, uint16_t attr_handle,
                                 struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }

    /* Read the command from the mbuf */
    uint16_t om_len = OS_MBUF_PKTLEN(ctxt->om);
    if (om_len < 1) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }

    uint8_t cmd_buf[32]; /* Max command + payload */
    uint16_t copy_len = (om_len > sizeof(cmd_buf)) ? sizeof(cmd_buf) : om_len;

    int rc = ble_hs_mbuf_to_flat(ctxt->om, cmd_buf, copy_len, NULL);
    if (rc != 0) {
        return BLE_ATT_ERR_UNLIKELY;
    }

    uint8_t cmd = cmd_buf[0];
    const uint8_t *payload = (copy_len > 1) ? &cmd_buf[1] : NULL;
    uint16_t payload_len = (copy_len > 1) ? copy_len - 1 : 0;

    /* ── Autonomy Guard ── */
    /* Reject commands that would compromise anti-cheat integrity */
    switch (cmd) {
        case BLE_CMD_SESSION_START:
        case BLE_CMD_SESSION_STOP:
        case BLE_CMD_TOURNAMENT_ON:
        case BLE_CMD_TOURNAMENT_OFF:
        case BLE_CMD_FORCE_SYNC:
            /* Allowed — these don't affect PoAC generation */
            break;

        case BLE_CMD_SET_SENSE_INTERVAL:
            /* Validate: interval must be 100µs - 10s (0.1ms - 10000ms) */
            if (payload_len < 2) {
                ESP_LOGW(TAG, "SET_SENSE_INTERVAL: missing payload");
                return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
            }
            {
                uint16_t interval_ms = payload[0] | (payload[1] << 8);
                if (interval_ms < 1 || interval_ms > 10000) {
                    ESP_LOGW(TAG, "Autonomy Guard: REJECTED interval %u ms", interval_ms);
                    return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
                }
            }
            break;

        default:
            ESP_LOGW(TAG, "Autonomy Guard: REJECTED unknown command 0x%02x", cmd);
            return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    }

    /* Dispatch to registered callback */
    if (s_cmd_callback) {
        s_cmd_callback(cmd, payload, payload_len);
    }

    ESP_LOGI(TAG, "BLE command received: 0x%02x (payload=%u bytes)", cmd, payload_len);
    return 0;
}

/* ══════════════════════════════════════════════════════════════════
 * GAP Event Handler
 * ══════════════════════════════════════════════════════════════════ */

static void gap_event_handler(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
        case BLE_GAP_EVENT_CONNECT:
            if (event->connect.status == 0) {
                s_conn_handle = event->connect.conn_handle;
                s_state = BLE_STATE_CONNECTED;
                s_backoff_ms = BLE_INITIAL_BACKOFF_MS; /* Reset backoff */

                /* Request larger MTU for 228-byte records */
                ble_att_set_preferred_mtu(BLE_REQUESTED_MTU);
                ble_gattc_exchange_mtu(s_conn_handle, NULL, NULL);

                ESP_LOGI(TAG, "BLE connected (handle=%d)", s_conn_handle);

                /* Trigger immediate drain of buffered records */
                if (s_drain_task) {
                    xTaskNotifyGive(s_drain_task);
                }
            } else {
                ESP_LOGW(TAG, "BLE connect failed: %d", event->connect.status);
                s_state = BLE_STATE_DISCONNECTED;
                s_stats.reconnect_count++;

                /* Exponential backoff reconnect */
                vTaskDelay(pdMS_TO_TICKS(s_backoff_ms));
                if (s_backoff_ms < BLE_MAX_BACKOFF_MS) {
                    s_backoff_ms *= 2;
                    if (s_backoff_ms > BLE_MAX_BACKOFF_MS) {
                        s_backoff_ms = BLE_MAX_BACKOFF_MS;
                    }
                }
                start_advertising();
            }
            break;

        case BLE_GAP_EVENT_DISCONNECT:
            ESP_LOGI(TAG, "BLE disconnected (reason=%d)",
                     event->disconnect.reason);
            s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
            s_notify_enabled = false;
            s_state = BLE_STATE_DISCONNECTED;
            s_stats.reconnect_count++;

            /* Backoff then re-advertise */
            vTaskDelay(pdMS_TO_TICKS(s_backoff_ms));
            if (s_backoff_ms < BLE_MAX_BACKOFF_MS) {
                s_backoff_ms *= 2;
                if (s_backoff_ms > BLE_MAX_BACKOFF_MS) {
                    s_backoff_ms = BLE_MAX_BACKOFF_MS;
                }
            }
            start_advertising();
            break;

        case BLE_GAP_EVENT_MTU:
            s_negotiated_mtu = event->mtu.value;
            s_stats.current_mtu = s_negotiated_mtu;
            ESP_LOGI(TAG, "MTU negotiated: %d", s_negotiated_mtu);
            break;

        case BLE_GAP_EVENT_SUBSCRIBE:
            if (event->subscribe.attr_handle == s_poac_attr_handle) {
                s_notify_enabled = event->subscribe.cur_notify;
                ESP_LOGI(TAG, "PoAC notifications %s",
                         s_notify_enabled ? "ENABLED" : "DISABLED");

                /* Start draining if notifications just enabled */
                if (s_notify_enabled && s_drain_task) {
                    xTaskNotifyGive(s_drain_task);
                }
            }
            break;

        case BLE_GAP_EVENT_ADV_COMPLETE:
            /* Advertising timed out or completed — restart */
            if (s_state != BLE_STATE_CONNECTED) {
                start_advertising();
            }
            break;

        default:
            break;
    }
}

/* ══════════════════════════════════════════════════════════════════
 * Advertising
 * ══════════════════════════════════════════════════════════════════ */

static void start_advertising(void)
{
    struct ble_gap_adv_params adv_params = {0};
    adv_params.conn_mode = BLE_GAP_CONN_MODE_UND;  /* Undirected connectable */
    adv_params.disc_mode = BLE_GAP_DISC_MODE_GEN;  /* General discoverable */

    /* Advertising data: device name + service UUID */
    struct ble_hs_adv_fields fields = {0};
    const char *name = ble_svc_gap_device_name();
    fields.name = (const uint8_t *)name;
    fields.name_len = strlen(name);
    fields.name_is_complete = 1;
    fields.tx_pwr_lvl_is_present = 1;
    fields.tx_pwr_lvl = BLE_HS_ADV_TX_PWR_LVL_AUTO;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to set adv fields: %d", rc);
        return;
    }

    /* Include 128-bit service UUID in scan response */
    struct ble_hs_adv_fields rsp_fields = {0};
    rsp_fields.uuids128 = (ble_uuid128_t[]){svc_uuid};
    rsp_fields.num_uuids128 = 1;
    rsp_fields.uuids128_is_complete = 1;

    rc = ble_gap_adv_rsp_set_fields(&rsp_fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to set scan rsp: %d", rc);
        return;
    }

    /* Start advertising indefinitely (0 = forever) */
    rc = ble_gap_adv_start(
        BLE_OWN_ADDR_PUBLIC, NULL, BLE_HS_FOREVER,
        &adv_params, (ble_gap_event_fn *)gap_event_handler, NULL
    );
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to start adv: %d", rc);
        return;
    }

    s_state = BLE_STATE_ADVERTISING;
    ESP_LOGI(TAG, "BLE advertising started");
}

/* ══════════════════════════════════════════════════════════════════
 * Drain Task — sends buffered records as notifications
 * ══════════════════════════════════════════════════════════════════ */

static void drain_task_fn(void *arg)
{
    ESP_LOGI(TAG, "BLE drain task started");

    while (1) {
        /* Wait for notification (connect event, subscribe, force sync) */
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(5000));

        if (!s_notify_enabled || s_conn_handle == BLE_HS_CONN_HANDLE_NONE) {
            continue;
        }

        s_state = BLE_STATE_DRAINING;
        uint32_t drained = 0;

        xSemaphoreTake(s_buf_mutex, portMAX_DELAY);
        while (s_buf_count > 0) {
            /* Get the oldest record */
            uint8_t *record = s_buffer[s_buf_tail];
            uint16_t send_len = POAC_RECORD_WIRE_SIZE;

            /* Check MTU allows this payload (ATT header = 3 bytes) */
            if (send_len > (s_negotiated_mtu - 3)) {
                /* Fragment not supported in this version — skip oversized */
                ESP_LOGW(TAG, "Record %uB exceeds MTU %u, skipping",
                         send_len, s_negotiated_mtu);
                s_buf_tail = (s_buf_tail + 1) % BLE_POAC_BUFFER_SIZE;
                s_buf_count--;
                s_stats.records_dropped++;
                continue;
            }

            /* Build mbuf and send notification */
            struct os_mbuf *om = ble_hs_mbuf_from_flat(record, send_len);
            if (!om) {
                ESP_LOGW(TAG, "Failed to allocate mbuf");
                break;
            }

            /* Release mutex during BLE send (can block) */
            xSemaphoreGive(s_buf_mutex);

            int rc = ble_gatts_notify_custom(s_conn_handle,
                                              s_poac_attr_handle, om);
            xSemaphoreTake(s_buf_mutex, portMAX_DELAY);

            if (rc != 0) {
                ESP_LOGW(TAG, "Notify failed: %d, will retry", rc);
                break;
            }

            /* Successfully sent — advance tail */
            s_buf_tail = (s_buf_tail + 1) % BLE_POAC_BUFFER_SIZE;
            s_buf_count--;
            s_stats.records_sent++;
            drained++;

            /* Small yield between records to avoid flooding BLE */
            xSemaphoreGive(s_buf_mutex);
            vTaskDelay(pdMS_TO_TICKS(10));
            xSemaphoreTake(s_buf_mutex, portMAX_DELAY);
        }

        s_stats.buffer_occupancy = s_buf_count;
        xSemaphoreGive(s_buf_mutex);

        if (drained > 0) {
            ESP_LOGI(TAG, "Drained %lu PoAC records via BLE", (unsigned long)drained);
        }

        if (s_state == BLE_STATE_DRAINING) {
            s_state = BLE_STATE_CONNECTED;
        }
    }
}

/* ══════════════════════════════════════════════════════════════════
 * NimBLE Host Sync Callback
 * ══════════════════════════════════════════════════════════════════ */

static void ble_on_sync(void)
{
    /* Use best available address (public or random) */
    int rc = ble_hs_util_ensure_addr(0);
    if (rc != 0) {
        ESP_LOGE(TAG, "No valid BLE address");
        return;
    }

    start_advertising();
}

static void ble_on_reset(int reason)
{
    ESP_LOGW(TAG, "NimBLE host reset: reason=%d", reason);
}

/* ══════════════════════════════════════════════════════════════════
 * Public API Implementation
 * ══════════════════════════════════════════════════════════════════ */

int ble_sync_init(void)
{
    /* Create buffer mutex */
    s_buf_mutex = xSemaphoreCreateMutex();
    if (!s_buf_mutex) {
        ESP_LOGE(TAG, "Failed to create buffer mutex");
        return -ENOMEM;
    }

    /* Initialize NimBLE */
    int rc = nimble_port_init();
    if (rc != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init failed: %d", rc);
        return -EIO;
    }

    /* Set device name */
    ble_svc_gap_device_name_set("VAPI-Controller");

    /* Configure NimBLE host callbacks */
    ble_hs_cfg.sync_cb  = ble_on_sync;
    ble_hs_cfg.reset_cb = ble_on_reset;

    /* Register GATT services */
    ble_svc_gap_init();
    ble_svc_gatt_init();

    rc = ble_gatts_count_cfg(gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "GATT count cfg failed: %d", rc);
        return -EIO;
    }

    rc = ble_gatts_add_svcs(gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "GATT add svcs failed: %d", rc);
        return -EIO;
    }

    /* Create drain task */
    BaseType_t ret = xTaskCreatePinnedToCore(
        drain_task_fn, "ble_drain", 4096, NULL, 8, &s_drain_task, 0
    );
    if (ret != pdPASS) {
        ESP_LOGE(TAG, "Failed to create drain task");
        return -ENOMEM;
    }

    /* Start NimBLE host task (will call ble_on_sync when ready) */
    nimble_port_freertos_init(nimble_port_run);

    ESP_LOGI(TAG, "BLE sync initialized (buffer=%d, MTU=%d)",
             BLE_POAC_BUFFER_SIZE, BLE_REQUESTED_MTU);
    return 0;
}

int ble_sync_enqueue(const uint8_t *record, uint16_t len)
{
    if (!record || len != POAC_RECORD_WIRE_SIZE) {
        return -EINVAL;
    }

    int result = 0;
    xSemaphoreTake(s_buf_mutex, portMAX_DELAY);

    if (s_buf_count >= BLE_POAC_BUFFER_SIZE) {
        /* Buffer full — drop oldest record */
        s_buf_tail = (s_buf_tail + 1) % BLE_POAC_BUFFER_SIZE;
        s_buf_count--;
        s_stats.records_dropped++;
        result = -ENOMEM;
        ESP_LOGW(TAG, "Buffer full, dropped oldest record (total dropped: %lu)",
                 (unsigned long)s_stats.records_dropped);
    }

    memcpy(s_buffer[s_buf_head], record, POAC_RECORD_WIRE_SIZE);
    s_buf_head = (s_buf_head + 1) % BLE_POAC_BUFFER_SIZE;
    s_buf_count++;
    s_stats.buffer_occupancy = s_buf_count;

    xSemaphoreGive(s_buf_mutex);

    /* Trigger drain if connected and notifications enabled */
    if (s_notify_enabled && s_drain_task) {
        xTaskNotifyGive(s_drain_task);
    }

    return result;
}

int ble_sync_force_drain(void)
{
    if (s_state == BLE_STATE_IDLE) {
        return -ENODEV;
    }

    if (s_conn_handle == BLE_HS_CONN_HANDLE_NONE) {
        /* Not connected — restart advertising immediately */
        s_backoff_ms = BLE_INITIAL_BACKOFF_MS;
        start_advertising();
        return 0;
    }

    if (s_drain_task) {
        xTaskNotifyGive(s_drain_task);
    }

    xSemaphoreTake(s_buf_mutex, portMAX_DELAY);
    int count = s_buf_count;
    xSemaphoreGive(s_buf_mutex);

    return count;
}

ble_sync_state_t ble_sync_get_state(void)
{
    return s_state;
}

void ble_sync_get_stats(ble_sync_stats_t *out)
{
    if (!out) return;

    xSemaphoreTake(s_buf_mutex, portMAX_DELAY);
    memcpy(out, &s_stats, sizeof(ble_sync_stats_t));
    xSemaphoreGive(s_buf_mutex);
}

void ble_sync_deinit(void)
{
    /* Stop drain task */
    if (s_drain_task) {
        vTaskDelete(s_drain_task);
        s_drain_task = NULL;
    }

    /* Disconnect if connected */
    if (s_conn_handle != BLE_HS_CONN_HANDLE_NONE) {
        ble_gap_terminate(s_conn_handle, BLE_ERR_REM_USER_CONN_TERM);
        s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
    }

    /* Stop advertising */
    ble_gap_adv_stop();

    /* Deinit NimBLE */
    nimble_port_freertos_deinit();
    nimble_port_deinit();

    /* Free mutex */
    if (s_buf_mutex) {
        vSemaphoreDelete(s_buf_mutex);
        s_buf_mutex = NULL;
    }

    s_state = BLE_STATE_IDLE;
    ESP_LOGI(TAG, "BLE sync deinitialized");
}

void ble_sync_register_cmd_callback(ble_cmd_callback_t cb)
{
    s_cmd_callback = cb;
}
