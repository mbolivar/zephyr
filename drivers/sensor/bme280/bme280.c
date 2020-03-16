/* bme280.c - Driver for Bosch BME280 temperature and pressure sensor */

/*
 * Copyright (c) 2016, 2017 Intel Corporation
 * Copyright (c) 2017 IpTronix S.r.l.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <kernel.h>
#include <drivers/sensor.h>
#include <init.h>
#include <drivers/gpio.h>
#include <drivers/i2c.h>
#include <drivers/spi.h>
#include <sys/byteorder.h>
#include <sys/__assert.h>

#include <logging/log.h>

#include "bme280.h"

LOG_MODULE_REGISTER(BME280, CONFIG_SENSOR_LOG_LEVEL);

#if !BME28_BUS_SPI && !BME280_BUS_I2C
#error "No devices enabled (neither SPI nor I2C)"
#endif

static inline struct bme280_data *to_data(struct device *dev)
{
	return dev->driver_data;
}

static inline const struct bme280_config *to_config(struct device *dev)
{
	return dev->config->config_info;
}

static inline struct device *to_bus(struct device *dev)
{
	return to_data(dev)->bus;
}

static inline const union bme280_bus_config *to_bus_config(struct device *dev)
{
	return &to_config(dev)->bus_config;
}

#if BME280_BUS_SPI
static inline const struct spi_config *
to_spi_config(const union bme280_bus_config *bus_config)
{
	return &bus_config->spi_cfg->spi_cfg;
}

static int bme280_reg_read_spi(struct device *bus,
			       const union bme280_bus_config *bus_config,
			       u8_t start, u8_t *buf, int size)
{
	u8_t addr;
	const struct spi_buf tx_buf = {
		.buf = &addr,
		.len = 1
	};
	const struct spi_buf_set tx = {
		.buffers = &tx_buf,
		.count = 1
	};
	struct spi_buf rx_buf[2];
	const struct spi_buf_set rx = {
		.buffers = rx_buf,
		.count = 2
	};
	int i;

	rx_buf[0].buf = NULL;
	rx_buf[0].len = 1;

	rx_buf[1].len = 1;

	for (i = 0; i < size; i++) {
		int ret;

		addr = (start + i) | 0x80;
		rx_buf[1].buf = &buf[i];

		ret = spi_transceive(bus, to_spi_config(bus_config), &tx, &rx);
		if (ret) {
			LOG_DBG("spi_transceive FAIL %d\n", ret);
			return ret;
		}
	}

	return 0;
}

static int bme280_reg_write_spi(struct device *bus,
				const union bme280_bus_config *bus_config,
				u8_t reg, u8_t val)
{
	u8_t cmd[2] = { reg & 0x7F, val };
	const struct spi_buf tx_buf = {
		.buf = cmd,
		.len = 2
	};
	const struct spi_buf_set tx = {
		.buffers = &tx_buf,
		.count = 1
	};
	int ret;

	ret = spi_write(bus, to_spi_config(bus_config), &tx);
	if (ret) {
		LOG_DBG("spi_write FAIL %d\n", ret);
		return ret;
	}
	return 0;
}

static const struct bme280_reg_io bme280_reg_io_spi = {
	.read = bme280_reg_read_spi,
	.write = bme280_reg_write_spi,
};
#endif /* BME280_BUS_SPI */

#if BME280_BUS_I2C
static int bme280_reg_read_i2c(struct device *bus,
			       const union bme280_bus_config *bus_config,
			       u8_t start, u8_t *buf, int size)
{
	return i2c_burst_read(bus, bus_config->i2c_addr,
			      start, buf, size);
}

static int bme280_reg_write_i2c(struct device *bus,
				const union bme280_bus_config *bus_config,
				u8_t reg, u8_t val)
{
	return i2c_reg_write_byte(bus, bus_config->i2c_addr,
				  reg, val);
}

static const struct bme280_reg_io bme280_reg_io_i2c = {
	.read = bme280_reg_read_i2c,
	.write = bme280_reg_write_i2c,
};
#endif /* BME280_BUS_I2C */

static inline int bme280_reg_read(struct device *dev,
				  u8_t start, u8_t *buf, int size)
{
	return to_config(dev)->reg_io->read(to_bus(dev), to_bus_config(dev),
					    start, buf, size);
}

static inline int bme280_reg_write(struct device *dev, u8_t reg, u8_t val)
{
	return to_config(dev)->reg_io->write(to_bus(dev), to_bus_config(dev),
					     reg, val);
}

/*
 * Compensation code taken from BME280 datasheet, Section 4.2.3
 * "Compensation formula".
 */
static void bme280_compensate_temp(struct bme280_data *data, s32_t adc_temp)
{
	s32_t var1, var2;

	var1 = (((adc_temp >> 3) - ((s32_t)data->dig_t1 << 1)) *
		((s32_t)data->dig_t2)) >> 11;
	var2 = (((((adc_temp >> 4) - ((s32_t)data->dig_t1)) *
		  ((adc_temp >> 4) - ((s32_t)data->dig_t1))) >> 12) *
		((s32_t)data->dig_t3)) >> 14;

	data->t_fine = var1 + var2;
	data->comp_temp = (data->t_fine * 5 + 128) >> 8;
}

static void bme280_compensate_press(struct bme280_data *data, s32_t adc_press)
{
	s64_t var1, var2, p;

	var1 = ((s64_t)data->t_fine) - 128000;
	var2 = var1 * var1 * (s64_t)data->dig_p6;
	var2 = var2 + ((var1 * (s64_t)data->dig_p5) << 17);
	var2 = var2 + (((s64_t)data->dig_p4) << 35);
	var1 = ((var1 * var1 * (s64_t)data->dig_p3) >> 8) +
		((var1 * (s64_t)data->dig_p2) << 12);
	var1 = (((((s64_t)1) << 47) + var1)) * ((s64_t)data->dig_p1) >> 33;

	/* Avoid exception caused by division by zero. */
	if (var1 == 0) {
		data->comp_press = 0U;
		return;
	}

	p = 1048576 - adc_press;
	p = (((p << 31) - var2) * 3125) / var1;
	var1 = (((s64_t)data->dig_p9) * (p >> 13) * (p >> 13)) >> 25;
	var2 = (((s64_t)data->dig_p8) * p) >> 19;
	p = ((p + var1 + var2) >> 8) + (((s64_t)data->dig_p7) << 4);

	data->comp_press = (u32_t)p;
}

static void bme280_compensate_humidity(struct bme280_data *data,
				       s32_t adc_humidity)
{
	s32_t h;

	h = (data->t_fine - ((s32_t)76800));
	h = ((((adc_humidity << 14) - (((s32_t)data->dig_h4) << 20) -
		(((s32_t)data->dig_h5) * h)) + ((s32_t)16384)) >> 15) *
		(((((((h * ((s32_t)data->dig_h6)) >> 10) * (((h *
		((s32_t)data->dig_h3)) >> 11) + ((s32_t)32768))) >> 10) +
		((s32_t)2097152)) * ((s32_t)data->dig_h2) + 8192) >> 14);
	h = (h - (((((h >> 15) * (h >> 15)) >> 7) *
		((s32_t)data->dig_h1)) >> 4));
	h = (h > 419430400 ? 419430400 : h);

	data->comp_humidity = (u32_t)(h >> 12);
}

static int bme280_sample_fetch(struct device *dev, enum sensor_channel chan)
{
	struct bme280_data *data = to_data(dev);
	u8_t buf[8];
	s32_t adc_press, adc_temp, adc_humidity;
	int size = 6;
	int ret;

	__ASSERT_NO_MSG(chan == SENSOR_CHAN_ALL);

	if (data->chip_id == BME280_CHIP_ID) {
		size = 8;
	}
	ret = bme280_reg_read(dev, BME280_REG_PRESS_MSB, buf, size);
	if (ret < 0) {
		return ret;
	}

	adc_press = (buf[0] << 12) | (buf[1] << 4) | (buf[2] >> 4);
	adc_temp = (buf[3] << 12) | (buf[4] << 4) | (buf[5] >> 4);

	bme280_compensate_temp(data, adc_temp);
	bme280_compensate_press(data, adc_press);

	if (data->chip_id == BME280_CHIP_ID) {
		adc_humidity = (buf[6] << 8) | buf[7];
		bme280_compensate_humidity(data, adc_humidity);
	}

	return 0;
}

static int bme280_channel_get(struct device *dev,
			      enum sensor_channel chan,
			      struct sensor_value *val)
{
	struct bme280_data *data = to_data(dev);

	switch (chan) {
	case SENSOR_CHAN_AMBIENT_TEMP:
		/*
		 * data->comp_temp has a resolution of 0.01 degC.  So
		 * 5123 equals 51.23 degC.
		 */
		val->val1 = data->comp_temp / 100;
		val->val2 = data->comp_temp % 100 * 10000;
		break;
	case SENSOR_CHAN_PRESS:
		/*
		 * data->comp_press has 24 integer bits and 8
		 * fractional.  Output value of 24674867 represents
		 * 24674867/256 = 96386.2 Pa = 963.862 hPa
		 */
		val->val1 = (data->comp_press >> 8) / 1000U;
		val->val2 = (data->comp_press >> 8) % 1000 * 1000U +
			(((data->comp_press & 0xff) * 1000U) >> 8);
		break;
	case SENSOR_CHAN_HUMIDITY:
		/*
		 * data->comp_humidity has 22 integer bits and 10
		 * fractional.  Output value of 47445 represents
		 * 47445/1024 = 46.333 %RH
		 */
		val->val1 = (data->comp_humidity >> 10);
		val->val2 = (((data->comp_humidity & 0x3ff) * 1000U * 1000U) >> 10);
		break;
	default:
		return -EINVAL;
	}

	return 0;
}

static const struct sensor_driver_api bme280_api_funcs = {
	.sample_fetch = bme280_sample_fetch,
	.channel_get = bme280_channel_get,
};

static int bme280_read_compensation(struct device *dev)
{
	struct bme280_data *data = to_data(dev);
	u16_t buf[12];
	u8_t hbuf[7];
	int err = 0;

	err = bme280_reg_read(dev, BME280_REG_COMP_START,
			      (u8_t *)buf, sizeof(buf));

	if (err < 0) {
		return err;
	}

	data->dig_t1 = sys_le16_to_cpu(buf[0]);
	data->dig_t2 = sys_le16_to_cpu(buf[1]);
	data->dig_t3 = sys_le16_to_cpu(buf[2]);

	data->dig_p1 = sys_le16_to_cpu(buf[3]);
	data->dig_p2 = sys_le16_to_cpu(buf[4]);
	data->dig_p3 = sys_le16_to_cpu(buf[5]);
	data->dig_p4 = sys_le16_to_cpu(buf[6]);
	data->dig_p5 = sys_le16_to_cpu(buf[7]);
	data->dig_p6 = sys_le16_to_cpu(buf[8]);
	data->dig_p7 = sys_le16_to_cpu(buf[9]);
	data->dig_p8 = sys_le16_to_cpu(buf[10]);
	data->dig_p9 = sys_le16_to_cpu(buf[11]);

	if (data->chip_id == BME280_CHIP_ID) {
		err = bme280_reg_read(dev, BME280_REG_HUM_COMP_PART1,
				      &data->dig_h1, 1);
		if (err < 0) {
			return err;
		}

		err = bme280_reg_read(dev, BME280_REG_HUM_COMP_PART2, hbuf, 7);
		if (err < 0) {
			return err;
		}

		data->dig_h2 = (hbuf[1] << 8) | hbuf[0];
		data->dig_h3 = hbuf[2];
		data->dig_h4 = (hbuf[3] << 4) | (hbuf[4] & 0x0F);
		data->dig_h5 = ((hbuf[4] >> 4) & 0x0F) | (hbuf[5] << 4);
		data->dig_h6 = hbuf[6];
	}

	return 0;
}

static int bme280_chip_init(struct device *dev)
{
	struct bme280_data *data = to_data(dev);
	int err;

	err = bme280_reg_read(dev, BME280_REG_ID, &data->chip_id, 1);
	if (err < 0) {
		return err;
	}

	if (data->chip_id == BME280_CHIP_ID) {
		LOG_DBG("BME280 chip detected");
	} else if (data->chip_id == BMP280_CHIP_ID_MP ||
		   data->chip_id == BMP280_CHIP_ID_SAMPLE_1) {
		LOG_DBG("BMP280 chip detected");
	} else {
		LOG_DBG("bad chip id 0x%x", data->chip_id);
		return -ENOTSUP;
	}

	err = bme280_read_compensation(dev);
	if (err < 0) {
		return err;
	}

	if (data->chip_id == BME280_CHIP_ID) {
		err = bme280_reg_write(dev, BME280_REG_CTRL_HUM,
				       BME280_HUMIDITY_OVER);
		if (err < 0) {
			return err;
		}
	}

	err = bme280_reg_write(dev, BME280_REG_CTRL_MEAS,
			       BME280_CTRL_MEAS_VAL);
	if (err < 0) {
		return err;
	}

	err = bme280_reg_write(dev, BME280_REG_CONFIG,
			       BME280_CONFIG_VAL);
	if (err < 0) {
		return err;
	}

	return 0;
}

#if BME280_BUS_SPI
static inline int bme280_is_on_spi(struct device *dev)
{
	return to_config(dev)->reg_io == &bme280_reg_io_spi;
}

static inline int bme280_spi_init(struct device *dev)
{
	struct bme280_data *data = to_data(dev);
	const struct bme280_spi_cfg *spi_cfg = to_bus_config(dev)->spi_cfg;

	if (spi_cfg->cs_gpios_label != NULL) {
		data->spi_cs.gpio_dev = device_get_binding(
			spi_cfg->cs_gpios_label);
		if (!data->spi_cs.gpio_dev) {
			LOG_DBG("Unable to get GPIO SPI CS device %s",
				spi_cfg->cs_gpios_label);
			return -ENODEV;
		}
	}

	return 0;
}
#else
static inline int bme280_is_on_spi(struct device *dev)
{
	return 0;
}

static inline int bme280_spi_init(struct device *dev)
{
	return 0;
}
#endif

int bme280_init(struct device *dev)
{
	struct bme280_data *data = to_data(dev);
	const struct bme280_config *config = to_config(dev);
	int rc;

	data->bus = device_get_binding(config->bus_label);
	if (!data->bus) {
		LOG_DBG("bus \"%s\" not found", config->bus_label);
		return -EINVAL;
	}

	if (bme280_is_on_spi(dev)) {
		rc = bme280_spi_init(dev);
		if (rc < 0) {
			LOG_DBG("spi init failed: %d", rc);
			return -EINVAL;
		}
	}

	rc = bme280_chip_init(dev);
	if (rc < 0) {
		LOG_DBG("chip init failed: %d", rc);
		return -EINVAL;
	}

	LOG_DBG("device %s initialized", dev->config->name);
	return 0;
}


/* bme280_is_on_spi() can't be used at preprocessor time. */
#define BME280_ON_SPI(inst) DT_INST_ON_BUS(inst, spi)

/* This is only usable if BME280_ON_SPI(inst) */
#define BME280_HAS_CS(inst) DT_INST_SPI_DEV_HAS_CS(inst)

#define BME280_DEFINE(inst)						\
	COND_CODE_1(BME280_ON_SPI(inst),				\
		    (BME280_DEFINE_SPI(inst)),				\
		    (BME280_DEFINE_I2C(inst)))

#define BME280_DEFINE_SPI(inst)						\
	static struct bme280_data bme280_data_##inst =			\
		BME280_DATA_SPI(inst);					\
	static const struct bme280_config bme280_config_##inst =	\
		BME280_CONFIG_SPI(inst);				\
	BME280_DEVICE_INIT(inst)

#define BME280_DEFINE_I2C(inst)						\
	static struct bme280_data bme280_data_##inst;			\
	static const struct bme280_config bme280_config_##inst =	\
		BME280_CONFIG_I2C(inst);				\
        BME280_DEVICE_INIT(inst)

#define BME280_DEVICE_INIT(inst)					\
	DEVICE_AND_API_INIT(bme280_##inst,				\
			    DT_INST_LABEL(inst),			\
			    bme280_init,				\
			    &bme280_data_##inst,			\
			    &bme280_config_##inst,			\
			    POST_KERNEL,				\
			    CONFIG_SENSOR_INIT_PRIORITY,		\
			    &bme280_api_funcs)

#define BME280_DATA_SPI(inst)						\
	COND_CODE_1(BME280_HAS_CS(inst),				\
		    (BME280_DATA_SPI_CS(inst)),				\
		    ({}))

#define BME280_DATA_SPI_CS(inst)					\
	{ .spi_cs = { .gpio_pin = DT_INST_SPI_DEV_CS_GPIO_PIN(inst), }, }

#define BME280_CONFIG_SPI(inst)						\
	{								\
		.bus_label = DT_INST_BUS_LABEL(inst),			\
		.reg_io = &bme280_reg_io_spi,				\
		.bus_config = { .spi_cfg = BME280_SPI_CFG(inst)	}	\
	}

#define BME280_SPI_CFG(inst)						\
	(&(struct bme280_spi_cfg) {					\
		.spi_cfg = {						\
			.frequency =					\
				DT_INST_PROP(inst, spi_max_frequency),	\
			.operation = (SPI_WORD_SET(8) |			\
				      SPI_TRANSFER_MSB |		\
				      SPI_MODE_CPOL |			\
				      SPI_MODE_CPHA),			\
			.slave = DT_INST_REG_ADDR(inst),		\
			.cs = BME280_SPI_CS_PTR(inst),			\
		},							\
		.cs_gpios_label = BME280_SPI_CS_LABEL(inst),		\
	})

#define BME280_SPI_CS_PTR(inst)						\
	COND_CODE_1(BME280_HAS_CS(inst),				\
		    (&(bme280_data_##inst.spi_cs)),			\
		    (NULL))

#define BME280_SPI_CS_LABEL(inst)					\
	COND_CODE_1(BME280_HAS_CS(inst),				\
		    (DT_INST_SPI_DEV_CS_GPIO_LABEL(inst)), (NULL))

#define BME280_CONFIG_I2C(inst)						\
	{								\
		.bus_label = DT_INST_BUS_LABEL(inst),			\
		.reg_io = &bme280_reg_io_i2c,				\
		.bus_config =  { .i2c_addr = DT_INST_REG_ADDR(inst), }	\
	}

/* TODO replace with a foreach over all enabled devices when available */
#define HAVE_BME280(inst) DT_HAS_NODE(DT_DRV_INST(inst))

#if HAVE_BME280(0)
BME280_DEFINE(0);
#endif

#if HAVE_BME280(1)
BME280_DEFINE(1);
#endif
