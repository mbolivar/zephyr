#!/usr/bin/env python3
# Copyright (c) 2026 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Compare classic bindings with their dt-schema translations.

For each compatible that is matched by both a classic binding and a
dt-schema document, this tool constructs both Binding objects and
diffs everything edtlib consumes: property specs (type, required,
default, const, enum), specifier cell names, bus typing, and child
bindings. It is the fast feedback loop for binding conversions.

By default it enforces a *faithful-superset* contract: the dt-schema
document must reproduce everything the classic binding specified -- so
the devicetree macros generated for any property the classic binding
defined are identical -- but it may also add properties, validation
constraints (e.g. value ranges via a reused upstream schema) and child
bindings. Dropping or contradicting anything the classic binding had is
a failure. Pass --strict to instead require exact equivalence, which is
useful for auditing a pure conversion that is not meant to add anything.

Example:

    python3 scripts/dts/check_schema_parity.py \
        --bindings-dir dts/bindings --schemas-dir dts/schemas
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent / 'python-devicetree' / 'src'))

from devicetree.dtschema_bindings import _COMMON_PROP_SPECS, DtSchemaBindings
from devicetree.edtlib import Binding


def _load_classic(bindings_dirs: list[str]
                 ) -> dict[tuple[str, str | None], Binding]:
    # Keyed by (compatible, on-bus), like edtlib's own binding registry,
    # so the multi-bus pattern (one compatible, several on-bus variants)
    # is compared variant by variant instead of silently last-wins.
    fname2path = {}
    paths = []
    for bindings_dir in bindings_dirs:
        for root, _, fnames in os.walk(bindings_dir):
            for fname in fnames:
                if fname.endswith(('.yaml', '.yml')):
                    path = os.path.join(root, fname)
                    fname2path[fname] = path
                    paths.append(path)
    key2binding = {}
    for path in paths:
        with open(path, encoding='utf-8') as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict) or 'compatible' not in raw:
            continue
        binding = Binding(path, fname2path, require_description=False)
        key2binding[binding.compatible, binding.on_bus] = binding
    return key2binding


def _diff_specs(compat: str, where: str, classic: Binding,
                translated: Binding, diffs: list[str],
                strict: bool) -> None:
    lspecs = classic.prop2specs
    tspecs = translated.prop2specs

    for name, lspec in sorted(lspecs.items()):
        tspec = tspecs.get(name)
        if tspec is None:
            if lspec.type == 'compound':
                continue
            if name == 'interrupt-parent':
                # Deliberately not translated: edtlib resolves it
                # internally, no macros are generated for it, and the
                # dt-schema meta-schema forbids documenting it.
                continue
            if name == '#gpio-cells' and 'gpio-controller' not in tspecs:
                # GPIO nexus nodes: deliberately not documented (the
                # meta-schema only allows it on gpio controllers); the
                # cell count comes from the devicetree and the cell
                # names from zephyr-extras.yaml, which the
                # specifier2cells comparison covers.
                continue
            diffs.append(f'{compat}{where}: property {name}: '
                         'missing from translated binding')
            continue
        for attr in ('type', 'required', 'default', 'const', 'enum',
                     'specifier_space'):
            lval = getattr(lspec, attr)
            tval = getattr(tspec, attr)
            if attr == 'specifier_space' and lval == tval:
                pass
            if lval != tval:
                diffs.append(f'{compat}{where}: property {name}: '
                             f'{attr}: classic={lval!r} translated={tval!r}')

    if strict:
        for name in sorted(set(tspecs) - set(lspecs)):
            if name in _COMMON_PROP_SPECS:
                continue
            diffs.append(f'{compat}{where}: property {name}: '
                         'only in translated binding')


def compare(compat: str, classic: Binding, translated: Binding,
            strict: bool) -> list[str]:
    diffs: list[str] = []

    if classic.specifier2cells != translated.specifier2cells:
        diffs.append(f'{compat}: specifier2cells: '
                     f'classic={classic.specifier2cells} '
                     f'translated={translated.specifier2cells}')
    if classic.buses != translated.buses:
        diffs.append(f'{compat}: buses: classic={classic.buses} '
                     f'translated={translated.buses}')
    if classic.on_bus != translated.on_bus:
        diffs.append(f'{compat}: on-bus: classic={classic.on_bus} '
                     f'translated={translated.on_bus}')

    _diff_specs(compat, '', classic, translated, diffs, strict)

    lchild, tchild = classic.child_binding, translated.child_binding
    where = ''
    while lchild or tchild:
        where += ' (child)'
        if lchild and not tchild:
            diffs.append(f'{compat}{where}: missing from translated binding')
            break
        if tchild and not lchild:
            # An extra child-binding is additive: the dt-schema document
            # describes child nodes the classic binding did not. Allowed
            # under the faithful-superset contract; reported only under
            # --strict (exact equivalence).
            if strict:
                diffs.append(f'{compat}{where}: only in translated binding')
            break
        _diff_specs(compat, where, lchild, tchild, diffs, strict)
        if lchild.specifier2cells != tchild.specifier2cells:
            diffs.append(f'{compat}{where}: specifier2cells: '
                         f'classic={lchild.specifier2cells} '
                         f'translated={tchild.specifier2cells}')
        lchild, tchild = lchild.child_binding, tchild.child_binding

    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bindings-dir', action='append', default=[],
                        help='classic bindings directory (multiple allowed)')
    parser.add_argument('--schemas-dir', action='append', default=[],
                        help='dt-schema directory (multiple allowed)')
    parser.add_argument('--strict', action='store_true',
                        help='''require exact equivalence: also report
                        properties or child-bindings that exist only in the
                        translated binding. By default the check enforces the
                        weaker faithful-superset contract -- the dt-schema
                        document may add properties, validation constraints and
                        child-bindings, but must never drop or contradict
                        anything the classic binding specified.''')
    parser.add_argument('compatibles', nargs='*',
                        help='compatibles to check (default: all that have '
                        'both a classic binding and a dt-schema document)')
    args = parser.parse_args()

    zephyr_root = Path(__file__).parents[2]
    bindings_dirs = args.bindings_dir or [str(zephyr_root / 'dts/bindings')]
    schemas_dirs = args.schemas_dir or [str(zephyr_root / 'dts/schemas')]

    classic = _load_classic(bindings_dirs)
    loader = DtSchemaBindings(schemas_dirs)

    classic_compats = {compat for compat, _ in classic}
    if args.compatibles:
        scope = set(args.compatibles)
    else:
        scope = classic_compats & set(loader._compat2schemas)

    translated = {(b.compatible, b.on_bus): b
                  for b in loader.bindings_for(scope)}

    # Every (compatible, on-bus) variant in scope, from either side.
    keys = sorted({k for k in classic if k[0] in scope}
                  | {k for k in translated if k[0] in scope})

    exit_code = 0
    for compat, on_bus in keys:
        label = compat if on_bus is None else f'{compat} (on-bus: {on_bus})'
        lbinding = classic.get((compat, on_bus))
        tbinding = translated.get((compat, on_bus))
        if lbinding is None:
            print(f'{label}: no classic binding found', file=sys.stderr)
            exit_code = 1
            continue
        if tbinding is None:
            print(f'{label}: no dt-schema document found', file=sys.stderr)
            exit_code = 1
            continue
        diffs = compare(label, lbinding, tbinding, args.strict)
        if diffs:
            exit_code = 1
            print(f'{label}: {len(diffs)} difference(s):')
            for diff in diffs:
                print(f'  {diff}')
        else:
            print(f'{label}: OK')

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
