/*
 * Copyright (c) 2019 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief HTS221 rtio sensor driver, device specific API
 */

#ifndef ZEPHYR_INCLUDE_DRIVERS_RTIO_HTS221_H_
#define ZEPHYR_INCLUDE_DRIVERS_RTIO_HTS221_H_

#include <zephyr/types.h>
#include <toolchain.h>
#include <sys/byteorder.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Output data rate
 */
enum rtio_hts221_odr {
	/** Humidity and temperature both read at 1 Hz */
	RTIO_HTS221_ODR_1_HZ = 1,
	/** Humidity and temperature both read at 7 Hz */
	RTIO_HTS221_ODR_7_HZ = 2,
	/** Humidity and temperature both read at 12.5 Hz */
	RTIO_HTS221_ODR_12_5_HZ = 3,
};

struct rtio_hts221_config {
	enum rtio_hts221_odr odr;
};

/**
 * @brief HTS221 raw sensor reading
 *
 * This is the raw sample data read from the device. It is *not* in SI units.
 */
struct rtio_hts221_raw {
	/* Relative humidity */
	u8_t rh_l;
	u8_t rh_h;
	/* Temperature */
	u8_t temp_l;
	u8_t temp_h;
} __packed;

/**
 * @brief HTS221 calibration data structure
 *
 * This contains the calibration data read from sensor flash.
 *
 * The T0 and T1 calibration values in registers 0x32, 0x33, and 0x35
 * have been separated into two u16_ts as the t0_degc_x8 and
 * t1_degc_x8 structure members.
 */
struct rtio_hts221_calib {
	u8_t h0_rh_x2;
	u8_t h1_rh_x2;
	u16_t t0_degc_x8;
	u16_t t1_degc_x8;
	s16_t h0_t0_out;
	s16_t h1_t0_out;
	s16_t t0_out;
	s16_t t1_out;
};

/**
 * @brief Get a pointer to calibration data from device
 *
 * This may not be called until after the device has been successfully
 * configured.
 *
 * @param dev RTIO sensor HTS221 device
 * @param calib Out parameter which will point to calibration data on success
 * @retval 0 on success
 * @retval -errno on failure
 */
void rtio_hts221_get_calibration(struct device *dev,
				 const struct rtio_hts221_calib **calib);

/**
 * @brief Convert a raw sensor sample to degrees C multiplied by 8
 * @param raw Raw sensor data
 * @param calib Calibration data
 * @retval Temperature in degrees Celsius, multiplied by 8
 */
static inline int rtio_hts221_degc_x8(struct rtio_hts221_raw *raw,
				      const struct rtio_hts221_calib *calib) {
	s16_t temp_val = sys_le16_to_cpu((raw->temp_h << 8) | raw->temp_l);

	/* TODO: just save the multiplicative operands and linear
	 * offset instead of recomputing every time */
	return (int)(calib->t1_degc_x8 - calib->t0_degc_x8) *
		(temp_val - calib->t0_out) / (calib->t1_out - calib->t0_out) +
		calib->t0_degc_x8;
}

/**
 * @brief Convert a raw sensor sample to relative humidity multiplied by 2
 * @param raw Raw sensor data
 * @param calib Calibration data
 * @retval Relative humidity in percent, multiplied by 2
 */
static inline int rtio_hts221_rh_x2(struct rtio_hts221_raw *raw,
				    const struct rtio_hts221_calib *calib)
{
	s16_t rh_val = sys_le16_to_cpu((raw->rh_h << 8) | raw->rh_l);

	/* TODO: just save the multiplicative operands and linear
	 * offset instead of recomputing every time */
	return (int)(calib->h1_rh_x2 - calib->h0_rh_x2) *
		(rh_val - calib->h0_t0_out) / (calib->h1_t0_out -
					       calib->h0_t0_out) +
		calib->h0_rh_x2;
}

#ifdef __cplusplus
}
#endif

#endif
