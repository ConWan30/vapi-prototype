/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Proof of Autonomous Cognition (PoAC) — Core Module Implementation
 *
 * Implements the cryptographic primitive for Verified Autonomous Physical
 * Intelligence (VAPI). Each PoAC record commits the agent's full
 * perception-reasoning-action loop into a tamper-evident, hardware-attested
 * chain using CryptoCell-310 on the nRF9160.
 *
 * Cryptographic backend: PSA Crypto API (CryptoCell-310 hardware accelerator)
 * Persistent storage:    Zephyr NVS (non-volatile storage in flash)
 * Thread safety:         k_mutex around all mutable shared state
 *
 * Target: IoTeX Pebble Tracker (nRF9160, Zephyr RTOS, nRF Connect SDK v2.7+)
 */

#include <errno.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/device.h>
#include <zephyr/drivers/flash.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/fs/nvs.h>

#include <psa/crypto.h>

#include "poac.h"

/* --------------------------------------------------------------------------
 * Logging
 * -------------------------------------------------------------------------- */
LOG_MODULE_REGISTER(poac, CONFIG_POAC_LOG_LEVEL);

/* --------------------------------------------------------------------------
 * NVS configuration
 *
 * We use a dedicated NVS partition to persist:
 *   - Monotonic counter  (NVS_ID_COUNTER)
 *   - Chain head hash    (NVS_ID_CHAIN_HEAD)
 *
 * The partition label "poac_storage" must be defined in the board DTS overlay.
 * -------------------------------------------------------------------------- */
#define NVS_PARTITION_LABEL  poac_storage
#define NVS_PARTITION_ID     FIXED_PARTITION_ID(NVS_PARTITION_LABEL)

#define NVS_ID_COUNTER       1   /* uint32_t: monotonic counter */
#define NVS_ID_CHAIN_HEAD    2   /* uint8_t[32]: SHA-256 of previous record */

/* --------------------------------------------------------------------------
 * PSA key identity
 *
 * Persistent key slot for the device ECDSA-P256 keypair. The key ID must be
 * in the range reserved for application use (>= PSA_KEY_ID_USER_MIN).
 * -------------------------------------------------------------------------- */
#define POAC_KEY_ID          ((psa_key_id_t)0x00010001)

/* --------------------------------------------------------------------------
 * Size of the serialized record body (everything before the signature).
 *
 * Layout (in struct field order):
 *   prev_poac_hash        32 bytes
 *   sensor_commitment     32 bytes
 *   model_manifest_hash   32 bytes
 *   world_model_hash      32 bytes
 *   inference_result       1 byte
 *   action_code            1 byte
 *   confidence             1 byte
 *   battery_pct            1 byte
 *   monotonic_ctr          4 bytes  (big-endian)
 *   timestamp_ms           8 bytes  (big-endian)
 *   latitude               8 bytes  (IEEE 754 big-endian)
 *   longitude              8 bytes  (IEEE 754 big-endian)
 *   bounty_id              4 bytes  (big-endian)
 *   -----------------------------------------------
 *   Total:               164 bytes
 * -------------------------------------------------------------------------- */
#define POAC_SERIALIZE_LEN   164

/* Compile-time sanity: the serialized body must fit in POAC_RECORD_SIZE minus
 * the signature field. */
BUILD_ASSERT(POAC_SERIALIZE_LEN <= POAC_RECORD_SIZE - POAC_SIG_SIZE,
             "Serialized PoAC body exceeds allocated record space");

/* --------------------------------------------------------------------------
 * Module state — protected by poac_mutex
 * -------------------------------------------------------------------------- */
static struct {
    bool              initialized;
    struct nvs_fs     nvs;
    psa_key_id_t      key_id;        /* handle to the persistent keypair   */
    uint32_t          counter;       /* monotonic counter, always goes up   */
    uint8_t           chain_head[POAC_HASH_SIZE]; /* prev_poac_hash        */
    uint8_t           model_hash[POAC_HASH_SIZE]; /* cached model manifest */
    bool              model_hash_set;
} poac_state;

static K_MUTEX_DEFINE(poac_mutex);

/* --------------------------------------------------------------------------
 * Internal helpers — byte-order serialization
 *
 * All multi-byte integers are serialized big-endian (network byte order).
 * Doubles are serialized in IEEE 754 binary64 big-endian representation.
 * -------------------------------------------------------------------------- */

/**
 * Write a 32-bit unsigned integer in big-endian to buf.
 * Returns the number of bytes written (always 4).
 */
static inline size_t put_be32(uint8_t *buf, uint32_t v)
{
    buf[0] = (uint8_t)(v >> 24);
    buf[1] = (uint8_t)(v >> 16);
    buf[2] = (uint8_t)(v >> 8);
    buf[3] = (uint8_t)(v);
    return 4;
}

/**
 * Write a 64-bit signed integer in big-endian to buf.
 * Returns the number of bytes written (always 8).
 */
static inline size_t put_be64(uint8_t *buf, int64_t v)
{
    uint64_t u = (uint64_t)v;
    buf[0] = (uint8_t)(u >> 56);
    buf[1] = (uint8_t)(u >> 48);
    buf[2] = (uint8_t)(u >> 40);
    buf[3] = (uint8_t)(u >> 32);
    buf[4] = (uint8_t)(u >> 24);
    buf[5] = (uint8_t)(u >> 16);
    buf[6] = (uint8_t)(u >> 8);
    buf[7] = (uint8_t)(u);
    return 8;
}

/**
 * Write an IEEE 754 double in big-endian to buf.
 *
 * We memcpy the double into a uint64_t to get its bit pattern without
 * aliasing violations, then write those 8 bytes in big-endian order.
 * Returns the number of bytes written (always 8).
 */
static inline size_t put_be_double(uint8_t *buf, double v)
{
    uint64_t bits;

    memcpy(&bits, &v, sizeof(bits));
    buf[0] = (uint8_t)(bits >> 56);
    buf[1] = (uint8_t)(bits >> 48);
    buf[2] = (uint8_t)(bits >> 40);
    buf[3] = (uint8_t)(bits >> 32);
    buf[4] = (uint8_t)(bits >> 24);
    buf[5] = (uint8_t)(bits >> 16);
    buf[6] = (uint8_t)(bits >> 8);
    buf[7] = (uint8_t)(bits);
    return 8;
}

/* --------------------------------------------------------------------------
 * Internal: SHA-256 helper using PSA Crypto
 * -------------------------------------------------------------------------- */

/**
 * Compute SHA-256 over an arbitrary buffer.
 *
 * @param data      Input data.
 * @param data_len  Length of input data.
 * @param out_hash  Output: 32-byte SHA-256 digest.
 * @return 0 on success, negative errno on failure.
 */
static int sha256(const uint8_t *data, size_t data_len,
                  uint8_t out_hash[POAC_HASH_SIZE])
{
    psa_status_t status;
    size_t hash_len;

    status = psa_hash_compute(PSA_ALG_SHA_256,
                              data, data_len,
                              out_hash, POAC_HASH_SIZE,
                              &hash_len);
    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_hash_compute failed: %d", (int)status);
        return -EIO;
    }

    if (hash_len != POAC_HASH_SIZE) {
        LOG_ERR("SHA-256 produced unexpected length: %zu", hash_len);
        return -EIO;
    }

    return 0;
}

/* --------------------------------------------------------------------------
 * Internal: NVS persistence helpers
 * -------------------------------------------------------------------------- */

/**
 * Read the monotonic counter from NVS.
 * If no counter exists yet (first boot), initializes to 0.
 */
static int nvs_load_counter(void)
{
    ssize_t rc;

    rc = nvs_read(&poac_state.nvs, NVS_ID_COUNTER,
                  &poac_state.counter, sizeof(poac_state.counter));
    if (rc < 0) {
        if (rc == -ENOENT) {
            /* First boot: no counter stored yet */
            LOG_INF("No stored counter found — initializing to 0");
            poac_state.counter = 0;
            return 0;
        }
        LOG_ERR("NVS read counter failed: %zd", rc);
        return (int)rc;
    }

    if (rc != sizeof(poac_state.counter)) {
        LOG_WRN("Counter size mismatch (got %zd) — resetting to 0", rc);
        poac_state.counter = 0;
    }

    LOG_INF("Loaded monotonic counter: %u", poac_state.counter);
    return 0;
}

/**
 * Read the chain head hash from NVS.
 * If none exists (first boot), the chain head is all zeros.
 */
static int nvs_load_chain_head(void)
{
    ssize_t rc;

    rc = nvs_read(&poac_state.nvs, NVS_ID_CHAIN_HEAD,
                  poac_state.chain_head, sizeof(poac_state.chain_head));
    if (rc < 0) {
        if (rc == -ENOENT) {
            /* First boot: chain head is the zero hash (genesis) */
            LOG_INF("No stored chain head — genesis record");
            memset(poac_state.chain_head, 0, POAC_HASH_SIZE);
            return 0;
        }
        LOG_ERR("NVS read chain head failed: %zd", rc);
        return (int)rc;
    }

    if (rc != POAC_HASH_SIZE) {
        LOG_WRN("Chain head size mismatch (got %zd) — resetting to zero", rc);
        memset(poac_state.chain_head, 0, POAC_HASH_SIZE);
    }

    LOG_INF("Loaded chain head from NVS");
    return 0;
}

/**
 * Write the current counter and chain head to NVS.
 */
static int nvs_save_state(void)
{
    ssize_t rc;

    rc = nvs_write(&poac_state.nvs, NVS_ID_COUNTER,
                   &poac_state.counter, sizeof(poac_state.counter));
    if (rc < 0) {
        LOG_ERR("NVS write counter failed: %zd", rc);
        return (int)rc;
    }

    rc = nvs_write(&poac_state.nvs, NVS_ID_CHAIN_HEAD,
                   poac_state.chain_head, sizeof(poac_state.chain_head));
    if (rc < 0) {
        LOG_ERR("NVS write chain head failed: %zd", rc);
        return (int)rc;
    }

    LOG_DBG("Persisted state: counter=%u", poac_state.counter);
    return 0;
}

/* --------------------------------------------------------------------------
 * Internal: PSA key management
 *
 * On first boot, no key exists at POAC_KEY_ID. We generate a new
 * ECDSA-P256 keypair and persist it in the PSA key store (backed by
 * CryptoCell-310 secure storage on nRF9160).
 *
 * On subsequent boots, we simply open the existing persistent key.
 * -------------------------------------------------------------------------- */

/**
 * Try to open an existing persistent key. If it does not exist, generate
 * a new ECDSA-P256 keypair and persist it.
 *
 * @return 0 on success, negative errno on failure.
 */
static int key_load_or_generate(void)
{
    psa_status_t status;
    psa_key_attributes_t attributes = PSA_KEY_ATTRIBUTES_INIT;

    /*
     * Attempt to read the key attributes. If the key exists, this succeeds
     * and we can use it directly. PSA persistent keys are referenced by ID;
     * there is no separate "open" step — we just use the key ID in sign/verify
     * operations. We verify it exists by fetching its attributes.
     */
    status = psa_get_key_attributes(POAC_KEY_ID, &attributes);
    if (status == PSA_SUCCESS) {
        /* Key exists — verify it is ECDSA-P256 */
        psa_key_type_t type = psa_get_key_type(&attributes);
        size_t bits = psa_get_key_bits(&attributes);

        psa_reset_key_attributes(&attributes);

        if (type != PSA_KEY_TYPE_ECC_KEY_PAIR(PSA_ECC_FAMILY_SECP_R1) ||
            bits != 256) {
            LOG_ERR("Existing key has wrong type (0x%04x) or size (%zu)",
                    (unsigned)type, bits);
            return -EINVAL;
        }

        poac_state.key_id = POAC_KEY_ID;
        LOG_INF("Loaded existing ECDSA-P256 keypair (key ID 0x%08x)",
                (unsigned)POAC_KEY_ID);
        return 0;
    }

    if (status != PSA_ERROR_DOES_NOT_EXIST &&
        status != PSA_ERROR_INVALID_HANDLE) {
        LOG_ERR("psa_get_key_attributes unexpected error: %d", (int)status);
        return -EIO;
    }

    /*
     * Key does not exist — generate a fresh ECDSA-P256 keypair.
     *
     * Key policy:
     *   - Persistent with lifetime PERSISTENT (survives reboot)
     *   - Usage: SIGN_HASH | VERIFY_HASH | EXPORT (export public key)
     *   - Algorithm: ECDSA with SHA-256 pre-hash (we compute SHA-256 ourselves
     *     and pass the digest, so we use PSA_ALG_ECDSA(PSA_ALG_SHA_256))
     */
    LOG_INF("No existing keypair — generating new ECDSA-P256 key");

    psa_set_key_id(&attributes, POAC_KEY_ID);
    psa_set_key_lifetime(&attributes, PSA_KEY_LIFETIME_PERSISTENT);
    psa_set_key_type(&attributes,
                     PSA_KEY_TYPE_ECC_KEY_PAIR(PSA_ECC_FAMILY_SECP_R1));
    psa_set_key_bits(&attributes, 256);
    psa_set_key_usage_flags(&attributes,
                            PSA_KEY_USAGE_SIGN_HASH |
                            PSA_KEY_USAGE_VERIFY_HASH |
                            PSA_KEY_USAGE_EXPORT);
    psa_set_key_algorithm(&attributes, PSA_ALG_ECDSA(PSA_ALG_SHA_256));

    psa_key_id_t key_id;

    status = psa_generate_key(&attributes, &key_id);
    psa_reset_key_attributes(&attributes);

    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_generate_key failed: %d", (int)status);
        return -EIO;
    }

    poac_state.key_id = key_id;
    LOG_INF("Generated new ECDSA-P256 keypair (key ID 0x%08x)",
            (unsigned)key_id);

    return 0;
}

/* --------------------------------------------------------------------------
 * Internal: Signing and verification
 * -------------------------------------------------------------------------- */

/**
 * Sign a SHA-256 digest using the device's ECDSA-P256 private key.
 *
 * The PSA API with PSA_ALG_ECDSA(PSA_ALG_SHA_256) expects a pre-computed
 * SHA-256 digest when called via psa_sign_hash(). The output is the raw
 * (r || s) signature, each component being 32 bytes.
 *
 * @param digest     32-byte SHA-256 digest to sign.
 * @param out_sig    Output: 64-byte ECDSA signature (r || s).
 * @return 0 on success, negative errno on failure.
 */
static int sign_digest(const uint8_t digest[POAC_HASH_SIZE],
                       uint8_t out_sig[POAC_SIG_SIZE])
{
    psa_status_t status;
    size_t sig_len;

    status = psa_sign_hash(poac_state.key_id,
                           PSA_ALG_ECDSA(PSA_ALG_SHA_256),
                           digest, POAC_HASH_SIZE,
                           out_sig, POAC_SIG_SIZE,
                           &sig_len);
    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_sign_hash failed: %d", (int)status);
        return -EIO;
    }

    if (sig_len != POAC_SIG_SIZE) {
        LOG_ERR("Unexpected signature length: %zu (expected %d)",
                sig_len, POAC_SIG_SIZE);
        return -EIO;
    }

    return 0;
}

/**
 * Verify an ECDSA-P256 signature over a SHA-256 digest.
 *
 * @param key_id     PSA key ID to verify against.
 * @param digest     32-byte SHA-256 digest.
 * @param sig        64-byte ECDSA signature (r || s).
 * @return 0 if valid, -EINVAL if signature is invalid, negative errno otherwise.
 */
static int verify_digest_with_key(psa_key_id_t key_id,
                                  const uint8_t digest[POAC_HASH_SIZE],
                                  const uint8_t sig[POAC_SIG_SIZE])
{
    psa_status_t status;

    status = psa_verify_hash(key_id,
                             PSA_ALG_ECDSA(PSA_ALG_SHA_256),
                             digest, POAC_HASH_SIZE,
                             sig, POAC_SIG_SIZE);
    if (status == PSA_ERROR_INVALID_SIGNATURE) {
        return -EINVAL;
    }

    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_verify_hash failed: %d", (int)status);
        return -EIO;
    }

    return 0;
}

/* --------------------------------------------------------------------------
 * Public API implementation
 * -------------------------------------------------------------------------- */

int poac_init(void)
{
    int rc;
    psa_status_t psa_rc;
    const struct flash_area *fa;

    k_mutex_lock(&poac_mutex, K_FOREVER);

    if (poac_state.initialized) {
        LOG_WRN("PoAC already initialized");
        k_mutex_unlock(&poac_mutex);
        return 0;
    }

    /* ----------------------------------------------------------------
     * Step 1: Initialize PSA Crypto (CryptoCell-310 backend)
     * ---------------------------------------------------------------- */
    psa_rc = psa_crypto_init();
    if (psa_rc != PSA_SUCCESS) {
        LOG_ERR("psa_crypto_init failed: %d", (int)psa_rc);
        k_mutex_unlock(&poac_mutex);
        return -EIO;
    }
    LOG_INF("PSA Crypto initialized (CryptoCell-310)");

    /* ----------------------------------------------------------------
     * Step 2: Initialize NVS for persistent state
     *
     * We look up the flash partition by the fixed partition macro,
     * then mount NVS on it.
     * ---------------------------------------------------------------- */
    rc = flash_area_open(NVS_PARTITION_ID, &fa);
    if (rc) {
        LOG_ERR("Failed to open NVS flash area: %d", rc);
        k_mutex_unlock(&poac_mutex);
        return rc;
    }

    poac_state.nvs.flash_device = fa->fa_dev;
    poac_state.nvs.offset = fa->fa_off;
    poac_state.nvs.sector_size = 4096;  /* Typical nRF9160 flash sector */
    poac_state.nvs.sector_count = fa->fa_size / poac_state.nvs.sector_size;

    flash_area_close(fa);

    if (poac_state.nvs.sector_count < 2) {
        LOG_ERR("NVS partition too small (need >= 2 sectors, have %u)",
                poac_state.nvs.sector_count);
        k_mutex_unlock(&poac_mutex);
        return -ENOSPC;
    }

    rc = nvs_mount(&poac_state.nvs);
    if (rc) {
        LOG_ERR("NVS mount failed: %d", rc);
        k_mutex_unlock(&poac_mutex);
        return rc;
    }
    LOG_INF("NVS mounted (%u sectors)", poac_state.nvs.sector_count);

    /* ----------------------------------------------------------------
     * Step 3: Load persistent state (counter, chain head)
     * ---------------------------------------------------------------- */
    rc = nvs_load_counter();
    if (rc) {
        k_mutex_unlock(&poac_mutex);
        return rc;
    }

    rc = nvs_load_chain_head();
    if (rc) {
        k_mutex_unlock(&poac_mutex);
        return rc;
    }

    /* ----------------------------------------------------------------
     * Step 4: Load or generate the ECDSA-P256 keypair
     * ---------------------------------------------------------------- */
    rc = key_load_or_generate();
    if (rc) {
        k_mutex_unlock(&poac_mutex);
        return rc;
    }

    /* ----------------------------------------------------------------
     * Step 5: Mark initialized, clear model hash
     * ---------------------------------------------------------------- */
    poac_state.model_hash_set = false;
    memset(poac_state.model_hash, 0, POAC_HASH_SIZE);
    poac_state.initialized = true;

    LOG_INF("PoAC subsystem initialized (counter=%u)", poac_state.counter);

    k_mutex_unlock(&poac_mutex);
    return 0;
}

int poac_commit_sensors(const uint8_t *sensor_buf, size_t sensor_len,
                        uint8_t out_hash[POAC_HASH_SIZE])
{
    if (sensor_buf == NULL || out_hash == NULL) {
        return -EINVAL;
    }

    if (sensor_len == 0) {
        LOG_WRN("Empty sensor buffer — hashing zero-length input");
    }

    /*
     * Compute SHA-256 of the raw sensor buffer using CryptoCell-310.
     * No mutex needed: this is a pure function with no shared state.
     */
    return sha256(sensor_buf, sensor_len, out_hash);
}

int poac_attest_model(const uint8_t *weights, size_t weights_len,
                      uint32_t version,
                      const char *arch_id, size_t arch_id_len)
{
    psa_status_t status;
    psa_hash_operation_t op = PSA_HASH_OPERATION_INIT;
    uint8_t ver_be[4];

    if (weights == NULL || arch_id == NULL) {
        return -EINVAL;
    }

    /*
     * Compute SHA-256(weights || version_be32 || arch_id).
     *
     * We use the multi-part hash API so we do not need to allocate a single
     * contiguous buffer for potentially large model weights.
     */
    status = psa_hash_setup(&op, PSA_ALG_SHA_256);
    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_hash_setup failed: %d", (int)status);
        return -EIO;
    }

    /* Feed weights */
    status = psa_hash_update(&op, weights, weights_len);
    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_hash_update (weights) failed: %d", (int)status);
        psa_hash_abort(&op);
        return -EIO;
    }

    /* Feed version as big-endian uint32 */
    put_be32(ver_be, version);
    status = psa_hash_update(&op, ver_be, sizeof(ver_be));
    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_hash_update (version) failed: %d", (int)status);
        psa_hash_abort(&op);
        return -EIO;
    }

    /* Feed architecture ID string */
    status = psa_hash_update(&op, (const uint8_t *)arch_id, arch_id_len);
    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_hash_update (arch_id) failed: %d", (int)status);
        psa_hash_abort(&op);
        return -EIO;
    }

    /* Finalize into the cached model manifest hash */
    k_mutex_lock(&poac_mutex, K_FOREVER);

    size_t hash_len;

    status = psa_hash_finish(&op, poac_state.model_hash,
                             POAC_HASH_SIZE, &hash_len);
    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_hash_finish failed: %d", (int)status);
        k_mutex_unlock(&poac_mutex);
        return -EIO;
    }

    poac_state.model_hash_set = true;

    LOG_INF("Model manifest hash computed (version=%u, arch_id_len=%zu)",
            version, arch_id_len);

    k_mutex_unlock(&poac_mutex);
    return 0;
}

int poac_serialize(const poac_record_t *record,
                   uint8_t *buf, size_t buf_len, size_t *out_len)
{
    size_t offset = 0;

    if (record == NULL || buf == NULL || out_len == NULL) {
        return -EINVAL;
    }

    if (buf_len < POAC_SERIALIZE_LEN) {
        LOG_ERR("Serialize buffer too small: %zu < %d",
                buf_len, POAC_SERIALIZE_LEN);
        return -ENOBUFS;
    }

    /*
     * Deterministic serialization in struct field order.
     * All multi-byte integers are big-endian, doubles are IEEE 754 big-endian.
     */

    /* prev_poac_hash: 32 bytes, raw */
    memcpy(buf + offset, record->prev_poac_hash, POAC_HASH_SIZE);
    offset += POAC_HASH_SIZE;

    /* sensor_commitment: 32 bytes, raw */
    memcpy(buf + offset, record->sensor_commitment, POAC_HASH_SIZE);
    offset += POAC_HASH_SIZE;

    /* model_manifest_hash: 32 bytes, raw */
    memcpy(buf + offset, record->model_manifest_hash, POAC_HASH_SIZE);
    offset += POAC_HASH_SIZE;

    /* world_model_hash: 32 bytes, raw */
    memcpy(buf + offset, record->world_model_hash, POAC_HASH_SIZE);
    offset += POAC_HASH_SIZE;

    /* inference_result: 1 byte */
    buf[offset++] = record->inference_result;

    /* action_code: 1 byte */
    buf[offset++] = record->action_code;

    /* confidence: 1 byte */
    buf[offset++] = record->confidence;

    /* battery_pct: 1 byte */
    buf[offset++] = record->battery_pct;

    /* monotonic_ctr: 4 bytes, big-endian */
    offset += put_be32(buf + offset, record->monotonic_ctr);

    /* timestamp_ms: 8 bytes, big-endian */
    offset += put_be64(buf + offset, record->timestamp_ms);

    /* latitude: 8 bytes, IEEE 754 big-endian */
    offset += put_be_double(buf + offset, record->latitude);

    /* longitude: 8 bytes, IEEE 754 big-endian */
    offset += put_be_double(buf + offset, record->longitude);

    /* bounty_id: 4 bytes, big-endian */
    offset += put_be32(buf + offset, record->bounty_id);

    /* Sanity check: we should have written exactly POAC_SERIALIZE_LEN bytes */
    __ASSERT_NO_MSG(offset == POAC_SERIALIZE_LEN);

    *out_len = offset;
    return 0;
}

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
                  poac_record_t *out_record)
{
    int rc;
    uint8_t serialized[POAC_SERIALIZE_LEN];
    size_t serialized_len;
    uint8_t digest[POAC_HASH_SIZE];

    if (sensor_hash == NULL || out_record == NULL) {
        return -EINVAL;
    }

    k_mutex_lock(&poac_mutex, K_FOREVER);

    if (!poac_state.initialized) {
        LOG_ERR("PoAC not initialized — call poac_init() first");
        k_mutex_unlock(&poac_mutex);
        return -ENODEV;
    }

    if (!poac_state.model_hash_set) {
        LOG_WRN("Model manifest not attested — using zero hash");
    }

    /*
     * Step 1: Populate the record fields.
     *
     * The prev_poac_hash links this record to the chain, forming a
     * tamper-evident sequence. On the very first record, this is all zeros
     * (the genesis sentinel).
     */
    memset(out_record, 0, sizeof(*out_record));
    memcpy(out_record->prev_poac_hash, poac_state.chain_head, POAC_HASH_SIZE);
    memcpy(out_record->sensor_commitment, sensor_hash, POAC_HASH_SIZE);
    memcpy(out_record->model_manifest_hash, poac_state.model_hash,
           POAC_HASH_SIZE);

    /* World model hash — NULL means zero hash (e.g., at boot before first cycle) */
    if (wm_hash != NULL) {
        memcpy(out_record->world_model_hash, wm_hash, POAC_HASH_SIZE);
    }
    /* else: already zeroed by memset above */

    out_record->inference_result = inference_result;
    out_record->action_code      = action_code;
    out_record->confidence       = confidence;
    out_record->battery_pct      = battery_pct;
    out_record->timestamp_ms     = timestamp_ms;
    out_record->latitude         = latitude;
    out_record->longitude        = longitude;
    out_record->bounty_id        = bounty_id;

    /*
     * Step 2: Increment the monotonic counter.
     *
     * This provides replay protection: any observer can verify that the
     * counter strictly increases, making record substitution detectable.
     */
    poac_state.counter++;
    out_record->monotonic_ctr = poac_state.counter;

    LOG_DBG("Record counter=%u action=0x%02x inference=0x%02x",
            out_record->monotonic_ctr, action_code, inference_result);

    /*
     * Step 3: Serialize the record body (everything except the signature)
     *         into a deterministic byte buffer.
     */
    rc = poac_serialize(out_record, serialized, sizeof(serialized),
                        &serialized_len);
    if (rc) {
        LOG_ERR("Serialization failed: %d", rc);
        poac_state.counter--;  /* Roll back on failure */
        k_mutex_unlock(&poac_mutex);
        return rc;
    }

    /*
     * Step 4: Hash the serialized body to produce the signing digest.
     *
     * We sign SHA-256(serialized_body), not the raw body, because ECDSA
     * operates on a fixed-size digest.
     */
    rc = sha256(serialized, serialized_len, digest);
    if (rc) {
        LOG_ERR("SHA-256 of record body failed: %d", rc);
        poac_state.counter--;
        k_mutex_unlock(&poac_mutex);
        return rc;
    }

    /*
     * Step 5: Sign the digest using the device's ECDSA-P256 private key
     *         via CryptoCell-310.
     */
    rc = sign_digest(digest, out_record->signature);
    if (rc) {
        LOG_ERR("ECDSA sign failed: %d", rc);
        poac_state.counter--;
        k_mutex_unlock(&poac_mutex);
        return rc;
    }

    /*
     * Step 6: Update the chain head.
     *
     * The chain head is the SHA-256 of the serialized body (excluding the
     * signature). The next record will reference this hash, forming the chain.
     */
    memcpy(poac_state.chain_head, digest, POAC_HASH_SIZE);

    /*
     * Step 7: Persist the updated counter and chain head to NVS.
     *
     * If persistence fails, we log a warning but do not roll back the
     * in-memory state — the record has already been signed and the caller
     * will transmit it. On next boot, the counter will be re-read from
     * the last successful write, which is safe because the chain head
     * and counter are always written together.
     */
    rc = nvs_save_state();
    if (rc) {
        LOG_WRN("State persistence failed (counter=%u): %d — "
                "data may be lost on power failure",
                poac_state.counter, rc);
        /* Do NOT roll back: the signed record is already committed. */
    }

    LOG_INF("PoAC record generated: counter=%u, action=0x%02x",
            out_record->monotonic_ctr, action_code);

    k_mutex_unlock(&poac_mutex);
    return 0;
}

int poac_verify(const poac_record_t *record,
                const uint8_t *pubkey, size_t pubkey_len)
{
    int rc;
    uint8_t serialized[POAC_SERIALIZE_LEN];
    size_t serialized_len;
    uint8_t digest[POAC_HASH_SIZE];

    if (record == NULL) {
        return -EINVAL;
    }

    /*
     * Step 1: Re-serialize the record body to get the canonical byte
     *         representation that was signed.
     */
    rc = poac_serialize(record, serialized, sizeof(serialized), &serialized_len);
    if (rc) {
        LOG_ERR("Serialize for verify failed: %d", rc);
        return rc;
    }

    /*
     * Step 2: Hash the serialized body to reconstruct the signing digest.
     */
    rc = sha256(serialized, serialized_len, digest);
    if (rc) {
        LOG_ERR("SHA-256 for verify failed: %d", rc);
        return rc;
    }

    /*
     * Step 3: Verify the signature.
     *
     * If pubkey is NULL, we verify against this device's own key (self-check).
     * Otherwise, we import the provided public key into a volatile key slot,
     * verify, then destroy the temporary key.
     */
    if (pubkey == NULL || pubkey_len == 0) {
        /* Verify with device's own key */
        k_mutex_lock(&poac_mutex, K_FOREVER);

        if (!poac_state.initialized) {
            k_mutex_unlock(&poac_mutex);
            return -ENODEV;
        }

        rc = verify_digest_with_key(poac_state.key_id, digest,
                                    record->signature);
        k_mutex_unlock(&poac_mutex);
        return rc;
    }

    /*
     * External public key provided — import it as a volatile (ephemeral)
     * key, verify, then destroy it.
     *
     * Expected format: 65 bytes, uncompressed SEC1 (0x04 || x || y).
     */
    if (pubkey_len != 65 || pubkey[0] != 0x04) {
        LOG_ERR("Invalid public key format: len=%zu, first_byte=0x%02x",
                pubkey_len, pubkey[0]);
        return -EINVAL;
    }

    psa_key_attributes_t attr = PSA_KEY_ATTRIBUTES_INIT;
    psa_key_id_t tmp_key;
    psa_status_t status;

    psa_set_key_type(&attr, PSA_KEY_TYPE_ECC_PUBLIC_KEY(PSA_ECC_FAMILY_SECP_R1));
    psa_set_key_bits(&attr, 256);
    psa_set_key_usage_flags(&attr, PSA_KEY_USAGE_VERIFY_HASH);
    psa_set_key_algorithm(&attr, PSA_ALG_ECDSA(PSA_ALG_SHA_256));
    psa_set_key_lifetime(&attr, PSA_KEY_LIFETIME_VOLATILE);

    status = psa_import_key(&attr, pubkey, pubkey_len, &tmp_key);
    psa_reset_key_attributes(&attr);

    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_import_key (public) failed: %d", (int)status);
        return -EIO;
    }

    rc = verify_digest_with_key(tmp_key, digest, record->signature);

    /* Always destroy the temporary key, regardless of verify outcome */
    status = psa_destroy_key(tmp_key);
    if (status != PSA_SUCCESS) {
        LOG_WRN("Failed to destroy temporary key: %d", (int)status);
    }

    return rc;
}

int poac_verify_chain(const poac_record_t *prev, const poac_record_t *record)
{
    int rc;
    uint8_t serialized[POAC_SERIALIZE_LEN];
    size_t serialized_len;
    uint8_t prev_hash[POAC_HASH_SIZE];

    if (prev == NULL || record == NULL) {
        return -EINVAL;
    }

    /*
     * Serialize the predecessor record (excluding its signature) and compute
     * its SHA-256 hash. Then compare with the successor's prev_poac_hash.
     */
    rc = poac_serialize(prev, serialized, sizeof(serialized), &serialized_len);
    if (rc) {
        LOG_ERR("Serialize prev record failed: %d", rc);
        return rc;
    }

    rc = sha256(serialized, serialized_len, prev_hash);
    if (rc) {
        LOG_ERR("SHA-256 of prev record failed: %d", rc);
        return rc;
    }

    if (memcmp(prev_hash, record->prev_poac_hash, POAC_HASH_SIZE) != 0) {
        LOG_WRN("Chain integrity check failed: "
                "prev hash mismatch at counter=%u",
                record->monotonic_ctr);
        return -EINVAL;
    }

    LOG_DBG("Chain integrity verified: counter %u -> %u",
            prev->monotonic_ctr, record->monotonic_ctr);
    return 0;
}

int poac_get_device_pubkey(uint8_t *out_pubkey, size_t *out_len)
{
    psa_status_t status;
    size_t key_len;

    if (out_pubkey == NULL || out_len == NULL) {
        return -EINVAL;
    }

    k_mutex_lock(&poac_mutex, K_FOREVER);

    if (!poac_state.initialized) {
        k_mutex_unlock(&poac_mutex);
        return -ENODEV;
    }

    /*
     * Export the public key from the persistent keypair.
     *
     * PSA_EXPORT_PUBLIC_KEY_OUTPUT_SIZE gives the buffer size needed for an
     * uncompressed SEC1 P-256 public key: 1 + 32 + 32 = 65 bytes.
     * The caller must provide at least 65 bytes.
     */
    status = psa_export_public_key(poac_state.key_id,
                                   out_pubkey, 65,
                                   &key_len);
    if (status != PSA_SUCCESS) {
        LOG_ERR("psa_export_public_key failed: %d", (int)status);
        k_mutex_unlock(&poac_mutex);
        return -EIO;
    }

    *out_len = key_len;

    k_mutex_unlock(&poac_mutex);

    LOG_DBG("Exported device public key (%zu bytes)", key_len);
    return 0;
}

uint32_t poac_get_counter(void)
{
    uint32_t val;

    k_mutex_lock(&poac_mutex, K_FOREVER);
    val = poac_state.counter;
    k_mutex_unlock(&poac_mutex);

    return val;
}

int poac_persist_state(void)
{
    int rc;

    k_mutex_lock(&poac_mutex, K_FOREVER);

    if (!poac_state.initialized) {
        k_mutex_unlock(&poac_mutex);
        return -ENODEV;
    }

    rc = nvs_save_state();

    k_mutex_unlock(&poac_mutex);

    if (rc) {
        LOG_ERR("Manual state persistence failed: %d", rc);
    } else {
        LOG_INF("PoAC state persisted (counter=%u)", poac_state.counter);
    }

    return rc;
}
