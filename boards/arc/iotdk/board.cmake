# SPDX-License-Identifier: Apache-2.0

# TODO: can this board just use the usual openocd runner?
board_set_flasher_ifndef(em-starterkit)
board_set_debugger_ifndef(em-starterkit)
board_finalize_runner_args(em-starterkit)
