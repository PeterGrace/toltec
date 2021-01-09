#!/usr/bin/env python3

from itertools import product
import shlex
import subprocess

def _parse_string(token):
    return token.replace('\\$', '$')

def _parse_array(lexer):
    assert lexer.get_token() == '('
    result = []

    while True:
        token = lexer.get_token()
        assert token != lexer.eof

        if token == ')':
            break

        assert token == '['
        index = int(lexer.get_token())
        assert lexer.get_token() == ']'
        assert lexer.get_token() == '='
        value = _parse_string(lexer.get_token())

        # Grow the result array so that the index exists
        if index >= len(result):
            result.extend([None] * (index - len(result) + 1))

        result[index] = value

    return result

def _parse_dict(lexer):
    assert lexer.get_token() == '('
    result = {}

    while True:
        token = lexer.get_token()
        assert token != lexer.eof

        if token == ')':
            break

        assert token == '['
        key = lexer.get_token()
        assert lexer.get_token() == ']'
        assert lexer.get_token() == '='
        value = _parse_string(lexer.get_token())

        result[key] = value

    return result

def _parse_var(lexer):
    flags_token = lexer.get_token()

    if flags_token != '--':
        var_flags = set(flags_token[1:])
    else:
        var_flags = set()

    var_name = lexer.get_token()
    lookahead = lexer.get_token()

    if lookahead == '=':
        if 'a' in var_flags:
            var_value = _parse_array(lexer)
        elif 'A' in var_flags:
            var_value = _parse_dict(lexer)
        else:
            var_value = _parse_string(lexer.get_token())
    else:
        lexer.push_token(lookahead)
        var_value = None

    return var_name, var_value

def _parse_func(lexer):
    assert lexer.get_token() == '{'
    brace_depth = 1

    start_byte = lexer.instream.tell()

    while brace_depth > 0:
        token = lexer.get_token()
        assert token != lexer.eof

        if token == '{':
            brace_depth += 1
        elif token == '}':
            brace_depth -= 1

    end_byte = lexer.instream.tell() - 1
    return start_byte, end_byte

def _get_declarations(src):
    # Run the script and ask for all declared functions and variables
    src += '''
declare -f
declare -p
'''

    declarations_subshell = subprocess.run(
        ['/usr/bin/env', 'bash'],
        input=src.encode(),
        capture_output=True,
        env={})

    declarations = declarations_subshell.stdout.decode()

    # Parse `declare` statements and function statements
    lexer = shlex.shlex(declarations, posix=True)
    lexer.wordchars = lexer.wordchars + '-'

    variables = {}
    functions = {}

    while True:
        token = lexer.get_token()

        if token == lexer.eof:
            break

        next_token = lexer.get_token()

        if token == 'declare' and next_token[0] == '-':
            lexer.push_token(next_token)
            name, value = _parse_var(lexer)
            variables[name] = value
        else:
            assert next_token == '('
            assert lexer.get_token() == ')'
            start, end = _parse_func(lexer)
            functions[token] = declarations[start:end]

    return variables, functions

class InvalidRecipeError(Exception):
    pass

def _check_field(variables, name, expected_type, required):
    if required and name not in variables:
        raise InvalidRecipeError(f'Missing required field {name}')

    if name in variables:
        if type(variables[name]) != expected_type:
            raise InvalidRecipeError(f'Field {name} must be of type \
{expected_type.__name__}, got {type(variables[name]).__name__}')

def _read_recipe_header(variables):
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

class Recipe:
    def __init__(self, name, source):
        declarations = _get_declarations(source)
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
    def from_file(cls, name, path):
        with open(path, 'r') as recipe:
            return Recipe(name, recipe.read())

    def control(self):
        return f"Maintainer: {self.header['maintainer']}\n"

def _read_package_header(variables):
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

class Package:
    def __init__(self, name, parent_declarations, source):
        parent_variables, parent_functions = parent_declarations
        variables, functions = _get_declarations(source)
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

    def id(self):
        return '_'.join((self.name, self.header['pkgver'], self.header['arch']))

    def filename(self):
        return self.id() + '.ipk'

    def control(self):
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
