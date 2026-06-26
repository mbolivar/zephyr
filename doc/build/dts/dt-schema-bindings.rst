.. _dt-schema-bindings:

dt-schema based bindings
########################

.. warning::

   This feature is experimental, part of an RFC to add support for the
   dt-schema devicetree binding language to Zephyr. Interfaces and file
   locations may change based on community feedback, and the feature can
   be removed again without affecting any binding under
   :file:`dts/bindings/`.

Zephyr supports devicetree bindings written in two languages:

- the :ref:`classic bindings language <dt-bindings>` Zephyr has always
  used (YAML files under :file:`dts/bindings/`), and
- the `dt-schema <https://github.com/devicetree-org/dt-schema>`_
  language: JSON-Schema documents maintained by the devicetree.org
  community and used by the Linux kernel (YAML files under
  :file:`dts/schemas/`).

The two languages coexist: when a compatible is described in both, the
dt-schema document is used, so a tree can migrate one binding at a time.
When a compatible is described in neither, behavior is unchanged from
previous releases.

Status and support
******************

The long-term goal of this effort is for Zephyr to adopt dt-schema as
its devicetree binding language, which would eventually mean deprecating
and removing the classic language. That is the intended destination, and
this document does not pretend otherwise.

It is not where things stand today, and merging this support does not get
there. This is an experimental, revertible change whose purpose is to let
the community evaluate and collaborate on dt-schema *in the main branch*
rather than in a fork. There is no flag day, no removal date, and no
requirement to convert anything: bindings under :file:`dts/bindings/`
keep working exactly as before, and dt-schema support is opt-in and
incremental -- a single binding, a single board, or none at all. Any
actual deprecation or removal of the classic language would be a
separate, announced Technical Steering Committee decision with its own
migration period; merging this RFC neither makes that decision nor
schedules it.

What dt-schema adds
*******************

dt-schema is the binding language maintained by the devicetree.org
community and used by the Linux kernel. Writing a binding in it offers
two things the classic language cannot:

- **Value validation.** The classic language checks a property's type
  and presence; dt-schema also checks its *values* -- enums, numeric
  ranges, item counts, and conditional or cross-property requirements.
  :file:`scripts/dts/dts_validate.py` runs this validation against a
  built devicetree; for example, a reused ``i2c-controller`` schema
  rejects an out-of-range I2C address or bus frequency that the classic
  language and edtlib accept without complaint.
- **Ecosystem reuse.** A document can ``$ref`` a schema the devicetree
  community already maintains, instead of re-describing the same
  hardware Zephyr-side. The in-tree I2C controllers reuse the standard
  ``i2c-controller`` schema this way, and ``arm,pl011`` reuses the
  standard ``serial`` schema -- both resolved from the dtschema package,
  with nothing copied into the Zephyr tree. For hardware that ships both
  a Linux and a Zephyr image, this also means one binding dialect
  instead of two.

None of this is mandatory; it is available when the payoff is worth it
to you.

Classic-to-dt-schema reference
==============================

A quick map between the two languages:

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - Classic (:file:`dts/bindings/`)
     - dt-schema (:file:`dts/schemas/`)
   * - ``compatible: "vnd,dev"``
     - ``properties: {compatible: {const: "vnd,dev"}}``
   * - ``include: [base.yaml, foo.yaml]``
     - ``allOf: [{$ref: "foo.yaml#"}]`` (base properties are implicit)
   * - ``foo: {type: int}``
     - ``foo: {$ref: "/schemas/types.yaml#/definitions/uint32"}``
   * - per-property ``required: true``
     - node-level ``required: [foo]``
   * - ``enum:`` / ``const:`` / ``default:`` / ``deprecated:``
     - identical
   * - ``bus:`` / ``on-bus:``
     - :file:`zephyr-extras.yaml` supplement (see below)
   * - ``child-binding:``
     - object-valued ``patternProperties:``

Quick start: building with dt-schema bindings
*********************************************

#. Install the tooling and fetch the dt-schema module::

      pip install dtschema
      west config manifest.group-filter +optional && west update dt-schema

#. Build normally. Directories named :file:`dts/schemas` in any
   devicetree root (the zephyr repository, your board root, any module
   with a ``dts_root``) are discovered automatically, and any schema
   matching a compatible in your devicetree is used as its binding.

#. To *prove* a configuration does not depend on the classic language at
   all, disable it::

      west build -b <board> <app> -- -DDTS_NO_CLASSIC_BINDINGS=ON

   In this mode classic bindings are not read, and the
   ``DT_HAS_<compat>_ENABLED`` Kconfig symbols that drive driver
   defaults are derived from dt-schema documents only. The blinky and
   hello_world samples build this way for ``nrf52840dk/nrf52840``,
   ``rpi_pico``, ``qemu_cortex_m3`` and ``native_sim``; see
   :file:`tests/dts/dtschema_bindings` for the build-only test that
   enforces it.

   The opposite switch, ``-DDTS_NO_DTSCHEMA_BINDINGS=ON``, ignores
   :file:`dts/schemas` directories instead; it exists mainly for A/B
   comparisons.

For users: migrating out-of-tree bindings
*****************************************

If your application or module carries classic bindings, the
:file:`scripts/dts/migrate_binding.py` tool converts them. If your
binding works with edtlib today, the tool produces a working dt-schema
document from it::

   python3 $ZEPHYR_BASE/scripts/dts/migrate_binding.py \
       --bindings-dir my-module/dts/bindings \
       --out-dir my-module/dts/schemas \
       --extras-out my-module/dts/schemas/zephyr-extras.yaml \
       --namespace my-module \
       my-module/dts/bindings/sensor/vnd,my-sensor.yaml

The output is a starting point by design. Review it, tighten the
``reg``/``interrupts`` item counts, replace generated child node name
patterns with your real naming convention, and add constraints the
classic language could not express. The tool prints a "manual review
needed" list flagging anything lossy. When several bindings share a
compatible but differ by bus, the tool emits one
``<compatible>-<bus>.yaml`` document per bus rather than silently
overwriting.

Then verify the conversion:

- ``dt-doc-validate dts/schemas`` checks your documents against the
  upstream dt-schema meta-schema.
- :file:`scripts/dts/check_schema_parity.py` diffs the classic binding
  against the dt-schema translation property by property (see
  `Testing`_).
- :file:`scripts/dts/dts_validate.py` checks property *values* in a
  built devicetree against the documents -- useful once you start adding
  constraints the classic language could not express.
- Build your application twice (with ``-DDTS_NO_DTSCHEMA_BINDINGS=ON``
  and with ``-DDTS_NO_CLASSIC_BINDINGS=ON``) and compare:
  :file:`scripts/dts/diff_dt_headers.py` compares the generated
  devicetree macros semantically, and a plain ``diff`` of the two
  :file:`.config` files catches drivers silently dropping out.

.. warning::

   Pay special attention to nodes with multiple compatibles. Driver
   ``DT_HAS_<compat>_ENABLED`` symbols may key off a *fallback*
   compatible (for example ``arm,pl011`` on RP2040 uarts) rather than
   the compatible your binding matches. Convert every compatible your
   configuration mentions, not just the ones edtlib matches bindings
   for; comparing ``.config`` files catches this class of mistake.

For developers: how it works
****************************

dt-schema documents are translated at build time into the same in-memory
representation the classic language parses into, by
:file:`scripts/dts/python-devicetree/src/devicetree/dtschema_bindings.py`.
Everything downstream of binding loading -- property value conversion,
specifier-space handling, :file:`gen_defines.py`, the pickled ``EDT`` --
is unchanged and cannot tell which language a binding was written in. The
translation rules are documented in that module; the highlights:

- Property types come from :file:`/schemas/types.yaml` references
  (``uint32`` |rarr| ``int``, ``phandle-array`` |rarr| ``phandle-array``,
  and so on). A ``phandle-array`` whose entries are constrained to a
  single cell (``items: {maxItems: 1}``) is a list of bare phandles, the
  classic ``phandles`` type. Properties with standard unit suffixes
  (``-us``, ``-ms``, ``-bits``, ...) are implicitly typed and scalar.
- ``required:``, ``default:``, ``const:``, ``enum:``,
  ``minimum:``/``maximum:`` and ``deprecated:`` map directly.
- ``allOf: [{$ref: ...}]`` includes of other schemas behave like the
  classic ``include:`` for property definitions and requirements. The
  referenced schema can be another Zephyr document or one the dt-schema
  package ships (so a binding can reuse a community-maintained schema
  without copying it).
- Object-valued ``patternProperties`` become child bindings
  (recursively, so two-level structures like pinctrl states/groups work).
- A single compatible may be described by more than one document -- the
  multi-bus pattern, where ``<compatible>-i2c.yaml`` and
  ``<compatible>-spi.yaml`` differ only in bus typing. edtlib registers
  each under its own ``(compatible, on-bus)`` key and selects per node.
- The standard properties every classic binding gets from
  :file:`base.yaml` (``status``, ``reg``, ``interrupts``, ``clocks``,
  ``pinctrl-N``, ...) are provided automatically.

The supplement file
===================

Two things Zephyr's C API needs have no machine-readable home in
dt-schema today, and live in one ``zephyr-extras.yaml`` file at the root
of each schemas directory:

- **Specifier cell names.** dt-schema records that ``#gpio-cells`` is 2,
  but the names DT_GPIO_PIN_BY_IDX() needs (``pin``, ``flags``) exist
  only in prose. The supplement provides defaults per specifier space and
  overrides per compatible.
- **Bus typing** (the classic ``bus:``/``on-bus:`` keys).

Proposing named specifier cells to dt-schema upstream -- which would
shrink this file over time -- is part of the RFC's follow-up work.

Known divergences from dt-schema conventions
============================================

Converting Zephyr's bindings surfaced a handful of properties whose
Zephyr semantics cannot be expressed in dt-schema today. They are worth
knowing about because they are the pattern to avoid in new bindings:

- ``stop-bits`` (uart controllers) is a string enum (``"0_5"``, ``"1"``,
  ...) but the ``-bits`` unit suffix implies integer cells upstream.
- ``nfct-pins-as-gpios`` and ``length-field-length-8-bits`` (Nordic) are
  booleans whose names collide with the ``-gpios`` and ``-bits`` suffix
  conventions.
- Zephyr's ``memory-regions`` (plural, phandle-array) coexists uneasily
  with the upstream singular ``memory-region``.

The upstream meta-schema already encodes exceptions for legacy Linux
properties of exactly this kind (for example ``ti,reset-bits``), so the
path forward is either proposing Zephyr exceptions upstream or
deprecating/renaming the properties. Until then, ``dt-doc-validate``
reports these (and only these) as errors.

Testing
*******

A conversion has three layers of verification, in increasing order of
strength:

#. **Meta-schema validity**: ``dt-doc-validate dts/schemas``.
#. **Binding-level parity**: :file:`scripts/dts/check_schema_parity.py`
   compares everything edtlib consumes (property types, requiredness,
   defaults, enums, specifier cells, bus typing, child bindings) for
   every compatible that has both a classic binding and a schema. By
   default it enforces a *faithful-superset* contract: the dt-schema
   document must reproduce everything the classic binding specified -- so
   the generated macros for those properties are identical -- but may
   also add properties, validation constraints and child bindings (as the
   reused ``i2c-controller`` schema does). Pass ``--strict`` to require
   exact equivalence, which is what a pure conversion should meet.
#. **Build-level parity**: build twice (with
   ``-DDTS_NO_DTSCHEMA_BINDINGS=ON`` and with
   ``-DDTS_NO_CLASSIC_BINDINGS=ON``) and compare
   :file:`devicetree_generated.h` with
   :file:`scripts/dts/diff_dt_headers.py` and the two :file:`.config`
   files with ``diff``. For a pure conversion the output is identical;
   for a schema that adds constraints, the macros for classic-defined
   properties are unchanged and only new ones appear.

The dt-schema parity compliance check in
:file:`scripts/ci/check_compliance.py` runs the binding-level parity for
the compatibles a change touches, so a dt-schema document cannot silently
drop or contradict its classic binding. Unit tests for the translation
itself live in
:file:`scripts/dts/python-devicetree/tests/test_dtschema_bindings.py`.

.. |rarr| unicode:: U+2192
