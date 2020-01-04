/*
 * Copyright (c) 2019 Nordic Semiconductor ASA
 * Copyright (c) 2016 Intel Corporation
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#define LOG_DOMAIN "rtio_hts221"
#define LOG_LEVEL CONFIG_RTIO_LOG_LEVEL
#include <logging/log.h>
LOG_MODULE_REGISTER(rtio_hts221);

#include <string.h>

#include <device.h>
#include <drivers/i2c.h>
#include <drivers/rtio/hts221.h>
#include <drivers/sensor.h>
#include <generated_dts_board.h>
#include <init.h>
#include <sys/__assert.h>
#include <sys/byteorder.h>
#include <sys/util.h>
#include <zephyr/types.h>

#include "rtio_context.h"

/*
 * TODO clean this up -- various portions stolen from the regular driver
 */

#define HTS221_AUTOINCREMENT_ADDR	BIT(7)

#define HTS221_REG_WHO_AM_I		0x0F
#define HTS221_CHIP_ID			0xBC

#define HTS221_REG_CTRL1		0x20
#define HTS221_PD_BIT			BIT(7)
#define HTS221_BDU_BIT			BIT(2)
#define HTS221_ODR_SHIFT		0

#define HTS221_REG_CTRL3		0x22
#define HTS221_DRDY_EN			BIT(2)

#define HTS221_REG_DATA_START		0x28
#define HTS221_REG_CONVERSION_START	0x30

struct hts221_data {
	struct rtio_context rtio_context;
	struct rtio_hts221_calib calib;
	struct device *i2c;

	bool ready;
};

struct hts221_config {
	u8_t addr;		/* on I2C bus */
};

static inline struct hts221_data *dev_data(struct device *dev)
{
	return dev->driver_data;
}

static inline const struct hts221_config *dev_cfg(struct device *dev)
{
	return dev->config->config_info;
}

static inline u8_t dev_i2c_addr(struct device *dev)
{
	return dev_cfg(dev)->addr;
}

/* TODO memcpy? */
void rtio_hts221_get_calibration(struct device *dev,
				 const struct rtio_hts221_calib **calib)
{
	*calib = &dev_data(dev)->calib;
}

static int rtio_hts221_read_calib(struct device *dev)
{
	struct hts221_data *data = dev_data(dev);
	struct rtio_hts221_calib *calib = &data->calib;
	u8_t buf[16];
	int err;

	err = i2c_burst_read(data->i2c, dev_i2c_addr(dev),
			     HTS221_REG_CONVERSION_START |
			     HTS221_AUTOINCREMENT_ADDR,
			     buf, sizeof(buf));
	if (err) {
		LOG_ERR("Failed to read calibration data: %d", err);
		goto out;
	}

	calib->h0_rh_x2 = buf[0];
	calib->h1_rh_x2 = buf[1];
	calib->t0_degc_x8 = sys_le16_to_cpu(buf[2] | ((buf[5] & 0x3) << 8));
	calib->t1_degc_x8 = sys_le16_to_cpu(buf[3] | ((buf[5] & 0xC) << 6));
	calib->h0_t0_out = sys_le16_to_cpu(buf[6] | (buf[7] << 8));
	calib->h1_t0_out = sys_le16_to_cpu(buf[10] | (buf[11] << 8));
	calib->t0_out = sys_le16_to_cpu(buf[12] | (buf[13] << 8));
	calib->t1_out = sys_le16_to_cpu(buf[14] | (buf[15] << 8));

out:
	return err;
}

static int rtio_hts221_configure(struct device *dev, struct rtio_config *config)
{
	struct hts221_data *data = dev_data(dev);
	struct rtio_context *ctx = &data->rtio_context;
	struct rtio_hts221_config *driver_config = config->driver_config;
	const u8_t odr = driver_config->odr;
	int err;

	err = rtio_context_configure_begin(ctx);
	if (err) {
		goto out;
	}

	err = i2c_reg_write_byte(data->i2c, dev_i2c_addr(dev), HTS221_REG_CTRL1,
				 odr << HTS221_ODR_SHIFT |
				 HTS221_BDU_BIT |
				 HTS221_PD_BIT);
	if (err) {
		LOG_ERR("Failed to configure chip: %d", err);
		goto out;
	}

	/*
	 * The device requires about 2.2 ms to download the flash content
	 * into volatile memory.
	 */
	k_sleep(K_MSEC(3));
	err = rtio_hts221_read_calib(dev);

out:
	rtio_context_configure_end(ctx, config);
	return err;
}

static int rtio_hts221_sample_fetch(struct device *dev,
				    struct rtio_hts221_raw *raw)
{
	int err;

	err = i2c_burst_read(dev_data(dev)->i2c, dev_i2c_addr(dev),
			     HTS221_REG_DATA_START | HTS221_AUTOINCREMENT_ADDR,
			     (u8_t*)raw, sizeof(raw));
	if (err) {
		LOG_ERR("Failed to fetch data sample: %d", err);
	}
	return err;
}

static int rtio_hts221_trigger_read(struct device *dev, s32_t timeout)
{
	struct rtio_hts221_raw raw;
	struct rtio_context *ctx = &dev_data(dev)->rtio_context;
	struct rtio_block *block = NULL;
	int err;

	err = rtio_context_trigger_read_begin(ctx, &block, timeout);
	if (err) {
		goto out;
	}

	while (rtio_block_available(block) >= sizeof(struct rtio_hts221_raw)) {
		err = rtio_hts221_sample_fetch(dev, &raw);
		if (err) {
			goto out;
		}

		rtio_block_add_mem(block, &raw, sizeof(raw));
	}

out:
	rtio_context_trigger_read_end(ctx);
	return err;
}

static int rtio_hts221_check_who_am_i(struct device *dev)
{
	struct hts221_data *data = dev_data(dev);
	u8_t id;
	int err;

	err = i2c_reg_read_byte(data->i2c, dev_i2c_addr(dev),
				HTS221_REG_WHO_AM_I, &id);
	if (err) {
		LOG_ERR("Failed to read chip ID");
		return -EIO;
	}

	if (id != HTS221_CHIP_ID) {
		LOG_ERR("Invalid chip ID 0x%x", id);
		return -EINVAL;
	}

	return 0;
}

static int rtio_hts221_init(struct device *dev)
{
	struct hts221_data *data = dev_data(dev);

	data->i2c = device_get_binding(DT_INST_0_ST_HTS221_BUS_NAME);
	if (!data->i2c) {
		LOG_ERR("Failed to get pointer to %s device!",
			DT_INST_0_ST_HTS221_BUS_NAME);
		return -ENODEV;
	}

	rtio_context_init(&data->rtio_context);

	if (rtio_hts221_check_who_am_i(dev)) {
		return -EIO;
	}

	return 0;
};

/* FIXME make this a proper sensor driver, not just an RTIO driver */
static const struct rtio_api rtio_hts221_api = {
	.configure = rtio_hts221_configure,
	.trigger_read = rtio_hts221_trigger_read,
};

static struct hts221_data rtio_hts221_0_data;

static struct hts221_config rtio_hts221_0_config = {
	.addr = DT_INST_0_ST_HTS221_BASE_ADDRESS,
};

DEVICE_AND_API_INIT(rtio_hts221_0, DT_INST_0_ST_HTS221_LABEL,
		    rtio_hts221_init, &rtio_hts221_0_data,
		    &rtio_hts221_0_config, POST_KERNEL,
		    CONFIG_RTIO_INIT_PRIORITY, &rtio_hts221_api);
