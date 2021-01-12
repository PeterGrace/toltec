# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""Make ipk packages."""

from typing import Dict
from io import IOBase, BytesIO
import tarfile

def _clean_info(root: str, epoch: int, info: tarfile.TarInfo) \
        -> tarfile.TarInfo:
    """
    Remove variable data from an archive entry.

    :param root: root path from which the entries are added
    :param epoch: fixed modification time to set
    :param info: tarinfo object to set
    :returns: changed tarinfo
    """
    info.name = '.' + info.name.removeprefix(root)
    info.uid = 0
    info.gid = 0
    info.uname = ''
    info.gname = ''
    info.mtime = epoch
    return info

def _add_file(
        archive: tarfile.TarFile,
        name: str, mode: int, epoch: int, data: bytes) -> None:
    """
    Add an in-memory file into a tar archive.

    :param archive: archive to append to
    :param name: name of the file to add
    :param mode: permissions of the file
    :param epoch: fixed modification time to set
    :param data: file contents
    """
    info = tarfile.TarInfo('./' + name)
    info.size = len(data)
    info.mode = mode
    archive.addfile(_clean_info('.', epoch, info), BytesIO(data))

def make_control(
        file: IOBase, epoch: int,
        metadata: str, scripts: Dict[str, str]):
    """
    Create the control sub-archive.

    See <https://www.debian.org/doc/debian-policy/ch-controlfields.html>
    and <https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html>.

    :param file: file to which the sub-archive will be written
    :param epoch: fixed modification time to set
    :param metadata: package metadata (main control file)
    :param scripts: optional maintainer scripts
    """
    with tarfile.open(mode='w:gz', fileobj=file) as archive:
        _add_file(archive, 'control', 0o644, epoch, metadata.encode())

        for name, script in scripts.items():
            _add_file(archive, name, 0o755, epoch, script.encode())

def make_data(
        file: IOBase,
        epoch: int,
        pkg_dir: str) -> None:
    """
    Create the data sub-archive.

    :param file: file to which the sub-archive will be written
    :param epoch: fixed modification time to set
    :param pkg_dir: directory in which the package tree exists
    """
    with tarfile.open(mode='w:gz', fileobj=file) as archive:
        archive.add(pkg_dir, filter=lambda info: \
            _clean_info(pkg_dir, epoch, info))

def make_ipk(
        file: IOBase,
        epoch: int,
        pkg_dir: str,
        metadata: str,
        scripts: Dict[str, str]) -> None:
    """
    Create an ipk package.

    :param file: file to which the package will be written
    :param epoch: fixed modification time to set
    :param pkg_dir: directory in which the package tree exists
    :param metadata: package metadata (main control file)
    :param scripts: optional maintainer scripts
    """
    with BytesIO() as control, BytesIO() as data, \
            tarfile.open(mode='w:gz', fileobj=file) as archive:
        make_control(control, epoch, metadata, scripts)
        _add_file(archive, 'control.tar.gz', 0o644, epoch, control.getvalue())

        make_data(data, epoch, pkg_dir)
        _add_file(archive, 'data.tar.gz', 0o644, epoch, data.getvalue())

        _add_file(archive, 'debian-binary', 0o644, epoch, b'2.0\n')
