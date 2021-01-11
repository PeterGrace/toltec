# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""Bridge Bash with Python."""

from typing import Dict, List, Optional, Tuple, Union
import shlex
import subprocess

AssociativeArray = Dict[str, str]
IndexedArray = List[Optional[str]]
Any = Union[str, AssociativeArray, IndexedArray]
Variables = Dict[str, Optional[Any]]
Functions = Dict[str, str]

# Variables which are defined by default by Bash.  Those variables are excluded
# from the result of `get_declarations()`. Subset of the list at:
# <https://www.gnu.org/software/bash/manual/html_node/Bash-Variables.html>
default_variables = {
    'BASH', 'BASHOPTS', 'BASHPID', 'BASH_ALIASES', 'BASH_ARGC', 'BASH_ARGV',
    'BASH_ARGV0', 'BASH_CMDS', 'BASH_COMMAND', 'BASH_LINENO', 'BASH_SOURCE',
    'BASH_SUBSHELL', 'BASH_VERSINFO', 'BASH_VERSION', 'COLUMNS',
    'COMP_WORDBREAKS', 'DIRSTACK', 'EPOCHREALTIME', 'EPOCHSECONDS', 'EUID',
    'FUNCNAME', 'GROUPS', 'HISTCMD', 'HISTFILE', 'HISTFILESIZE', 'HISTSIZE',
    'HOSTNAME', 'HOSTTYPE', 'IFS', 'LINENO', 'LINES', 'MACHTYPE', 'MAILCHECK',
    'OLDPWD', 'OPTERR', 'OPTIND', 'OSTYPE', 'PATH', 'PIPESTATUS', 'PPID',
    'PS1', 'PS2', 'PS4', 'PWD', 'RANDOM', 'SECONDS', 'SHELL', 'SHELLOPTS',
    'SHLVL', 'SRANDOM', 'TERM', 'UID', '_',
}

def get_declarations(src: str) -> Tuple[Variables, Functions]:
    """
    Extract all variables and functions defined by a Bash script.

    If a function or a variable is defined or assigned multiple times
    in the script, only the final value is extracted. The script must not
    output anything on the standard output stream.

    :param src: source string of the considered Bash string
    :returns: a tuple containing the declared variables and functions
    """
    src += '''
declare -f
declare -p
'''
    env: Dict[str, str] = {}
    declarations_subshell = subprocess.run(
        ['/usr/bin/env', 'bash'],
        input=src.encode(),
        capture_output=True,
        env=env)

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

            if name not in default_variables:
                variables[name] = value
        else:
            assert next_token == '('
            assert lexer.get_token() == ')'
            start, end = _parse_func(lexer)
            functions[token] = declarations[start:end]

    return variables, functions

def put_variables(variables: Variables) -> str:
    """
    Generate a Bash script fragment which defines a set of variables.

    :param variables: set of variables to define
    :returns: generated Bash fragment
    """
    result = ''

    for name, value in variables.items():
        if value is None:
            result += f'declare -- {name}\n'
        elif isinstance(value, str):
            result += f'declare -- {name}={_generate_string(value)}\n'
        elif isinstance(value, list):
            result += f'declare -a {name}={_generate_indexed(value)}\n'
        elif isinstance(value, dict):
            result += f'declare -A {name}={_generate_assoc(value)}\n'
        else:
            raise ValueError(f'Unsupported type {type(value)} for variable \
{name}')

    return result

def _parse_string(token: str) -> str:
    """Remove escape sequences from a Bash string."""
    return token.replace('\\$', '$')

def _generate_string(string: str) -> str:
    """Generate a Bash string."""
    return shlex.quote(string)

def _parse_indexed(lexer: shlex.shlex) -> IndexedArray:
    """Parse an indexed Bash array."""
    assert lexer.get_token() == '('
    result: List[Optional[str]] = []

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

def _generate_indexed(array: IndexedArray) -> str:
    """Generate an indexed Bash array."""
    return '(' + ' '.join(
        f'[{index}]={_generate_string(value)}'
        for index, value in enumerate(array)
        if value is not None) + ')'

def _parse_assoc(lexer: shlex.shlex) -> AssociativeArray:
    """Parse an associative Bash array."""
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

def _generate_assoc(array: AssociativeArray) -> str:
    """Generate an associative Bash array."""
    return '(' + ' '.join(
        f'[{_generate_string(key)}]={_generate_string(value)}'
        for key, value in array.items()) + ')'

def _parse_var(lexer: shlex.shlex) -> Tuple[str, Optional[Any]]:
    """Parse a variable declaration."""
    flags_token = lexer.get_token()

    if flags_token != '--':
        var_flags = set(flags_token[1:])
    else:
        var_flags = set()

    var_name = lexer.get_token()
    var_value: Optional[Any] = None
    lookahead = lexer.get_token()

    if lookahead == '=':
        if 'a' in var_flags:
            var_value = _parse_indexed(lexer)
        elif 'A' in var_flags:
            var_value = _parse_assoc(lexer)
        else:
            var_value = _parse_string(lexer.get_token())
    else:
        lexer.push_token(lookahead)

    return var_name, var_value

def _parse_func(lexer) -> Tuple[int, int]:
    """Find the starting and end bounds of a function declaration."""
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
