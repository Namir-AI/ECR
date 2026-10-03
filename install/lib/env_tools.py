"""Deployment-only dotenv and template helpers.

Secrets are accepted through stdin and are never written to stdout.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values, set_key

# Explicit deployment-owned, NON-SECRET additions. Existing customized values
# win. A release introducing a new setting must opt in here, not copy every
# example value (especially example passwords) into production.
MANAGED_DEFAULTS = frozenset(
    {
        "APP_NAME",
        "SESSION_COOKIE_NAME",
        "SESSION_TTL_DAYS",
        "SESSION_TOUCH_INTERVAL_SECONDS",
        "CSRF_COOKIE_NAME",
        "PASSWORD_MIN_LENGTH",
        "PASSWORD_MAX_LENGTH",
        "ARGON2_TIME_COST",
        "ARGON2_MEMORY_COST_KIB",
        "ARGON2_PARALLELISM",
        "DB_CHARSET",
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_POOL_RECYCLE_SECONDS",
        "DB_CONNECT_TIMEOUT_SECONDS",
        "DB_ECHO",
    }
)


def storage_path(target: Path, install_dir: Path | None = None) -> Path:
    value = dotenv_values(target, interpolate=False).get("STORAGE_ROOT")
    if install_dir is not None and value in (None, "", "var/protected"):
        return install_dir / "shared/data/protected"
    path = Path(value or "")
    if not value or not path.is_absolute() or path == Path("/"):
        raise SystemExit("STORAGE_ROOT must be an absolute persistent directory.")
    if install_dir is not None and path.resolve().is_relative_to(
        (install_dir / "releases").resolve()
    ):
        raise SystemExit("STORAGE_ROOT must not be inside application releases.")
    return path


def upgrade_env(target: Path, template: Path, install_dir: Path) -> None:
    values = dotenv_values(target, interpolate=False)
    defaults = dotenv_values(template, interpolate=False)
    additions = {
        key: defaults[key]
        for key in MANAGED_DEFAULTS
        if key not in values and defaults.get(key) is not None
    }
    storage = storage_path(target, install_dir)
    if values.get("STORAGE_ROOT") != str(storage):
        additions["STORAGE_ROOT"] = str(storage)
    # Atomic replacement, protected throughout; never log dotenv values.
    fd, temporary = tempfile.mkstemp(prefix=".env.upgrade.", dir=target.parent)
    os.close(fd)
    staged = Path(temporary)
    try:
        shutil.copyfile(target, staged)
        for key, value in additions.items():
            set_key(staged, key, str(value), quote_mode="always")
        os.chown(staged, target.stat().st_uid, target.stat().st_gid)
        os.chmod(staged, target.stat().st_mode & 0o777)
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def validate_env(target: Path) -> None:
    # Import target-release settings without printing ValidationError input,
    # which can contain secrets. No database connections or app mutations.
    from pydantic import ValidationError

    from app.core.config import Settings

    values = dotenv_values(target, interpolate=False)
    try:
        settings = Settings(_env_file=None, **{k.lower(): v for k, v in values.items()})
    except ValidationError as exc:
        fields = sorted(
            {str(error["loc"][0]) for error in exc.errors() if error["loc"]}
        )
        raise SystemExit(
            "Invalid production configuration fields: " + ", ".join(fields)
        ) from None
    if settings.app_env != "production" or settings.app_debug or settings.db_echo:
        raise SystemExit(
            "Production must use APP_ENV=production and disable APP_DEBUG/DB_ECHO."
        )
    storage_path(target)
    if not all(values.get(k) for k in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")):
        raise SystemExit("Required database settings are missing.")


def _stdin_pairs() -> dict[str, str]:
    fields = sys.stdin.buffer.read().split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise SystemExit("Invalid deployment input.")
    decoded = [field.decode("utf-8") for field in fields]
    return dict(zip(decoded[0::2], decoded[1::2], strict=True))


def write_env(target: Path, template: Path) -> None:
    if not target.exists():
        shutil.copyfile(template, target)
    for key, value in _stdin_pairs().items():
        set_key(target, key, value, quote_mode="always")


def read_env(target: Path, keys: list[str]) -> None:
    values = dotenv_values(target, interpolate=False)
    for key in keys:
        value = values.get(key)
        if value is None:
            raise SystemExit(f"Missing deployment setting: {key}")
        sys.stdout.buffer.write(str(value).encode("utf-8") + b"\0")


def render_template(source: Path, target: Path) -> None:
    rendered = source.read_text(encoding="utf-8")
    for key, value in _stdin_pairs().items():
        rendered = rendered.replace(f"@@{key}@@", value)
    if "@@" in rendered:
        raise SystemExit("Deployment template contains unresolved placeholders.")
    target.write_text(rendered, encoding="utf-8")


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("Invalid deployment helper invocation.")
    command = sys.argv[1]
    if command == "write" and len(sys.argv) == 4:
        write_env(Path(sys.argv[2]), Path(sys.argv[3]))
    elif command == "read" and len(sys.argv) >= 4:
        read_env(Path(sys.argv[2]), sys.argv[3:])
    elif command == "render" and len(sys.argv) == 4:
        render_template(Path(sys.argv[2]), Path(sys.argv[3]))
    elif command == "upgrade" and len(sys.argv) == 5:
        upgrade_env(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
    elif command == "storage" and len(sys.argv) == 3:
        print(storage_path(Path(sys.argv[2])))
    elif command == "backup-storage" and len(sys.argv) == 4:
        print(storage_path(Path(sys.argv[2]), Path(sys.argv[3])))
    elif command == "validate" and len(sys.argv) == 3:
        validate_env(Path(sys.argv[2]))
    else:
        raise SystemExit("Invalid deployment helper invocation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
