#!/usr/bin/env python3
# Copyright (c) 2026 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Validate devicetree property *values* against dt-schema documents.

This is the piece of validation the classic Zephyr bindings language
cannot do. edtlib checks that a property has the right *type* and that
required properties are present, but never looks at the values: a
``current-speed`` of 99 GHz, three entries in a ``reg`` that should
have one, a ``reg-io-width`` of 3 where only 1 and 4 are legal -- all
of these build without complaint. dt-schema documents can constrain
values (``minimum``/``maximum``, item counts, ``enum``, conditional
``dependencies``, ...), and dt-schema's validator checks a *compiled*
devicetree against them.

This wraps that validator for a Zephyr build: it compiles the
devicetree source with ``dtc`` and validates the result against the
``dts/schemas`` documents. By default it validates each node against
the schema for its most specific compatible (``--compatible-match``),
so it reports on Zephyr's own bindings rather than on the upstream
core schemas Zephyr's devicetree does not fully follow.

Examples::

    # Validate a build's devicetree against the in-tree schemas:
    python3 scripts/dts/dts_validate.py --build-dir build

    # Validate a single source file against specific schema dirs:
    python3 scripts/dts/dts_validate.py --schemas-dir dts/schemas my.dts

``dtc`` is found on PATH or via the ``DTC`` environment variable (the
Zephyr SDK ships one). Exit status is non-zero if any value error is
found, so this can gate CI.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def find_dtc(explicit: str | None = None) -> str:
    """Locate the devicetree compiler."""
    for candidate in (explicit, os.environ.get('DTC'), 'dtc'):
        if candidate and shutil.which(candidate):
            return shutil.which(candidate)  # type: ignore[return-value]
    sys.exit("error: 'dtc' not found. Put it on PATH, pass --dtc, or set "
             "$DTC (the Zephyr SDK ships one under hosttools/).")


def compile_dts(dts_path: str, dtc: str) -> bytes:
    """Compile a devicetree source file to a flattened blob (.dtb)."""
    with tempfile.NamedTemporaryFile(suffix='.dtb', delete=False) as tmp:
        out = tmp.name
    try:
        result = subprocess.run(
            [dtc, '-I', 'dts', '-O', 'dtb', '-o', out, dts_path],
            capture_output=True, text=True)
        if result.returncode != 0:
            sys.exit(f"error: dtc failed on {dts_path}:\n{result.stderr}")
        with open(out, 'rb') as f:
            return f.read()
    finally:
        if os.path.exists(out):
            os.unlink(out)


def validate_dtb(blob: bytes, schema_dirs: list[str],
                 compatible_match: bool = True,
                 limit: str | None = None) -> list[str]:
    """
    Validate a flattened devicetree against dt-schema documents.

    Returns a list of human-readable value-error strings (empty if the
    devicetree satisfies every matching schema's value constraints).
    """
    import dtschema

    validator = dtschema.DTValidator([os.path.abspath(d) for d in schema_dirs])
    tree = validator.decode_dtb(blob)
    errors: list[str] = []

    def check(node: dict, nodename: str) -> None:
        if not isinstance(node, dict):
            return
        disabled = 'disabled' in (node.get('status') or [])
        local = dict(node)
        local['$nodename'] = [nodename]
        compat = node.get('compatible', [None])[0]
        for error in validator.iter_errors(local, filter=limit,
                                           compatible_match=compatible_match):
            if error.schema_file == 'generated-compatibles':
                # Node whose compatible matches no schema: not a value
                # error, just an unconverted binding.
                continue
            if disabled and {'required', 'unevaluatedProperties'} & set(
                    error.schema_path):
                continue
            errors.append(dtschema.format_error('', error, nodename=nodename,
                                                compatible=compat))
        for name, value in node.items():
            if isinstance(value, dict):
                check(value, name)

    for subtree in tree:
        check(subtree, '/')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument('--build-dir',
                     help="Zephyr build directory; validates "
                          "<build-dir>/zephyr/zephyr.dts")
    src.add_argument('dts', nargs='?', help="devicetree source file")
    parser.add_argument('--schemas-dir', action='append', default=[],
                        help="dt-schema directory (repeatable); "
                             "default: <zephyr>/dts/schemas")
    parser.add_argument('--dtc', help="path to the devicetree compiler")
    parser.add_argument('--all-schemas', action='store_true',
                        help="validate against every matching schema, not "
                             "just the most-specific-compatible one (noisier: "
                             "also checks upstream core schemas)")
    parser.add_argument('--limit',
                        help="only schemas whose $id contains this substring")
    args = parser.parse_args()

    if args.build_dir:
        dts_path = os.path.join(args.build_dir, 'zephyr', 'zephyr.dts')
    else:
        dts_path = args.dts
    if not os.path.isfile(dts_path):
        sys.exit(f"error: devicetree source not found: {dts_path}")

    schema_dirs = args.schemas_dir or [
        str(Path(__file__).parents[2] / 'dts' / 'schemas')]

    dtc = find_dtc(args.dtc)
    blob = compile_dts(dts_path, dtc)
    errors = validate_dtb(blob, schema_dirs,
                          compatible_match=not args.all_schemas,
                          limit=args.limit)

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"\n{len(errors)} value error(s) in {dts_path}", file=sys.stderr)
        return 1
    print(f"{dts_path}: devicetree values satisfy all matching schemas")
    return 0


if __name__ == '__main__':
    sys.exit(main())
