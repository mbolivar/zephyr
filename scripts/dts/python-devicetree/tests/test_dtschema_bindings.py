# Copyright (c) 2026 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""
Tests for dt-schema based bindings support (dtschema_bindings.py).

Run with pytest. The fixture inputs are:

- test-dtschema.dts: the devicetree
- test-dtschema/: dt-schema documents plus the zephyr-extras.yaml
  supplement (specifier cell names, bus typing)
- test-dtschema-classic-bindings/: classic bindings used by the
  precedence tests

Each test documents one aspect of the translation contract: a
dt-schema document must behave exactly like the equivalent classic
binding would, all the way down to edtlib property values.
"""

import contextlib
import os
import shutil
import sys
from pathlib import Path

import pytest
from devicetree import edtlib

HERE = os.path.dirname(__file__)

pytest.importorskip(
    'dtschema',
    reason='dt-schema bindings tests require the dtschema package')


@contextlib.contextmanager
def from_here():
    cwd = os.getcwd()
    try:
        os.chdir(HERE)
        yield
    finally:
        os.chdir(cwd)


@pytest.fixture(scope='module')
def edt():
    # One EDT shared by most tests: dt-schema documents only.
    with from_here():
        return edtlib.EDT('test-dtschema.dts', [],
                          dtschema_dirs=['test-dtschema'])


def test_property_types(edt):
    '''Every translatable dt-schema property type, as edtlib values.'''
    node = edt.get_node('/device@3000')

    def val(name):
        return node.props[name].val

    assert val('an-int') == 7
    assert val('an-array') == [1, 2, 3]
    assert val('bytes') == b'\x01\x02'
    assert val('a-string') == 'alpha'
    assert val('strings') == ['a', 'b']
    assert val('a-phandle') is edt.get_node('/gpio@2000')
    assert val('a-bool') is True

    # 'items: {maxItems: 1}' phandle-arrays are lists of bare
    # phandles: classic type 'phandles'.
    assert node.props['some-phandles'].type == 'phandles'
    assert val('some-phandles') == [edt.get_node('/gpio@2000'),
                                    edt.get_node('/interrupt-controller@1000')]

    # Unit-suffix properties ('-us') have no type $ref in the schema;
    # their dtb encoding is an array, but the logical type is scalar.
    assert node.props['delay-us'].type == 'int'
    assert val('delay-us') == 100


def test_property_constraints(edt):
    '''default/const/enum/required/deprecated survive translation.'''
    binding = edt.get_node('/device@3000')._binding
    spec = binding.prop2specs

    assert spec['an-int'].required
    assert spec['an-int'].default == 11
    assert spec['an-int'].min == 1
    assert spec['an-int'].max == 100
    assert spec['enum-int'].enum == [10, 20, 30]
    assert spec['const-int'].const == 42
    assert spec['a-string'].enum == ['alpha', 'beta']
    assert spec['deprecated-int'].deprecated
    assert not spec['an-array'].required


def test_standard_properties(edt):
    '''Standard properties work without the schema declaring them,
    mirroring base.yaml in the classic language.'''
    node = edt.get_node('/device@3000')
    assert node.props['compatible'].val == ['vnd,dtschema-device']
    assert len(node.regs) == 1
    assert node.regs[0].addr == 0x3000
    # 'status' has a spec with the standard enum even though no test
    # schema mentions it.
    assert node._binding.prop2specs['status'].enum[:2] == ['okay', 'disabled']


def test_specifier_cells_from_default_space(edt):
    '''GPIO cell names come from the specifier-spaces: supplement.'''
    node = edt.get_node('/device@3000')
    gpio = node.props['det-gpios'].val[0]
    assert gpio.controller is edt.get_node('/gpio@2000')
    assert gpio.data == {'pin': 4, 'flags': 1}


def test_specifier_cells_per_compatible(edt):
    '''Interrupt cell names come from the per-compatible supplement.'''
    node = edt.get_node('/device@3000')
    assert node.interrupts[0].data == {'irq': 5, 'prio': 1}


def test_bus_typing(edt):
    '''bus:/on-bus: come from the supplement.'''
    ctrl = edt.get_node('/serial@4000')
    assert ctrl.buses == ['vndserial']
    dev = edt.get_node('/serial@4000/serial-dev')
    assert dev.on_buses == ['vndserial']
    assert dev.bus_node is ctrl


def test_allof_ref_include(edt):
    '''allOf: [$ref: ...] pulls in another schema's properties and
    requirements, like include: in the classic language.'''
    ctrl = edt.get_node('/serial@4000')
    assert ctrl.props['current-speed'].val == 115200
    assert ctrl._binding.prop2specs['current-speed'].required
    assert ctrl.props['fifo-depth'].val == 16


def test_child_binding(edt):
    '''Object-valued patternProperties translate to child bindings.'''
    leds = edt.get_node('/leds')
    child = leds._binding.child_binding
    assert child is not None
    assert child.prop2specs['gpios'].required
    led = edt.get_node('/leds/led_0')
    gpio = led.props['gpios'].val[0]
    assert gpio.data == {'pin': 1, 'flags': 0}
    assert led.props['label'].val == 'L0'


def test_multibus(edt):
    '''A single compatible with one document per bus (the multi-bus
    pattern) resolves to the matching per-bus binding, selected by the
    parent controller's bus.'''
    i2c_sensor = edt.get_node('/i2c@5000/sensor@1')
    spi_sensor = edt.get_node('/spi@6000/sensor@0')

    # Same compatible, different bus, different binding document.
    assert i2c_sensor.props['compatible'].val == ['vnd,dtschema-sensor']
    assert spi_sensor.props['compatible'].val == ['vnd,dtschema-sensor']
    assert i2c_sensor.on_buses == ['i2c']
    assert spi_sensor.on_buses == ['spi']
    assert i2c_sensor.binding_path.endswith('vnd,dtschema-sensor-i2c.yaml')
    assert spi_sensor.binding_path.endswith('vnd,dtschema-sensor-spi.yaml')

    # Bus-specific properties exist only on their own variant.
    assert 'i2c-extra' in i2c_sensor._binding.prop2specs
    assert 'i2c-extra' not in spi_sensor._binding.prop2specs
    assert 'spi-extra' in spi_sensor._binding.prop2specs
    assert 'spi-extra' not in i2c_sensor._binding.prop2specs
    assert spi_sensor.props['spi-extra'].val == 7

    # The property shared via 'allOf: [$ref: ...common]' is on both.
    assert i2c_sensor.props['sample-count'].val == 100
    assert spi_sensor.props['sample-count'].val == 200


def test_precedence_over_classic():
    '''When a compatible has both a dt-schema document and a classic
    binding, the dt-schema document wins; compatibles with only a
    classic binding keep using it.'''
    with from_here():
        edt = edtlib.EDT('test-dtschema.dts',
                         ['test-dtschema-classic-bindings'],
                         dtschema_dirs=['test-dtschema'])

    dev = edt.get_node('/device@3000')
    assert 'classic-bindings' not in dev.binding_path
    assert dev.binding_path.endswith('vnd,dtschema-device.yaml')
    assert 'from-classic-binding' not in dev._binding.prop2specs

    classic = edt.get_node('/classic-device')
    assert classic.binding_path.endswith('vnd,dtschema-classic-only.yaml')
    assert classic.props['classic-prop'].val == 3


def test_no_classic_bindings():
    '''no_classic_bindings=True must not read classic bindings at all.'''
    with from_here():
        edt = edtlib.EDT('test-dtschema.dts',
                         ['test-dtschema-classic-bindings'],
                         dtschema_dirs=['test-dtschema'],
                         no_classic_bindings=True)

    assert edt.get_node('/classic-device').binding_path is None
    assert edt.get_node('/device@3000').binding_path is not None


def test_dtschema_only_is_default_compatible():
    '''Without dtschema_dirs, nothing changes for existing users.'''
    with from_here():
        edt = edtlib.EDT('test-dtschema.dts',
                         ['test-dtschema-classic-bindings'])

    dev = edt.get_node('/device@3000')
    assert dev.binding_path.endswith('vnd,dtschema-device.yaml')
    assert 'classic-bindings' in dev.binding_path
    assert 'from-classic-binding' in dev._binding.prop2specs


def test_value_validation():
    '''dt-schema validates property *values* -- enums, ranges, item
    counts -- which the classic language and edtlib cannot. This is the
    capability the new language adds. Requires dtc (skipped without it).'''
    dtc = shutil.which('dtc')
    if not dtc and os.environ.get('DTC'):
        dtc = shutil.which(os.environ['DTC']) or os.environ['DTC']
    if not dtc or not os.path.exists(dtc):
        pytest.skip('dtc not available')

    sys.path.insert(0, str(Path(HERE).parents[1]))  # scripts/dts
    import dts_validate

    sdir = os.path.join(HERE, 'test-dtschema-validate')

    # Valid values: no errors.
    good = dts_validate.validate_dtb(
        dts_validate.compile_dts(os.path.join(sdir, 'good.dts'), dtc), [sdir])
    assert good == [], good

    # Out-of-range / not-in-enum values: both caught, by property name.
    bad = dts_validate.validate_dtb(
        dts_validate.compile_dts(os.path.join(sdir, 'bad.dts'), dtc), [sdir])
    joined = '\n'.join(bad)
    assert 'poll-rate' in joined
    assert 'resolution' in joined


def test_i2c_controller_reuse():
    '''A dt-schema document that reuses the ecosystem-standard
    i2c-controller schema by $ref inherits real value validation -- a
    child's I2C address and the bus clock-frequency -- that the classic
    bindings language cannot express. This is how a Zephyr binding adopts
    a standard schema without copying it. Requires dtc.'''
    dtc = shutil.which('dtc')
    if not dtc and os.environ.get('DTC'):
        dtc = shutil.which(os.environ['DTC']) or os.environ['DTC']
    if not dtc or not os.path.exists(dtc):
        pytest.skip('dtc not available')

    sys.path.insert(0, str(Path(HERE).parents[1]))  # scripts/dts
    import dts_validate

    sdir = os.path.join(HERE, 'test-dtschema-i2c-reuse')

    # A legal address and frequency: no errors.
    good = dts_validate.validate_dtb(
        dts_validate.compile_dts(os.path.join(sdir, 'good.dts'), dtc), [sdir])
    assert good == [], good

    # 0x99 is not a valid 7-bit I2C address and 9 MHz is over the max;
    # both come from the reused upstream schema, not anything written here.
    bad = dts_validate.validate_dtb(
        dts_validate.compile_dts(os.path.join(sdir, 'bad.dts'), dtc), [sdir])
    joined = '\n'.join(bad)
    assert 'clock-frequency' in joined
    assert 'sensor@99' in joined
