/*
 * Copyright (c) 2026 Qualcomm Innovation Center, Inc.
 * SPDX-License-Identifier: Apache-2.0
 *
 * This application exists to be built with -DDTS_NO_CLASSIC_BINDINGS=ON,
 * i.e. with the classic bindings language disabled, proving that the
 * board's devicetree is fully described by dt-schema documents. The
 * value of the test is that it builds at all in that mode; the program
 * itself only needs to reference the devicetree so the build is real.
 */

#include <zephyr/kernel.h>
#include <zephyr/devicetree.h>

int main(void)
{
	printk("dt-schema bindings build OK\n");
	return 0;
}
