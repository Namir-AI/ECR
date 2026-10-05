"""Protected objects: unchanged signature namespace and shared package evidence."""

from app.storage.protected import (
    LocalProtectedStorage,
    ProtectedStorage,
    get_protected_storage,
)

__all__ = ["LocalProtectedStorage", "ProtectedStorage", "get_protected_storage"]
