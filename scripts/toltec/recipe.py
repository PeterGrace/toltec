# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""
Load and execute recipes.

A package is a final user-installable software archive. A recipe is a Bash file
which contains the instructions necessary to build one or more related
packages (in the latter case, it is called a split package).
"""

from itertools import product
from typing import Optional
from collections.abc import Iterable
import glob
import logging
import os
import re
import shutil
import dateutil.parser
from docker.client import DockerClient
from docker.types import Mount
import requests
from . import bash, util, ipk, version

logger = logging.getLogger(__name__)

# Detect non-local paths
_URL_REGEX = re.compile(r'[a-z]+://')

# Prefix for all Toltec Docker images
_IMAGE_PREFIX = 'ghcr.io/toltec-dev/'

# Toltec Docker image used for generic tasks
_DEFAULT_IMAGE = 'base:v1.2.2'

# Contents of the Bash library for install scripts
def _read_bash_lib(path):
    result = ''

    with open(path, 'r') as file:
        for line in file:
            if not line.strip().startswith('#'):
                result += line

    return result

_INSTALL_LIB = _read_bash_lib(os.path.join(
    os.path.dirname(__file__), '..', 'install-lib'))


class InvalidRecipeError(Exception):
    """Raised when a recipe contains an error."""


class BuildError(Exception):
    """Raised when a build step fails."""


class RecipeAdapter(logging.LoggerAdapter):
    """Prefix log entries with the current recipe name."""
    def process(self, msg, kwargs):
        return '%s: %s' % (self.extra['recipe'], msg), kwargs


class Recipe: # pylint:disable=too-many-instance-attributes
    """Load and execute recipes."""
    def __init__(self, name: str, root: str, source: str):
        """
        Load a recipe from a Bash source.

        :param name: name of the recipe
        :param root: directory where the recipe is stored
        :param source: source string of the recipe
        :raises InvalidRecipeError: if the recipe contains an error
        """
        declarations = bash.get_declarations(source)
        variables, functions = declarations

        self.name = name
        self.root = root
        self._bash_variables = variables
        self._bash_functions = functions

        # Parse and check recipe metadata
        self.pkgnames = _check_field_indexed(variables, 'pkgnames')
        timestamp_str = _check_field_string(variables, 'timestamp')

        try:
            self.timestamp = dateutil.parser.isoparse(timestamp_str)
        except ValueError as err:
            raise InvalidRecipeError("Field 'timestamp' does not contain a \
valid ISO-8601 date") from err

        self.maintainer = _check_field_string(variables, 'maintainer')
        self.image = _check_field_string(variables, 'image', '')
        self.source = _check_field_indexed(variables, 'source', [])
        self.noextract = _check_field_indexed(variables, 'noextract', [])
        self.sha256sums = _check_field_indexed(variables, 'sha256sums', [])

        if len(self.source) != len(self.sha256sums):
            raise InvalidRecipeError(f"Expected the same number of sources \
and checksums, got {len(self.source)} source(s) and \
{len(self.sha256sums)} checksum(s)")

        # Parse recipe build hooks
        self.actions = {}

        if self.image and 'build' not in functions:
            raise InvalidRecipeError('Missing build() function for a recipe \
which declares a build image')

        if not self.image and 'build' in functions:
            raise InvalidRecipeError('Missing image declaration for a recipe \
which has a build() step')

        self.actions['prepare'] = functions.get('prepare', '')
        self.actions['build'] = functions.get('build', '')

        # Parse packages contained in the recipe
        self.packages = {}

        if len(self.pkgnames) == 1:
            pkg_name = self.pkgnames[0]
            self.packages[pkg_name] = Package(name, self, source)
        else:
            for pkg_name in self.pkgnames:
                if pkg_name not in functions:
                    raise InvalidRecipeError('Missing required function \
{pkg_name}() for corresponding package')

                self.packages[pkg_name] = Package(pkg_name, self,
                    functions[pkg_name])

        self.logger = RecipeAdapter(logger, {'recipe': name})


    @classmethod
    def from_file(cls, name: str, root: str) -> 'Recipe':
        """Load a recipe from a file."""
        with open(os.path.join(root, 'package'), 'r') as recipe:
            return Recipe(name, root, recipe.read())

    def make(
            self, src_dir: str, pkg_dir: str, docker: DockerClient,
            packages: Optional[Iterable[str]] = None) -> None:
        """
        Make this recipe.

        Both ``src_dir`` and ``pkg_dir`` should be existing but empty
        directories.

        :param src_dir: directory into which source files will be fetched
            and the build will happen
        :param pkg_dir: directory under which packages will be created
        :param docker: docker client to use for running the build
        :param packages: list of packages to make (default: all packages
            defined by this recipe)
        """
        self.fetch_source(src_dir)
        self.prepare(src_dir)
        self.build(src_dir, docker)
        self.strip(src_dir, docker)

        for package in packages if packages is not None \
                else self.packages.keys():
            if package not in self.packages:
                raise BuildError(f'Package {package} does not exist in \
recipe {self.name}')

            sub_pkg_dir = os.path.join(pkg_dir, package or '')
            os.makedirs(sub_pkg_dir, exist_ok=True)
            self.packages[package].package(src_dir, sub_pkg_dir)
            self.packages[package].archive(sub_pkg_dir, pkg_dir)

    def fetch_source(self, src_dir: str) -> None:
        """
        Fetch all source files required to build this recipe and automatically
        extract source archives.

        The ``src_dir`` parameter should point to an existing but empty
        directory where the source files will be saved.

        :param src_dir: directory into which source files are fetched
        """
        self.logger.info('Fetching source files')

        sources = self.source
        checksums = self.sha256sums
        noextract = self.noextract

        for source, checksum in zip(sources, checksums):
            source = source or ''
            checksum = checksum or ''

            filename = os.path.basename(source)
            local_path = os.path.join(src_dir, filename)

            if _URL_REGEX.match(source) is None:
                # Get source file from the recipe’s root
                shutil.copy2(os.path.join(self.root, source), local_path)
            else:
                # Fetch source file from the network
                req = requests.get(source)

                if req.status_code != 200:
                    raise BuildError(f"Unexpected status code while fetching \
source file '{source}', got {req.status_code}")

                with open(local_path, 'wb') as local:
                    for chunk in req.iter_content(chunk_size=1024):
                        local.write(chunk)

            # Verify checksum
            if checksum != 'SKIP' and util.file_sha256(local_path) != checksum:
                raise BuildError(f"Invalid checksum for source file {source}")

            # Automatically extract source archives
            if filename not in noextract:
                util.auto_extract(local_path, src_dir)

    def prepare(self, src_dir: str) -> None:
        """
        Prepare source files before building.

        The ``src_dir`` parameter should point to a directory containing all
        the required source files for the recipe (see :func:`fetch_source`).

        :param src_dir: directory into which source files are stored
        """
        if not self.actions['prepare']:
            self.logger.info('Skipping prepare (nothing to do)')
            return

        self.logger.info('Preparing source files')

        logs = bash.run_script(
            variables={**self._bash_variables, 'srcdir': src_dir},
            script=self.actions['prepare'])

        for line in logs:
            self.logger.debug(line)

    def build(self, src_dir: str, docker: DockerClient) -> None:
        """
        Build source files.

        The ``src_dir`` parameter should point to a directory containing all
        the required source files for the recipe (see :func:`fetch_source`
        and :func:`prepare`).

        :param src_dir: directory into which source files are stored
        :param docker: docker client to use for running the build
        """
        if not self.actions['build']:
            self.logger.info('Skipping build (nothing to do)')
            return

        self.logger.info('Building artifacts')
        mount_src = '/src'
        uid = os.getuid()

        logs = bash.run_script_in_container(
            docker, image=_IMAGE_PREFIX + self.image,
            mounts=[Mount(
                type='bind',
                source=os.path.abspath(src_dir),
                target=mount_src)],
            variables={**self._bash_variables, 'srcdir': mount_src},
            script='\n'.join((
                f'cd "{mount_src}"',
                self.actions['build'],
                f'chown -R {uid}:{uid} "{mount_src}"',
            )))

        for line in logs:
            self.logger.debug(line)

    def strip(self, src_dir: str, docker: DockerClient) -> None:
        """
        Strip all debugging symbols from binaries.

        The ``src_dir`` parameter should point to a directory containing all
        the build artifacts from the recipe (see :func:`fetch_source`,
        :func:`prepare`, and :func:`build`).

        :param src_dir: directory into which source files were compiled
        :param docker: docker client to use for stripping
        """
        self.logger.info('Stripping binaries')
        mount_src = '/src'

        logs = bash.run_script_in_container(
            docker, image=_IMAGE_PREFIX + _DEFAULT_IMAGE,
            mounts=[Mount(
                type='bind',
                source=os.path.abspath(src_dir),
                target=mount_src)],
            variables={},
            script='\n'.join((
                # Strip binaries in the target arch
                f'find "{mount_src}" -type f -executable -print0 \
| xargs --no-run-if-empty --null "${{CROSS_COMPILE}}strip" --strip-all || true',
                # Strip binaries in the host arch
                f'find "{mount_src}" -type f -executable -print0 \
| xargs --no-run-if-empty --null strip --strip-all || true',
            )))

        for line in logs:
            self.logger.debug(line)


class PackageAdapter(logging.LoggerAdapter):
    """Prefix log entries with the current package name."""
    def process(self, msg, kwargs):
        return '%s (%s): %s' % (
            self.extra['package'],
            self.extra['recipe'],
            msg
        ), kwargs


class Package: # pylint:disable=too-many-instance-attributes
    """Load and execute a package from a recipe."""
    def __init__(self, name: str, parent: Recipe, source: str):
        """
        Load a package from a Bash source.

        :param name: name of the package
        :param parent: recipe which declares this package
        :param source: source string of the package (either the full recipe
            script if it contains only a single package, or the package
            script for split packages)
        :raises InvalidRecipeError: if the package contains an error
        """
        variables, functions = bash.get_declarations(source)
        variables = {**parent._bash_variables, **variables}
        functions = {**parent._bash_functions, **functions}

        self.name = name
        self.parent = parent
        self._bash_variables = variables
        self._bash_functions = functions

        # Parse and check package metadata
        pkgver_str = _check_field_string(variables, 'pkgver')
        self.pkgver = version.Version.parse(pkgver_str)

        self.arch = _check_field_string(variables, 'arch', 'armv7-3.2')
        self.pkgdesc = _check_field_string(variables, 'pkgdesc')
        self.url = _check_field_string(variables, 'url')
        self.section = _check_field_string(variables, 'section')
        self.license = _check_field_string(variables, 'license')
        self.depends = _check_field_indexed(variables, 'depends', [])
        self.conflicts = _check_field_indexed(variables, 'conflicts', [])

        if 'package' not in functions:
            raise InvalidRecipeError('Missing required function package() \
for package {self.name}')

        self.action = functions['package']
        self.install = {}

        for action in ('preinstall', 'configure'):
            self.install[action] = functions.get(action, '')

        for rel, step in product(('pre', 'post'), ('remove', 'upgrade')):
            self.install[rel + step] = functions.get(rel + step, '')

        self.logger = PackageAdapter(logger, {
            'recipe': parent.name,
            'package': self.pkgid()})

    def pkgid(self) -> str:
        """Get the unique identifier of this package."""
        return '_'.join((self.name, str(self.pkgver), self.arch))

    def filename(self) -> str:
        """Get the name of the archive corresponding to this package."""
        return self.pkgid() + '.ipk'

    def control_fields(self) -> str:
        """Get the control fields for this package."""
        control = f'''Package: {self.name}
Version: {self.pkgver}
Maintainer: {self.parent.maintainer}
Section: {self.section}
Architecture: {self.arch}
Description: {self.pkgdesc}
HomePage: {self.url}
License: {self.license}
'''

        if self.depends:
            control += f"Depends: \
                {', '.join(item for item in self.depends if item)}\n"

        if self.conflicts:
            control += f"Conflicts: \
                {', '.join(item for item in self.conflicts if item)}\n"

        return control

    def package(self, src_dir: str, pkg_dir: str) -> None:
        """
        Make the package structure from existing build artifacts.

        The ``src_dir`` parameter should point to a directory containing all
        build artifacts from the parent recipe (see :func:`Package.build`).
        The ``pkg_dir`` parameter should point to an empty directory where the
        package structure will be constructed.

        :param src_dir: directory into which source files are stored
        :param pkg_dir: directory into which the package shall be constructed
        """
        self.logger.info('Packaging build artifacts')

        logs = bash.run_script(
            variables={
                **self._bash_variables,
                'srcdir': src_dir,
                'pkgdir': pkg_dir},
            script=self.action)

        for line in logs:
            self.logger.debug(line)

        self.logger.debug('Resulting tree:')

        for filename in glob.iglob(pkg_dir + '/**/*', recursive=True):
            self.logger.debug(' - %s', filename.removeprefix(pkg_dir))

    def archive(self, pkg_dir: str, ar_dir: str) -> None:
        """
        Create an archive for this package.

        :param pkg_dir: directory where the package structure is located
            (see :func:`package`)
        :param ar_dir: directory into which the resulting archive will be saved
        """
        self.logger.info('Creating archive')
        ar_path = os.path.join(ar_dir, self.filename())

        # Convert install scripts to Debian format
        scripts = {}
        script_header = f'''\
#!/usr/bin/env bash
set -e
{bash.put_variables(self._bash_variables)}
{_INSTALL_LIB}
'''

        for name, script, action in (
                ('preinstall', 'preinst', 'install'),
                ('configure', 'postinst', 'configure')):
            if self.install[name]:
                scripts[script] = f'''\
{script_header}
if [[ $1 = {action} ]]; then
    fun() {{
    {self.install[name]}
    }}
    fun
fi
'''
        for step in ('pre', 'post'):
            if self.install[step + 'upgrade'] or self.install[step + 'remove']:
                script = script_header

                for action in ('upgrade', 'remove'):
                    if self.install[step + action]:
                        script += f'''\
if [[ $1 = {action} ]]; then
    fun() {{
    {self.install[step + action]}
    }}
    fun
fi
'''

                scripts[step + 'rm'] = script

        self.logger.debug('Install scripts:')

        if scripts:
            for script in scripts:
                self.logger.debug(' - %s', script)
        else:
            self.logger.debug('(none)')

        epoch = int(self.parent.timestamp.timestamp())

        with open(ar_path, 'wb') as file:
            ipk.make_ipk(
                file, epoch=epoch, pkg_dir=pkg_dir,
                metadata=self.control_fields(),
                scripts=scripts)

        # Set fixed mtime for the resulting archive
        os.utime(ar_path, (epoch, epoch))


# Helpers to check that fields of the right type are defined in a recipe
# and to otherwise return a default value
def _check_field_string(
    variables: bash.Variables, name: str,
    default: Optional[str] = None
) -> str:
    if name not in variables:
        if default is None:
            raise InvalidRecipeError(f'Missing required field {name}')
        return default

    value = variables[name]

    if not isinstance(value, str):
        raise InvalidRecipeError(f"Field {name} must be a string, \
got {type(variables[name]).__name__}")

    return value


def _check_field_indexed(
    variables: bash.Variables, name: str,
    default: Optional[bash.IndexedArray] = None
) -> bash.IndexedArray:
    if name not in variables:
        if default is None:
            raise InvalidRecipeError(f"Missing required field '{name}'")
        return default

    value = variables[name]

    if not isinstance(value, list):
        raise InvalidRecipeError(f"Field '{name}' must be an indexed array, \
got {type(variables[name]).__name__}")

    return value
