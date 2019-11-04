from textwrap import dedent

import yaml

from west.commands import WestCommand
from west import log

class GenBobCheckout(WestCommand):

    def __init__(self):
        super().__init__('gen-bob-checkout',
            'convert west.yml to bob format',
            dedent('''\
            Convert the manifest contents into a checkoutSCM
            map suitable for Bob the Builder.'''))

    def do_add_parser(self, parser_adder):
        return parser_adder.add_parser(self.name, help=self.help,
                                       description=self.description)

    def do_run(self, args, unknown_args):
        bob_projs = []
        for p in self.manifest.projects:
            rev = p.revision
            url = p.url
            if p.path == 'zephyr':
                # assumes upstream zephyr (i.e. manifest.path == zephyr).
                # the manifest repository path is not tracked by west itself.
                url = 'https://github.com/zephyrproject-rtos/zephyr'
                rev = p.sha('HEAD')
            bob_projs.append({'scm': 'git',
                              'url': url,
                              'commit': rev,
                              'dir': p.path})
        log.inf(yaml.dump({'checkoutSCM': bob_projs}))
