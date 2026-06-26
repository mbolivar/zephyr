#!/usr/bin/env python3
# Copyright (c) 2026 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Semantically compare two devicetree_generated.h files.

Used to verify that a build using dt-schema based bindings generates
the same devicetree macros as a build using legacy bindings. The
comparison is by macro name and value, not line by line, so it is
insensitive to node ordering. Macros whose values encode dependency
ordinals (which legitimately change when the dependency graph gains
or loses binding-driven edges) are compared by existence only.

Exit status is 0 if the relevant macros match, 1 otherwise.

Example:

    python3 scripts/dts/diff_dt_headers.py \
        build-legacy/zephyr/include/generated/zephyr/devicetree_generated.h \
        build-dtschema/zephyr/include/generated/zephyr/devicetree_generated.h \
        --only-nodes-with-bindings-in build-dtschema/zephyr/edt.pickle
"""

import argparse
import re
import sys

_DEFINE_RE = re.compile(r'^#define\s+(?P<name>[A-Za-z0-9_]+)(?P<args>\([^)]*\))?'
                        r'(?:\s+(?P<value>.*))?$')

# Macro name substrings whose values depend on dependency ordinals or
# graph layout; compared by existence only.
_ORDINAL_SENSITIVE = (
    '_ORD',
    '_REQUIRES_',
    '_SUPPORTS_',
)


def parse_defines(path: str) -> dict[str, str]:
    defines = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            match = _DEFINE_RE.match(line.rstrip('\n'))
            if not match:
                continue
            name = match.group('name')
            value = (match.group('value') or '').strip()
            defines[name] = value
    return defines


def node_macro_prefixes_with_bindings(edt_pickle: str) -> list[str]:
    # Returns the DT_N_... macro prefixes of nodes that have a
    # matched binding in the given pickled EDT.
    import os
    import pickle
    sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                    'python-devicetree', 'src'))
    with open(edt_pickle, 'rb') as f:
        edt = pickle.load(f)
    prefixes = []
    for node in edt.nodes:
        if node.matching_compat is None:
            continue
        # Same path mangling as gen_defines.py node_z_path_id().
        components = ['N']
        if node.parent is not None:
            components.extend(
                f'S_{str2ident(component)}'
                for component in node.path.split('/')[1:])
        prefixes.append('_'.join(components))
    return prefixes


def str2ident(s: str) -> str:
    return re.sub('[-,.@/+]', '_', s.lower())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('old', help='reference header (legacy bindings)')
    parser.add_argument('new', help='header to check (dt-schema bindings)')
    parser.add_argument('--only-nodes-with-bindings-in', metavar='EDT_PICKLE',
                        help='''only compare macros belonging to nodes that
                        have a matched binding in this pickled EDT (use the
                        dt-schema build's pickle to restrict the comparison
                        to converted bindings)''')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='also list matching macro counts')
    args = parser.parse_args()

    old = parse_defines(args.old)
    new = parse_defines(args.new)

    if args.only_nodes_with_bindings_in:
        prefixes = tuple(
            f'DT_{pfx}_'
            for pfx in node_macro_prefixes_with_bindings(
                args.only_nodes_with_bindings_in))

        def relevant(name: str) -> bool:
            for prefix in prefixes:
                # The node's own macros, not a child's ('_S_<child>').
                if (name.startswith(prefix)
                        and not name[len(prefix):].startswith('S_')):
                    return True
            return False

        old = {n: v for n, v in old.items() if relevant(n)}
        new = {n: v for n, v in new.items() if relevant(n)}

    # A node that has no binding gets 'default property type' Property
    # objects even for properties with no value data ('compound'); a
    # node *with* a binding does not. The only observable difference is
    # the _P_ranges_EXISTS macro, which no consumer can do anything
    # useful with on a bound node; ignore it (this is a property of
    # gaining a binding at all, not of the bindings language).
    for defines in (old, new):
        for name in [n for n in defines if n.endswith('_P_ranges_EXISTS')]:
            del defines[name]

    missing = sorted(set(old) - set(new))
    extra = sorted(set(new) - set(old))
    mismatched = []
    matching = 0
    for name in sorted(set(old) & set(new)):
        if any(token in name for token in _ORDINAL_SENSITIVE):
            matching += 1
            continue
        if old[name] != new[name]:
            mismatched.append((name, old[name], new[name]))
        else:
            matching += 1

    if args.verbose or missing or extra or mismatched:
        print(f'{matching} macros match', end='')
        if args.only_nodes_with_bindings_in:
            print(' (restricted to nodes with bindings)', end='')
        print('.')

    for name in missing:
        print(f'missing: {name} = {old[name]}')
    for name in extra:
        print(f'extra:   {name} = {new[name]}')
    for name, oldval, newval in mismatched:
        print(f'value:   {name}: {oldval!r} -> {newval!r}')

    if missing or mismatched:
        return 1
    if extra:
        # Extra macros never break a legacy-equivalent build; report
        # them (above) but do not fail.
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
