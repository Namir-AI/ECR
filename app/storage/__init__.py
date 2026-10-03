"""Protected signature storage only; general attachments remain deferred."""

from app.storage.protected import (
    LocalProtectedStorage,
    ProtectedStorage,
    get_protected_storage,
)

__all__ = ["LocalProtectedStorage", "ProtectedStorage", "get_protected_storage"]
