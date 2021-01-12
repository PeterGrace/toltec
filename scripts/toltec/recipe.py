# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""
Load and execute recipes.

A package is a final user-installable software archive. A recipe is a Bash file
which contains the instructions necessary to build one or more related
packages (in the latter case, it is called a split package).
"""

from itertools import product
from typing import Tuple, Optional
import logging
import os
import re
import shutil
import subprocess
from docker.client import DockerClient
from docker.types import Mount
import requests
from . import bash, util

logger = logging.getLogger(__name__)
url_regex = re.compile(r'[a-z]+://')
image_prefix = 'ghcr.io/toltec-dev/'
default_image = 'base:v1.2.2'


class InvalidRecipeError(Exception):
    pass


class BuildError(Exception):
    pass


class RecipeAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return '%s: %s' % (self.extra['recipe'], msg), kwargs


class Recipe:
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
        self.logger = RecipeAdapter(logger, {'recipe': name})
        self._bash_variables = variables

        # Parse and check recipe metadata
        self.pkgnames = _check_field_indexed(variables, 'pkgnames')
        self.timestamp = _check_field_string(variables, 'timestamp')
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
            self.packages[pkg_name] = Package(name, declarations, source)
        else:
            for pkg_name in self.pkgnames:
                if pkg_name not in functions:
                    raise InvalidRecipeError('Missing required function \
{pkg_name}() for corresponding package')

                self.packages[pkg_name] = Package(pkg_name, declarations,
                        functions[pkg_name])


    @classmethod
    def from_file(cls, name: str, root: str) -> 'Recipe':
        """Load a recipe from a file."""
        with open(os.path.join(root, 'package'), 'r') as recipe:
            return Recipe(name, root, recipe.read())

    def control_fields(self) -> str:
        """Get the recipe-wide control fields."""
        return f"Maintainer: {self.maintainer}\n"

    def fetch_source(self, src_dir: str) -> None:
        """
        Fetch all source files required to build this recipe and automatically
        extract source archives.

        :param src_dir: directory into which source files are fetched
        """
        self.logger.info('Fetching source files')
        os.makedirs(src_dir)

        sources = self.source
        checksums = self.sha256sums
        noextract = self.noextract

        for i in range(len(sources)):
            source = sources[i] or ''
            checksum = checksums[i] or ''

            filename = os.path.basename(source)
            local_path = os.path.join(src_dir, filename)

            if url_regex.match(source) is None:
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
            self.logger.debug(line.decode().strip())

    def build(self, src_dir: str, docker: DockerClient) -> None:
        """
        Build source files.

        :param src_dir: directory into which source files are stored
        :param docker: docker client to use for running the build
        """
        if not self.actions['build']:
            self.logger.info('Skipping build (nothing to do)')
            return

        self.logger.info('Building binaries')
        mount_src = '/src'
        uid = os.getuid()

        logs = bash.run_script_in_container(
            docker, image=image_prefix + self.image,
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
            self.logger.debug(line.decode().strip())

    def strip(self, src_dir: str, docker: DockerClient) -> None:
        """
        Strip all debugging symbols from binaries.

        :param src_dir: directory into which source files were compiled
        :param docker: docker client to use for stripping
        """
        self.logger.info('Stripping binaries')
        mount_src = '/src'

        logs = bash.run_script_in_container(
            docker, image=image_prefix + default_image,
            mounts=[Mount(
                type='bind',
                source=os.path.abspath(src_dir),
                target=mount_src)],
            variables={},
            script='\n'.join((
                # Strip binaries in the target arch
                f'find "{mount_src}" -type f -executable -print0 \
| xargs --null "${{CROSS_COMPILE}}strip" --strip-all || true',
                # Strip binaries in the host arch
                f'find "{mount_src}" -type f -executable -print0 \
| xargs --null strip --strip-all || true',
            )))

        for line in logs:
            self.logger.debug(line.decode().strip())


class Package:
    def __init__(
        self,
        name: str,
        parent_declarations: Tuple[bash.Variables, bash.Functions],
        source: str
    ):
        """
        Load a package from a Bash source.

        :param name: name of the package
        :param parent_declarations: variables and functions from the recipe
            which declares this package
        :param source: source string of the package (either the full recipe
            script if it contains only a single package, or the package
            script for split packages)
        :raises InvalidRecipeError: if the package contains an error
        """
        parent_variables, parent_functions = parent_declarations
        variables, functions = bash.get_declarations(source)
        variables = {**parent_variables, **variables}
        functions = {**parent_functions, **functions}

        self.name = name
        self._bash_variables = variables

        # Parse and check package metadata
        self.pkgver = _check_field_string(variables, 'pkgver')
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

        for rel, step in product(('pre', 'post'), ('remove', 'upgrade')):
            self.install[rel + step] = functions.get(rel + step, '')

    def id(self) -> str:
        """Get the unique identifier of this package."""
        return '_'.join((self.name, self.pkgver, self.arch))

    def filename(self) -> str:
        """Get the name of the archive corresponding to this package."""
        return self.id() + '.ipk'

    def control_fields(self) -> str:
        """Get the package-specific control fields."""
        control = f'''Package: {self.name}
Version: {self.pkgver}
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
            raise InvalidRecipeError(f'Missing required field {name}')
        return default

    value = variables[name]

    if not isinstance(value, list):
        raise InvalidRecipeError(f"Field {name} must be an indexed array, \
got {type(variables[name]).__name__}")

    return value
