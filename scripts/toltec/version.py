# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""Work with Debian-style package versions."""

import re

# Characters permitted in the upstream version part of a version number
_VERSION_CHARS = re.compile('^[A-Za-z0-9.+~-]+$')

class InvalidVersionError(Exception):
    """Raised when construction of an invalid version is attempted."""

class Version:
    """
    Parse package versions.

    See <https://www.debian.org/doc/debian-policy/ch-controlfields.html#s-f-version>
    for details about the format and the comparison rules.
    """
    def __init__(self, upstream: str, revision: str = '0', epoch: int = 0):
        self.upstream = upstream
        self.revision = revision
        self.epoch = epoch

        if _VERSION_CHARS.fullmatch(upstream) is None:
            raise InvalidVersionError('Invalid chars in upstream version')

        if _VERSION_CHARS.fullmatch(revision) is None:
            raise InvalidVersionError('Invalid chars in revision')

        self._original = None

    @classmethod
    def parse(cls, version: str):
        """Parse a version number."""
        colon = version.find(':')

        if colon == -1:
            epoch = 0
        else:
            epoch = int(version[:colon])
            version = version[colon + 1:]

        dash = version.find('-')

        if dash == -1:
            revision = '0'
        else:
            revision = version[dash + 1:]
            version = version[:dash]

        upstream = version

        result = Version(upstream, revision, epoch)
        result._original = version
        return result

    def __str__(self):
        if self._original is not None:
            # Use the original parsed version string
            return self._original

        epoch = '' if self.epoch == 0 else f'{self.epoch}:'
        revision = '' if self.revision == '0' and '-' not in self.upstream \
                else f'-{self.revision}'

        return f'{epoch}{self.upstream}{revision}'

    def __repr__(self):
        return f'Version(upstream={repr(self.upstream)}, \
revision={repr(self.revision)}, epoch={repr(self.epoch)})'
