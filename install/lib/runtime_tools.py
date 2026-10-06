"""Make only isolated virtualenv runtime files service-readable.

Never follow symlinks or chmod shared cache inodes. Existing uv hardlinks are
copied to private venv inodes before permissions change; new uv installs use copy.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from uuid import uuid4


def harden_runtime(application: Path, python_root: Path) -> None:
    application = application.resolve(strict=True)
    root = application / ".venv"
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected an isolated regular application virtualenv.")
    python_root = python_root.resolve(strict=True)
    for directory, dirs, files, directory_fd in os.fwalk(root, follow_symlinks=False):
        os.fchmod(directory_fd, 0o755)
        for name in dirs + files:
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(entry.st_mode):
                path = Path(directory) / name
                target = path.resolve(strict=True)
                if target.is_relative_to(root):
                    continue
                if (
                    path.parent == root / "bin"
                    and name in {"python", "python3", "python3.12"}
                    and target.is_relative_to(python_root)
                ):
                    continue  # Managed interpreter: verified separately, never chmod it.
                raise ValueError("Virtualenv contains an external runtime symlink.")
            if stat.S_ISDIR(entry.st_mode):
                continue
            if not stat.S_ISREG(entry.st_mode):
                raise ValueError("Virtualenv contains a non-regular runtime object.")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            temporary = None
            try:
                current = os.fstat(fd)
                mode = (current.st_mode & 0o700) | 0o444
                if current.st_mode & 0o111:
                    mode |= 0o111
                if current.st_nlink > 1:
                    temporary_name = f".ecr-runtime-{uuid4().hex}"
                    out_fd = os.open(
                        temporary_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    temporary = temporary_name
                    with (
                        os.fdopen(out_fd, "wb") as output,
                        os.fdopen(os.dup(fd), "rb") as source,
                    ):
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fchmod(output.fileno(), mode)
                        os.fsync(output.fileno())
                    os.replace(
                        temporary,
                        name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                    )
                    temporary = None
                else:
                    os.fchmod(fd, mode)
            finally:
                os.close(fd)
                if temporary:
                    os.unlink(temporary, dir_fd=directory_fd)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("Invalid runtime hardening invocation.")
        harden_runtime(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError):
        raise SystemExit(
            "Isolated runtime permission hardening failed; deployment stopped."
        ) from None
