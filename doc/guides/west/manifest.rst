.. _west-manifests:

West Manifests
##############

This page contains detailed information about west's multiple repository model,
manifest files, and the ``west manifest`` command. For API documentation on the
``west.manifest`` module, see :ref:`west-apis-manifest`. For a more general
introduction and command overview, see :ref:`west-multi-repo`.

.. _west-mr-model:

Multiple Repository Model
*************************

West's view of the repositories in a :term:`west installation`, and their
history, looks like the following figure (though some parts of this example are
specific to upstream Zephyr's use of west):

.. figure:: west-mr-model.png
   :align: center
   :alt: West multi-repo history
   :figclass: align-center

   West multi-repo history

The history of the manifest repository is the line of Git commits which is
"floating" on top of the gray plane. Parent commits point to child commits
using solid arrows. The plane below contains the Git commit history of the
repositories in the installation, with each project repository boxed in by a
rectangle. Parent/child commit relationships in each repository are also shown
with solid arrows.

The commits in the manifest repository (again, for upstream Zephyr this is the
zephyr repository itself) each have a manifest file. The manifest file in each
commit specifies the corresponding commits which it expects in each of the
project repositories. This relationship is shown using dotted line arrows in the
diagram. Each dotted line arrow points from a commit in the manifest repository
to a corresponding commit in a project repository.

Notice the following important details:

- Projects can be added (like ``P1`` between manifest repository
  commits ``D`` and ``E``) and removed (``P2`` between the same
  manifest repository commits)

- Project and manifest repository histories don't have to move
  forwards or backwards together:

  - ``P2`` stays the same from ``A → B``, as do ``P1`` and ``P3`` from ``F →
    G``.
  - ``P3`` moves forward from ``A → B``.
  - ``P3`` moves backward from ``C → D``.

  One use for moving backward in project history is to "revert" a regression by
  going back to a revision before it was introduced.

- Project repository commits can be "skipped": ``P3`` moves forward
  multiple commits in its history from ``B → C``.

- In the above diagram, no project repository has two revisions "at
  the same time": every manifest file refers to exactly one commit in
  the projects it cares about. This can be relaxed by using a branch
  name as a manifest revision, at the cost of being able to bisect
  manifest repository history.

.. _west-manifest-files:

Manifest Files
**************

A west manifest is a YAML file named :file:`west.yml`. Manifests have a
top-level ``manifest`` section with some subsections, like this:

.. code-block:: yaml

   manifest:
     defaults:
       # default project attributes (optional)
     remotes:
       # short names for project URLs (optional)
     projects:
       # a list of projects managed by west (mandatory)
     self:
       # configuration related to the manifest repository itself,
       # i.e. the repository containing west.yml (optional)

In YAML terms, the manifest file contains a mapping, with a ``manifest``
key. Any other keys and their contents are ignored (west v0.5 also required a
``west`` key, but this is ignored starting with v0.6).

There are four subsections: ``defaults``, ``remotes``, ``projects``, and
``self``. In YAML terms, the value of the ``manifest`` key is also a mapping,
with these "subsections" as keys. Only ``projects`` is mandatory: this is the
list of repositories managed by west and their metadata.

We'll cover the ``remotes`` and ``projects`` subsections in detail first.

The ``remotes`` subsection contains a sequence which specifies the base URLs
where projects can be fetched from. Each sequence element has a name and a "URL
base". These are used to form the complete fetch URL for each project. For
example:

.. code-block:: yaml

   manifest:
     # [...]
     remotes:
       - name: remote1
         url-base: https://git.example.com/base1
       - name: remote2
         url-base: https://git.example.com/base2

Above, two remotes are given, with names ``remote1`` and ``remote2``. Their URL
bases are respectively ``https://git.example.com/base1`` and
``https://git.example.com/base2``. You can use SSH URL bases as well; for
example, you might use ``git@example.com:base1`` if ``remote1`` supported Git
over SSH as well. Anything acceptable to Git will work.

The ``projects`` subsection contains a sequence describing the project
repositories in the west installation. Every project has a unique name. You can
specify what Git remote URLs to use when cloning and fetching the projects,
what revisions to track, and where the project should be stored on the local
file system.

Here is an example. We'll assume the ``remotes`` given above.

.. Note: if you change this example, keep the equivalent manifest below in
   sync.

.. code-block:: yaml

   manifest:
     # [... same remotes as above...]
     projects:
       - name: proj1
         remote: remote1
         path: extra/project-1
       - name: proj2
         repo-path: my-path
         remote: remote2
         revision: v1.3
       - name: proj3
         url: https://github.com/user/project-three
         revision: abcde413a111

In this manifest:

- ``proj1`` has remote ``remote1``, so its Git fetch URL is
  ``https://git.example.com/base1/proj1``. The remote ``url-base`` plus a ``/``
  is prepended to the project ``name`` to form the URL in this case.

  Locally, this project will be cloned at path ``extra/project-1`` relative to
  the west installation's root directory, since it has an explicit ``path``
  attribute with this value.

  Since the project has no ``revision`` specified, ``master`` is used by
  default. The current tip of this branch will be fetched and checked out as a
  detached ``HEAD`` when west next updates this project.

- ``proj2`` has a ``remote`` and a ``repo-path``, so its fetch URL is
  ``https://git.example.com/base2/my-path``. The ``repo-path`` attribute, if
  present, overrides the default ``name`` when forming the fetch URL.

  Since the project has no ``path`` attribute, its ``name`` is used by
  default. It will be cloned into a directory named ``proj2``. The commit
  pointed to by the ``v1.3`` tag will be checked out when west updates the
  project.

- ``proj3`` has an explicit ``url``, so it will be fetched from
  ``https://github.com/user/project-three``.

  Its local path defaults to its name, ``proj3``. Commit ``abcde413a111`` will
  be checked out when it is next updated.

The list of project keys and their usage follows. Sometimes we'll refer to the
``defaults`` subsection; it will be described next.

- ``name``: Mandatory, the name of the project. The name cannot be one of the
  reserved values "west" or "manifest". The name must be unique in the manifest
  file.
- ``remote`` or ``url``:

  If the project has a ``remote``, that remote's ``url-base`` will be combined
  with the project's ``name`` (or ``repo-path``, if it has one) to form the
  fetch URL instead.

  If the project has a ``url``, that's the complete fetch URL for the
  remote Git repository.

  If the project has neither, the ``defaults`` section must specify a
  ``remote``, which will be used as the the project's remote. Otherwise, the
  manifest is invalid.
- ``repo-path``: Optional. If given, this is concatenated on to the remote's
  ``url-base`` instead of the project's ``name`` to form its fetch URL.
  Projects may not have both ``url`` and ``repo-path`` attributes.
- ``revision``: Optional. The Git revision that ``west update`` should check
  out. This will be checked out as a detached HEAD by default, to avoid
  conflicting with local branch names.  If not given, the ``revision`` value
  from the ``defaults`` subsection will be used if present.

  A project revision can be a branch, tag, or SHA. The default ``revision`` is
  ``master`` if not otherwise specified.
- ``path``: Optional. Relative path specifying where to clone the repository
  locally, relative to the top directory in the west installation. If missing,
  the project's ``name`` is used as a directory name.
- ``clone-depth``: Optional. If given, a positive integer which creates a
  shallow history in the cloned repository limited to the given number of
  commits. This can only be used if the ``revision`` is a branch or tag.
- ``west-commands``: Optional. If given, a relative path to a YAML file within
  the project which describes additional west commands provided by that
  project. This file is named :file:`west-commands.yml` by convention. See
  :ref:`west-extensions` for details.
- ``import``: Optional. If ``true``, imports projects from manifest files in
  the given repository into the current manifest. See
  :ref:`west-manifest-import` for more details.

The ``defaults`` subsection can provide default values for project
attributes. In particular, the default remote name and revision can be
specified here. Another way to write the same manifest we have been describing
so far using ``defaults`` is:

.. code-block:: yaml

   manifest:
     defaults:
       remote: remote1
       revision: v1.3

     remotes:
       - name: remote1
         url-base: https://git.example.com/base1
       - name: remote2
         url-base: https://git.example.com/base2

     projects:
       - name: proj1
         path: extra/project-1
         revision: master
       - name: proj2
         repo-path: my-path
         remote: remote2
       - name: proj3
         url: https://github.com/user/project-three
         revision: abcde413a111

Finally, the ``self`` subsection can be used to control the behavior of the
manifest repository itself. Its value is a map with the following keys:

- ``path``: Optional. The path to clone the manifest repository into, relative
  to the west installation's root directory. If not given, the basename of the
  path component in the manifest repository URL will be used by default.  For
  example, if the URL is ``https://git.example.com/project-repo``, the manifest
  repository would be cloned to the directory :file:`project-repo`.

- ``west-commands``: Optional. This is analogous to the same key in a
  project sequence element.

- ``import``: Optional. This is also analogous to the ``projects`` key, but
  allows importing projects from other files in the manifest repository. See
  :ref:`west-manifest-import`.

As an example, let's consider this snippet from the zephyr repository's
:file:`west.yml`:

.. code-block:: yaml

   manifest:
     # [...]
     self:
       path: zephyr
       west-commands: scripts/west-commands.yml

This ensures that the zephyr repository is cloned into path ``zephyr``, though
as explained above that would have happened anyway if cloning from the default
manifest URL, ``https://github.com/zephyrproject-rtos/zephyr``. Since the
zephyr repository does contain extension commands, its ``self`` entry declares
the location of the corresponding :file:`west-commands.yml` relative to the
repository root.

The pykwalify schema :file:`manifest-schema.yml` in the west source code
repository is used to validate the manifest section.

.. _west-manifest-import:

Multiple Manifests
******************

This section describes how the ``import`` key introduced above in
:ref:`west-manifest-files` lets you import projects from another manifest file
or files into your :file:`west.yml`.

.. important::

   For ease of implementation, ``import`` is currently not recursive: it's an
   error to import a manifest file which itself uses ``import``. This
   limitation will be revisited if it proves too restrictive in practice.

West builds the final list of projects and their attributes by importing
manifest files in this order:

#. imported manifests from the ``projects`` subsection
#. the top-level :file:`west.yml`
#. imported manifests from the ``self`` subsection

Manifest files later on in the order can override project attributes (like
``revision``, ``clone-depth``, etc.) from earlier manifest files.

The ``import`` key's value can be:

#. :ref:`A boolean <west-manifest-import-bool>`
#. :ref:`A relative path <west-manifest-import-path>`
#. :ref:`A mapping with additional configuration <west-manifest-import-map>`
#. :ref:`A sequence of paths and mappings <west-manifest-import-seq>`

We'll describe these options in order, with examples for different use cases:

- :ref:`west-manifest-ex1.1`
- :ref:`west-manifest-ex1.2`
- :ref:`west-manifest-ex2.1`
- :ref:`west-manifest-ex2.2`
- :ref:`west-manifest-ex2.3`
- :ref:`west-manifest-ex3.1`
- :ref:`west-manifest-ex3.2`
- :ref:`west-manifest-ex3.3`
- :ref:`west-manifest-ex4.1`
- :ref:`west-manifest-ex4.2`

A more :ref:`formal description <west-manifest-formal>` of how this works is
last, after the examples.

Troubleshooting
===============

If you're importing manifest files and find west's behavior confusing, try
running :ref:`west manifest --resolve <west-manifest-resolve>` to see the
"final" manifest and its projects.

.. _west-manifest-import-bool:

Option 1: Boolean
=================

If ``import`` is missing, it defaults to the boolean ``false`` and has no
effect. If ``true`` inside of a ``projects`` element, west imports projects
from the :file:`west.yml` file in that project's root directory.

If ``import`` is ``true`` inside the ``self`` subsection, it's ignored.

.. _west-manifest-ex1.1:

Example 1.1: Downstream of a Fixed Zephyr Release
-------------------------------------------------

You have a source code repository you want to use with Zephyr v1.14.1 LTS.  You
want to maintain the whole thing using west. You don't want to modify any of
the upstream repositories.

In other words, the west installation you want looks like this:

.. code-block:: none

   downstream
   ├── .west                      # west directory
   ├── zephyr                     # upstream zephyr repository
   ├── modules                    # modules from upstream zephyr
   │   ├── hal
   │   └── [...other directories..]
   ├── [ ... other projects ...]  # other upstream repositories
   └── my-repo                    # your downstream repository
       ├── west.yml
       └── [...other files..]

You can do this with the following :file:`my-repo/west.yml`:

.. code-block:: yaml

   # my-repo/west.yml:
   manifest:
     remotes:
       - name: zephyrproject-rtos
         url-base: https://github.com/zephyrproject-rtos
     projects:
       - name: zephyr
         remote: zephyrproject-rtos
         revision: v1.14.1
         import: true

You can then create the installation on your computer like this, assuming
``my-repo`` is hosted at ``https://git.example.com/my-repo``:

.. code-block:: console

   west init -m https://git.example.com/my-repo downstream
   cd downstream
   west update

In detail:

- ``west init`` creates a :file:`downstream` directory, clones
  ``https://git.example.com/my-repo`` inside it, and sets up a :file:`.west`
  next to the :file:`downstream/my-repo` clone.
- the first time you run ``west update``, it parses :file:`my-repo/west.yml`;
  since ``import`` is ``true`` for the ``zephyr`` project in that file, ``west
  update`` also clones all the projects in :file:`zephyr/west.yml`, such as
  ``net-tools``, at whatever revisions the zephyr v1.14.0 manifest file set
  them to.

.. _west-manifest-ex1.2:

Example 1.2: Zephyr downstream with project forks
-------------------------------------------------

This is similar to :ref:`west-manifest-ex1.1`, except with a downstream fork of
the :file:`modules/hal/nordic` repository. We also won't set a ``revision`` for
the zephyr repository in order to always get the latest from ``master``.

.. code-block:: yaml

   # Example 2, my-repo/west.yml:
   manifest:
     defaults:
       remote: my-remote
     remotes:
       - name: zephyrproject-rtos
         url-base: https://github.com/zephyrproject-rtos
       - name: my-remote
         url-base: https://git.example.com
     projects:
       - name: hal_nordic
         repo-path: nrfx
         revision: my-nrfx-branch
       - name: zephyr
         remote: zephyrproject-rtos
         import: true

With this manifest file, the project named ``hal_nordic`` is cloned from
``https://git.example.com/nrfx`` instead of
``https://github.com/zephyrproject-rtos/hal_nordic``, because the ``defaults``
subsection contains ``remote: my-remote``. The revision ``my-nrfx-branch`` is
checked out instead of any revision set in :file:`zephyr/west.yml`.

Since it's the main manifest file, west looks at the ``projects`` in
:file:`my-repo/west.yml` *after* the same section in
:file:`zephyr/west.yml`. Projects are identified by name, so your
``hal_nordic`` project configuration (in :file:`my-repo/`) overrides upstream's
(in :file:`zephyr/`).

Since ``zephyr`` has no revision set, the ``master`` branch is used by
default. Each time you run ``west update``, west fetches zephyr's latest master
branch, checks its tip out as a detached HEAD, and then imports the projects
from the newly updated :file:`zephyr/west.yml`. Any updated ``revision``
attributes in its projects will be checked out in your installation.

Your ``hal_nordic`` revision overrides upstream's. In general, the top-level
manifest file is processed after any imported manifests in its ``projects``
(but before any ``self`` imports, as will be shown below in
:ref:`west-manifest-ex2.2`), so it can override imported project attributes
like ``revision``, ``path``, etc.

.. _west-manifest-import-path:

Option 2: Relative path
=======================

The ``import`` value can also be a relative path to a manifest file or a
directory containing manifest files. The path is relative to the root directory
of the ``projects`` or ``self`` repository the ``import`` key appears in.

If the value is a file, its projects will be imported. If it is a directory,
any files inside ending in :file:`.yml` or :file:`.yaml` are sorted by name and
imported in that sorted order.

.. _west-manifest-ex2.1:

Example 2.1: Zephyr downstream with explicit path
-------------------------------------------------

This is an explicit way to write an equivalent manifest to the one in
:ref:`west-manifest-ex1.1`.

.. code-block:: yaml

   manifest:
     remotes:
       - name: zephyrproject-rtos
         url-base: https://github.com/zephyrproject-rtos
     projects:
       - name: zephyr
         remote: zephyrproject-rtos
         revision: v1.14.1
         import: west.yml

The setting ``import: west.yml`` means to use the file :file:`west.yml`
inside the ``zephyr`` project. While this example is contrived, this can be
useful when the name of the manifest file you want is not :file:`west.yml`.

.. _west-manifest-ex2.2:

Example 2.2: Downstream with directory of manifest files
--------------------------------------------------------

Your Zephyr downstream has a lot of additional repositories. So many, in fact,
that you want to split them up into multiple manifest files, but keep track of
them all in a single manifest repository, like this:

.. code-block:: none

   split-manifest/
   ├── west.d
   │   ├── 01-libraries.yml
   │   ├── 02-vendor-hals.yml
   │   └── 03-applications.yml
   └── west.yml

You want to add all the files in :file:`split-manifest/west.d` to the main
manifest file, :file:`split-manifest/west.yml`, in addition to the projects the
upstream zephyr manifest file. You want to track the latest upstream master
instead of using a fixed revision.

Here's how:

.. code-block:: yaml

   # split-manifest/west.yml:
   manifest:
     remotes:
       - name: zephyrproject-rtos
         url-base: https://github.com/zephyrproject-rtos
     projects:
       - name: zephyr
         remote: zephyrproject-rtos
         import: true
     self:
       import: west.d

The above implies projects are imported from files in this order:

#. :file:`zephyr/west.yml`
#. :file:`split-manifest/west.yml`
#. :file:`split-manifest/west.d/01-libraries.yml`
#. :file:`split-manifest/west.d/02-vendor-hals.yml`
#. :file:`split-manifest/west.d/03-applications.yml`

.. note::

   The :file:`.yml` file names are prefixed with numbers to make sure they are
   imported in the desired order relative to each other. Numbers are not
   mandatory; you can name the files whatever you want.

Notice how the manifests in :file:`west.d` are imported *after*
:file:`zephyr/west.yml` and :file:`split-manifest/west.yml` are processed.
This means files in :file:`split-manifest/west.d` override any project
attributes in these earlier files. For example, :file:`01-libraries.yml` can
fetch from your fork of a project in :file:`zephyr/west.yml` if you want to use
a different version.

In general, an ``import`` in the ``self`` section is processed after the
manifest files named in ``projects`` and the main manifest file. This may
seem strange, but allows you to override attributes "after the fact", as
we'll see in the next example.

.. _west-manifest-ex2.3:

Example 2.3: Continuous Integration overrides
---------------------------------------------

Your continuous integration system needs to fetch and test multiple
repositories in your west installation from a developer's forks instead of your
mainline development trees, to see if the changes all work well together.

Starting with :ref:`west-manifest-ex2.2`, the CI scripts add a
file :file:`99-ci.yml` in :file:`split-manifest/west.d`, with these contents:

.. code-block:: yaml

   # split-manifest/west.d/99-ci.yml:
   manifest:
     projects:
       - name: a-vendor-hal
         url: https://github.com/a-developer/hal
         revision: a-pull-request-branch
       - name: an-application
         url: https://github.com/a-developer/application
         revision: another-pull-request-branch

The CI scripts run ``west update`` after generating this file in
:file:`split-manifest/west.d`. The project attributes in :file:`99-ci.yml`
override those set in any other files in :file:`split-manifest/west.d`, because
the name :file:`99-ci.yml` comes after the other files in that directory when
sorted by name. This checks out the developer's branches in the projects named
``a-vendor-hal`` and ``an-application``.

.. _west-manifest-import-map:

Option 3: Mapping
=================

The ``import`` key can also contain a mapping with the following keys:

- ``file``: The name of the manifest file or directory. This defaults
  to :file:`west.yml` if not present.
- ``whitelist``: Optional. This can be a sequence of project names to include,
  or a mapping with ``names`` and ``paths`` keys that contain sequences of
  projects to include. Projects are added in the order specified, regardless of
  the order they appear in the named file or files, with ``names`` coming
  before ``paths`` (i.e. in alphabetic order by key). If you need a different
  order, use a sequence of mappings.
- ``blacklist``: Optional, projects to exclude. This takes the same
  values as ``whitelist``. If both are given, ``blacklist`` is applied
  before ``whitelist``.
- ``list-syntax``: Optional, specifies how the elements in ``whitelist`` and
  ``blacklist`` should be interpreted. The default value is ``literal``,
  meaning to interpret each element as the actual name of a project. It can
  also be ``re`` to treat them as regular expressions, and ``glob`` to treat
  them as shell globs. The Python `re`_ module's regular expression syntax is
  currently used.
- ``rename``: Optional mapping of ``from: to`` keys, specifying that the
  project named ``from`` should be renamed ``to`` in the final manifest.

.. _west-manifest-ex3.1:

Example 3.1: Downstream with project whitelist
----------------------------------------------

Here is a pair of manifest files, representing an upstream and a
downstream. The downstream doesn't want to use all the upstream
projects, however. We'll assume the upstream :file:`west.yml` is
hosted at ``https://git.example.com/upstream/manifest``.

.. code-block:: yaml

   # upstream west.yml:
   manifest:
     projects:
       - name: app
         url: https://git.example.com/upstream/app
       - name: library
         url: https://git.example.com/upstream/library
         revision: refs/heads/only-in-upstream
       - name: library2
         url: https://git.example.com/upstream/library-2
       - name: unnecessary-project
         url: https://git.example.com/upstream/unnecessary-project

   # downstream west.yml:
   manifest:
     projects:
       - name: upstream
         url: https://git.example.com/upstream/manifest
         import:
           whitelist:
             - library2
             - app
           rename:
             app: upstream-app
       - name: library2
         path: upstream-lib2
       - name: app
         url: https://git.example.com/downstream/app
       - name: library
         url: https://git.example.com/downstream/library

An equivalent manifest in a single file would be:

.. code-block:: yaml

   manifest:
     projects:
       - name: library2
         path: upstream-lib2
         url: https://git.example.com/upstream/library-2
       - name: upstream-app
         url: https://git.example.com/upstream/app
       - name: upstream
         url: https://git.example.com/upstream/manifest
       - name: app
         url: https://git.example.com/downstream/app
       - name: library
         url: https://git.example.com/downstream/library

If a whitelist had not been used:

- The ``library`` project in the final manifest would have had its ``revision``
  set to ``refs/heads/only-in-upstream``.
- ``unnecessary-project`` would have been imported.

.. _west-manifest-ex3.2:

Example 3.2: Another whitelist example
--------------------------------------

Here is an example showing how to whitelist upstream's libraries only,
using ``glob`` syntax.

.. code-block:: yaml

   # upstream west.yml:
   manifest:
     projects:
       - name: app
         url: https://git.example.com/upstream/app
       - name: library
         url: https://git.example.com/upstream/library
         revision: refs/heads/only-in-upstream
       - name: library2
         url: https://git.example.com/upstream/library-2
       - name: unnecessary-project
         url: https://git.example.com/upstream/unnecessary-project

   # downstream west.yml:
   manifest:
     projects:
       - name: upstream
         url: https://git.example.com/upstream/manifest
         import:
           whitelist: library*
           list-syntax: glob
       - name: app
         url: https://git.example.com/downstream/app

An equivalent manifest in a single file would be:

.. code-block:: yaml

   manifest:
     projects:
       - name: library
         url: https://git.example.com/upstream/library
       - name: library2
         url: https://git.example.com/upstream/library-2
       - name: upstream
         url: https://git.example.com/upstream/manifest
       - name: app
         url: https://git.example.com/downstream/app

.. _west-manifest-ex3.3:

Example 3.3: Downstream with blacklist by path
----------------------------------------------

Here's an example showing how to blacklist all vendor HALs from upstream by
common path prefix in the installation, add your own version for the chip
you're targeting, and keep everything else.

.. code-block:: yaml

   # upstream west.yml:
   manifest:
     defaults:
       remote: upstream
     remotes:
       - name: upstream
         url-base: https://git.example.com/upstream
     projects:
       - name: app
       - name: library
       - name: library2
       - name: foo
         path: modules/hals/foo
       - name: bar
         path: modules/hals/bar
       - name: baz
         path: modules/hals/baz

   # downstream west.yml:
   manifest:
     projects:
       - name: upstream
         url: https://git.example.com/upstream/manifest
         import:
           blacklist:
             paths:
               - modules/hals/*
           list-syntax: glob
       - name: foo
         url: https://git.example.com/downstream/foo

An equivalent manifest in a single file would be:

.. code-block:: yaml

   manifest:
     projects:
       - name: app
         url: https://git.example.com/upstream/app
       - name: library
         url: https://git.example.com/upstream/library
       - name: library2
         url: https://git.example.com/upstream/library-2
       - name: upstream
         url: https://git.example.com/upstream/manifest
       - name: foo
         path: modules/hals/foo
         url: https://git.example.com/downstream/foo

Note that the ``path`` attribute for the ``foo`` project was last set by the
upstream west.yml; this determines its final value.

.. _west-manifest-import-seq:

Option 4: Sequence of Files, Directories, and Mappings
======================================================

The ``import`` key can also contain a sequence of files, directories,
and mappings.

.. _west-manifest-ex4.1:

Example 4.1: Downstream with sequence of manifest files
-------------------------------------------------------

This example manifest is equivalent to the manifest in
:ref:`west-manifest-ex2.2`, with a sequence of explicitly named files.

.. code-block:: yaml

   manifest:
     projects:
       - name: zephyr
         url: https://github.com/zephyrproject-rtos/zephyr
         import: west.yml
     self:
       import:
         - west.d/01-libraries.yml
         - west.d/02-vendor-hals.yml
         - west.d/03-applications.yml

.. _west-manifest-ex4.2:

Example 4.2: Import order illustration
--------------------------------------

A contrived example combining several possibilities to illustrate the order
that manifest files are processed:

.. code-block:: yaml

   # kitchen-sink/west.yml
   manifest:
     remotes:
       - name: my-remote
         url-base: https://git.example.com
     projects:
       - name: my-library
       - name: my-app
       - name: zephyr
         url: https://github.com/zephyrproject-rtos/zephyr
         import: true
       - name: another-upstream-manifest
         url: https://git.example.com/another-upstream-manifest
         import: west.d
     self:
       import:
         - west.d/01-libraries.yml
         - west.d/02-vendor-hals.yml
         - west.d/03-applications.yml
     defaults:
       remote: my-remote

In the above example, the manifest files are processed in the following order:

#. :file:`zephyr/west.yml` is first, since it's the first file named in a
   project's ``import`` key
#. the files in :file:`another-upstream-manifest/west.d` are next (sorted by
   file name), since that's the next project's ``import``
#. :file:`kitchen-sink/west.yml` follows (with projects ``my-library`` etc.);
   the main manifest comes after imported files in ``projects``
#. files in :file:`kitchen-sink/west.d` follow, ordered by name, as the
   ``import`` key in a ``self`` subsection is always processed last to
   allow for final overrides

.. _west-manifest-formal:

Manifest Import Details
=======================

This section describes how west imports a manifest file a bit more formally.

In a manifest file, the ``self`` section and any element in the ``projects``
section can have an ``import`` key, whose value can be a boolean, path,
map, or sequence of these as described above.

West sorts and processes files named by these ``import`` keys in this order:

#. The ``import`` keys in ``projects`` are considered first. They are processed
   in the order they appear in the ``projects`` sequence.
#. The main :file:`west.yml` manifest file and its projects are processed next.
#. The file or files named by the ``import`` key in the ``self`` subsection of
   the main manifest file are processed last.

An individual ``import`` attribute may name multiple manifest files to
import. For example, this happens when the value is a sequence or a relative
path to a directory. Files in directories are processed in lexicographic
order. Sequence elements are processed in the order in which they appear. Since
the order of files in the final list is well defined, project attribute
overrides are as well.

.. note::

   This only defines the order that imported *manifest files* are handled in.
   It does not define the order projects appear in the output of commands like
   ``west list``.

Once the list of all the manifest files is ready, west repeatedly imports each
one into the next, starting from an "empty" manifest which contains no
projects.

Here's some Python-like pseudocode explaining the idea:

.. code-block:: python

   def combine_projects(p1, p2):
       '''Combine projects p1 and p2; p1's settings "win".

       See below for an explanation of the "|" notation and N, U, R,
       etc. attributes.'''
       name = p1.N return (name, (p2.U | p1.U), (p2.R |
       p1.R), (p2.P | p1.P), (p2.CD | p1.CD), (p2.WC | p1.WC))

   def import_manifest(m1, m2):
       '''Import manifest m2 into m1 and return the resulting projects.

       Project attributes set in m1 override values set in m2.'''
       result = m1.projects.copy()
       for m2p in m2.projects:
           name = m2p.N
           if name in result.names:
               m1p = result[name]
               result[name] = combine_projects(m1p, m2p)
           else:
               result.add(m2p)
       return result

   def get_imports_in_order(manifest):
       '''Get a list of manifest files to process.

       The returned files are ordered by increasing precedence.'''
       result = []
       result.extend(p.imports for p in manifest.projects)
       result.append(manifest)
       result.extend(manifest.self.imports)
       return result

   def process_imports(manifest):
       current_manifest = empty_manifest
       for f in get_import_files_in_order(manifest):
           current_manifest = import_manifest(f, current_manifest)
       return current_manifest

For the purposes of this section, we'll treat a manifest as a sequence of
projects, where each project is a simple tuple:

.. code-block:: none

   project = (name, url, revision, path, clone-depth, west-commands)

We'll write ``P = (N, U, R, P, CD, WC)`` as a shorter way to say the same
thing. That is, we won't bother remembering anything about the project's
``remote`` key in the manifest file, if there is one. That's only used to form
the project's URL, which is what we really care about. We'll also apply any
``defaults`` values in the file when forming each each project tuple.

Some project information (``R``, ``P``, ``CD``, and ``WC`` in this notation) is
optional in manifest files. We'll use an underscore (``_``) to denote a missing
key in the tuple notation (assuming it doesn't have a value in
``defaults``). For example, we'll represent this manifest:

.. code-block:: yaml

   manifest:
     projects:
       - name: my-name
         url: https://git.example.com/repo-url
         path: modules/my-name

As if it contained just this tuple in the projects list:

.. code-block:: none

   (my-name, https://git.example.com/repo-url, _, modules/my-name, _, _)

If an attribute is missing but has a default, we'll apply it to the
corresponding tuple. For example, we'll represent this manifest:

.. code-block:: yaml

   manifest:
     defaults:
       revision: master
     projects:
       - name: my-name
         url: https://git.example.com/repo-url
         path: modules/my-name

As if it contained this tuple:

.. code-block:: none

   (my-name, https://git.example.com/repo-url, master, modules/my-name, _, _)

When manifest ``M1`` imports another manifest ``M2``, two projects with the
same name in both are combined into a single project. Any project attributes in
``M2`` are overridden if set in ``M1``.

We'll use a pipe (``|``) as a binary operator that defines how individual
project attributes are combined. For example, ``R1 | R2`` is the revision when
two project tuples with revisions ``R1`` and ``R2`` are combined. A missing
key's value, ``_``, is an identity element with respect to ``|``, and ``R1 |
R2`` is ``R2`` for non-identity values ``R1`` and ``R2``. That is, for
non-identity elements ``R``, ``R1``, and ``R2``:

.. code-block:: none

   R  | _  == R
   _  | R  == R
   _  | _  == _
   R1 | R2 == R2

The same operator can be used to combine ``U``, ``P``, ``CD``, and ``WC``
values.

Note that the ``|`` operator is associative for any attribute ``A``: ``(A1 |
A2) | A3 == A1 | (A2 | A3)``.

Let's put these pieces together to describe how west handles a project that
appears in both ``M1`` and ``M2``. If ``M1`` contains this project tuple:

.. code-block:: none

   (N, U1, R1, P1, CD1, WC1)

And ``M2`` contains this one:

.. code-block:: none

   (N, U2, R2, P2, CD2, WC2)

Then the combined project is:

.. code-block:: none

   (N, U1 | U2, R1 | R2, P1 | P2, CD1 | CD2, WC1 | WC2)

(A ``west`` subsection in a manifest, if present, can be treated as if it
defines a project tuple ``(west, U, R, .west/west, _, _)``. The same type of
rule applies if both manifest files have a ``west`` section: the last ``url``
or ``revision`` in an input file is the value used in the output file.)

The indexes of projects in the combined manifest are not specified. For example,
if ``M1`` looks like this:

.. code-block:: yaml

   manifest:
     projects:
       - name: p1
         url: https://git.example.com/p1
         revision: p1-tag
         path: modules/p1
       - name: p2
         url: https://git.example.com/p2
         revision: p2-tag

And ``M2`` looks like this:

.. code-block:: yaml

   manifest:
     projects:
       - name: p1
         url: git@example.com:p1
       - name: p3
         url: git@example.com:p3
         revision: topic-branch

Then a representation of the final manifest where ``M1`` imports ``M2`` could
be:

.. code-block:: yaml

   manifest:
     projects:
       - name: p3
         url: git@example.com:p3
         revision: topic-branch
       - name: p2
         url: https://git.example.com/p2
         revision: c001ca7
       - name: p1
         url: git@example.com:p1
         revision: p1-tag
         path: modules/p1

The above guarantees:

#. All projects in any imported manifest file are present in the output.
#. If a project appears in multiple imported manifest files, its attributes are
   given by the last manifest in the import order that sets them.

.. _west-manifest-cmd:

Manifest Command
****************

The ``west manifest`` command can be used to manipulate manifest files.
It takes an action, and action-specific arguments.

The following sections describe each action and provides a basic signature for
simple uses. Run ``west manifest --help`` for full details on all options.

Freezing Manifests
==================

The ``--freeze`` action outputs a frozen manifest:

.. code-block:: none

   west manifest --freeze [-o outfile]

A "frozen" manifest is a manifest file where every project's revision is a SHA.
You can use ``--freeze`` to produce a frozen manifest that's equivalent to your
current manifest file. The ``-o`` option specifies an output file; if not
given, standard output is used.

.. _west-manifest-resolve:

Resolving Manifests
===================

The ``--resolve`` action outputs a single manifest file equivalent to your
current manifest and all its :ref:`imported manifests <west-manifest-import>`:

.. code-block:: none

   west manifest --resolve [-o outfile]

The main use for this action is to see the "final" manifest contents after
performing any ``import``\ s.
