#!/usr/bin/env python3
# Copyright (c) 2026 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Convert classic Zephyr devicetree bindings to dt-schema documents.

This tool reads bindings written in Zephyr's classic bindings language
(with 'include:'s fully resolved, exactly as edtlib sees them) and
writes dt-schema documents plus, when needed, entries for the
'zephyr-extras.yaml' supplement file (specifier cell names and bus
typing, which dt-schema cannot express yet).

It is meant both for converting in-tree bindings and for out-of-tree
users migrating their own bindings: if your binding works with edtlib
today, this tool gives you a working dt-schema starting point. The
output is a *starting point* by design -- a human should review the
generated schema, tighten 'reg'/'interrupts' item counts, give child
node patterns real names, and add constraints the classic language
could not express.

Example:

    python3 scripts/dts/migrate_binding.py \
        --bindings-dir dts/bindings \
        --out-dir dts/schemas \
        --extras-out dts/schemas/zephyr-extras.yaml \
        dts/bindings/serial/ti,stellaris-uart.yaml
"""

import argparse
import io
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent / 'python-devicetree' / 'src'))

from devicetree.dtschema_bindings import _COMMON_PROP_SPECS
from devicetree.edtlib import Binding, HexInt, PropertySpec

META_SCHEMA = 'http://devicetree.org/meta-schemas/core.yaml#'
TYPES = '/schemas/types.yaml#/definitions'
DEFAULT_MAINTAINER = ('Zephyr Devicetree subsystem '
                      '<devel@lists.zephyrproject.org>')

# Properties never emitted into a schema: either the dt-schema
# meta-schema forbids documenting them (interrupt-parent), or
# Zephyr's variant conflicts with a dt-schema core rule and is
# handled by the standard-property table instead
# (memory-region-names depends on the singular 'memory-region'
# upstream; Zephyr pairs it with 'memory-regions').
SKIP_PROPS = {'interrupt-parent', 'memory-region-names'}

# The dt-schema meta-schema types properties with these unit suffixes
# implicitly (uint32 cells) and rejects an explicit type $ref.
UNIT_SUFFIX_RE = re.compile(
    r'-(bps|kBps|percent|bp|db|mhz|hz|sec|us|ns|ps|ms|mm|bits|ohms'
    r'|nanoamp|microamp(-hours)?|micro-ohms|microwatt-hours|microvolt'
    r'|(femto|pico)farads|(milli)?celsius|kelvin|k?pascal)$')

# Boolean flags the meta-schema requires to be declared as just 'true'.
CONTROLLER_FLAGS = {'interrupt-controller', 'gpio-controller'}

# Classic property type -> emitter. Each returns a dict (the property's
# JSON Schema) or None if the type cannot be expressed.
def _int_schema(spec: PropertySpec) -> dict:
    signed = any(
        isinstance(v, int) and v < 0
        for v in [spec.default, spec.const, spec.min, spec.max,
                  *(spec.enum or [])])
    return {'$ref': f'{TYPES}/{"int32" if signed else "uint32"}'}

# Properties whose classic 'array' values are structured as tuples and
# which the dt-schema core schemas already type as uint32-matrix.
# Reusing the established type avoids conflicting global property
# types (both translate back to classic 'array').
_MATRIX_PROPS = {'gpios', 'gpio-reserved-ranges'}


def _array_schema(spec: PropertySpec) -> dict:
    if spec.name in _MATRIX_PROPS:
        return {'$ref': f'{TYPES}/uint32-matrix'}
    return {'$ref': f'{TYPES}/uint32-array'}


_TYPE2SCHEMA = {
    'boolean': lambda spec: {'type': 'boolean'},
    'int': _int_schema,
    'array': _array_schema,
    'uint8-array': lambda spec: {'$ref': f'{TYPES}/uint8-array'},
    'string': lambda spec: {'$ref': f'{TYPES}/string'},
    'string-array': lambda spec: {'$ref': f'{TYPES}/string-array'},
    'phandle': lambda spec: {'$ref': f'{TYPES}/phandle'},
    'phandles': lambda spec: {'$ref': f'{TYPES}/phandle-array',
                              'items': {'maxItems': 1}},
    'phandle-array': lambda spec: {'$ref': f'{TYPES}/phandle-array'},
    'path': None,
    'compound': None,
}


def prop_to_schema(spec: PropertySpec) -> tuple[str, object]:
    """
    Returns (comment, subschema) for one classic property spec, where
    subschema is the dt-schema representation (possibly True for
    "allowed, unconstrained") and comment flags anything lossy.
    """
    emit = _TYPE2SCHEMA.get(spec.type)
    if emit is None:
        return (f"classic type '{spec.type}' has no dt-schema equivalent; "
                "constraint not expressed", True)

    if spec.name in CONTROLLER_FLAGS:
        return ('', True)

    sub = emit(spec)
    comment = ''

    if UNIT_SUFFIX_RE.search(spec.name):
        if spec.type in ('int', 'array'):
            # Unit-suffix properties are implicitly typed by the
            # dt-schema meta-schema, which rejects an explicit $ref.
            sub.pop('$ref', None)
        else:
            comment = (f"'{spec.name}' has classic type '{spec.type}', but "
                       "its name matches a dt-schema unit suffix implying "
                       "uint32 cells; schema will not validate against the "
                       "meta-schema (consider an upstream meta-schema "
                       "exception or renaming the property)")
    if spec.description:
        sub['description'] = spec.description.strip()
    if spec.const is not None:
        sub.pop('$ref', None)
        sub['const'] = spec.const
    if spec.enum is not None:
        sub['enum'] = spec.enum
    if spec.default is not None:
        sub['default'] = spec.default
    if spec.min is not None:
        sub['minimum'] = spec.min
    if spec.max is not None:
        sub['maximum'] = spec.max
    if spec.deprecated:
        sub['deprecated'] = True
    return (comment, sub)


def _is_common(name: str, spec: PropertySpec) -> bool:
    # True if the property is one of the standard ones every
    # translated binding gets automatically AND this binding does not
    # constrain it beyond the standard spec.
    common = _COMMON_PROP_SPECS.get(name)
    if common is None:
        return False
    return (
        not spec.required
        and spec.const is None
        and spec.default == common.get('default')
        and (spec.enum is None or spec.enum == common.get('enum'))
    )


def binding_to_schema(binding: Binding, category: str, namespace: str,
                      maintainer: str,
                      stem: str | None = None) -> tuple[dict, dict, list[str]]:
    """
    Converts a classic Binding to (schema_doc, extras_fragment, notes).

    'stem' is the document's filename stem; it defaults to the bare
    compatible but is '<compatible>-<bus>' for one binding of a
    multi-bus compatible, so the documents get distinct $id values.
    """
    notes = []
    compat = binding.compatible
    stem = stem or compat
    schema: dict = {
        '$id': (f'http://devicetree.org/schemas/{namespace}/{category}/'
                f'{stem}.yaml#'),
        '$schema': META_SCHEMA,
        'title': (binding.title
                  or (binding.description or compat).strip().split('\n')[0]),
        'maintainers': [maintainer],
    }
    if binding.description and binding.description.strip().count('\n'):
        schema['description'] = binding.description.strip()

    props: dict = {'compatible': {'const': compat}}
    required = ['compatible']

    for name, spec in sorted(binding.prop2specs.items()):
        if name == 'compatible' or name in SKIP_PROPS:
            continue
        if _is_common(name, spec):
            continue
        if name in _COMMON_PROP_SPECS:
            # Standard property that this binding constrains further:
            # express only the extra constraints; the type and meaning
            # are already established (by the dt-schema core schemas
            # at validation time, and by the standard property specs
            # at edtlib translation time).
            common = _COMMON_PROP_SPECS[name]
            sub: object = {}
            if spec.const is not None:
                sub['const'] = spec.const
            if spec.enum is not None and spec.enum != common.get('enum'):
                sub['enum'] = spec.enum
            if (spec.default is not None
                    and spec.default != common.get('default')):
                sub['default'] = spec.default
            sub = sub or True
        else:
            comment, sub = prop_to_schema(spec)
            if comment:
                notes.append(f'{compat}: {name}: {comment}')
            if spec.type == 'boolean' and re.search(r'-gpios?$', name):
                notes.append(
                    f"{compat}: {name}: boolean, but '-gpios' names imply "
                    "GPIO specifiers in dt-schema; schema will not validate "
                    "against the meta-schema (consider renaming)")
        props[name] = sub
        if spec.required and name not in required:
            required.append(name)

    if '#gpio-cells' in props and 'gpio-controller' not in props:
        # GPIO nexus nodes: the meta-schema only allows '#gpio-cells'
        # together with 'gpio-controller'. The cell count still comes
        # from the devicetree and the cell names from
        # zephyr-extras.yaml, so nothing is lost by not documenting it.
        del props['#gpio-cells']
        required = [r for r in required if r != '#gpio-cells']
        notes.append(f"{compat}: '#gpio-cells' dropped (GPIO nexus node; "
                     "meta-schema requires gpio-controller alongside it)")

    schema['properties'] = props

    if binding.child_binding is not None:
        child_schema, child_extras, child_notes = binding_to_schema(
            binding.child_binding, category, namespace, maintainer)
        for k in ('$id', '$schema', 'title', 'description', 'maintainers'):
            child_schema.pop(k, None)
        # Deliberately no 'type: object' here. The catch-all '^.*$' pattern
        # below also matches the parent's scalar properties (compatible,
        # reg, status, ...); 'type: object' would make dt-schema value
        # validation reject every one of them. Child *node* validation
        # still applies, because the child's 'properties' are checked on
        # any object-valued match (an actual child node).
        child_props = child_schema.get('properties', {})
        child_props.pop('compatible', None)
        child_req = [r for r in child_schema.get('required', [])
                     if r != 'compatible']
        if child_req:
            child_schema['required'] = child_req
        else:
            child_schema.pop('required', None)
        child_schema.pop('additionalProperties', None)
        child_desc = (binding.child_binding.description or '').strip()
        if child_desc:
            child_schema['description'] = child_desc
        # A human should replace this catch-all with the project's
        # actual child node naming convention.
        schema['patternProperties'] = {'^.*$': child_schema}
        notes.append(f"{compat}: child-binding converted with catch-all "
                     "node name pattern '^.*$'; please tighten it")
        notes.extend(child_notes)

    schema['required'] = required
    schema['additionalProperties'] = True

    extras: dict = {}
    for space, cells in binding.specifier2cells.items():
        extras[f'{space}-cells'] = list(cells)
    if binding.bus is not None:
        extras['bus'] = binding.bus
    if binding.on_bus is not None:
        extras['on-bus'] = binding.on_bus

    return schema, extras, notes


#
# YAML output tuned for human review: block-literal descriptions,
# stable key order, no aliases.
#

class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _str_representer(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data,
                                       style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


def _hexint_representer(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:int', hex(data))


_Dumper.add_representer(str, _str_representer)
_Dumper.add_representer(HexInt, _hexint_representer)


def dump_schema(schema: dict, src_relpath: str) -> str:
    out = io.StringIO()
    out.write('# SPDX-License-Identifier: Apache-2.0\n')
    out.write(f'# Converted from {src_relpath} by migrate_binding.py.\n')
    out.write('%YAML 1.2\n---\n')
    out.write(yaml.dump(schema, Dumper=_Dumper, sort_keys=False,
                        default_flow_style=False, width=80,
                        allow_unicode=True))
    return out.getvalue()


def _binding_fname2path(bindings_dirs: list[str]) -> dict[str, str]:
    fname2path = {}
    for bindings_dir in bindings_dirs:
        for root, _, fnames in os.walk(bindings_dir):
            for fname in fnames:
                if fname.endswith(('.yaml', '.yml')):
                    fname2path[fname] = os.path.join(root, fname)
    return fname2path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bindings-dir', action='append', default=[],
                        help='''directory with classic bindings, for resolving
                        include:s (may be given multiple times); defaults to
                        zephyr's dts/bindings''')
    parser.add_argument('--out-dir', required=True,
                        help='directory to write dt-schema documents to')
    parser.add_argument('--extras-out',
                        help='''zephyr-extras.yaml file to create or update
                        with specifier cell names and bus information''')
    parser.add_argument('--namespace', default='zephyr',
                        help='''schema namespace; schemas get
                        $id: http://devicetree.org/schemas/<namespace>/... and
                        are written to <out-dir>/<namespace>/<category>/
                        (default: zephyr)''')
    parser.add_argument('--maintainer', default=DEFAULT_MAINTAINER,
                        help="value for the schemas' maintainers: list")
    parser.add_argument('bindings', nargs='+',
                        help='classic binding files to convert')
    args = parser.parse_args()

    bindings_dirs = args.bindings_dir or [
        str(Path(__file__).parents[2] / 'dts' / 'bindings')]
    fname2path = _binding_fname2path(bindings_dirs)

    extras_compat: dict = {}
    extras_spaces: dict = {}
    if args.extras_out and os.path.exists(args.extras_out):
        with open(args.extras_out, encoding='utf-8') as f:
            existing = yaml.safe_load(f) or {}
        extras_compat = existing.get('compatibles') or {}
        extras_spaces = existing.get('specifier-spaces') or {}
    all_notes = []

    # First pass: load every binding and group by compatible so the
    # multi-bus pattern (several bindings, same compatible, different
    # on-bus -- e.g. st,lsm6dsv16x-{i2c,spi,i3c}.yaml) can be detected
    # before conversion, because it decides each document's stem and $id.
    records = []
    by_compat: dict[str, list] = {}
    for binding_file in args.bindings:
        binding = Binding(os.path.abspath(binding_file), fname2path,
                          require_compatible=True)
        rec = {
            'src': binding_file,
            'binding': binding,
            'compat': binding.compatible,
            'on_bus': binding.on_bus,
            'category': Path(binding_file).parent.name,
        }
        records.append(rec)
        by_compat.setdefault(binding.compatible, []).append(rec)

    # Assign each record a document stem: the bare compatible normally,
    # but '<compatible>-<on-bus>' when several bindings share a
    # compatible, mirroring Zephyr's '<compatible>-<bus>.yaml' naming.
    for compat, recs in by_compat.items():
        multi = len(recs) > 1
        for rec in recs:
            if not multi:
                rec['stem'] = compat
                continue
            if rec['on_bus'] is None:
                sys.exit(
                    f"error: {len(recs)} bindings share compatible "
                    f"'{compat}' but {rec['src']} has no 'on-bus:', so "
                    "their dt-schema documents cannot be told apart. Give "
                    "each an 'on-bus:' (the multi-bus pattern) or convert "
                    "them one at a time.")
            rec['stem'] = f'{compat}-{rec["on_bus"]}'

    # Second pass: convert each binding now that its stem is known.
    for rec in records:
        schema, extras, notes = binding_to_schema(
            rec['binding'], rec['category'], args.namespace,
            args.maintainer, stem=rec['stem'])
        rec['schema'] = schema
        rec['extras'] = extras
        all_notes.extend(notes)

    # Detect output collisions instead of silently overwriting, which is
    # the failure mode that silently dropped multi-bus conversions before.
    seen_out: dict[Path, str] = {}
    for rec in records:
        out_dir = Path(args.out_dir) / args.namespace / rec['category']
        out_path = out_dir / f'{rec["stem"]}.yaml'
        if out_path in seen_out:
            sys.exit(
                f"error: {rec['src']} and {seen_out[out_path]} both convert "
                f"to {out_path}; refusing to overwrite. (Two bindings with "
                "the same compatible need distinct 'on-bus:' values.)")
        seen_out[out_path] = rec['src']
        rec['out_dir'] = out_dir
        rec['out_path'] = out_path

    for rec in records:
        rec['out_dir'].mkdir(parents=True, exist_ok=True)
        src_rel = os.path.relpath(rec['src'])
        rec['out_path'].write_text(dump_schema(rec['schema'], src_rel),
                                   encoding='utf-8')
        print(f'wrote {rec["out_path"]}')

        if rec['extras']:
            key = rec['stem']
            if key in extras_compat and extras_compat[key] != rec['extras']:
                sys.exit(
                    f"error: conflicting zephyr-extras.yaml entries for "
                    f"'{key}' (from {rec['src']}); refusing to overwrite")
            extras_compat[key] = rec['extras']

    if args.extras_out and (extras_compat or extras_spaces):
        doc = {}
        if extras_spaces:
            doc['specifier-spaces'] = extras_spaces
        doc['compatibles'] = dict(sorted(extras_compat.items()))
        with open(args.extras_out, 'w', encoding='utf-8') as f:
            f.write('# SPDX-License-Identifier: Apache-2.0\n')
            f.write('# Zephyr supplement to dt-schema documents: specifier\n'
                    '# cell names and bus typing, which dt-schema cannot\n'
                    '# express (yet). See dtschema_bindings.py.\n')
            yaml.dump(doc, f, Dumper=_Dumper, sort_keys=False,
                      default_flow_style=False)
        print(f'updated {args.extras_out}')

    if all_notes:
        print('\nManual review needed:', file=sys.stderr)
        for note in all_notes:
            print(f'  - {note}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
