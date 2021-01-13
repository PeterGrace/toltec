# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""Collection of useful functions."""

from collections.abc import Iterable
import hashlib
import itertools
import os
import shutil
import sys
from typing import Dict, List, Optional
import zipfile

# Date format used in HTTP headers such as Last-Modified
HTTP_DATE_FORMAT = "%a, %d %b %Y %H:%M:%S %Z"

# Logging format for build scripts
LOGGING_FORMAT = '[%(levelname)8s] %(name)s: %(message)s'

def file_sha256(path: str) -> str:
    """Compute the SHA-256 checksum of a file."""
    sha256 = hashlib.sha256()
    buffer = bytearray(128 * 1024)
    view = memoryview(buffer)

    with open(path, 'rb', buffering=0) as file:
        for length in iter(lambda: file.readinto(view), 0): # type:ignore
            sha256.update(view[:length])

    return sha256.hexdigest()

def split_all(path: str) -> List[str]:
    """Split a file path into all its directory components."""
    parts = []
    prefix = path

    while prefix not in ('', '/'):
        prefix, base = os.path.split(prefix)
        if base:
            parts.append(base)

    parts.reverse()
    return parts

def all_equal(seq: Iterable) -> bool:
    """Check that all elements of a sequence are equal."""
    grouped = itertools.groupby(seq)
    first = next(grouped, (None, grouped))
    second = next(grouped, None)
    return first and not second

def remove_prefix(filenames: List[str]) -> Dict[str, str]:
    """Find and remove the longest directory prefix shared by all files."""
    split_filenames = [split_all(filename) for filename in filenames]

    # Find the longest directory prefix shared by all files
    min_len = min(len(filename) for filename in split_filenames)
    prefix = 0

    while prefix < min_len \
            and all_equal(filename[prefix] for filename in split_filenames):
        prefix += 1

    # If there’s only one file, keep the last component
    if len(filenames) == 1:
        prefix -= 1

    mapping = {}

    for filename, split_filename in zip(filenames, split_filenames):
        if split_filename[prefix:]:
            mapping[filename] = os.path.join(*split_filename[prefix:])

    return mapping

def auto_extract(archive_path: str, dest_path: str) -> bool:
    """
    Automatically extract an archive and strip useless components.

    :param archive_path: path to the archive to extract
    :param dest_path: destination folder for the archive contents
    :returns: true if something was extracted, false if not a supported archive
    """
    if archive_path[-4:] == '.zip':
        with zipfile.ZipFile(archive_path) as archive:
            members = remove_prefix(archive.namelist())

            for filename, stripped in members.items():
                member = archive.getinfo(filename)
                file_path = os.path.join(dest_path, stripped)

                if member.is_dir():
                    os.makedirs(file_path, exist_ok=True)
                else:
                    with archive.open(member) as source, \
                            open(file_path, 'wb') as target:
                        shutil.copyfileobj(source, target)

                    mode = member.external_attr >> 16
                    os.chmod(file_path, mode)

        return True

    return False

def query_user(
        question: str,
        default: str,
        options: Optional[List[str]] = None,
        aliases: Optional[Dict[str, str]] = None):
    """
    Ask the user to make a choice.

    :param question: message to display before the choice
    :param default: default choice if the user inputs an empty string
    :param options: list of valid options (should be lowercase strings)
    :param aliases: accepted aliases for the valid options
    :returns: option chosen by the user
    """
    options = options or ['y', 'n']
    aliases = aliases or {'yes': 'y', 'no': 'n'}

    if default not in options:
        raise ValueError(f'Default value {default} is not a valid option')

    prompt = '/'.join(
        option if option != default else option.upper()
        for option in options)

    while True:
        sys.stdout.write(f'{question} [{prompt}] ')
        choice = input().lower()

        if not choice:
            return default

        if choice in options:
            return choice

        if choice in aliases:
            return aliases[choice]

        print('Invalid answer. Please choose among the valid options.')
