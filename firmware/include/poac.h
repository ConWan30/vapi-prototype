/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Proof of Autonomous Cognition (PoAC) — Core Module
 *
 * Implements the cryptographic primitive for Verified Autonomous Physical
 * Intelligence (VAPI). Each PoAC record commits the agent's full
 * perception-reasoning-action loop into a tamper-evident, hardware-attested
 * chain using CryptoCell-310 on the nRF9160.
 *
 * Target: IoTeX Pebble Tracker (nRF9160, Zephyr RTOS, nRF Connect SDK v2.7+)
 */

#ifndef POAC_H
#define POAC_H

#include <stdint.h>
#include <stdbool.h>
#include <zephyr/kernel.h>

#ifdef __cplusplus
extern "C" {
#endif

/* PoAC protocol version — increment on record layout changes */
#define POAC_PROTOCOL_VERSION  1

/* PoAC record size constants */
#define POAC_HASH_SIZE       32   /* SHA-256 digest */
#define POAC_SIG_SIZE        64   /* ECDSA-P256 signature (r‖s) */
#define POAC_RECORD_SIZE     202  /* Approximate serialized size */

/* Action codes — extend as agent behaviors grow */
#define POAC_ACTION_NONE           0x00
#define POAC_ACTION_REPORT         0x01  /* Standard telemetry report */
#define POAC_ACTION_ALERT          0x02  /* Anomaly alert (buzzer + uplink) */
#define POAC_ACTION_BOUNTY_ACCEPT  0x03  /* Accepted on-chain bounty */
#define POAC_ACTION_BOUNTY_DECLINE 0x04  /* Declined bounty (cost > reward) */
#define POAC_ACTION_BOUNTY_CLAIM   0x05  /* Submitting bounty fulfillment */
#define POAC_ACTION_PSM_ENTER      0x06  /* Entering power save mode */
#define POAC_ACTION_PSM_EXIT       0x07  /* Exiting power save mode */
#define POAC_ACTION_MODEL_UPDATE   0x08  /* OTA model weights updated */
#define POAC_ACTION_BOOT           0x09  /* Agent boot / self-test */
#define POAC_ACTION_SWARM_SYNC     0x0A  /* Swarm coordination message */

/* Inference result codes — application-specific, these are examples */
#define POAC_INFER_NOMINAL         0x00  /* Normal conditions */
#define POAC_INFER_ANOMALY_LOW     0x01  /* Low-confidence anomaly */
#define POAC_INFER_ANOMALY_HIGH    0x02  /* High-confidence anomaly */
#define POAC_INFER_CLASS_STATIONARY 0x10
#define POAC_INFER_CLASS_WALKING    0x11
#define POAC_INFER_CLASS_VEHICLE    0x12
#define POAC_INFER_CLASS_FALL       0x13

/* No active bounty sentinel */
#define POAC_NO_BOUNTY  0

/**
 * The PoAC record — atomic unit of verified autonomous cognition.
 *
 * ~202 bytes serialized. Fits in a single NB-IoT uplink frame.
 * All hashes computed via CryptoCell-310 (PSA Crypto API).
 * Signature covers all fields except itself.
 */
typedef struct __attribute__((packed)) {
    /* Chain linkage — SHA-256 of previous record (serialized, excluding sig) */
    uint8_t  prev_poac_hash[POAC_HASH_SIZE];

    /* Perceptual commitment — SHA-256 of raw sensor buffer */
    uint8_t  sensor_commitment[POAC_HASH_SIZE];

    /* Model attestation — SHA-256(weights ‖ version ‖ arch_id), cached at boot */
    uint8_t  model_manifest_hash[POAC_HASH_SIZE];

    /* World model commitment — SHA-256 of compressed agent state at decision time.
     * Enables forensic reconstruction of the agent's accumulated context,
     * distinguishing decisions driven by different observation histories. */
    uint8_t  world_model_hash[POAC_HASH_SIZE];

    /* Inference output — encoded class ID or anomaly score */
    uint8_t  inference_result;

    /* Action taken by the agent */
    uint8_t  action_code;

    /* Model confidence — maps [0.0, 1.0] to [0, 255] */
    uint8_t  confidence;

    /* Battery percentage at decision time — physical constraint evidence */
    uint8_t  battery_pct;

    /* Monotonic counter — strictly increasing, replay protection */
    uint32_t monotonic_ctr;

    /* GPS-synced timestamp in milliseconds since Unix epoch */
    int64_t  timestamp_ms;

    /* WGS84 coordinates */
    double   latitude;
    double   longitude;

    /* On-chain bounty reference (0 = no bounty context) */
    uint32_t bounty_id;

    /* ECDSA-P256 signature over all preceding fields */
    uint8_t  signature[POAC_SIG_SIZE];
} poac_record_t;

/**
 * Initialize the PoAC subsystem.
 *
 * - Initializes PSA Crypto (CryptoCell-310 backend).
 * - Loads or generates the device ECDSA-P256 keypair in secure storage.
 * - Initializes the monotonic counter from persistent storage.
 * - Zeros the chain head (prev_poac_hash).
 *
 * Must be called once at boot, before any other poac_* functions.
 *
 * @return 0 on success, negative errno on failure.
 */
int poac_init(void);

/**
 * Compute sensor commitment from raw sensor buffer.
 *
 * SHA-256 is computed inside CryptoCell-310.
 *
 * @param sensor_buf   Pointer to raw sensor data buffer.
 * @param sensor_len   Length of sensor data in bytes.
 * @param out_hash     Output: 32-byte SHA-256 digest.
 * @return 0 on success, negative errno on failure.
 */
int poac_commit_sensors(const uint8_t *sensor_buf, size_t sensor_len,
                        uint8_t out_hash[POAC_HASH_SIZE]);

/**
 * Compute model manifest hash.
 *
 * Should be called once at boot (and on OTA model update).
 * Result is cached internally for inclusion in subsequent records.
 *
 * @param weights      Pointer to model weight data.
 * @param weights_len  Length of weight data in bytes.
 * @param version      Model version identifier.
 * @param arch_id      Architecture identifier (e.g., "edge_impulse_v1").
 * @param arch_id_len  Length of arch_id string.
 * @return 0 on success, negative errno on failure.
 */
int poac_attest_model(const uint8_t *weights, size_t weights_len,
                      uint32_t version,
                      const char *arch_id, size_t arch_id_len);

/**
 * Generate a complete PoAC record.
 *
 * Performs the following atomically:
 *   1. Sets prev_poac_hash from internal chain head.
 *   2. Copies sensor_commitment and model_manifest_hash.
 *   3. Populates inference/action/confidence/battery fields.
 *   4. Increments and writes monotonic counter.
 *   5. Serializes all fields (excluding signature).
 *   6. Signs via ECDSA-P256 using CryptoCell-310.
 *   7. Updates internal chain head to hash of this record.
 *
 * @param sensor_hash       Pre-computed sensor commitment (from poac_commit_sensors).
 * @param wm_hash           Pre-computed world model hash (SHA-256 of serialized
 *                          world_model_t). NULL to use zero hash (e.g., at boot).
 * @param inference_result  Encoded inference output.
 * @param action_code       Action taken.
 * @param confidence        Model confidence [0-255].
 * @param battery_pct       Current battery percentage [0-100].
 * @param timestamp_ms      GPS-synced timestamp.
 * @param latitude          WGS84 latitude.
 * @param longitude         WGS84 longitude.
 * @param bounty_id         On-chain bounty ID (0 if none).
 * @param out_record        Output: fully populated and signed PoAC record.
 * @return 0 on success, negative errno on failure.
 */
int poac_generate(const uint8_t sensor_hash[POAC_HASH_SIZE],
                  const uint8_t *wm_hash,
                  uint8_t inference_result,
                  uint8_t action_code,
                  uint8_t confidence,
                  uint8_t battery_pct,
                  int64_t timestamp_ms,
                  double latitude,
                  double longitude,
                  uint32_t bounty_id,
                  poac_record_t *out_record);

/**
 * Verify a PoAC record's signature.
 *
 * Uses the device's own public key (for self-check) or a provided key.
 *
 * @param record     The record to verify.
 * @param pubkey     Public key bytes (65 bytes, uncompressed SEC1).
 *                   NULL to use this device's own key.
 * @param pubkey_len Length of pubkey (65 for uncompressed, 0 if NULL).
 * @return 0 if valid, -EINVAL if signature invalid, negative errno otherwise.
 */
int poac_verify(const poac_record_t *record,
                const uint8_t *pubkey, size_t pubkey_len);

/**
 * Verify chain integrity between two consecutive records.
 *
 * Checks that record->prev_poac_hash == SHA-256(serialize(prev, excluding sig)).
 *
 * @param prev    The predecessor record.
 * @param record  The successor record (whose prev_poac_hash is checked).
 * @return 0 if chain is valid, -EINVAL if broken, negative errno otherwise.
 */
int poac_verify_chain(const poac_record_t *prev, const poac_record_t *record);

/**
 * Serialize a PoAC record to a byte buffer (excluding signature).
 *
 * Used for hashing and signing. The serialization is deterministic:
 * fields are written in struct order, big-endian for multi-byte integers,
 * IEEE 754 for doubles.
 *
 * @param record   The record to serialize.
 * @param buf      Output buffer (must be >= POAC_RECORD_SIZE - POAC_SIG_SIZE).
 * @param buf_len  Size of output buffer.
 * @param out_len  Output: actual serialized length.
 * @return 0 on success, -ENOBUFS if buffer too small.
 */
int poac_serialize(const poac_record_t *record,
                   uint8_t *buf, size_t buf_len, size_t *out_len);

/**
 * Get the device's public key for on-chain registration.
 *
 * Returns the uncompressed SEC1 public key (65 bytes: 0x04 ‖ x ‖ y).
 *
 * @param out_pubkey  Output buffer (must be >= 65 bytes).
 * @param out_len     Output: actual key length (65).
 * @return 0 on success, negative errno on failure.
 */
int poac_get_device_pubkey(uint8_t *out_pubkey, size_t *out_len);

/**
 * Get the current monotonic counter value (for diagnostics).
 */
uint32_t poac_get_counter(void);

/**
 * Persist PoAC state to flash (chain head, counter).
 *
 * Called automatically by poac_generate(), but can be invoked manually
 * before entering PSM to ensure state survives deep sleep.
 *
 * @return 0 on success, negative errno on failure.
 */
int poac_persist_state(void);

#ifdef __cplusplus
}
#endif

#endif /* POAC_H */
