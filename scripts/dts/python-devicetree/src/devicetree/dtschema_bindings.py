# Copyright (c) 2026 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: Apache-2.0

"""
Support for dt-schema based devicetree bindings.

This module lets edtlib consume bindings written in the dt-schema
language (https://github.com/devicetree-org/dt-schema) -- the
JSON-Schema-based bindings format used by the Linux kernel and
maintained by the devicetree.org community -- as a first-class
alternative to Zephyr's classic bindings language.

Approach
========

Rather than teaching all of edtlib about JSON Schema, each dt-schema
document that matches a compatible string in the devicetree is
*translated* into the same in-memory representation that the classic
bindings language parses into ("raw" binding dicts), and then wrapped
in an ordinary edtlib.Binding object. Everything downstream of binding
loading -- property value conversion, specifier-space handling,
gen_defines.py, the pickled EDT, twister, ... -- works unchanged and
cannot tell which language a binding was written in.

The translation is intentionally a small, pure, table-driven mapping
so that it is easy to review and to test. The same mapping, applied in
reverse, is what scripts/dts/migrate_binding.py uses to convert classic
bindings (including out-of-tree ones) to dt-schema documents.

What dt-schema cannot express (yet)
===================================

Two pieces of information that Zephyr's C devicetree API requires have
no machine-readable home in dt-schema today:

1. *Names* for specifier cells. dt-schema knows that '#gpio-cells' is 2,
   but the meaning of each cell ("pin", "flags") exists only in prose.
   Zephyr's DT_GPIO_PIN_BY_IDX() etc. need those names.

2. Bus typing. The classic 'bus:' / 'on-bus:' keys drive binding
   selection for devices on buses and the DT_ON_BUS() API.

Until those can be expressed upstream (which this RFC proposes to
pursue), each dt-schema bindings directory may carry a single
supplemental file, ``zephyr-extras.yaml``::

    specifier-spaces:
      # Default cell names, keyed by specifier space. Used for any
      # controller binding with a '#<space>-cells' property that has
      # no per-compatible override below.
      gpio:
        cells: [pin, flags]

    compatibles:
      arm,v7m-nvic:
        interrupt-cells: [irq, priority]
      vnd,uart-device:
        on-bus: uart

The supplement deliberately contains *only* what dt-schema cannot
express: everything else (property names, types, requiredness,
defaults, enums, child nodes) lives in the dt-schema documents.
"""

import logging
import os
import re
import urllib.parse
from typing import Any, NoReturn

import yaml

try:
    from yaml import CSafeLoader as _YamlLoader
except ImportError:
    from yaml import SafeLoader as _YamlLoader  # type: ignore

from devicetree.edtlib import Binding, EDTError

_LOG = logging.getLogger(__name__)

#: Name of the per-directory Zephyr supplement file.
ZEPHYR_EXTRAS_NAME = 'zephyr-extras.yaml'

#: Name of a subdirectory (anywhere under a schemas directory) holding
#: vendored upstream schemas. These are loaded so they can be the
#: target of a ``$ref`` from a Zephyr schema, but are not matched as
#: bindings directly -- this is how a Zephyr schema adopts a schema for
#: a compatible the upstream devicetree project owns. See the
#: documentation on adopting upstream schemas.
REFERENCE_SUBDIR = 'upstream'

# Map from dt-schema scalar/array type names (as used in
# /schemas/types.yaml and produced by dtschema's type extraction) to
# classic binding property types.
_DTSCHEMA2CLASSIC_TYPE = {
    'flag': 'boolean',
    'int8': 'int',
    'int16': 'int',
    'int32': 'int',
    # 64-bit scalars occupy two devicetree cells; the classic 'int' type
    # is a single cell, so they map to 'array' (the same choice a classic
    # binding makes, which has no 64-bit scalar type either).
    'int64': 'array',
    'uint8': 'int',
    'uint16': 'int',
    'uint32': 'int',
    'uint64': 'array',
    'cell': 'int',
    'int8-array': 'array',
    'int16-array': 'array',
    'int32-array': 'array',
    'int64-array': 'array',
    'uint8-array': 'uint8-array',
    'uint16-array': 'array',
    'uint32-array': 'array',
    'uint64-array': 'array',
    'int8-matrix': 'array',
    'int16-matrix': 'array',
    'int32-matrix': 'array',
    'int64-matrix': 'array',
    'uint8-matrix': 'array',
    'uint16-matrix': 'array',
    'uint32-matrix': 'array',
    'uint64-matrix': 'array',
    'address': 'array',
    'string': 'string',
    'string-array': 'string-array',
    'phandle': 'phandle',
    'phandle-array': 'phandle-array',
}

# Matches the type name in a /schemas/types.yaml $ref, e.g.
# "http://devicetree.org/schemas/types.yaml#/definitions/phandle-array".
_TYPES_YAML_REF = re.compile(
    r'types\.yaml#/definitions/'
    r'(?P<type>[a-z0-9-]+)$')

# Properties with standard unit suffixes are implicitly typed by
# dt-schema (see dtschema/fixups.py) as single-cell arrays/matrices to
# match their dtb encoding; their *logical* type, which is what the
# classic model and the C macros care about, is a scalar.
_UNIT_SUFFIX_RE = re.compile(
    r'-(bps|kBps|percent|bp|db|mhz|hz|sec|us|ns|ps|ms|mm|bits|ohms'
    r'|nanoamp|microamp(-hours)?|micro-ohms|micro(watt|volt)(-hours)?'
    r'|milliwatt|(femto|pico)farads|(milli)?celsius|kelvin|k?pascal)$')

# Properties that the classic language models in every binding (via
# base.yaml and friends) and that dt-schema models in its core schemas
# (dtschema/schemas/dt-core.yaml, pinctrl/pinctrl-consumer.yaml, ...)
# or that edtlib handles independently of bindings. These specs are
# merged into every translated binding, mirroring how virtually every
# classic binding includes base.yaml. A schema's own 'properties' and
# 'required' entries take precedence.
#
# This is a Python data table, not a use of the classic bindings
# language: builds with --no-classic-bindings never read a classic YAML
# file.
_COMMON_PROP_SPECS: dict[str, dict[str, Any]] = {
    'status': {
        'type': 'string',
        'enum': ['okay', 'disabled', 'reserved', 'fail', 'fail-sss'],
        'description': 'operational status of the hardware',
    },
    'compatible': {
        'type': 'string-array',
        'required': True,
        'description': 'compatible strings, most- to least-specific',
    },
    'reg': {
        'type': 'array',
        'description': 'register space',
    },
    'reg-names': {
        'type': 'string-array',
        'description': 'names of the register spaces in reg',
    },
    'ranges': {
        'type': 'compound',
        'description': 'child address space mapping',
    },
    'interrupts': {
        'type': 'array',
        'description': 'interrupt specifiers',
    },
    'interrupts-extended': {
        'type': 'compound',
        'description': 'extended interrupt specifiers',
    },
    'interrupt-names': {
        'type': 'string-array',
        'description': 'names of the interrupts',
    },
    # 'interrupt-parent' is deliberately absent: edtlib resolves it
    # internally (and allows it undeclared); a phandle property spec
    # for it creates dependency cycles on bus nodes whose
    # 'interrupt-parent' points at a child interrupt controller.
    'label': {
        'type': 'string',
        'description': 'human-readable device description (deprecated)',
    },
    'clocks': {
        'type': 'phandle-array',
        'description': 'clock providers',
    },
    'clock-names': {
        'type': 'string-array',
        'description': 'names of the clocks',
    },
    'clock-frequency': {
        'type': 'int',
        'description': 'clock frequency in Hz',
    },
    # Standard clock-consumer properties. dt-schema's fixups inject
    # these into any schema that declares 'clocks', so they are modeled
    # here too (a node that does not use them generates no macros for
    # them, exactly as in the classic language).
    'assigned-clocks': {
        'type': 'phandle-array',
        'description': 'clocks to be reparented/reconfigured',
    },
    'assigned-clock-parents': {
        'type': 'phandle-array',
        'description': 'parent clocks for assigned-clocks',
    },
    'assigned-clock-rates': {
        'type': 'array',
        'description': 'rates (Hz) for assigned-clocks',
    },
    'assigned-clock-rates-u64': {
        'type': 'array',
        'description': '64-bit rates (Hz) for assigned-clocks',
    },
    'assigned-clock-sscs': {
        'type': 'array',
        'description': 'spread-spectrum settings for assigned-clocks',
    },
    '#address-cells': {
        'type': 'int',
        'description': 'number of address cells in reg properties of children',
    },
    '#size-cells': {
        'type': 'int',
        'description': 'number of size cells in reg properties of children',
    },
    'dmas': {
        'type': 'phandle-array',
        'description': 'DMA channel specifiers',
    },
    'dma-names': {
        'type': 'string-array',
        'description': 'names of the DMA channels',
    },
    'io-channels': {
        'type': 'phandle-array',
        'description': 'IO channel specifiers',
    },
    'io-channel-names': {
        'type': 'string-array',
        'description': 'names of the IO channels',
    },
    'mboxes': {
        'type': 'phandle-array',
        'specifier-space': 'mbox',
        'description': 'mailbox / IPM channel specifiers',
    },
    'mbox-names': {
        'type': 'string-array',
        'description': 'names of the mbox specifiers',
    },
    'power-domains': {
        'type': 'phandle-array',
        'description': 'power domain specifiers',
    },
    'power-domain-names': {
        'type': 'string-array',
        'description': 'names of the power domain specifiers',
    },
    '#power-domain-cells': {
        'type': 'int',
        'description': 'number of cells in power-domains specifiers',
    },
    'dma-coherent': {
        'type': 'boolean',
        'description': 'device is capable of coherent DMA operations',
    },
    'hwlocks': {
        'type': 'phandle-array',
        'specifier-space': 'hwlock',
        'description': 'HW spinlock specifiers',
    },
    'hwlock-names': {
        'type': 'string-array',
        'description': 'names of the hwlock specifiers',
    },
    # Zephyr's memory-regions/-names pair predates (and conflicts
    # with) the dt-schema core 'memory-region' singular property; see
    # the migration documentation. Modeled here so nodes using them
    # keep their macros without schemas having to declare them.
    'memory-regions': {
        'type': 'phandle-array',
        'description': 'memory region phandles',
    },
    'memory-region-names': {
        'type': 'string-array',
        'description': 'names of the memory regions',
    },
    'wakeup-source': {
        'type': 'boolean',
        'description': 'device can wake the system up',
    },
    'zephyr,deferred-init': {
        'type': 'boolean',
        'description': 'do not initialize the device automatically on boot',
    },
    'zephyr,pm-device-runtime-auto': {
        'type': 'boolean',
        'description': 'enable runtime power management after init',
    },
    'zephyr,disabling-power-states': {
        'type': 'phandles',
        'description': 'power states that disable this device',
    },
    'pinctrl-names': {
        'type': 'string-array',
        'description': 'names of the pin configuration states',
    },
    # The classic language types pinctrl-N as 'phandles' (each phandle
    # is a pin configuration group node); Zephyr's pinctrl API depends
    # on that, so the translation does too even though dt-schema's
    # pinctrl-consumer.yaml calls it phandle-array.
    **{
        f'pinctrl-{i}': {
            'type': 'phandles',
            'description': f'pin configuration state {i}',
        }
        for i in range(5)
    },
}

# Properties that appear in (fixed-up) dt-schema documents but must
# not be translated to classic property specs: either dtschema fixups
# inject them, or they are JSON-Schema/devicetree machinery with no
# classic equivalent, or edtlib computes them without binding help.
_SKIP_PROPS = {
    '$nodename',
    'phandle',
    'device_type',
    'secure-status',
    'dma-ranges',
    'dma-noncoherent',
    # 'compatible' constraints (const/enum) express how the schema
    # *matches*, not a value constraint edtlib should re-check against
    # the node's full compatible list; the standard spec is used.
    'compatible',
}

_SKIP_PROP_PREFIXES = ('bootph-',)

# Nexus-node map properties: edtlib parses '<space>-map' internally
# and the classic language types it 'compound' (declared, but no
# Property object or macros); the mask/pass-thru siblings are plain
# cell arrays. The translation mirrors that regardless of how a
# schema constrains their values.
_COMPOUND_PROP_SUFFIXES = ('-map',)
_ARRAY_PROP_SUFFIXES = ('-map-mask', '-map-pass-thru')

# pinctrl-<n> state properties (any index), typed 'phandles' in Zephyr.
_PINCTRL_RE = re.compile(r'pinctrl-\d+')


def _err(msg) -> NoReturn:
    raise EDTError(msg)


def _load_yaml(path: str) -> Any:
    with open(path, encoding='utf-8') as f:
        return yaml.load(f, Loader=_YamlLoader)


def _schema_stem(schema: dict) -> str:
    # The document's filename without extension, used as the supplement
    # lookup key. Falls back to the last path component of its $id.
    filename = schema.get('$filename')
    if filename:
        return os.path.splitext(os.path.basename(filename))[0]
    base = (schema.get('$id') or '').split('#')[0].rstrip('/').split('/')[-1]
    return base[:-5] if base.endswith('.yaml') else base


def _schema_compatibles(schema: dict):
    # Yields the compatible strings a schema document declares, from
    # const/enum constraints on its 'compatible' property (possibly
    # nested in items/oneOf/anyOf/allOf), mirroring how
    # gen_driver_kconfig_dts.py derives DT_HAS_* symbols.
    props = schema.get('properties')
    if not isinstance(props, dict):
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

    yield from walk(props.get('compatible'))


class DtSchemaBindings:
    """
    Loads dt-schema documents (plus the dt-schema core schemas) from a
    list of directories and translates the ones matching a set of
    devicetree compatibles into edtlib.Binding objects.
    """

    def __init__(self, schema_dirs: list[str]):
        try:
            import dtschema
        except ImportError as e:
            _err(f"dt-schema bindings directories were given "
                 f"({schema_dirs}), but the 'dtschema' Python package is "
                 f"not installed: {e}")

        self._schema_dirs = [os.path.abspath(d) for d in schema_dirs]
        self._validator = dtschema.DTValidator(self._schema_dirs)
        self._extras = self._load_extras()
        self._compat2schemas = self._build_compat2schemas()

    def _load_extras(self) -> dict:
        # Loads and merges the zephyr-extras.yaml supplements from all
        # schema directories.
        merged: dict[str, dict] = {'specifier-spaces': {}, 'compatibles': {}}
        for schema_dir in self._schema_dirs:
            path = os.path.join(schema_dir, ZEPHYR_EXTRAS_NAME)
            if not os.path.isfile(path):
                continue
            extras = _load_yaml(path)
            if not isinstance(extras, dict):
                _err(f"malformed {path}: expected a YAML mapping")
            for key in ('specifier-spaces', 'compatibles'):
                section = extras.get(key) or {}
                for name, val in section.items():
                    if name in merged[key]:
                        _err(f"{path}: '{name}' in '{key}:' is already "
                             "defined by another zephyr-extras.yaml file")
                    merged[key][name] = val
        return merged

    def bindings_for(self, dt_compats: set[str]) -> list[Binding]:
        """
        Returns a list of Binding objects: one per (compatible,
        dt-schema document) pair where the compatible is in 'dt_compats'
        and the document matches it.

        A single compatible can map to more than one document -- the
        multi-bus pattern, where e.g. ``st,lsm6dsv16x`` has separate
        ``st,lsm6dsv16x-i2c.yaml``/``-spi.yaml``/``-i3c.yaml`` documents
        differing in bus typing and bus-specific properties. Each
        becomes its own Binding; edtlib registers them under distinct
        ``(compatible, on-bus)`` keys and selects per node by bus, and
        errors out if two documents collide on the same key.
        """
        ret = []
        for compat in sorted(dt_compats):
            for schema_id, schema in self._compat2schemas.get(compat, []):
                raw = self._translate(compat, schema)
                path = schema.get('$filename') or schema_id
                ret.append(Binding(path, {}, raw=raw))
        return ret

    def _build_compat2schemas(self) -> dict[str, list[tuple[str, dict]]]:
        # Maps each compatible to the list of (schema_id, schema) for the
        # Zephyr binding documents that declare it. Unlike dtschema's
        # compat_map (one schema per compatible), this preserves all
        # matching documents so the multi-bus pattern works. Only
        # documents under the configured schema dirs are considered (not
        # dtschema's bundled core schemas), and documents in a
        # REFERENCE_SUBDIR are skipped (they exist only as $ref targets).
        compat2schemas: dict[str, list[tuple[str, dict]]] = {}
        for schema_id, schema in self._validator.schemas.items():
            if not isinstance(schema, dict):
                continue
            if not self._is_binding_schema(schema.get('$filename')):
                continue
            for compat in _schema_compatibles(schema):
                compat2schemas.setdefault(compat, []).append(
                    (schema_id, schema))
        for entries in compat2schemas.values():
            entries.sort(key=lambda e: e[0])
        return compat2schemas

    def _is_binding_schema(self, filename: str | None) -> bool:
        # True if 'filename' is a Zephyr binding document: under one of
        # the configured schema dirs and not in a REFERENCE_SUBDIR.
        if not filename:
            return False
        abspath = os.path.abspath(filename)
        for schema_dir in self._schema_dirs:
            if abspath == schema_dir or abspath.startswith(schema_dir + os.sep):
                rel = os.path.relpath(abspath, schema_dir)
                return REFERENCE_SUBDIR not in rel.split(os.sep)
        return False

    #
    # Translation: dt-schema document -> classic raw binding dict
    #

    def _translate(self, compat: str, schema: dict) -> dict:
        merged = self._merge_refs(schema, _RefTrail(schema.get('$id')))

        raw: dict[str, Any] = {
            'compatible': compat,
            'description': (schema.get('title')
                            or schema.get('description')
                            or f'dt-schema binding {schema.get("$id")}'),
        }

        raw['properties'] = self._translate_props(merged, schema)

        child = self._translate_children(merged)
        if child is not None:
            raw['child-binding'] = child

        self._apply_extras(compat, schema, raw)
        return raw

    def _merge_refs(self, schema: dict, trail: '_RefTrail') -> dict:
        # Resolves 'allOf: [{$ref: ...}]' and top-level '$ref' includes
        # of other *loaded* schemas, merging their 'properties',
        # 'patternProperties' and 'required' so the translation sees
        # the same property set that JSON Schema validation would
        # evaluate. Schema-local '$defs'/'definitions' refs are not
        # binding includes and are skipped; so are refs to schemas
        # that are not loaded (e.g. dt-core.yaml machinery handled by
        # _COMMON_PROP_SPECS).
        merged = {
            'properties': {},
            'patternProperties': {},
            'required': [],
        }

        def merge_from(sub: dict) -> None:
            for ref in self._ref_targets(sub):
                inner = self._merge_refs(ref, trail.push(ref.get('$id')))
                _merge_translated(merged, inner)
            _merge_translated(merged, sub)

        merge_from(schema)

        # dtschema's fixup_interrupts() rewrites "required: [interrupts]"
        # into a oneOf so that interrupts-extended satisfies it too.
        # Either form satisfies edtlib (which models the pair the same
        # way), so fold it back into a plain requirement.
        for sub in schema.get('oneOf') or []:
            if (isinstance(sub, dict) and list(sub.keys()) == ['required']
                    and sub['required'] == ['interrupts']
                    and 'interrupts' not in merged['required']):
                merged['required'].append('interrupts')

        return merged

    def _ref_targets(self, schema: dict) -> list[dict]:
        ret = []
        refs = []
        if '$ref' in schema:
            refs.append(schema['$ref'])
        for sub in schema.get('allOf', []):
            if isinstance(sub, dict) and '$ref' in sub:
                refs.append(sub['$ref'])
        base = schema.get('$id') or ''
        for ref in refs:
            if ref.startswith('#'):
                # Schema-local reference ($defs etc.), not an include.
                continue
            # Relative references resolve against the document's $id,
            # exactly as during JSON Schema validation.
            target_id = urllib.parse.urljoin(base, ref).split('#')[0]
            target = None
            for schema_id, candidate in self._validator.schemas.items():
                if schema_id.split('#')[0] == target_id:
                    target = candidate
                    break
            if target is not None:
                ret.append(target)
        return ret

    def _translate_props(self, merged: dict, schema: dict) -> dict:
        props: dict[str, dict] = {}
        required = set(merged['required'])

        for name, sub in merged['properties'].items():
            if self._skip_prop(name):
                continue
            spec = self._prop_spec(name, sub, schema)
            if spec is None:
                continue
            if name in required:
                spec['required'] = True
            props[name] = spec

        # Mirror base.yaml & friends: every device binding gets specs
        # for the standard properties. Schema-declared specs win.
        for name, common in _COMMON_PROP_SPECS.items():
            props.setdefault(name, dict(common))

        return props

    def _skip_prop(self, name: str) -> bool:
        if name in _SKIP_PROPS:
            return True
        return any(name.startswith(p) for p in _SKIP_PROP_PREFIXES)

    def _prop_spec(self, name: str, sub: Any,
                   schema: dict) -> dict | None:
        # Translates one property subschema to a classic property spec
        # dict, or None if no useful spec can be derived.

        if not isinstance(sub, dict):
            # 'true' subschemas ("property allowed, unconstrained"):
            # all we can do is infer the type.
            sub = {}

        if any(name.endswith(s) for s in _COMPOUND_PROP_SUFFIXES):
            return {'type': 'compound',
                    'description': sub.get('description',
                                           'nexus node map property')}
        if any(name.endswith(s) for s in _ARRAY_PROP_SUFFIXES):
            return {'type': 'array',
                    'description': sub.get('description',
                                           'nexus node map property')}

        if _PINCTRL_RE.fullmatch(name):
            # Zephyr types pinctrl-<n> as 'phandles' for any state index
            # (a schema may declare 'pinctrl-0: true', as upstream
            # bindings do); _COMMON_PROP_SPECS only auto-provides the
            # first few, so handle an explicitly declared one here.
            return {'type': 'phandles',
                    'description': sub.get('description',
                                           'pin configuration state')}

        dts_type = self._dtschema_type(name, sub)
        if dts_type is None:
            if name in _COMMON_PROP_SPECS:
                # Schema mentions the property (e.g. just 'required')
                # without typing it locally; fall back to the common
                # spec for the type but honor schema constraints.
                spec = dict(_COMMON_PROP_SPECS[name])
                self._copy_constraints(sub, spec)
                return spec
            _LOG.warning(
                "%s: cannot determine a property type for '%s'; "
                "no macros will be generated for it",
                schema.get('$filename') or schema.get('$id'), name)
            return None

        if dts_type == 'node':
            return None

        if (_UNIT_SUFFIX_RE.search(name)
                and dts_type.endswith(('-array', '-matrix'))):
            dts_type = dts_type.split('-')[0]

        if dts_type == 'phandle-array' and _single_cell_items(sub):
            # A list of bare phandles ('phandle-array' whose entries
            # are all single cells, e.g. 'items: {maxItems: 1}'):
            # classic 'phandles', with no specifier data to decode.
            dts_type = 'phandles'

        classic_type = (
            'phandles' if dts_type == 'phandles'
            else _DTSCHEMA2CLASSIC_TYPE.get(dts_type))
        if classic_type is None:
            _LOG.warning(
                "%s: dt-schema type '%s' of property '%s' has no classic "
                "equivalent; no macros will be generated for it",
                schema.get('$filename') or schema.get('$id'),
                dts_type, name)
            return None

        spec: dict[str, Any] = {'type': classic_type}
        if 'description' in sub:
            spec['description'] = sub['description']
        if 'deprecated' in sub and sub['deprecated']:
            spec['deprecated'] = True
        self._copy_constraints(sub, spec)
        return spec

    @staticmethod
    def _copy_constraints(sub: dict, spec: dict) -> None:
        # Copies value constraints that both languages can express.
        # JSON Schema wraps scalar constraints of array-coded types in
        # nested 'items' lists; unwrap one or two levels.
        scalar = sub
        for _ in range(2):
            items = scalar.get('items')
            if isinstance(items, dict):
                scalar = items
            elif (isinstance(items, list) and len(items) == 1
                  and isinstance(items[0], dict)):
                scalar = items[0]
            else:
                break

        if 'const' in scalar:
            spec['const'] = scalar['const']
        if 'enum' in scalar:
            spec['enum'] = scalar['enum']
        if 'default' in sub:
            spec['default'] = sub['default']
        elif 'default' in scalar and scalar is not sub:
            spec['default'] = scalar['default']
        if 'minimum' in scalar:
            spec['min'] = scalar['minimum']
        if 'maximum' in scalar:
            spec['max'] = scalar['maximum']

    def _dtschema_type(self, name: str, sub: dict) -> str | None:
        # Determines the dt-schema type of a property: first from
        # $refs in its (possibly nested) subschema, then from JSON
        # Schema shape, then from dtschema's global property-type map.

        found = _find_types_yaml_ref(sub)
        if found:
            return found

        if sub.get('type') == 'boolean':
            return 'flag'
        if sub.get('type') == 'object' or 'properties' in sub \
                or 'patternProperties' in sub:
            return 'node'
        if name.startswith('#') and name.endswith('-cells'):
            return 'uint32'
        if name in _COMMON_PROP_SPECS:
            # No local type information: prefer the standard property
            # spec over the (possibly ambiguous) global type map.
            return None

        try:
            types = self._validator.property_get_type(name)
        except Exception:
            types = set()
        types = {t for t in types if t and t != 'node'}
        if len(types) == 1:
            return types.pop()
        if types:
            # Ambiguous global type; pick deterministically but warn.
            picked = sorted(types)[0]
            _LOG.warning(
                "property '%s' has multiple possible dt-schema types %s; "
                "using '%s'", name, sorted(types), picked)
            return picked
        return None

    def _translate_children(self, merged: dict) -> dict | None:
        # Translates object-valued properties/patternProperties into a
        # classic 'child-binding'. The classic model has a single child
        # binding per node, so multiple child patterns are merged; this
        # is lossy only for schemas that give *different* child nodes
        # different shapes, which the classic language cannot express at
        # all.
        children = []
        for sub in merged['patternProperties'].values():
            if isinstance(sub, dict) and (
                    sub.get('type') == 'object'
                    or 'properties' in sub
                    or 'patternProperties' in sub):
                children.append(sub)
        for name, sub in merged['properties'].items():
            if self._skip_prop(name):
                continue
            if isinstance(sub, dict) and sub.get('type') == 'object' \
                    and ('properties' in sub or 'patternProperties' in sub):
                children.append(sub)

        if not children:
            return None

        child_merged = {
            'properties': {},
            'patternProperties': {},
            'required': [],
        }
        description = None
        for child in children:
            inner = self._merge_refs(child, _RefTrail(None))
            _merge_translated(child_merged, inner)
            description = description or child.get('description')

        raw: dict[str, Any] = {
            'description': description or 'child node',
        }
        props: dict[str, dict] = {}
        required = set(child_merged['required'])
        for name, sub in child_merged['properties'].items():
            if self._skip_prop(name):
                continue
            spec = self._prop_spec(name, sub, {})
            if spec is None:
                continue
            if name in required:
                spec['required'] = True
            props[name] = spec
        # Child nodes get the standard property specs too, except that
        # 'compatible' is not required: children are commonly matched
        # via their parent's binding instead of a compatible.
        for name, common in _COMMON_PROP_SPECS.items():
            common = dict(common)
            common.pop('required', None)
            props.setdefault(name, common)
        if props:
            raw['properties'] = props

        grandchild = self._translate_children(child_merged)
        if grandchild is not None:
            raw['child-binding'] = grandchild

        return raw

    def _apply_extras(self, compat: str, schema: dict, raw: dict) -> None:
        # Adds the Zephyr supplement (specifier cell names, bus typing)
        # to a translated binding. Supplement entries are keyed by schema
        # document stem (filename without extension), which is the bare
        # compatible for a single-document compatible and
        # '<compatible>-<bus>' for a per-bus document in the multi-bus
        # pattern; a bare-compatible key is accepted as a fallback so
        # single-bus bindings need no '-<bus>' suffix.
        stem = _schema_stem(schema)
        compatibles = self._extras['compatibles']
        per_compat = compatibles.get(stem)
        if per_compat is None:
            per_compat = compatibles.get(compat, {})
        spaces = self._extras['specifier-spaces']

        for key, val in per_compat.items():
            if key.endswith('-cells') or key in ('bus', 'on-bus'):
                raw[key] = val
            else:
                _err(f"zephyr-extras.yaml: unknown key '{key}' for "
                     f"compatible '{compat}'")

        # Default cell names for any '#<space>-cells' property the
        # schema declares that has no per-compatible override.
        for prop_name in raw.get('properties', {}):
            match = re.fullmatch(r'#(.+)-cells', prop_name)
            if not match:
                continue
            space = match.group(1)
            if f'{space}-cells' in raw:
                continue
            default = spaces.get(space)
            if default and 'cells' in default:
                const = raw['properties'][prop_name].get('const')
                cells = default['cells']
                if const is not None and const != len(cells):
                    # e.g. '#clock-cells = 0' with default ['id'].
                    cells = cells[:const]
                raw[f'{space}-cells'] = cells


class _RefTrail:
    # Tracks the chain of $refs being merged, to break cycles.

    def __init__(self, schema_id, seen=None):
        self._seen = set(seen or ())
        if schema_id:
            self._seen.add(schema_id)

    def push(self, schema_id) -> '_RefTrail':
        if schema_id and schema_id in self._seen:
            _err(f"$ref cycle involving {schema_id}")
        return _RefTrail(schema_id, self._seen)


def _merge_translated(merged: dict, sub: dict) -> None:
    # Merges 'properties', 'patternProperties' and 'required' from
    # 'sub' into 'merged'; entries already in 'merged' are
    # dict-combined with 'sub' taking precedence (the referencing
    # schema is more specific than the referenced one).
    for key in ('properties', 'patternProperties'):
        for name, val in (sub.get(key) or {}).items():
            if (name in merged[key]
                    and isinstance(merged[key][name], dict)
                    and isinstance(val, dict)):
                combined = dict(merged[key][name])
                combined.update(val)
                merged[key][name] = combined
            else:
                merged[key][name] = val
    for name in (sub.get('required') or []):
        if name not in merged['required']:
            merged['required'].append(name)


def _single_cell_items(sub: dict) -> bool:
    # True if a phandle-array property subschema constrains every
    # entry to a single cell (i.e. it is a list of bare phandles).
    items = sub.get('items')
    if isinstance(items, dict):
        return items.get('maxItems') == 1
    if isinstance(items, list):
        return bool(items) and all(
            isinstance(i, dict) and i.get('maxItems') == 1 for i in items)
    return False


def _find_types_yaml_ref(sub: Any, depth: int = 0) -> str | None:
    # Searches a property subschema (shallowly: through allOf/oneOf/
    # anyOf/items nesting, not into named subproperties) for a $ref to
    # /schemas/types.yaml and returns the referenced type name.
    if depth > 4 or not isinstance(sub, dict):
        return None
    ref = sub.get('$ref')
    if isinstance(ref, str):
        match = _TYPES_YAML_REF.search(ref)
        if match:
            return match.group('type')
    for key in ('allOf', 'oneOf', 'anyOf'):
        for inner in sub.get(key) or []:
            found = _find_types_yaml_ref(inner, depth + 1)
            if found:
                return found
    items = sub.get('items')
    if isinstance(items, list):
        for inner in items:
            found = _find_types_yaml_ref(inner, depth + 1)
            if found:
                return found
    return _find_types_yaml_ref(items, depth + 1)
