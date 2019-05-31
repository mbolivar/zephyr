# SPDX-License-Identifier: Apache-2.0

board_set_flasher_ifndef(pyocd.sh)
board_set_debugger_ifndef(pyocd.sh)

set(PYOCD_TARGET nrf52)

set_property(GLOBAL APPEND PROPERTY FLASH_SCRIPT_ENV_VARS
  PYOCD_TARGET
  )
