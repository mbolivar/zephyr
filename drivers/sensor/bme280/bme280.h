/*
 * Copyright (c) 2016, 2017 Intel Corporation
 * Copyright (c) 2017 IpTronix S.r.l.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef ZEPHYR_DRIVERS_SENSOR_BME280_BME280_H_
#define ZEPHYR_DRIVERS_SENSOR_BME280_BME280_H_

#include <zephyr/types.h>
#include <device.h>
#include <devicetree.h>

#define DT_DRV_COMPAT bosch_bme280
#define BME280_BUS_SPI DT_ANY_INST_ON_BUS(spi)
#define BME280_BUS_I2C DT_ANY_INST_ON_BUS(i2c)

#define BME280_REG_PRESS_MSB            0xF7
#define BME280_REG_COMP_START           0x88
#define BME280_REG_HUM_COMP_PART1       0xA1
#define BME280_REG_HUM_COMP_PART2       0xE1
#define BME280_REG_ID                   0xD0
#define BME280_REG_CONFIG               0xF5
#define BME280_REG_CTRL_MEAS            0xF4
#define BME280_REG_CTRL_HUM             0xF2

#define BMP280_CHIP_ID_SAMPLE_1         0x56
#define BMP280_CHIP_ID_SAMPLE_2         0x57
#define BMP280_CHIP_ID_MP               0x58
#define BME280_CHIP_ID                  0x60
#define BME280_MODE_NORMAL              0x03
#define BME280_SPI_3W_DISABLE           0x00

#if defined CONFIG_BME280_TEMP_OVER_1X
#define BME280_TEMP_OVER                (1 << 5)
#elif defined CONFIG_BME280_TEMP_OVER_2X
#define BME280_TEMP_OVER                (2 << 5)
#elif defined CONFIG_BME280_TEMP_OVER_4X
#define BME280_TEMP_OVER                (3 << 5)
#elif defined CONFIG_BME280_TEMP_OVER_8X
#define BME280_TEMP_OVER                (4 << 5)
#elif defined CONFIG_BME280_TEMP_OVER_16X
#define BME280_TEMP_OVER                (5 << 5)
#endif

#if defined CONFIG_BME280_PRESS_OVER_1X
#define BME280_PRESS_OVER               (1 << 2)
#elif defined CONFIG_BME280_PRESS_OVER_2X
#define BME280_PRESS_OVER               (2 << 2)
#elif defined CONFIG_BME280_PRESS_OVER_4X
#define BME280_PRESS_OVER               (3 << 2)
#elif defined CONFIG_BME280_PRESS_OVER_8X
#define BME280_PRESS_OVER               (4 << 2)
#elif defined CONFIG_BME280_PRESS_OVER_16X
#define BME280_PRESS_OVER               (5 << 2)
#endif

#if defined CONFIG_BME280_HUMIDITY_OVER_1X
#define BME280_HUMIDITY_OVER            1
#elif defined CONFIG_BME280_HUMIDITY_OVER_2X
#define BME280_HUMIDITY_OVER            2
#elif defined CONFIG_BME280_HUMIDITY_OVER_4X
#define BME280_HUMIDITY_OVER            3
#elif defined CONFIG_BME280_HUMIDITY_OVER_8X
#define BME280_HUMIDITY_OVER            4
#elif defined CONFIG_BME280_HUMIDITY_OVER_16X
#define BME280_HUMIDITY_OVER            5
#endif

#if defined CONFIG_BME280_STANDBY_05MS
#define BME280_STANDBY                  0
#elif defined CONFIG_BME280_STANDBY_62MS
#define BME280_STANDBY                  (1 << 5)
#elif defined CONFIG_BME280_STANDBY_125MS
#define BME280_STANDBY                  (2 << 5)
#elif defined CONFIG_BME280_STANDBY_250MS
#define BME280_STANDBY                  (3 << 5)
#elif defined CONFIG_BME280_STANDBY_500MS
#define BME280_STANDBY                  (4 << 5)
#elif defined CONFIG_BME280_STANDBY_1000MS
#define BME280_STANDBY                  (5 << 5)
#elif defined CONFIG_BME280_STANDBY_2000MS
#define BME280_STANDBY                  (6 << 5)
#elif defined CONFIG_BME280_STANDBY_4000MS
#define BME280_STANDBY                  (7 << 5)
#endif

#if defined CONFIG_BME280_FILTER_OFF
#define BME280_FILTER                   0
#elif defined CONFIG_BME280_FILTER_2
#define BME280_FILTER                   (1 << 2)
#elif defined CONFIG_BME280_FILTER_4
#define BME280_FILTER                   (2 << 2)
#elif defined CONFIG_BME280_FILTER_8
#define BME280_FILTER                   (3 << 2)
#elif defined CONFIG_BME280_FILTER_16
#define BME280_FILTER                   (4 << 2)
#endif

#define BME280_CTRL_MEAS_VAL            (BME280_PRESS_OVER | \
					 BME280_TEMP_OVER |  \
					 BME280_MODE_NORMAL)
#define BME280_CONFIG_VAL               (BME280_STANDBY | \
					 BME280_FILTER |  \
					 BME280_SPI_3W_DISABLE)

/*
 * This driver is an example of why devices should be resolvable at
 * link time instead of only at runtime via device_get_binding().
 *
 * We only need to store 'bus' and 'spi_cs' in RAM because we can't
 * resolve devices at link time. They should be moved to ROM if that
 * becomes possible. That would in turn enable several further
 * cleanups.
 */

struct bme280_data {
	struct device *bus;
#if BME280_BUS_SPI
	struct spi_cs_control spi_cs;
#endif

	/* Compensation parameters. */
	u16_t dig_t1;
	s16_t dig_t2;
	s16_t dig_t3;
	u16_t dig_p1;
	s16_t dig_p2;
	s16_t dig_p3;
	s16_t dig_p4;
	s16_t dig_p5;
	s16_t dig_p6;
	s16_t dig_p7;
	s16_t dig_p8;
	s16_t dig_p9;
	u8_t dig_h1;
	s16_t dig_h2;
	u8_t dig_h3;
	s16_t dig_h4;
	s16_t dig_h5;
	s8_t dig_h6;

	/* Compensated values. */
	s32_t comp_temp;
	u32_t comp_press;
	u32_t comp_humidity;

	/* Carryover between temperature and pressure/humidity compensation. */
	s32_t t_fine;

	u8_t chip_id;
};

struct bme280_spi_cfg {
	struct spi_config spi_cfg;
	const char *cs_gpios_label;
};

union bme280_bus_config {
#if BME280_BUS_SPI
	const struct bme280_spi_cfg *spi_cfg;
#endif
#if BME280_BUS_I2C
	u16_t i2c_addr;
#endif
};

struct bme280_config {
	const char *bus_label;
	const struct bme280_reg_io *reg_io;
	const union bme280_bus_config bus_config;
};

typedef int (*bme280_reg_read_fn)(struct device *bus,
				  const union bme280_bus_config *bus_config,
				  u8_t start, u8_t *buf, int size);
typedef int (*bme280_reg_write_fn)(struct device *bus,
				   const union bme280_bus_config *bus_config,
				   u8_t reg, u8_t val);

struct bme280_reg_io {
	bme280_reg_read_fn read;
	bme280_reg_write_fn write;
};

#endif /* ZEPHYR_DRIVERS_SENSOR_BME280_BME280_H_ */
