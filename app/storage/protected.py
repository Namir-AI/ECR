"""Protected object interface and backward-compatible private signature backend."""

import os
import re
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from fastapi import Request


class ProtectedStorage(Protocol):
    """An S3 adapter can later implement this interface without changing DB keys."""

    def put(self, content: bytes, media_type: str = "image/png") -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalProtectedStorage:
    """Immutable, server-generated PNG objects in a service-owned private folder."""

    def __init__(self, root: Path):
        self.root = root / "signatures"

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}\.png", key):
            raise ValueError("Invalid private object key")
        return self.root / key

    def put(self, content: bytes, media_type: str = "image/png") -> str:
        if media_type != "image/png":
            raise ValueError("Signature storage accepts PNG drawings only")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        key = f"{uuid4().hex}.png"
        path = self._path(key)
        # Exclusive creation failure must never unlink an existing valid object.
        stream = path.open("xb")
        try:
            with stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return key

    def read(self, key: str) -> bytes:
        # Never follow a substituted symlink in private object storage.
        fd = os.open(self._path(key), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            return stream.read()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_protected_storage(request: Request) -> ProtectedStorage:
    """Override via app.state in tests or for a future protected S3 adapter."""
    configured = getattr(request.app.state, "protected_storage", None)
    if configured is not None:
        return configured
    settings = request.app.state.settings
    if settings.storage_backend == "s3":
        from app.storage.s3 import S3ProtectedStorage

        return S3ProtectedStorage(
            settings.s3_bucket, settings.s3_region, settings.s3_prefix
        )
    root = settings.storage_root
    if settings.app_env == "production" and not root.is_absolute():
        raise OSError("Production STORAGE_ROOT must be an absolute persistent path")
    return LocalProtectedStorage(root)
