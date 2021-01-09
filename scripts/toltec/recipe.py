# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""
Load and execute recipes.

A package is a final user-installable software archive. A recipe is a Bash file
which contains the instructions necessary to build one or more related
packages (in the latter case, it is called a split package).
"""

from . import bash
from itertools import product
from typing import Any


class InvalidRecipeError(Exception):
    pass


class Recipe:
    def __init__(self, name: str, source: str):
        """
        Load a recipe from a Bash source.

        :param name: name of the recipe
        :param source: source string of the recipe
        :raises InvalidRecipeError: if the recipe contains an error
        """
        declarations = bash.get_declarations(source)
        variables, functions = declarations

        self.name = name
        self.header = _read_recipe_header(variables)
        self.packages = {}

        if len(self.header['pkgnames']) == 1:
            name = self.header['pkgnames'][0]
            self.packages[name] = Package(name, declarations, source)
        else:
            for name in self.header['pkgnames']:
                if name not in functions:
                    raise InvalidRecipeError('Missing required function \
{name}() for corresponding package')

                self.packages[name] = Package(name, declarations,
                        functions[name])

        self.actions = {}

        if self.header['image'] is not None and 'build' not in functions:
            raise InvalidRecipeError('Missing build() function for a recipe \
which declares a build image')

        if self.header['image'] is None and 'build' in functions:
            raise InvalidRecipeError('Missing image declaration for a recipe \
which has a build() step')

        self.actions['build'] = functions.get('build', '')
        self.actions['prepare'] = functions.get('prepare', '')

    @classmethod
    def from_file(cls, name: str, path: str) -> 'Recipe':
        """Load a recipe from a file."""
        with open(path, 'r') as recipe:
            return Recipe(name, recipe.read())

    def control(self) -> str:
        """Get the recipe-wide control fields."""
        return f"Maintainer: {self.header['maintainer']}\n"


class Package:
    def __init__(
        self,
        name: str,
        parent_declarations: tuple[bash.Variables, bash.Functions],
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
        self.header = _read_package_header(variables)

        if 'package' not in functions:
            raise InvalidRecipeError('Missing required function package() \
for package {self.name}')

        self.action = functions['package']
        self.install = {}

        for rel, step in product(('pre', 'post'), ('remove', 'upgrade')):
            self.install[rel + step] = functions.get(rel + step, '')

    def id(self) -> str:
        """Get the unique identifier of this package."""
        return '_'.join((self.name, self.header['pkgver'], self.header['arch']))

    def filename(self) -> str:
        """Get the name of the archive corresponding to this package."""
        return self.id() + '.ipk'

    def control(self):
        """Get the package-specific control fields."""
        control = f'''Package: {self.name}
Version: {self.header['pkgver']}
Section: {self.header['section']}
Architecture: {self.header['arch']}
Description: {self.header['pkgdesc']}
HomePage: {self.header['url']}
License: {self.header['license']}
'''

        if self.header['depends']:
            control += f"Depends: {', '.join(self.header['depends'])}\n"

        if self.header['conflicts']:
            control += f"Conflicts: {', '.join(self.header['conflicts'])}\n"

        return control


def _check_field(
    variables: dict[str, Any], name: str,
    expected_type: type, required: bool
):
    """
    Check that a field is properly defined in a recipe.

    :param variables: set of variables declared in the recipe
    :param name: name of the field to check
    :param expected_type: if the field is defined, its expected type
    :param required: if true, requires that the field be defined
    """
    if required and name not in variables:
        raise InvalidRecipeError(f'Missing required field {name}')

    if name in variables:
        if type(variables[name]) != expected_type:
            raise InvalidRecipeError(f'Field {name} must be of type \
{expected_type.__name__}, got {type(variables[name]).__name__}')

def _read_recipe_header(variables: dict[str, Any]) -> dict[str, Any]:
    """Read and check all recipe-wide fields."""
    header = {}

    _check_field(variables, 'pkgnames', list, True)
    header['pkgnames'] = variables['pkgnames']

    _check_field(variables, 'timestamp', str, True)
    header['timestamp'] = variables['timestamp']

    _check_field(variables, 'maintainer', str, True)
    header['maintainer'] = variables['maintainer']

    _check_field(variables, 'image', str, False)
    header['image'] = variables.get('image')

    _check_field(variables, 'source', list, False)
    header['source'] = variables.get('source', [])

    _check_field(variables, 'noextract', list, False)
    header['noextract'] = variables.get('noextract', [])

    _check_field(variables, 'sha256sums', list, False)
    header['sha256sums'] = variables.get('sha256sums', [])

    return header

def _read_package_header(variables: dict[str, Any]) -> dict[str, Any]:
    """Read and check all package-specific fields."""
    header = {}

    _check_field(variables, 'pkgver', str, True)
    header['pkgver'] = variables['pkgver']

    _check_field(variables, 'arch', str, False)
    header['arch'] = variables.get('arch', 'armv7-3.2')

    _check_field(variables, 'pkgdesc', str, True)
    header['pkgdesc'] = variables['pkgdesc']

    _check_field(variables, 'url', str, True)
    header['url'] = variables['url']

    _check_field(variables, 'section', str, True)
    header['section'] = variables['section']

    _check_field(variables, 'license', str, True)
    header['license'] = variables['license']

    _check_field(variables, 'depends', list, False)
    header['depends'] = variables.get('depends', [])

    _check_field(variables, 'conflicts', list, False)
    header['conflicts'] = variables.get('conflicts', [])

    return header
