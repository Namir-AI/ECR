"""Argon2id password hashing and password-policy helpers."""

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import AppSettings


class PasswordPolicyError(ValueError):
    """Raised when a proposed password violates the configured policy."""


class PasswordManager:
    """Hash and verify passwords without exposing their stored representation."""

    def __init__(self, settings: AppSettings) -> None:
        self._min_length = settings.password_min_length
        self._max_length = settings.password_max_length
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._dummy_hash = self._hasher.hash("dummy-password-used-for-timing-only")

    def validate(self, password: str) -> None:
        """Validate only owner-independent security length bounds."""
        if len(password) < self._min_length:
            raise PasswordPolicyError(
                f"Password must be at least {self._min_length} characters."
            )
        if len(password) > self._max_length:
            raise PasswordPolicyError(
                f"Password must be no more than {self._max_length} characters."
            )

    def hash(self, password: str) -> str:
        """Validate and hash a password using Argon2id."""
        self.validate(password)
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """Verify a password while treating malformed hashes as failure."""
        if len(password) > self._max_length:
            return False
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    def verify_dummy(self, password: str) -> None:
        """Perform equivalent work when no user record exists."""
        self.verify(self._dummy_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        """Return whether the current Argon2 settings require a fresh hash."""
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True
