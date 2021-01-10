# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""Bridge Bash declaration files with Python."""

from typing import Dict, List, Optional, Tuple, Union
import shlex
import subprocess

Any = Union[str, Dict[str, str], List[Optional[str]]]
Variables = Dict[str, Optional[Any]]
Functions = Dict[str, str]

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
            variables[name] = value
        else:
            assert next_token == '('
            assert lexer.get_token() == ')'
            start, end = _parse_func(lexer)
            functions[token] = declarations[start:end]

    return variables, functions

def _parse_string(token: str) -> str:
    """Remove escape sequences from a Bash string."""
    return token.replace('\\$', '$')

def _parse_array(lexer: shlex.shlex) -> List[Optional[str]]:
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

def _parse_dict(lexer: shlex.shlex) -> Dict[str, str]:
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
            var_value = _parse_array(lexer)
        elif 'A' in var_flags:
            var_value = _parse_dict(lexer)
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
