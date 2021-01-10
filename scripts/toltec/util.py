import hashlib
import itertools
import os
import shutil
import zipfile

http_date_format = "%a, %d %b %Y %H:%M:%S %Z"

def file_sha256(path: str) -> str:
    """Compute the SHA-256 checksum of a file."""
    sha256 = hashlib.sha256()
    buffer = bytearray(128 * 1024)
    view = memoryview(buffer)

    with open(path, 'rb', buffering=0) as file:
        for length in iter(lambda: file.readinto(view), 0):
            sha256.update(view[:length])

    return sha256.hexdigest()

def split_all(path: str):
    parts = []
    prefix = path

    while prefix not in ('', '/'):
        prefix, base = os.path.split(prefix)
        if base: parts.append(base)

    parts.reverse()
    return parts

def all_equal(iterable):
    grouped = itertools.groupby(iterable)
    return next(grouped, True) and not next(grouped, False)

def remove_prefix(filenames: list[str]):
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

    for i in range(len(filenames)):
        if split_filenames[i][prefix:]:
            mapping[filenames[i]] = os.path.join(*split_filenames[i][prefix:])

    return mapping

def auto_extract(archive_path, dest_path):
    """Automatically extract an archive and strip useless components."""
    if archive_path[-4:] == '.zip':
        with zipfile.ZipFile(archive_path) as archive:
            members = remove_prefix(archive.namelist())

            for member, stripped in members.items():
                file_path = os.path.join(dest_path, stripped)
                parent_dir, base = os.path.split(file_path)

                os.makedirs(parent_dir, exist_ok=True)

                if base:
                    source = archive.open(member)
                    target = open(file_path, 'wb')

                    with source, target:
                        shutil.copyfileobj(source, target)

        return True

    return False
