import hashlib

http_date_format = "%a, %d %b %Y %H:%M:%S %Z"

def file_sha256(path):
    sha256 = hashlib.sha256()
    buffer = bytearray(128 * 1024)
    view = memoryview(buffer)

    with open(path, 'rb', buffering=0) as file:
        for length in iter(lambda: file.readinto(view), 0):
            sha256.update(view[:length])

    return sha256.hexdigest()
