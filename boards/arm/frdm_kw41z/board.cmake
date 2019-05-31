# SPDX-License-Identifier: Apache-2.0

set_ifndef(OPENSDA_FW daplink)

if(OPENSDA_FW STREQUAL jlink)
  board_set_debugger_ifndef(jlink)
  board_set_flasher_ifndef(jlink)
elseif(OPENSDA_FW STREQUAL daplink)
  board_set_debugger_ifndef(pyocd)
  board_set_flasher_ifndef(pyocd)
endif()

board_runner_args(jlink "--device=MKW41Z512xxx4")
board_runner_args(pyocd "--target=kw41z4")

include(${ZEPHYR_BASE}/boards/common/jlink.board.cmake)
include(${ZEPHYR_BASE}/boards/common/pyocd.board.cmake)
