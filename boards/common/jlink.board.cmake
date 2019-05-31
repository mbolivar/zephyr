# SPDX-License-Identifier: Apache-2.0

board_set_flasher_ifndef(jlink)
board_set_debugger_ifndef(jlink)
board_finalize_runner_args(jlink "--dt-flash=y")
