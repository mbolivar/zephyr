#!/usr/bin/env python3
# Copyright (c) 2018,2020 Intel Corporation
# Copyright (c) 2022 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

"""
Compliance checks for Zephyr devicetree bindings.

This module contains the DevicetreeBindingsCheck class, which enforces rules
documented at https://docs.zephyrproject.org/latest/build/dts/bindings-upstream.html
"""

import argparse
import functools
import glob
import re
import sys
from pathlib import Path

import yaml

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader


ZEPHYR_BASE = Path(__file__).resolve().parents[2]

# Pre-compiled regex for description_style_check: matches an indented
# 'description: >' line (inside a properties block, not at top level).
_FOLDED_SCALAR_RE = re.compile(r'^\s+description\s*:\s*>\s*(?:#.*)?$')


@functools.lru_cache(maxsize=None)
def _restate_patterns(default_val):
    """
    Return compiled regex patterns that detect descriptions which only
    restate *default_val* without providing any real justification.

    Results are cached by default_val to avoid recompiling the same patterns
    when multiple properties across different bindings share the same default.
    """
    escaped = re.escape(str(default_val))
    return (
        re.compile(rf'^default(?:\s+value)?\s+is\s+{escaped}\.?$', re.IGNORECASE),
        re.compile(rf'^defaults?\s+to\s+{escaped}\.?$', re.IGNORECASE),
        re.compile(rf'^the\s+default(?:\s+value)?\s+is\s+{escaped}\.?$', re.IGNORECASE),
    )


class Binding:
    """
    Minimal wrapper around a devicetree binding YAML file.

    Exposes the attributes needed by the compliance checks:

    path       - absolute path to the binding YAML file (string)
    raw        - parsed YAML content (dict)
    compatible - the binding's compatible string, or None
    on_bus     - the bus the device appears on, or None
    prop2specs  - dict mapping property names to their spec dicts
    child_binding - a Binding for the child-binding section, or None
    """

    def __init__(self, path, raw=None):
        self.path = str(path)

        if raw is None:
            with open(path) as f:
                raw = yaml.load(f, Loader=SafeLoader)

        self.raw = raw or {}

        self.compatible = self.raw.get('compatible')
        self.on_bus = self.raw.get('on-bus')

        props = self.raw.get('properties') or {}
        self.prop2specs = {name: spec for name, spec in props.items()
                          if isinstance(spec, dict)}

        child_raw = self.raw.get('child-binding')
        self.child_binding = Binding(path, child_raw) if child_raw else None


def bindings_from_paths(paths):
    """
    Load Binding objects from a list of YAML file paths, ignoring errors.
    Only returns bindings that have a top-level 'compatible' key.
    """
    bindings = []
    for path in paths:
        try:
            b = Binding(path)
            if b.compatible is not None:
                bindings.append(b)
        except Exception as e:
            print(f"Warning: could not parse {path}: {e}", file=sys.stderr)
    return bindings


class DevicetreeBindingsCheck:
    """
    Checks for devicetree bindings.

    Enforces rules documented at:
    https://docs.zephyrproject.org/latest/build/dts/bindings-upstream.html
    """

    def __init__(self):
        self._failures = []

    def failure(self, text):
        """Record a compliance failure with the given message."""
        self._failures.append(text)
        print(f"FAIL: {text}", file=sys.stderr)

    def run(self):
        """
        Run all binding checks and return the number of failures found.
        """
        bindings = self._get_yaml_bindings()

        def check(binding, callback, children=True):
            if children:
                while binding is not None:
                    callback(binding)
                    binding = binding.child_binding
            else:
                callback(binding)

        for binding in bindings:
            check(binding, self.check_yaml_property_name)
            check(binding, self.required_false_check)
            check(binding, self.default_description_check)
            check(binding, self.description_style_check)
            check(binding, self.compatible_and_file_name_match_check, children=False)

        return len(self._failures)

    def _get_yaml_bindings(self):
        """
        Return a list of Binding objects for all '*.yaml' files under
        dts/bindings/ in the Zephyr tree.
        """
        pattern = str(ZEPHYR_BASE / 'dts' / 'bindings' / '**' / '*.yaml')
        paths = glob.glob(pattern, recursive=True)
        return bindings_from_paths(paths)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_yaml_property_name(self, binding):
        """
        Flag property names that contain underscores.

        Underscores should be replaced with hyphens in modern bindings unless
        the name comes from Linux or another authoritative upstream source.
        """
        for prop_name in binding.prop2specs:
            if '_' in prop_name:
                better_prop = prop_name.replace('_', '-')
                self.failure(
                    f"{binding.path}: property '{prop_name}' contains underscores.\n"
                    f"\tUse '{better_prop}' instead unless this property name is from Linux\n"
                    f"\tor another authoritative upstream source of bindings for "
                    f"compatible '{binding.compatible}'."
                )

    def required_false_check(self, binding):
        """
        Flag 'required: false' in property specs, which is redundant.
        """
        raw_props = binding.raw.get('properties') or {}
        for prop_name, raw_prop in raw_props.items():
            if not isinstance(raw_prop, dict):
                continue
            if raw_prop.get('required') is False:
                self.failure(
                    f'{binding.path}: property "{prop_name}": '
                    "'required: false' is redundant, please remove"
                )

    def default_description_check(self, binding):
        """
        Ensure every property that uses 'default:' has a 'description:' that
        explains *why* the default was chosen, not merely what it is.

        Rule documented at:
        https://docs.zephyrproject.org/latest/build/dts/bindings-upstream.html#rules-for-default-values
        """
        # These properties cannot have defaults in bindings per the docs.
        _SKIP_PROPS = {'status', '#address-cells', '#size-cells'}
        _DOC_URL = (
            'https://docs.zephyrproject.org/latest/build/dts/'
            'bindings-upstream.html#rules-for-default-values'
        )

        raw_props = binding.raw.get('properties') or {}
        for prop_name, raw_prop in raw_props.items():
            if prop_name in _SKIP_PROPS:
                continue
            if not isinstance(raw_prop, dict):
                continue
            if 'default' not in raw_prop:
                continue

            description = raw_prop.get('description', '')
            desc_text = str(description).strip() if description else ''

            if not desc_text:
                self.failure(
                    f"{binding.path}: property '{prop_name}' has a 'default' value "
                    f"but no 'description' explaining why the default was chosen.\n"
                    f"\tSee {_DOC_URL}"
                )
                continue

            # Conservative heuristic: flag descriptions that appear to do
            # nothing more than restate the default value.  Only single-clause
            # descriptions that match one of the patterns below are flagged.
            for pat in _restate_patterns(raw_prop['default']):
                if pat.match(desc_text):
                    self.failure(
                        f"{binding.path}: property '{prop_name}' description "
                        f"only restates the default value without explaining "
                        f"why it was chosen.\n"
                        f"\tSee {_DOC_URL}"
                    )
                    break

    def description_style_check(self, binding):
        """
        Flag property 'description:' entries that use YAML folded-scalar
        style ('description: >').

        Only two styles are acceptable for property descriptions:
          - Short inline:   description: my short string
          - Literal block:  description: |

        The folded-block style ('>') silently collapses line breaks and is
        therefore not allowed for multi-line descriptions.

        Rule documented at:
        https://docs.zephyrproject.org/latest/build/dts/bindings-upstream.html#descriptions
        """
        _DOC_URL = (
            'https://docs.zephyrproject.org/latest/build/dts/'
            'bindings-upstream.html#descriptions'
        )

        try:
            with open(binding.path) as f:
                for line_num, line in enumerate(f, start=1):
                    # Match "description: >" only when indented (i.e. inside a
                    # properties block).  Top-level binding descriptions are at
                    # column 0 and are not checked here.
                    if _FOLDED_SCALAR_RE.match(line):
                        self.failure(
                            f"{binding.path}:{line_num}: "
                            f"use 'description: |' for multi-line descriptions, "
                            f"not 'description: >'. "
                            f"See {_DOC_URL}"
                        )
        except OSError:
            pass

    def compatible_and_file_name_match_check(self, binding):
        """
        Ensure the binding file name matches its 'compatible' string.
        """
        if binding.compatible is None:
            return

        allowed = [f"{binding.compatible}.yaml"]
        if binding.on_bus is not None:
            allowed.append(f"{binding.compatible}-{binding.on_bus}.yaml")

        actual_filename = Path(binding.path).name

        if actual_filename not in allowed:
            if len(allowed) > 1:
                allowed_names = ", ".join(f"'{fn}'" for fn in allowed)
                self.failure(
                    f"{binding.path}: bad file name for compatible "
                    f"'{binding.compatible}'.\n"
                    f"\tThe allowed file names for this binding are: {allowed_names}"
                )
            else:
                self.failure(
                    f"{binding.path}: bad file name for compatible "
                    f"'{binding.compatible}'; "
                    f"this should be named '{allowed[0]}' instead"
                )


def main():
    parser = argparse.ArgumentParser(
        description="Check Zephyr devicetree bindings for compliance."
    )
    parser.parse_args()

    checker = DevicetreeBindingsCheck()
    failures = checker.run()

    if failures:
        print(f"\n{failures} compliance failure(s) found.", file=sys.stderr)
        sys.exit(1)
    else:
        print("All bindings passed compliance checks.")
        sys.exit(0)


if __name__ == '__main__':
    main()
