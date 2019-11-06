/*
 * Copyright (c) 2017 Linaro Limited
 * Copyright (c) 2019, Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <drivers/led_strip.h>

#include <string.h>

#define LOG_LEVEL CONFIG_LED_STRIP_LOG_LEVEL
#include <logging/log.h>
LOG_MODULE_REGISTER(ws2812_spi);

#include <zephyr.h>
#include <device.h>
#include <drivers/spi.h>
#include <sys/util.h>

#define DEV_LABEL    DT_INST_0_WORLDSEMI_WS2812_SPI_LABEL

/*
 * WS2812-ish SPI master configuration:
 *
 * - mode 0 (the default), 8 bit, MSB first (arbitrary), one-line SPI
 * - no shenanigans (don't hold CS, don't hold the device lock, this
 *   isn't an EEPROM)
 */
#define SPI_BUS      DT_INST_0_WORLDSEMI_WS2812_SPI_BUS_NAME
#define SPI_SLAVE    DT_INST_0_WORLDSEMI_WS2812_SPI_BASE_ADDRESS
#define SPI_OPER     (SPI_OP_MODE_MASTER | SPI_TRANSFER_MSB | \
		      SPI_WORD_SET(8) | SPI_LINES_SINGLE)
#define SPI_FREQ     DT_INST_0_WORLDSEMI_WS2812_SPI_SPI_MAX_FREQUENCY
#define ONE_FRAME    DT_INST_0_WORLDSEMI_WS2812_SPI_SPI_ONE_FRAME
#define ZERO_FRAME   DT_INST_0_WORLDSEMI_WS2812_SPI_SPI_ZERO_FRAME

#define RED_ORDER    DT_INST_0_WORLDSEMI_WS2812_SPI_RED_ORDER
#define GREEN_ORDER  DT_INST_0_WORLDSEMI_WS2812_SPI_GREEN_ORDER
#define BLUE_ORDER   DT_INST_0_WORLDSEMI_WS2812_SPI_BLUE_ORDER
#define WHITE_ORDER  DT_INST_0_WORLDSEMI_WS2812_SPI_WHITE_ORDER

#if DT_INST_0_WORLDSEMI_WS2812_SPI_HAS_WHITE_CHANNEL
#define HAS_WHITE 1
#else
#define HAS_WHITE 0
#endif

#define RED_OFFSET   (8 * sizeof(u8_t) * RED_ORDER)
#define GRN_OFFSET   (8 * sizeof(u8_t) * GREEN_ORDER)
#define BLU_OFFSET   (8 * sizeof(u8_t) * BLUE_ORDER)
#if HAS_WHITE
#define WHT_OFFSET   (8 * sizeof(u8_t) * WHITE_ORDER)
#else
#define WHT_OFFSET   -1
#endif

#define NUM_PIXELS   DT_INST_0_WORLDSEMI_WS2812_SPI_CHAIN_LENGTH

/*
 * Despite datasheet claims, a 6 microsecond pulse is enough to reset
 * the strip. Convert that into a number of 8 bit SPI frames, adding
 * another just to be safe.
 */
#define RESET_NFRAMES ((size_t)ceiling_fraction(3 * SPI_FREQ, 4000000) + 1)

#if HAS_WHITE
#define BYTES_PER_PX 32
#else
#define BYTES_PER_PX 24
#endif

struct ws2812_data {
	struct device *spi;
	u8_t px_buf[BYTES_PER_PX * NUM_PIXELS];
};

/*
 * Convert a color channel's bits into a sequence of SPI frames (with
 * the proper pulse and inter-pulse widths) to shift out.
 */
static inline void ws2812_serialize_color(u8_t buf[8], u8_t color)
{
	int i;

	for (i = 0; i < 8; i++) {
		buf[i] = color & BIT(7 - i) ? ONE_FRAME : ZERO_FRAME;
	}
}

/*
 * Convert a pixel into SPI frames, returning the number of bytes used.
 */
static void ws2812_serialize_pixel(u8_t px[BYTES_PER_PX],
				   struct led_rgb *pixel)
{
	ws2812_serialize_color(px + RED_OFFSET, pixel->r);
	ws2812_serialize_color(px + GRN_OFFSET, pixel->g);
	ws2812_serialize_color(px + BLU_OFFSET, pixel->b);
	if (HAS_WHITE) {
		ws2812_serialize_color(px + WHT_OFFSET, 0); /* unused */
	}
}

/*
 * Latch current color values on strip and reset its state machines.
 */
static int ws2812_reset_strip(struct device *dev)
{
	struct ws2812_data *data = dev->driver_data;
	const struct spi_config *config = dev->config->config_info;
	u8_t reset_buf[RESET_NFRAMES];
	const struct spi_buf reset = {
		.buf = reset_buf,
		.len = sizeof(reset_buf),
	};
	const struct spi_buf_set tx = {
		.buffers = &reset,
		.count = 1
	};

	(void)memset(reset_buf, 0x00, sizeof(reset_buf));

	return spi_write(data->spi, config, &tx);
}

static int ws2812_strip_update_rgb(struct device *dev, struct led_rgb *pixels,
				   size_t num_pixels)
{
	struct ws2812_data *drv_data = dev->driver_data;
	const struct spi_config *config = dev->config->config_info;
	u8_t *px_buf = drv_data->px_buf;
	struct spi_buf buf = {
		.buf = px_buf,
		.len = BYTES_PER_PX * num_pixels,
	};
	const struct spi_buf_set tx = {
		.buffers = &buf,
		.count = 1
	};
	size_t i;
	int rc;

	if (num_pixels > NUM_PIXELS) {
		return -EINVAL;
	}

	for (i = 0; i < num_pixels; i++) {
		ws2812_serialize_pixel(&px_buf[BYTES_PER_PX * i], &pixels[i]);
	}

	rc = spi_write(drv_data->spi, config, &tx);
	if (rc) {
		(void)ws2812_reset_strip(dev);
		return rc;
	}

	return ws2812_reset_strip(dev);
}

static int ws2812_strip_update_channels(struct device *dev, u8_t *channels,
					size_t num_channels)
{
	struct ws2812_data *drv_data = dev->driver_data;
	const struct spi_config *config = dev->config->config_info;
	u8_t px_buf[8]; /* one byte per bit */
	const struct spi_buf buf = {
		.buf = px_buf,
		.len = sizeof(px_buf),
	};
	const struct spi_buf_set tx = {
		.buffers = &buf,
		.count = 1
	};
	size_t i;
	int rc;

	for (i = 0; i < num_channels; i++) {
		ws2812_serialize_color(px_buf, channels[i]);
		rc = spi_write(drv_data->spi, config, &tx);
		if (rc) {
			/*
			 * Latch anything we've shifted out first, to
			 * call visual attention to the problematic
			 * pixel.
			 */
			(void)ws2812_reset_strip(dev);
			LOG_ERR("can't set channel %u: %d", i, rc);
			return rc;
		}
	}

	return ws2812_reset_strip(dev);
}

static int ws2812_strip_init(struct device *dev)
{
	struct ws2812_data *data = dev->driver_data;

	data->spi = device_get_binding(SPI_BUS);
	if (!data->spi) {
		LOG_ERR("SPI device %s not found", SPI_BUS);
		return -ENODEV;
	}

	return 0;
}

static struct ws2812_data ws2812_strip_data;

static const struct spi_config ws2812_strip_spi_config = {
	.frequency = SPI_FREQ,
	.operation = SPI_OPER,
	.slave = SPI_SLAVE,
	.cs = NULL,
};

static const struct led_strip_driver_api ws2812_strip_spi_api = {
	.update_rgb = ws2812_strip_update_rgb,
	.update_channels = ws2812_strip_update_channels,
};

DEVICE_AND_API_INIT(ws2812_strip, DEV_LABEL, ws2812_strip_init,
		    &ws2812_strip_data, &ws2812_strip_spi_config,
		    POST_KERNEL, CONFIG_LED_STRIP_INIT_PRIORITY,
		    &ws2812_strip_spi_api);
