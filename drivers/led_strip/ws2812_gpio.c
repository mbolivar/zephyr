/*
 * Copyright (c) 2018 Intel Corporation
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <drivers/led_strip.h>

#include <string.h>

#define LOG_LEVEL CONFIG_LED_STRIP_LOG_LEVEL
#include <logging/log.h>
LOG_MODULE_REGISTER(ws2812_gpio);

#include <zephyr.h>
#include <soc.h>
#include <drivers/gpio.h>
#include <device.h>
#include <drivers/clock_control.h>

#define WS2812_DEV          DT_INST_0_WORLDSEMI_WS2812_GPIO_IN_GPIOS_CONTROLLER
#define WS2812_PIN          DT_INST_0_WORLDSEMI_WS2812_GPIO_IN_GPIOS_PIN
#define WS2812_PIN_FLAGS    DT_INST_0_WORLDSEMI_WS2812_GPIO_IN_GPIOS_FLAGS

#define RED_ORDER    DT_INST_0_WORLDSEMI_WS2812_GPIO_RED_ORDER
#define GREEN_ORDER  DT_INST_0_WORLDSEMI_WS2812_GPIO_GREEN_ORDER
#define BLUE_ORDER   DT_INST_0_WORLDSEMI_WS2812_GPIO_BLUE_ORDER
#define WHITE_ORDER  DT_INST_0_WORLDSEMI_WS2812_GPIO_WHITE_ORDER

#if (DT_INST_0_WORLDSEMI_WS2812_SPI_HAS_WHITE_CHANNEL == 1)
#define HAS_WHITE 1
#else
#define HAS_WHITE 0
#endif

#define RED_OFFSET   (sizeof(u8_t) * RED_ORDER)
#define GRN_OFFSET   (sizeof(u8_t) * GREEN_ORDER)
#define BLU_OFFSET   (sizeof(u8_t) * BLUE_ORDER)
#if HAS_WHITE
#define WHT_OFFSET   (sizeof(u8_t) * WHITE_ORDER)
#define STRIDE       4
#else
#define WHT_OFFSET   -1
#define STRIDE       3
#endif

/*
 * The inline assembly further below is designed to work on nRF51
 * devices with the 16 MHz clock enabled.
 *
 * TODO: try to make this portable, or at least port to more devices.
 */
#define CLOCK_LABEL  DT_INST_0_NORDIC_NRF_CLOCK_LABEL "_16M"

struct ws2812_gpio_data {
	struct device *clock;
};

static int send_buf(struct ws2812_gpio_data *data, u8_t *buf, size_t len)
{
	/* Address of OUTSET. OUTCLR is OUTSET + 4 */
	volatile u32_t *base = (u32_t *)(NRF_GPIO_BASE + 0x508);
	u32_t pin = BIT(WS2812_PIN);
	unsigned int key;
	/*
	 * Initialization of i is strictly not needed, but it avoids an
	 * uninitialized warning with the inline assembly.
	 */
	u32_t i = 0U;
	int rc = 0;

	rc = clock_control_on(data->clock, NULL);
	if (rc) {
		return rc;
	}

	key = irq_lock();

	while (len--) {
		u32_t b = *buf++;

		/* Generate signal out of the bits, MSB. 1-bit should be
		 * roughly 0.85us high, 0.4us low, whereas a 0-bit should be
		 * roughly 0.4us high, 0.85us low.
		 */
		__asm volatile ("movs %[i], #8\n" /* i = 8 */
				".start_bit:\n"

				/* OUTSET = BIT(LED_PIN) */
				"strb %[p], [%[r], #0]\n"

				/* if (b & 0x80) goto .long */
				"tst %[b], %[m]\n"
				"bne .long\n"

				/* 0-bit */
				"nop\nnop\n"
				/* OUTCLR = BIT(LED_PIN) */
				"strb %[p], [%[r], #4]\n"
				"nop\nnop\nnop\n"
				"b .next_bit\n"

				/* 1-bit */
				".long:\n"
				"nop\nnop\nnop\nnop\nnop\nnop\nnop\n"
				/* OUTCLR = BIT(LED_PIN) */
				"strb %[p], [%[r], #4]\n"

				".next_bit:\n"
				/* b <<= 1 */
				"lsl %[b], #1\n"
				/* i-- */
				"sub %[i], #1\n"
				/* if (i > 0) goto .start_bit */
				"bne .start_bit\n"
				:
				[i] "+r" (i)
				:
				[b] "l" (b),
				[m] "l" (0x80),
				[r] "l" (base),
				[p] "r" (pin)
				:);
	}

	irq_unlock(key);

	rc = clock_control_off(data->clock, NULL);

	return rc;
}

static int ws2812_gpio_update_rgb(struct device *dev, struct led_rgb *pixels,
				  size_t num_pixels)
{
	u8_t *ptr = (u8_t *)pixels;
	size_t i;


	/* Convert from RGB to on-wire format */
	for (i = 0; i < num_pixels; i++) {
		u8_t r = pixels[i].r;
		u8_t g = pixels[i].g;
		u8_t b = pixels[i].b;

		ptr[i * STRIDE + RED_OFFSET] = r;
		ptr[i * STRIDE + GRN_OFFSET] = g;
		ptr[i * STRIDE + BLU_OFFSET] = b;
#if HAS_WHITE
		ptr[i * STRIDE + WHT_OFFSET] = 0;
#endif
	}

	return send_buf(dev->driver_data, (u8_t *)pixels, num_pixels * STRIDE);
}

static int ws2812_gpio_update_channels(struct device *dev, u8_t *channels,
				       size_t num_channels)
{
	LOG_ERR("update_channels not implemented");
	return -ENOTSUP;
}

static int ws2812_gpio_init(struct device *dev)
{
	struct ws2812_gpio_data *data = dev->driver_data;
	struct device *gpio;

	gpio = device_get_binding(WS2812_DEV);
	if (!gpio) {
		LOG_ERR("Unable to find %s", WS2812_DEV);
		return -ENODEV;
	}

	data->clock = device_get_binding(CLOCK_LABEL);
	if (!data->clock) {
		LOG_ERR("Unable to find %s", CLOCK_LABEL);
		return -ENODEV;
	}

	return gpio_pin_configure(gpio, WS2812_PIN,
				  WS2812_PIN_FLAGS | GPIO_OUTPUT);
}

static struct ws2812_gpio_data ws2812_data;

static const struct led_strip_driver_api ws2812_gpio_api = {
	.update_rgb = ws2812_gpio_update_rgb,
	.update_channels = ws2812_gpio_update_channels,
};

DEVICE_AND_API_INIT(ws2812_gpio, DT_INST_0_WORLDSEMI_WS2812_GPIO_LABEL,
		    ws2812_gpio_init, &ws2812_data, NULL, POST_KERNEL,
		    CONFIG_LED_STRIP_INIT_PRIORITY, &ws2812_gpio_api);
