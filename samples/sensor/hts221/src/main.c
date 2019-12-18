/*
 * Copyright (c) 2017 Linaro Limited
 * Copyright (c) 2019 Nordic Semiconductor ASA
 * Copyright (c) 2019 Tom Burdick <tom.burdick@electromatic.us>
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr.h>
#include <device.h>
#include <drivers/rtio.h>
#include <drivers/rtio/hts221.h>
#include <stdio.h>
#include <sys/util.h>

/*
 * Memory allocator and FIFO
 */

#define MAX_BLOCK_SIZE 256
static RTIO_MEMPOOL_ALLOCATOR_DEFINE(block_alloc, 64, MAX_BLOCK_SIZE, 32, 4);
static K_FIFO_DEFINE(dev_fifo);

/*
 * Device and configuration
 */

static struct device *dev;

static struct rtio_hts221_config driver_cfg = {
	.odr = RTIO_HTS221_ODR_12_5_HZ,
};

static const struct rtio_hts221_calib *calibration;

/*
 * Reader thread stack
 */

#define TRIGGER_READ_STACK_SIZE 1024
static K_THREAD_STACK_DEFINE(trigger_read_stack, TRIGGER_READ_STACK_SIZE);
static struct k_thread trigger_read_thread;

static void trigger_read(void *s1, void *s2, void *s3)
{
	int err;

	while (true) {
		err = rtio_trigger_read(dev, K_NO_WAIT);
		__ASSERT_NO_MSG(err == 0);
		k_sleep(K_MSEC(80));
	}
}

void main(void)
{
	const size_t sample_size = sizeof(struct rtio_hts221_raw);
	const size_t samples_per_block = MAX_BLOCK_SIZE / sample_size;
	struct rtio_config dev_cfg =  {
		.output_config = {
			.allocator = block_alloc,
			.fifo = &dev_fifo,
			.timeout = K_FOREVER,
			.byte_size = samples_per_block * sample_size,
		},
		.driver_config = &driver_cfg,
	};
	unsigned int reading = 0, block_num = 0;
	int err;

	printf("RTIO based HTS221 sensor\n");

	dev = device_get_binding(DT_INST_0_ST_HTS221_LABEL); /* must be in DT */
	if (dev == NULL) {
		printf("Could not get HTS221 device\n");
		return;
	}
	printf("Found device %s - %p\n", DT_INST_0_ST_HTS221_LABEL, dev);

	err = rtio_configure(dev, &dev_cfg);
	if (err) {
		printf("Could not configure HTS221 device: %d\n", err);
		return;
	}
	printf("Configured.\n");

	rtio_hts221_get_calibration(dev, &calibration);
	printf("got device calibration: %p\n", calibration);

	k_thread_create(&trigger_read_thread,
			trigger_read_stack,
			K_THREAD_STACK_SIZEOF(trigger_read_stack),
			trigger_read,
			NULL, NULL, NULL,
			5, 0, K_NO_WAIT);

	while (true) {
		struct rtio_block *block;
		struct rtio_hts221_raw *raw;

		printf("Waiting for block %u\n", block_num);
		block = k_fifo_get(&dev_fifo, K_FOREVER);
		while (block->buf.len > sizeof(*raw)) {
			int degc_x8; /* Degrees celsius, multiplied by 8. */
			int rh_x2; /* Relative humidity (percent), x 2. */
			float degc, rh; /* Floating point SI units. */

			/* TODO ensure allocator respects alignment */
			raw = rtio_block_pull(block, sizeof(*raw));

			degc_x8 = rtio_hts221_degc_x8(raw, calibration);
			rh_x2 = rtio_hts221_rh_x2(raw, calibration);
			degc = (float)degc_x8 / 8.0f;
			rh = (float)rh_x2 / 2.0f;

			printf("\treading %u: %.1f°C, %.1f%% humidity\n",
			       ++reading, degc, rh);
		}

		printf("Block %u done\n", block_num++);
		rtio_block_free(block_alloc, block);
	}
}
