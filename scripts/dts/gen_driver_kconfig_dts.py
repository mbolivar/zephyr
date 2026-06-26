#!/usr/bin/env python3
#
# Copyright (c) 2022 Kumar Gala <galak@kernel.org>
# Copyright (c) 2025 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: Apache-2.0

import argparse
import os

import yaml

try:
    # Use the C LibYAML parser if available, rather than the Python parser.
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader  # type: ignore


HEADER = """\
# Generated devicetree Kconfig
#
# SPDX-License-Identifier: Apache-2.0"""


KCONFIG_TEMPLATE = """
DT_COMPAT_{COMPAT} := {compat}

config DT_HAS_{COMPAT}_ENABLED
\tdef_bool $(dt_compat_enabled,$(DT_COMPAT_{COMPAT}))"""


# Character translation table used to derive Kconfig symbol names
TO_UNDERSCORES = str.maketrans("-,.@/+", "______")


def binding_paths(bindings_dirs):
    # Yields paths to all bindings (.yaml files) in 'bindings_dirs'

    for bindings_dir in bindings_dirs:
        for root, _, filenames in os.walk(bindings_dir):
            for filename in filenames:
                if filename.endswith((".yaml", ".yml")):
                    yield os.path.join(root, filename)


def dtschema_compatibles(schema_path):
    # Yields the compatible strings documented by the dt-schema file
    # at 'schema_path'. These appear as 'const:' or 'enum:' constraints
    # on the 'compatible' property, possibly nested inside 'items:' or
    # 'oneOf:'/'anyOf:' lists.

    with open(schema_path, encoding="utf-8") as f:
        try:
            schema = yaml.load(f, Loader=SafeLoader)
        except yaml.YAMLError as e:
            print(
                f"WARNING: '{schema_path}' appears in dt-schema "
                f"directories but isn't valid YAML: {e}"
            )
            return

    if not isinstance(schema, dict) or '$schema' not in schema:
        # Not a dt-schema document (e.g. zephyr-extras.yaml).
        return

    def walk(sub):
        if not isinstance(sub, dict):
            return
        if isinstance(sub.get('const'), str):
            yield sub['const']
        for value in sub.get('enum') or []:
            if isinstance(value, str):
                yield value
        for key in ('items', 'oneOf', 'anyOf', 'allOf'):
            inner = sub.get(key)
            if isinstance(inner, dict):
                yield from walk(inner)
            elif isinstance(inner, list):
                for item in inner:
                    yield from walk(item)

    properties = schema.get('properties')
    if isinstance(properties, dict):
        yield from walk(properties.get('compatible'))


def parse_args():
    # Returns parsed command-line arguments

    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--kconfig-out", required=True, help="path to write the Kconfig file")
    parser.add_argument(
        "--bindings-dirs",
        nargs='+',
        default=[],
        help="directory with bindings in YAML format, we allow multiple",
    )
    parser.add_argument(
        "--dtschema-dirs",
        nargs='+',
        default=[],
        help="directory with dt-schema based bindings, we allow multiple",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    compats = set()

    for binding_path in binding_paths(args.bindings_dirs):
        with open(binding_path, encoding="utf-8") as f:
            try:
                # Parsed PyYAML representation graph. For our purpose,
                # we don't need the whole file converted into a dict.
                root = yaml.compose(f, Loader=SafeLoader)
            except yaml.YAMLError as e:
                print(
                    f"WARNING: '{binding_path}' appears in binding "
                    f"directories but isn't valid YAML: {e}"
                )
                continue

        if not isinstance(root, yaml.MappingNode):
            continue
        for key, node in root.value:
            if key.value == "compatible" and isinstance(node, yaml.ScalarNode):
                compats.add(node.value)
                break

    for schema_path in binding_paths(args.dtschema_dirs):
        compats.update(dtschema_compatibles(schema_path))

    with open(args.kconfig_out, "w", encoding="utf-8") as kconfig_file:
        print(HEADER, file=kconfig_file)

        for c in sorted(compats):
            out = KCONFIG_TEMPLATE.format(compat=c, COMPAT=c.upper().translate(TO_UNDERSCORES))
            print(out, file=kconfig_file)


if __name__ == "__main__":
    main()
