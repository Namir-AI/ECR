"""Private immutable attachment objects using descriptor-relative, no-symlink IO."""

import os
import re
import stat
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from fastapi import Request

EXTENSIONS = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}
CHUNK_SIZE = 65536


class AttachmentStorage(Protocol):
    """Private object streaming interface, independent of signature storage."""

    def put_stream(self, source: BinaryIO, media_type: str) -> str: ...
    def open_reader(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...


def iter_chunks(reader: BinaryIO):
    try:
        while chunk := reader.read(CHUNK_SIZE):
            yield chunk
    finally:
        reader.close()


class LocalAttachmentStorage:
    """Shares STORAGE_ROOT with signatures, but uses a separate private namespace.

    Never resolves symlinks, including ancestor directories. Every file operation
    is relative to a pinned directory descriptor, preventing substitution races.
    """

    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root)) / "attachments"

    @contextmanager
    def _directory(self, *, create=False):
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.root.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def _key(self, key):
        if not re.fullmatch(r"[0-9a-f]{32}\.(pdf|jpg|png)", key):
            raise ValueError("Invalid protected attachment key")
        return key

    def put(self, content: bytes, media_type: str = "image/png") -> str:
        """Convenience for bounded callers; attachment uploads use put_stream."""
        return self.put_stream(BytesIO(content), media_type)

    def put_stream(self, source: BinaryIO, media_type: str) -> str:
        key = self._key(f"{uuid4().hex}.{EXTENSIONS[media_type]}")
        with self._directory(create=True) as directory:
            fd = os.open(
                key,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    source.seek(0)
                    while chunk := source.read(CHUNK_SIZE):
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                os.unlink(key, dir_fd=directory)
                raise
        return key

    def read(self, key: str) -> bytes:
        """Compatibility for bounded callers/tests, never used by HTTP routes."""
        with self.open_reader(key) as reader:
            return b"".join(iter_chunks(reader))

    def open_reader(self, key: str) -> BinaryIO:
        key = self._key(key)
        with self._directory() as directory:
            fd = os.open(
                key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            stream = os.fdopen(fd, "rb")
            try:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError("Protected object is not a regular file")
            except Exception:
                stream.close()
                raise
            return stream

    def delete(self, key: str) -> None:
        key = self._key(key)
        try:
            with self._directory() as directory:
                try:
                    os.unlink(key, dir_fd=directory)
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass


def get_attachment_storage(request: Request) -> AttachmentStorage:
    configured = getattr(request.app.state, "attachment_storage", None)
    if configured is not None:
        return configured
    settings = request.app.state.settings
    if settings.app_env == "production" and not settings.storage_root.is_absolute():
        raise OSError("Production STORAGE_ROOT must be an absolute persistent path")
    return LocalAttachmentStorage(settings.storage_root)
