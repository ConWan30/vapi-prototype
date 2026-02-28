/**
 * vapi.h — VAPI Self-Verifying Integration SDK  (C99 / C++17 compatible)
 * Version: 1.0.0-phase20
 *
 * Targets: Sony DualSense Edge (PS5 firmware), SCUF Reflex Pro, Battle Beaver
 *          custom hardware, any platform with a SHA-256 implementation.
 *
 * Wire format: 228-byte PoAC record (immutable — never extend or alter).
 *
 * Minimal integration:
 *   1. Call vapi_record_parse() on each 228-byte frame from the device.
 *   2. Call vapi_chain_verify_link() to maintain on-device chain integrity.
 *   3. Forward the raw bytes to the VAPI bridge over USB/BLE.
 *
 * Full integration (PHCI certified path):
 *   1-3 above, plus:
 *   4. Call vapi_session_ingest() — fires on_cheat_detected callback.
 *   5. Call vapi_session_self_verify() — returns PITL layer attestation.
 *   6. Submit vapi_sdk_attestation_t to the VAPI gateway for on-chain proof.
 */

#ifndef VAPI_H
#define VAPI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* =========================================================================
 * Constants
 * ========================================================================= */

#define VAPI_SDK_VERSION        "1.0.0-phase20"
#define VAPI_RECORD_SIZE        228   /**< Total PoAC record bytes (body + sig)    */
#define VAPI_BODY_SIZE          164   /**< Unsigned body (hashed for record_hash)  */
#define VAPI_SIG_SIZE            64   /**< ECDSA-P256 signature bytes              */
#define VAPI_HASH_SIZE           32   /**< SHA-256 digest bytes                   */
#define VAPI_PUBKEY_SIZE         64   /**< Uncompressed P256 pubkey (x||y, no 04) */

/** Inference result codes */
#define VAPI_INF_NOMINAL         0x20  /**< Clean human gameplay                  */
#define VAPI_INF_DRIVER_INJECT   0x28  /**< L2: HID/XInput driver injection       */
#define VAPI_INF_WALLHACK        0x29  /**< L3: Wallhack / pre-aim behavioral      */
#define VAPI_INF_AIMBOT          0x2A  /**< L3: Aimbot behavioral pattern          */
#define VAPI_INF_TEMPORAL_BOT    0x2B  /**< L5: Constant-interval temporal bot     */
#define VAPI_INF_BIO_ANOMALY     0x30  /**< L4: Biometric Mahalanobis anomaly      */

/** PHCI tier values (mirrors PHCITier enum in Python SDK) */
#define VAPI_PHCI_NONE           0
#define VAPI_PHCI_STANDARD       1
#define VAPI_PHCI_CERTIFIED      2

/** Return codes */
#define VAPI_OK                  0
#define VAPI_ERR_BAD_SIZE       -1
#define VAPI_ERR_CHAIN_BROKEN   -2
#define VAPI_ERR_NULL_PTR       -3
#define VAPI_ERR_UNKNOWN_DEVICE -4


/* =========================================================================
 * PoAC Record — wire format view (read-only overlay, no allocation)
 * ========================================================================= */

/**
 * vapi_record_t — zero-copy view over a 228-byte PoAC record buffer.
 *
 * The caller owns the buffer; vapi_record_t holds a const pointer to it.
 * Never modify the buffer after calling vapi_record_parse().
 *
 * Body layout (164 bytes):
 *   [  0.. 31]  prev_poac_hash      SHA-256 of the previous record (genesis = 0x00*32)
 *   [ 32.. 63]  sensor_commitment   SHA-256 of the kinematic/haptic sensor frame
 *   [ 64.. 95]  model_manifest_hash SHA-256 of the TinyML model binary
 *   [ 96..127]  world_model_hash    SHA-256 of the EWC world model weights
 *   [128]       inference_result    uint8  (VAPI_INF_* constant)
 *   [129]       action_code         uint8  (game action code)
 *   [130]       confidence          uint8  (0–255; 220+ = high confidence)
 *   [131]       battery_pct         uint8  (0–100)
 *   [132..135]  monotonic_ctr       uint32 big-endian (device counter, never resets)
 *   [136..143]  timestamp_ms        uint64 big-endian (Unix epoch milliseconds)
 *   [144..151]  latitude            double big-endian (degrees, -90.0–90.0)
 *   [152..159]  longitude           double big-endian (degrees, -180.0–180.0)
 *   [160..163]  bounty_id           uint32 big-endian (0 = no active bounty)
 * Signature (64 bytes):
 *   [164..227]  sig                 ECDSA-P256 raw r||s over SHA-256(body)
 */
typedef struct {
    const uint8_t *_raw;           /**< Points into caller-owned 228-byte buffer */

    /* Parsed fields (populated by vapi_record_parse) */
    const uint8_t *prev_poac_hash; /**< raw[0..31]   */
    const uint8_t *sensor_hash;    /**< raw[32..63]  */
    const uint8_t *model_hash;     /**< raw[64..95]  */
    const uint8_t *world_hash;     /**< raw[96..127] */
    uint8_t        inference_result;
    uint8_t        action_code;
    uint8_t        confidence;
    uint8_t        battery_pct;
    uint32_t       monotonic_ctr;  /**< big-endian decoded                      */
    uint64_t       timestamp_ms;   /**< big-endian decoded, Unix epoch ms        */
    double         latitude;
    double         longitude;
    uint32_t       bounty_id;
    const uint8_t *signature;      /**< raw[164..227] r||s                      */

    /* Computed hashes (populated by vapi_record_parse) */
    uint8_t record_hash[VAPI_HASH_SIZE]; /**< SHA-256(raw[0..163])  */
    uint8_t chain_hash[VAPI_HASH_SIZE];  /**< SHA-256(raw[0..227])  */
} vapi_record_t;

/**
 * Parse a 228-byte PoAC record buffer into a vapi_record_t.
 *
 * Requires a caller-supplied sha256_fn because embedded targets vary:
 *   - mbedTLS: mbedtls_sha256(buf, len, out, 0)
 *   - OpenSSL: SHA256(buf, len, out)
 *   - Pico SDK: pico_sha256
 *
 * @param buf        Pointer to exactly VAPI_RECORD_SIZE (228) bytes.
 * @param buf_len    Must equal VAPI_RECORD_SIZE; returns VAPI_ERR_BAD_SIZE otherwise.
 * @param sha256_fn  fn(data, len, digest_out) — writes 32 bytes to digest_out.
 * @param out        Caller-allocated vapi_record_t to populate.
 * @return VAPI_OK or VAPI_ERR_*.
 */
typedef void (*vapi_sha256_fn)(const uint8_t *data, size_t len, uint8_t digest_out[32]);

int vapi_record_parse(
    const uint8_t   *buf,
    size_t           buf_len,
    vapi_sha256_fn   sha256_fn,
    vapi_record_t   *out
);

/**
 * Check whether this record's inference_result is a cheat code.
 * Cheat range: [VAPI_INF_DRIVER_INJECT, VAPI_INF_AIMBOT] (0x28–0x2A).
 */
bool vapi_record_is_cheat(const vapi_record_t *rec);

/**
 * Check whether this record carries an advisory inference (0x2B or 0x30).
 * Advisory records are not cheats but warrant platform review.
 */
bool vapi_record_is_advisory(const vapi_record_t *rec);

/**
 * Human-readable inference name (pointer to static string, do not free).
 * Returns "UNKNOWN_0xXX" for unregistered codes.
 */
const char *vapi_inference_name(uint8_t inference_result);


/* =========================================================================
 * Chain integrity
 * ========================================================================= */

/**
 * Verify that `current` correctly links to `prev`.
 *
 * Genesis check (prev == NULL):
 *   Returns true iff current->prev_poac_hash is all zeros (genesis record).
 *
 * Continuation check (prev != NULL):
 *   Returns true iff current->prev_poac_hash == prev->chain_hash.
 *
 * This is a pure client-side check — no RPC or network required.
 */
bool vapi_chain_verify_link(
    const vapi_record_t *current,
    const vapi_record_t *prev     /**< NULL for genesis check */
);

/**
 * Verify an ordered array of raw 228-byte record buffers forms an
 * unbroken chain. Internally calls vapi_record_parse + vapi_chain_verify_link.
 *
 * @param records    Array of pointers, each pointing to VAPI_RECORD_SIZE bytes.
 * @param count      Number of records in the array.
 * @param sha256_fn  SHA-256 implementation.
 * @return true if all links are valid, false on first broken link or parse error.
 */
bool vapi_chain_verify(
    const uint8_t * const *records,
    size_t                  count,
    vapi_sha256_fn          sha256_fn
);


/* =========================================================================
 * Device profile
 * ========================================================================= */

/**
 * PHCI device profile — mirrors DeviceProfile Python dataclass.
 * Populated by vapi_device_get_profile() or vapi_device_detect().
 */
typedef struct {
    const char *profile_id;            /**< e.g. "sony_dualshock_edge_v1"     */
    const char *display_name;          /**< Human-readable name                */
    const char *manufacturer;
    uint16_t    hid_vendor_id;
    uint16_t    hid_product_id;        /**< Primary PID (first in list)        */
    uint8_t     phci_tier;             /**< VAPI_PHCI_* constant               */
    int         schema_version;        /**< 1 = environmental, 2 = kinematic   */
    int         sensor_commitment_size_bytes; /**< 48 (STANDARD) or 56 (CERTIFIED) */
    bool        has_adaptive_triggers;
    bool        has_gyroscope;
    bool        has_accelerometer;
    uint8_t     pitl_layers[4];        /**< Active PITL layers (2–5), 0=unused */
    int         pitl_layer_count;
} vapi_device_profile_t;

/**
 * Populate `out` with the profile for the given profile_id string.
 * @return VAPI_OK or VAPI_ERR_UNKNOWN_DEVICE.
 */
int vapi_device_get_profile(
    const char           *profile_id,
    vapi_device_profile_t *out
);

/** Known profile IDs (null-terminated string constants). */
#define VAPI_PROFILE_DUALSHOCK_EDGE     "sony_dualshock_edge_v1"
#define VAPI_PROFILE_GENERIC_DUALSENSE  "sony_generic_dualsense_v1"
#define VAPI_PROFILE_SCUF_REFLEX_PRO    "scuf_reflex_pro_v1"
#define VAPI_PROFILE_BATTLE_BEAVER      "battle_beaver_dualshock_edge_v1"
#define VAPI_PROFILE_HORI_FIGHTING      "hori_fighting_commander_ps5_v1"


/* =========================================================================
 * Session — primary integration interface
 * ========================================================================= */

/** Callback fired when a cheat inference is detected. */
typedef void (*vapi_cheat_cb)(const vapi_record_t *record, void *user_data);

/** Callback fired when a record is successfully submitted on-chain. */
typedef void (*vapi_submit_cb)(const vapi_record_t *record,
                               const uint8_t tx_hash[32],
                               void *user_data);

/**
 * vapi_session_t — opaque session handle.
 * Allocate with vapi_session_create(), free with vapi_session_destroy().
 */
typedef struct vapi_session_s vapi_session_t;

/** Session summary returned by vapi_session_summary(). */
typedef struct {
    uint32_t total_records;
    uint32_t clean_records;
    uint32_t cheat_detections;
    uint32_t advisory_records;
    bool     chain_integrity_ok;
} vapi_session_summary_t;

/**
 * Create a new VAPI session.
 * @param profile_id  Device profile (VAPI_PROFILE_* constant), or NULL for auto.
 * @param sha256_fn   SHA-256 implementation used for all record parsing.
 * @return Opaque session handle, or NULL on allocation failure.
 */
vapi_session_t *vapi_session_create(
    const char     *profile_id,
    vapi_sha256_fn  sha256_fn
);

/** Register cheat-detection callback (may be called multiple times). */
void vapi_session_on_cheat_detected(
    vapi_session_t *session,
    vapi_cheat_cb   callback,
    void           *user_data
);

/** Register on-submit callback. */
void vapi_session_on_record_submitted(
    vapi_session_t *session,
    vapi_submit_cb  callback,
    void           *user_data
);

/**
 * Ingest a raw 228-byte record.
 * Parses the record, fires on_cheat_detected if inference is a cheat code,
 * and appends to the internal chain.
 * @return VAPI_OK, VAPI_ERR_BAD_SIZE, or VAPI_ERR_CHAIN_BROKEN.
 */
int vapi_session_ingest(
    vapi_session_t *session,
    const uint8_t  *raw_record,
    size_t          len
);

/** Verify the internal chain of all ingested records is unbroken. */
bool vapi_session_chain_integrity(const vapi_session_t *session);

/** Retrieve session summary statistics. */
void vapi_session_summary(
    const vapi_session_t  *session,
    vapi_session_summary_t *out
);

/** Free session resources. */
void vapi_session_destroy(vapi_session_t *session);


/* =========================================================================
 * SDK Self-Verification (novel feature)
 * ========================================================================= */

/**
 * PITL layer attestation — mirrors SDKAttestation Python dataclass.
 *
 * layers_active: bitmask where bit N = PITL layer (N+2) is active.
 *   Bit 0 = L2 (HID-XInput oracle)
 *   Bit 1 = L3 (behavioral classifier)
 *   Bit 2 = L4 (biometric fusion)
 *   Bit 3 = L5 (temporal rhythm oracle)
 *
 * pitl_scores: per-layer detection confidence 0–255 (scale: 0.0–1.0 × 255).
 */
#define VAPI_LAYER_L2_HID_XINPUT  (1u << 0)
#define VAPI_LAYER_L3_BEHAVIORAL  (1u << 1)
#define VAPI_LAYER_L4_BIOMETRIC   (1u << 2)
#define VAPI_LAYER_L5_TEMPORAL    (1u << 3)
#define VAPI_LAYER_ALL_ACTIVE     (0x0Fu)

typedef struct {
    uint8_t  layers_active;           /**< Bitmask of active PITL layers       */
    uint8_t  pitl_scores[4];          /**< Scores for L2/L3/L4/L5 (0–255)     */
    bool     zk_proof_available;      /**< ZK artifact setup complete          */
    char     sdk_version[32];
    uint64_t verified_at_ns;          /**< Unix epoch nanoseconds              */
    uint8_t  attestation_hash[32];    /**< SHA-256 commitment of all fields    */
} vapi_sdk_attestation_t;

/**
 * Perform SDK self-verification.
 *
 * Probes each PITL layer and synthesizes a 25-frame bot session at 100ms
 * constant inter-press intervals to functionally verify L5 fires
 * VAPI_INF_TEMPORAL_BOT. Produces a cryptographically-bound attestation
 * that can be submitted on-chain as proof of correct SDK wiring.
 *
 * This function does NOT require hardware. It works in CI, headless,
 * and Docker environments.
 *
 * On embedded targets, implement the probe hooks via the
 * vapi_self_verify_hooks_t struct (below).
 *
 * @param sha256_fn  SHA-256 implementation.
 * @param hooks      Optional hook table; NULL = use built-in probes.
 * @param out        Caller-allocated attestation struct to populate.
 * @return VAPI_OK on success (partial layer coverage also returns VAPI_OK).
 */

/** Hook table for embedded targets that cannot dlopen() PITL modules. */
typedef struct {
    /** Return true if L2 HID-XInput oracle is wired and functional. */
    bool (*probe_l2)(void *ctx);
    /** Return true if L3 behavioral classifier is wired and functional. */
    bool (*probe_l3)(void *ctx);
    /** Return true if L4 biometric fusion is wired and functional. */
    bool (*probe_l4)(void *ctx);
    /**
     * Inject frames and return L5 detection score (0–255).
     * Caller injects VAPI_SELF_VERIFY_FRAME_COUNT frames at
     * VAPI_SELF_VERIFY_INTERVAL_MS constant interval.
     * Score >= 128 (0.5) indicates L5 fired correctly.
     */
    uint8_t (*probe_l5)(void *ctx);
    void *ctx;
} vapi_self_verify_hooks_t;

#define VAPI_SELF_VERIFY_FRAME_COUNT   25
#define VAPI_SELF_VERIFY_INTERVAL_MS   100  /**< Constant bot timing (ms)    */
#define VAPI_L5_DETECTION_THRESHOLD    128  /**< Score >= 128 → L5 active    */

int vapi_session_self_verify(
    vapi_session_t           *session,
    vapi_sha256_fn            sha256_fn,
    const vapi_self_verify_hooks_t *hooks,  /**< NULL = built-in probes      */
    vapi_sdk_attestation_t   *out
);


/* =========================================================================
 * Sensor commitment helpers (schema v2 — kinematic/haptic)
 * ========================================================================= */

/**
 * Schema v2 sensor frame — DualSense Edge / SCUF Reflex Pro kinematic layout.
 * Pack with big-endian byte order before hashing.
 *
 * 48-byte (STANDARD) and 56-byte (CERTIFIED with adaptive triggers) variants.
 */
typedef struct {
    int16_t  left_stick_x;     /* -32768..32767 */
    int16_t  left_stick_y;
    int16_t  right_stick_x;
    int16_t  right_stick_y;
    uint8_t  l2_depression;    /* 0..255 */
    uint8_t  r2_depression;    /* 0..255 */
    uint8_t  l2_effect_mode;   /* 0=off, 1=feedback, 2=weapon, 3=vibration */
    uint8_t  r2_effect_mode;
    float    accel_x;          /* m/s² */
    float    accel_y;
    float    accel_z;
    float    gyro_x;           /* rad/s */
    float    gyro_y;
    float    gyro_z;           /* End of 48-byte STANDARD block */
    /* CERTIFIED-only fields (adaptive trigger resistance dynamics): */
    uint16_t l2_resistance_raw;  /* Raw ADC reading from trigger spring */
    uint16_t r2_resistance_raw;
    uint16_t l2_resistance_delta;
    uint16_t r2_resistance_delta;
    uint64_t timestamp_ms;
} vapi_sensor_frame_v2_t;

/**
 * Compute SHA-256 sensor_commitment from a v2 frame.
 * @param frame      Populated sensor frame.
 * @param certified  True → include adaptive trigger fields (56B), False → 48B.
 * @param sha256_fn  SHA-256 implementation.
 * @param out        32-byte digest output.
 */
void vapi_compute_sensor_commitment_v2(
    const vapi_sensor_frame_v2_t *frame,
    bool                          certified,
    vapi_sha256_fn                sha256_fn,
    uint8_t                       out[VAPI_HASH_SIZE]
);


/* =========================================================================
 * Version
 * ========================================================================= */

/** Returns VAPI_SDK_VERSION string. */
const char *vapi_sdk_version(void);

/** Returns 1 if the build was compiled with ZK proof support. */
int vapi_has_zk_support(void);


#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* VAPI_H */
