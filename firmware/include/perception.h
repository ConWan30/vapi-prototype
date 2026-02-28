/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Perception Layer — Unified Sensor Abstraction for VAPI Agent
 *
 * Provides a single interface to all Pebble Tracker sensors:
 *   - BME680:    Temperature, humidity, pressure, VOC gas resistance
 *   - ICM-42605: 6-axis IMU (accelerometer + gyroscope)
 *   - TSL2572:   Ambient light (lux)
 *   - GPS:       Position (lat/lon), fix quality, satellite count
 *
 * Sensor data is collected into a perception_t struct, then serialized
 * to a deterministic byte buffer for PoAC commitment (SHA-256 hash).
 *
 * Target: IoTeX Pebble Tracker (nRF9160, Zephyr RTOS)
 */

#ifndef PERCEPTION_H
#define PERCEPTION_H

#include <stdint.h>
#include <stdbool.h>
#include <zephyr/kernel.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Maximum serialized perception buffer size (for PoAC hashing) */
#define PERCEPTION_SERIAL_MAX_SIZE  128

/* GPS fix quality indicators */
#define GPS_FIX_NONE       0
#define GPS_FIX_2D         1
#define GPS_FIX_3D         2

/**
 * Environmental sensor readings (BME680).
 */
typedef struct {
    float temperature_c;      /* Celsius, +-1.0 accuracy */
    float humidity_pct;       /* Relative humidity 0-100% */
    float pressure_hpa;       /* Hectopascals (mbar) */
    float voc_resistance_ohm; /* Gas resistance — higher = cleaner air */
} env_data_t;

/**
 * Inertial measurement readings (ICM-42605).
 */
typedef struct {
    float accel_x_g;   /* Acceleration in g, X-axis */
    float accel_y_g;   /* Acceleration in g, Y-axis */
    float accel_z_g;   /* Acceleration in g, Z-axis */
    float gyro_x_dps;  /* Rotation in degrees/sec, X-axis */
    float gyro_y_dps;  /* Rotation in degrees/sec, Y-axis */
    float gyro_z_dps;  /* Rotation in degrees/sec, Z-axis */
} imu_data_t;

/**
 * Light sensor reading (TSL2572).
 */
typedef struct {
    uint32_t lux;       /* Ambient light in lux */
    uint16_t raw_ch0;   /* Visible + IR channel */
    uint16_t raw_ch1;   /* IR-only channel */
} light_data_t;

/**
 * GPS position data.
 */
typedef struct {
    double   latitude;       /* WGS84 degrees, + = North */
    double   longitude;      /* WGS84 degrees, + = East */
    float    altitude_m;     /* Meters above sea level */
    float    speed_mps;      /* Meters per second */
    float    hdop;           /* Horizontal dilution of precision */
    uint8_t  fix_quality;    /* GPS_FIX_NONE / 2D / 3D */
    uint8_t  satellites;     /* Number of satellites in view */
    int64_t  fix_timestamp;  /* Unix ms of last valid fix */
} gps_data_t;

/**
 * Complete perception snapshot — all sensors at one point in time.
 */
typedef struct {
    env_data_t   env;
    imu_data_t   imu;
    light_data_t light;
    gps_data_t   gps;
    uint8_t      battery_pct;    /* System battery level 0-100 */
    int64_t      timestamp_ms;   /* Capture time (GPS-synced or RTC) */
    bool         gps_valid;      /* True if GPS has a fix */
} perception_t;

/**
 * Initialize all sensor peripherals.
 *
 * Configures I2C/SPI buses, sets sensor sampling rates, and performs
 * self-tests. Must be called once at boot.
 *
 * @return 0 on success, negative errno if any sensor fails init.
 */
int perception_init(void);

/**
 * Capture a full sensor snapshot.
 *
 * Reads all sensors synchronously (blocking, ~50 ms worst case with GPS).
 * If GPS has no fix, gps_valid is set false and gps fields are zeroed.
 *
 * @param out  Output perception struct.
 * @return 0 on success, negative errno on sensor read failure.
 *         Partial data is still written (failing sensor fields zeroed).
 */
int perception_capture(perception_t *out);

/**
 * Serialize perception data to a deterministic byte buffer.
 *
 * Used for PoAC sensor commitment: hash this buffer to produce the
 * sensor_commitment field. Serialization is big-endian, IEEE 754 doubles,
 * fields in struct declaration order.
 *
 * @param p        Perception data to serialize.
 * @param buf      Output buffer.
 * @param buf_len  Size of output buffer (>= PERCEPTION_SERIAL_MAX_SIZE).
 * @param out_len  Actual bytes written.
 * @return 0 on success, -ENOBUFS if buffer too small.
 */
int perception_serialize(const perception_t *p,
                         uint8_t *buf, size_t buf_len, size_t *out_len);

/**
 * Get human-readable summary for debug logging.
 *
 * @param p    Perception data.
 * @param buf  Output string buffer.
 * @param len  Buffer length.
 * @return Number of characters written.
 */
int perception_to_string(const perception_t *p, char *buf, size_t len);

/**
 * Start continuous background GPS tracking.
 *
 * GPS acquisition is slow (~30s cold start). This starts it in a
 * background thread so perception_capture() can read cached fixes.
 *
 * @return 0 on success.
 */
int perception_gps_start(void);

/**
 * Stop GPS to save power (e.g., before PSM).
 */
void perception_gps_stop(void);

/**
 * Read battery voltage and convert to percentage.
 *
 * Uses nRF9160 ADC on the battery divider.
 *
 * @return Battery percentage 0-100, or -1 on read failure.
 */
int perception_read_battery(void);

#ifdef __cplusplus
}
#endif

#endif /* PERCEPTION_H */
