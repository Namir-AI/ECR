"""Deployment-only validated backup-set staging and filesystem recovery.

No shell metadata execution, unsafe tar extraction, or automatic SQL rollback.
Existing data directories are retained rather than recursively deleted.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from uuid import uuid4


def fail(message: str) -> None:
    raise SystemExit(message)


def metadata(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        fail("Backup metadata is missing or unsafe; checksum verification is required.")
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or key in result:
            fail("Invalid backup metadata.")
        # Understand the old printf %q format without sourcing shell code.
        if value.startswith("$'"):
            fail("Unsupported escaped metadata; operator review required.")
        try:
            words = shlex.split(value, posix=True)
        except ValueError:
            fail("Invalid backup metadata quoting.")
        if len(words) != 1:
            fail("Invalid backup metadata value.")
        result[key] = words[0]
    return result


def verify_checksum(path: Path, expected: str | None) -> None:
    if not path.is_file() or path.is_symlink():
        fail("Paired backup artifact is missing or unsafe.")
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        fail("Backup artifact checksum is missing or invalid.")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        fail("Backup checksum mismatch; nothing has been restored.")
    with gzip.open(path, "rb") as stream:
        while stream.read(1024 * 1024):
            pass


def safe_destination(path: Path, install: Path) -> Path:
    if not path.is_absolute():
        fail("Restore destination must be absolute.")
    destination = path.resolve()
    broad = {
        "/",
        "/opt",
        "/var",
        "/home",
        "/usr",
        "/etc",
        "/tmp",
        "/run",
        "/dev",
        "/proc",
        "/sys",
    }
    if str(destination) in broad or install.is_relative_to(destination):
        fail("Unsafe broad restore destination.")
    for name in ("releases", "repository.git", "backups", "state"):
        protected = install / name
        if destination.is_relative_to(protected) or protected.is_relative_to(
            destination
        ):
            fail("Restore destination overlaps protected deployment directories.")
    if destination.exists() and not destination.is_dir():
        fail("Restore destination is not a directory.")
    return destination


def members(
    archive: tarfile.TarFile, shared: bool
) -> list[tuple[tarfile.TarInfo, PurePosixPath]]:
    validated = []
    seen: dict[PurePosixPath, bool] = {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or "\\" in member.name:
            fail("Unsafe archive path; nothing has been restored.")
        if not (member.isdir() or member.isreg()) or member.issparse():
            fail("Unsafe archive member type (links/devices are not allowed).")
        if shared and (not path.parts or path.parts[0] != "data"):
            fail("Shared archive must contain only data/.")
        relative = PurePosixPath(*path.parts[1:]) if shared else path
        if not relative.parts and not member.isdir():
            fail("Archive root must be a directory.")
        if relative in seen:
            fail("Duplicate archive path.")
        seen[relative] = member.isdir()
        validated.append((member, relative))
    for path in seen:
        for parent in path.parents:
            if parent in seen and not seen[parent]:
                fail("Archive file conflicts with directory path.")
    if not validated:
        fail("Empty/unreadable archive; operator review required.")
    return validated


def mount_points() -> list[Path]:
    # Linux deployment: mount roots cannot be safely exchanged as directories.
    result = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        name = line.split()[4]
        decoded = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), name)
        result.append(Path(decoded).resolve())
    return result


def require_renameable(destination: Path) -> None:
    if any(point.is_relative_to(destination) for point in mount_points()):
        fail(
            "Destination contains a mount point; reviewed mount-aware recovery required before restore."
        )


def extract(path: Path, destination: Path, shared: bool, uid: int, gid: int) -> Path:
    wrapper = Path(
        tempfile.mkdtemp(prefix=".ecr-restore-stage.", dir=destination.parent)
    )
    content = wrapper / "content"
    content.mkdir(mode=0o700)
    try:
        with tarfile.open(path, "r:gz") as archive:
            validated = members(archive, shared)
            for member, relative in validated:
                target = content.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        fail("Unreadable archive file.")
                    with source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o600)
            # All nodes are freshly created; no links from the tar are allowed.
            for node in [content, *content.rglob("*")]:
                os.chown(node, uid, gid)
                if node.is_dir():
                    node.chmod(0o700)
            content.chmod(0o750 if shared else 0o700)
        return wrapper
    except BaseException:
        shutil.rmtree(wrapper)
        raise


def prepare(
    sql: Path, install: Path, storage: Path, database: str, user: str, workspace: Path
) -> None:
    install = install.resolve()
    base = str(sql).removesuffix(".sql.gz")
    meta = metadata(Path(base + ".meta"))
    if not sql.is_file() or sql.is_symlink():
        fail("Unsafe SQL backup artifact.")
    # Freeze the reviewed SQL too: retention or other operators cannot replace
    # the selected backup between confirmation and database restore.
    staged_sql = workspace / "database.sql.gz"
    shutil.copyfile(sql, staged_sql)
    staged_sql.chmod(0o600)
    verify_checksum(staged_sql, meta.get("SQL_SHA256"))
    if meta.get("DATABASE_NAME") != database:
        fail(
            "Backup database differs from the current database; operator review required."
        )
    if meta.get("BACKUP_FORMAT") == "2" and meta.get("BACKUP_COMPLETE") != "true":
        fail("Backup set was not completed successfully.")
    data_path = install / "shared/data"
    data = safe_destination(data_path, install)
    if data != data_path or data_path.is_symlink():
        fail("shared/data must be a real directory, not a symlink.")
    storage = safe_destination(storage, install)
    custom = not storage.is_relative_to(data)
    if custom and data.is_relative_to(storage):
        fail("Custom storage overlaps shared/data.")
    specs = []
    missing = []
    for suffix, key, destination, shared, relevant in (
        (".files.tar.gz", "FILES_SHA256", data, True, True),
        (".storage.tar.gz", "STORAGE_SHA256", storage, False, custom),
    ):
        archive = Path(base + suffix)
        if archive.exists() or archive.is_symlink() or key in meta:
            verify_checksum(archive, meta.get(key))
            if not shared:
                recorded = Path(meta.get("STORAGE_ROOT", ""))
                if (
                    not custom
                    or not recorded.is_absolute()
                    or recorded.resolve() != storage
                ):
                    fail(
                        "STORAGE_ROOT mismatch: current and backed-up canonical roots must match."
                    )
            # Validate every archive before staging any data.
            with tarfile.open(archive, "r:gz") as tar:
                members(tar, shared)
            require_renameable(destination)
            specs.append((archive, destination, shared))
        elif relevant:
            missing.append("shared/data" if shared else "custom STORAGE_ROOT")
    identity = pwd.getpwnam(user)
    plan = {"entries": [], "missing": missing, "install": str(install)}
    try:
        for archive, destination, shared in specs:
            wrapper = extract(
                archive, destination, shared, identity.pw_uid, identity.pw_gid
            )
            plan["entries"].append(
                {"stage": str(wrapper), "destination": str(destination)}
            )
        (workspace / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    except BaseException:
        for entry in plan["entries"]:
            shutil.rmtree(entry["stage"])
        raise
    if missing:
        label = "DB-only recovery" if not specs else "Partial files recovery"
        print(
            f"{label}: no paired persistent-file archive exists for "
            + ", ".join(missing)
        )
    else:
        print("Validated database and paired persistent-file archives.")


def load_plan(workspace: Path) -> dict:
    plan = json.loads((workspace / "plan.json").read_text(encoding="utf-8"))
    for entry in plan["entries"]:
        destination = safe_destination(
            Path(entry["destination"]), Path(plan["install"])
        )
        stage = Path(entry["stage"])
        if (
            stage.parent != destination.parent
            or not stage.name.startswith(".ecr-restore-stage.")
            or stage.is_symlink()
        ):
            fail("Unsafe restore staging path.")
    return plan


def publish(workspace: Path) -> None:
    plan = load_plan(workspace)
    swapped = []
    try:
        for entry in plan["entries"]:
            destination = Path(entry["destination"])
            old = destination.parent / (".ecr-before-restore." + uuid4().hex)
            existed = destination.exists()
            if destination.is_symlink():
                fail("Restore destination changed to a symlink.")
            if existed:
                destination.rename(old)
            swapped.append((destination, old, existed))
            (Path(entry["stage"]) / "content").rename(destination)
            entry["retained"] = str(old) if existed else None
    except BaseException:
        for destination, old, existed in reversed(swapped):
            if destination.exists():
                destination.rename(
                    destination.parent / (".ecr-failed-restore." + uuid4().hex)
                )
            if existed:
                old.rename(destination)
        raise
    (workspace / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    for entry in plan["entries"]:
        if entry.get("retained"):
            print("Previous files retained for review: " + entry["retained"])


def main() -> None:
    command = sys.argv[1]
    if command == "prepare":
        prepare(
            Path(sys.argv[2]),
            Path(sys.argv[3]),
            Path(sys.argv[4]),
            sys.argv[5],
            sys.argv[6],
            Path(sys.argv[7]),
        )
    elif command == "publish":
        publish(Path(sys.argv[2]))
    elif command == "missing":
        print("yes" if load_plan(Path(sys.argv[2]))["missing"] else "no")
    elif command == "cleanup":
        for entry in load_plan(Path(sys.argv[2]))["entries"]:
            stage = Path(entry["stage"])
            if stage.exists():
                shutil.rmtree(stage)
    else:
        fail("Invalid restore helper command.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        # No environment/SQL contents or secrets in diagnostics.
        raise SystemExit(
            f"Backup recovery failed ({type(exc).__name__}); operator review required."
        ) from None
