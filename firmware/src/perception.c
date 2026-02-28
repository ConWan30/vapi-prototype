/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Perception Layer — Sensor Driver Integration for IoTeX Pebble Tracker
 *
 * Reads all on-board sensors via Zephyr sensor API and produces a
 * unified perception_t snapshot for the agent's cognitive loop.
 *
 * Hardware:
 *   - BME680 (I2C 0x76): Temperature, humidity, pressure, VOC gas resistance
 *   - ICM-42605 (SPI): 6-axis IMU (accelerometer + gyroscope)
 *   - TSL2572 (I2C 0x39): Ambient light sensor
 *   - GPS: Via nRF9160 modem (AT commands / GNSS interface)
 *   - Battery: ADC on voltage divider
 */

#include "perception.h"

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/logging/log.h>
#include <nrf_modem_gnss.h>
#include <string.h>
#include <stdio.h>

LOG_MODULE_REGISTER(perception, CONFIG_VAPI_LOG_LEVEL);

/* --------------------------------------------------------------------------
 * Device references (from devicetree)
 * -------------------------------------------------------------------------- */

static const struct device *bme680_dev;
static const struct device *icm42605_dev;
static const struct device *tsl2572_dev;

/* GPS state — updated asynchronously by GNSS event handler */
static struct nrf_modem_gnss_pvt_data_frame gps_pvt;
static bool gps_has_fix;
static K_MUTEX_DEFINE(gps_mutex);

/* --------------------------------------------------------------------------
 * GPS GNSS handler
 * -------------------------------------------------------------------------- */

static void gnss_event_handler(int event)
{
    if (event == NRF_MODEM_GNSS_EVT_PVT) {
        k_mutex_lock(&gps_mutex, K_FOREVER);
        int err = nrf_modem_gnss_read(&gps_pvt, sizeof(gps_pvt),
                                       NRF_MODEM_GNSS_DATA_PVT);
        if (err == 0 && gps_pvt.flags & NRF_MODEM_GNSS_PVT_FLAG_FIX_VALID) {
            gps_has_fix = true;
        }
        k_mutex_unlock(&gps_mutex);
    }
}

/* --------------------------------------------------------------------------
 * Sensor helpers
 * -------------------------------------------------------------------------- */

static float sensor_value_to_float(const struct sensor_value *val)
{
    return (float)val->val1 + (float)val->val2 / 1000000.0f;
}

static int read_bme680(env_data_t *env)
{
    if (!bme680_dev) {
        return -ENODEV;
    }

    int err = sensor_sample_fetch(bme680_dev);
    if (err) {
        LOG_WRN("BME680 fetch failed: %d", err);
        return err;
    }

    struct sensor_value val;

    sensor_channel_get(bme680_dev, SENSOR_CHAN_AMBIENT_TEMP, &val);
    env->temperature_c = sensor_value_to_float(&val);

    sensor_channel_get(bme680_dev, SENSOR_CHAN_HUMIDITY, &val);
    env->humidity_pct = sensor_value_to_float(&val);

    sensor_channel_get(bme680_dev, SENSOR_CHAN_PRESS, &val);
    env->pressure_hpa = sensor_value_to_float(&val);

    sensor_channel_get(bme680_dev, SENSOR_CHAN_GAS_RES, &val);
    env->voc_resistance_ohm = sensor_value_to_float(&val);

    return 0;
}

static int read_icm42605(imu_data_t *imu)
{
    if (!icm42605_dev) {
        return -ENODEV;
    }

    int err = sensor_sample_fetch(icm42605_dev);
    if (err) {
        LOG_WRN("ICM-42605 fetch failed: %d", err);
        return err;
    }

    struct sensor_value val;

    sensor_channel_get(icm42605_dev, SENSOR_CHAN_ACCEL_X, &val);
    imu->accel_x_g = sensor_value_to_float(&val);

    sensor_channel_get(icm42605_dev, SENSOR_CHAN_ACCEL_Y, &val);
    imu->accel_y_g = sensor_value_to_float(&val);

    sensor_channel_get(icm42605_dev, SENSOR_CHAN_ACCEL_Z, &val);
    imu->accel_z_g = sensor_value_to_float(&val);

    sensor_channel_get(icm42605_dev, SENSOR_CHAN_GYRO_X, &val);
    imu->gyro_x_dps = sensor_value_to_float(&val);

    sensor_channel_get(icm42605_dev, SENSOR_CHAN_GYRO_Y, &val);
    imu->gyro_y_dps = sensor_value_to_float(&val);

    sensor_channel_get(icm42605_dev, SENSOR_CHAN_GYRO_Z, &val);
    imu->gyro_z_dps = sensor_value_to_float(&val);

    return 0;
}

static int read_tsl2572(light_data_t *light)
{
    if (!tsl2572_dev) {
        return -ENODEV;
    }

    int err = sensor_sample_fetch(tsl2572_dev);
    if (err) {
        LOG_WRN("TSL2572 fetch failed: %d", err);
        return err;
    }

    struct sensor_value val;

    sensor_channel_get(tsl2572_dev, SENSOR_CHAN_LIGHT, &val);
    light->lux = (uint32_t)val.val1;

    /* Raw channels — driver-specific, may not be available on all implementations */
    light->raw_ch0 = 0;
    light->raw_ch1 = 0;

    return 0;
}

static int read_gps(gps_data_t *gps, bool *valid)
{
    k_mutex_lock(&gps_mutex, K_FOREVER);

    *valid = gps_has_fix;

    if (gps_has_fix) {
        gps->latitude    = gps_pvt.latitude;
        gps->longitude   = gps_pvt.longitude;
        gps->altitude_m  = gps_pvt.altitude;
        gps->speed_mps   = gps_pvt.speed;
        gps->hdop        = gps_pvt.hdop;
        gps->fix_quality = (gps_pvt.flags & NRF_MODEM_GNSS_PVT_FLAG_FIX_VALID)
                            ? GPS_FIX_3D : GPS_FIX_2D;
        gps->satellites  = gps_pvt.sv_count;
        gps->fix_timestamp = k_uptime_get();
    } else {
        memset(gps, 0, sizeof(*gps));
        gps->fix_quality = GPS_FIX_NONE;
    }

    k_mutex_unlock(&gps_mutex);
    return 0;
}

/* --------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------- */

int perception_init(void)
{
    LOG_INF("Initializing perception layer...");

    /* BME680 */
    bme680_dev = DEVICE_DT_GET_ANY(bosch_bme680);
    if (!bme680_dev || !device_is_ready(bme680_dev)) {
        LOG_WRN("BME680 not ready — environmental data unavailable");
        bme680_dev = NULL;
    } else {
        LOG_INF("BME680 initialized");
    }

    /* ICM-42605 */
    icm42605_dev = DEVICE_DT_GET_ANY(invensense_icm42605);
    if (!icm42605_dev || !device_is_ready(icm42605_dev)) {
        LOG_WRN("ICM-42605 not ready — IMU data unavailable");
        icm42605_dev = NULL;
    } else {
        LOG_INF("ICM-42605 initialized");
    }

    /* TSL2572 */
    tsl2572_dev = DEVICE_DT_GET_ANY(ams_tsl2572);
    if (!tsl2572_dev || !device_is_ready(tsl2572_dev)) {
        LOG_WRN("TSL2572 not ready — light data unavailable");
        tsl2572_dev = NULL;
    } else {
        LOG_INF("TSL2572 initialized");
    }

    LOG_INF("Perception layer ready (bme680=%s, imu=%s, light=%s)",
            bme680_dev ? "OK" : "MISS",
            icm42605_dev ? "OK" : "MISS",
            tsl2572_dev ? "OK" : "MISS");

    return 0;
}

int perception_capture(perception_t *out)
{
    if (!out) {
        return -EINVAL;
    }

    memset(out, 0, sizeof(*out));
    out->timestamp_ms = k_uptime_get();

    /* Read all sensors — continue on partial failure */
    int err;
    int overall = 0;

    err = read_bme680(&out->env);
    if (err) { overall = err; }

    err = read_icm42605(&out->imu);
    if (err) { overall = err; }

    err = read_tsl2572(&out->light);
    if (err) { overall = err; }

    err = read_gps(&out->gps, &out->gps_valid);
    if (err) { overall = err; }

    out->battery_pct = (uint8_t)perception_read_battery();
    if (out->battery_pct > 100) {
        out->battery_pct = 0; /* Read failure */
    }

    return overall;
}

int perception_serialize(const perception_t *p,
                         uint8_t *buf, size_t buf_len, size_t *out_len)
{
    /*
     * Deterministic serialization for PoAC commitment.
     * Big-endian integers, IEEE 754 floats/doubles, struct field order.
     *
     * We use a simple memcpy-based approach. On ARM Cortex-M (little-endian),
     * we swap bytes for multi-byte values.
     */
    if (buf_len < PERCEPTION_SERIAL_MAX_SIZE) {
        return -ENOBUFS;
    }

    uint8_t *p_buf = buf;

    /* Helper: write big-endian float (4 bytes) */
    #define WRITE_FLOAT(f) do {                         \
        uint32_t _tmp;                                  \
        memcpy(&_tmp, &(f), 4);                         \
        *p_buf++ = (_tmp >> 24) & 0xFF;                 \
        *p_buf++ = (_tmp >> 16) & 0xFF;                 \
        *p_buf++ = (_tmp >> 8)  & 0xFF;                 \
        *p_buf++ = (_tmp)       & 0xFF;                 \
    } while (0)

    /* Helper: write big-endian double (8 bytes) */
    #define WRITE_DOUBLE(d) do {                        \
        uint64_t _tmp;                                  \
        memcpy(&_tmp, &(d), 8);                         \
        for (int _i = 7; _i >= 0; _i--) {              \
            *p_buf++ = (_tmp >> (_i * 8)) & 0xFF;       \
        }                                               \
    } while (0)

    /* Helper: write big-endian uint32 */
    #define WRITE_U32(v) do {                           \
        *p_buf++ = ((v) >> 24) & 0xFF;                  \
        *p_buf++ = ((v) >> 16) & 0xFF;                  \
        *p_buf++ = ((v) >> 8)  & 0xFF;                  \
        *p_buf++ = ((v))       & 0xFF;                  \
    } while (0)

    #define WRITE_U16(v) do {                           \
        *p_buf++ = ((v) >> 8) & 0xFF;                   \
        *p_buf++ = ((v))      & 0xFF;                   \
    } while (0)

    /* Environmental */
    WRITE_FLOAT(p->env.temperature_c);
    WRITE_FLOAT(p->env.humidity_pct);
    WRITE_FLOAT(p->env.pressure_hpa);
    WRITE_FLOAT(p->env.voc_resistance_ohm);

    /* IMU */
    WRITE_FLOAT(p->imu.accel_x_g);
    WRITE_FLOAT(p->imu.accel_y_g);
    WRITE_FLOAT(p->imu.accel_z_g);
    WRITE_FLOAT(p->imu.gyro_x_dps);
    WRITE_FLOAT(p->imu.gyro_y_dps);
    WRITE_FLOAT(p->imu.gyro_z_dps);

    /* Light */
    WRITE_U32(p->light.lux);
    WRITE_U16(p->light.raw_ch0);
    WRITE_U16(p->light.raw_ch1);

    /* GPS */
    WRITE_DOUBLE(p->gps.latitude);
    WRITE_DOUBLE(p->gps.longitude);
    WRITE_FLOAT(p->gps.altitude_m);
    WRITE_FLOAT(p->gps.speed_mps);
    *p_buf++ = p->gps.fix_quality;
    *p_buf++ = p->gps.satellites;

    /* Battery + timestamp */
    *p_buf++ = p->battery_pct;

    /* Timestamp as big-endian int64 */
    int64_t ts = p->timestamp_ms;
    for (int i = 7; i >= 0; i--) {
        *p_buf++ = (ts >> (i * 8)) & 0xFF;
    }

    #undef WRITE_FLOAT
    #undef WRITE_DOUBLE
    #undef WRITE_U32
    #undef WRITE_U16

    *out_len = (size_t)(p_buf - buf);
    return 0;
}

int perception_to_string(const perception_t *p, char *buf, size_t len)
{
    return snprintf(buf, len,
        "T=%.1fC H=%.0f%% P=%.0fhPa VOC=%.0f "
        "A=[%.2f,%.2f,%.2f]g G=[%.1f,%.1f,%.1f]dps "
        "Lux=%u GPS=%s(%.5f,%.5f) Bat=%u%%",
        p->env.temperature_c, p->env.humidity_pct,
        p->env.pressure_hpa, p->env.voc_resistance_ohm,
        p->imu.accel_x_g, p->imu.accel_y_g, p->imu.accel_z_g,
        p->imu.gyro_x_dps, p->imu.gyro_y_dps, p->imu.gyro_z_dps,
        p->light.lux,
        p->gps_valid ? "FIX" : "NO",
        p->gps.latitude, p->gps.longitude,
        p->battery_pct);
}

int perception_gps_start(void)
{
    LOG_INF("Starting GPS tracking...");

    int err = nrf_modem_gnss_event_handler_set(gnss_event_handler);
    if (err) {
        LOG_ERR("GNSS event handler set failed: %d", err);
        return err;
    }

    /* Configure for continuous tracking, 1Hz updates */
    uint16_t fix_interval = 1; /* seconds */
    err = nrf_modem_gnss_fix_interval_set(fix_interval);
    if (err) {
        LOG_ERR("GNSS fix interval set failed: %d", err);
        return err;
    }

    err = nrf_modem_gnss_start();
    if (err) {
        LOG_ERR("GNSS start failed: %d", err);
        return err;
    }

    LOG_INF("GPS tracking started (interval=%us)", fix_interval);
    return 0;
}

void perception_gps_stop(void)
{
    nrf_modem_gnss_stop();
    LOG_INF("GPS tracking stopped");
}

int perception_read_battery(void)
{
    /*
     * The Pebble Tracker has a battery voltage divider connected to an
     * ADC input on the nRF9160. Typical Li-Po range: 3.0V (empty) to 4.2V (full).
     *
     * For a real implementation, use Zephyr ADC API:
     *   const struct adc_dt_spec adc_chan = ADC_DT_SPEC_GET(DT_PATH(vbatt));
     *   adc_read(adc_chan.dev, &sequence);
     *   int mv = (raw * ref_mv) / resolution;
     *   pct = (mv - 3000) * 100 / (4200 - 3000);
     *
     * Stubbed with a reasonable default until devicetree overlay is finalized.
     */

    /* TODO: Replace with actual ADC read once DT overlay is configured */
    static int simulated_battery = 85;
    return simulated_battery;
}
