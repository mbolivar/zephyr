# SPDX-License-Identifier: Apache-2.0

set(PY_FILE "/tmp/out_of_tree.py")

file(WRITE "${PY_FILE}" "\
import pathlib
import os
import sys

sys.path.append(str(pathlib.Path(os.getenv('ZEPHYR_BASE')) /
                    'scripts' /
                    'west_commands'))

from runners.core import ZephyrBinaryRunner, RunnerCaps

class OutOfTreeRunner(ZephyrBinaryRunner):
    '''Runner front-end for nrfjprog.'''

    def __init__(self, cfg, foo):
        super().__init__(cfg)
        self.foo = foo

    @classmethod
    def name(cls):
        return 'oot'

    @classmethod
    def capabilities(cls):
        return RunnerCaps(commands={'flash'})

    @classmethod
    def do_add_parser(cls, parser):
        parser.add_argument('--foo', required=True)

    @classmethod
    def create(cls, cfg, args):
        return OutOfTreeRunner(cfg, args.foo)

    def do_run(self, command, **kwargs):
        self.logger.info(f'hello there, {self.foo}')
")


board_add_oot_runner(oot "${PY_FILE}" "--foo=\"from board.cmake\"")

board_runner_args(nrfjprog "--nrf-family=NRF52")
board_runner_args(jlink "--device=nrf52" "--speed=4000")
board_runner_args(pyocd "--target=nrf52" "--frequency=4000000")
include(${ZEPHYR_BASE}/boards/common/nrfjprog.board.cmake)
include(${ZEPHYR_BASE}/boards/common/jlink.board.cmake)
include(${ZEPHYR_BASE}/boards/common/pyocd.board.cmake)
include(${ZEPHYR_BASE}/boards/common/openocd-nrf5.board.cmake)
