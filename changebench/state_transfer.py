"""Trusted state transfer program, run in an offline container, never by candidates.

Candidate processes have stopped before capture. No candidate-created path is
opened by the host. Archives accept regular files/directories only, without links.
"""
import base64
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

MAX_BYTES = 256 * 1024
MAX_FILES = 200
MAX_DIRS = 256


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or str(path) != name or '\\' in name:
        raise ValueError('Unsafe state path')
    return path


def capture(root):
    files, directories, total = [], [], 0
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            safe_name(relative)
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.append({'path': relative, 'mode': stat.S_IMODE(info.st_mode)})
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if info.st_size + total > MAX_BYTES:
                    raise ValueError('State exceeds 256KiB')
                # O_NOFOLLOW also prevents a symlink substitution at open time.
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as stream:
                    data = stream.read(MAX_BYTES - total + 1)
                total += len(data)
                if total > MAX_BYTES:
                    raise ValueError('State exceeds 256KiB')
                files.append({'path': relative, 'mode': stat.S_IMODE(info.st_mode),
                              'data': base64.b64encode(data).decode('ascii')})
            else:
                raise ValueError('State links and special files are unsupported')
            if len(files) > MAX_FILES or len(directories) > MAX_DIRS:
                raise ValueError('Too many state entries')
    return {'files': sorted(files, key=lambda x: x['path']),
            'directories': sorted(directories, key=lambda x: x['path'])}


def restore(root, archive):
    if list(root.iterdir()):
        raise ValueError('Restore destination must be empty')
    for item in sorted(archive['directories'], key=lambda x: len(PurePosixPath(x['path']).parts)):
        path = root / safe_name(item['path'])
        path.mkdir()
    for item in archive['files']:
        path = root / safe_name(item['path'])
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(base64.b64decode(item['data'], validate=True))
        path.chmod(item['mode'])
    for item in sorted(archive['directories'], key=lambda x: -len(PurePosixPath(x['path']).parts)):
        (root / safe_name(item['path'])).chmod(item['mode'])
    return {'restored': True}


def clear(root):
    # Only this trusted container traverses candidate state. Never follow links.
    for entry in os.scandir(root):
        path = Path(entry.path)
        if entry.is_dir(follow_symlinks=False):
            path.chmod(0o700)
            clear(path)
            path.rmdir()
        else:
            path.unlink()
    return {'cleared': True}


def main():
    root = Path(os.environ['CB_STATE_DIR'])
    request = json.load(sys.stdin)
    if request['op'] == 'capture':
        result = capture(root)
    elif request['op'] == 'restore':
        result = restore(root, request['archive'])
    elif request['op'] == 'clear':
        result = clear(root)
    else:
        raise ValueError('Unknown state operation')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, PermissionError) as exc:
        print(json.dumps({'state_error': str(exc)}))
