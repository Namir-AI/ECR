"""Recovery uses real archives/files; root ownership, MySQL and services mocked."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import os
import pwd
import shlex
import shutil
import subprocess
import sys
import tarfile

import pytest

from tests.test_deployment import COMMON, PRIVILEGE_STUBS, ROOT, Q, bash, executable
from tests.test_deployment import (
    deployment as deployment,  # noqa: PLC0414 - pytest fixture re-export
)


@pytest.mark.parametrize("kind", ["root", "nested", "parent"])
def test_restore_mount_topology_is_checked_before_directory_exchange(
    tmp_path, monkeypatch, kind
):
    spec = importlib.util.spec_from_file_location(
        "restore_tools", ROOT / "install/lib/restore_tools.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = tmp_path / "data"
    point = {
        "root": destination,
        "nested": destination / "protected",
        "parent": tmp_path,
    }[kind]
    monkeypatch.setattr(module, "mount_points", lambda: [point])
    if kind == "parent":
        module.require_renameable(destination)
    else:
        with pytest.raises(SystemExit, match="mount-aware"):
            module.require_renameable(destination)


@pytest.mark.parametrize("failure", [False, True])
def test_atomic_env_restore_contents_permissions_and_owner_request(tmp_path, failure):
    shared = tmp_path / "shared"
    shared.mkdir()
    live = shared / ".env"
    live.write_bytes(b"changed by upgrade")
    snapshot = tmp_path / "snapshot"
    original = b"DB_PASSWORD='never-print-me'\nCUSTOM_VALUE='retain exactly'\n"
    snapshot.write_bytes(original)
    snapshot.chmod(0o600)
    ownership = tmp_path / "ownership"
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
ECR_INSTALL_DIR={Q(str(tmp_path))}; ECR_SERVICE_USER=ecr
ECR_TEST_OWNERSHIP_LOG={Q(str(ownership))}
{"install() { return 55; }" if failure else ":"}
restore_production_env {Q(str(snapshot))} || exit "$?"
run_as_service test -r "$ECR_INSTALL_DIR/shared/.env"
""")
    assert "never-print-me" not in result.stdout + result.stderr
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert not list(shared.glob(".ecr-file.*"))
    if failure:
        assert result.returncode == 55
        assert live.read_bytes() == b"changed by upgrade"
    else:
        assert result.returncode == 0, result.stderr
        assert live.read_bytes() == original
        assert live.stat().st_mode & 0o777 == 0o640
        assert "-o root -g ecr -m 0640" in ownership.read_text()


@pytest.mark.parametrize("failure", ["copy", "rename", "none"])
def test_atomic_launcher_publication(tmp_path, failure):
    source = tmp_path / "target-launcher"
    source.write_bytes(b"exact target tooling")
    target = tmp_path / "ecr-update"
    target.write_bytes(b"known-good launcher")
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
{"install() { return 56; }" if failure == "copy" else ":"}
{"mv() { return 57; }" if failure == "rename" else ":"}
atomic_install_file {Q(str(source))} {Q(str(target))} root root 0755
""")
    assert not list(tmp_path.glob(".ecr-file.*"))
    if failure == "none":
        assert result.returncode == 0, result.stderr
        assert target.read_bytes() == source.read_bytes()
        assert target.stat().st_mode & 0o777 == 0o755
        assert not target.is_symlink()
    else:
        assert result.returncode == (56 if failure == "copy" else 57)
        assert target.read_bytes() == b"known-good launcher"


@pytest.mark.parametrize("field", ["source", "target"])
def test_atomic_publication_rejects_symlinks(tmp_path, field):
    source = tmp_path / "source"
    target = tmp_path / "target"
    original = tmp_path / "original"
    original.write_bytes(b"do not touch")
    source.write_bytes(b"new")
    if field == "source":
        source.unlink()
        source.symlink_to(original)
        target.write_bytes(b"old")
    else:
        target.symlink_to(original)
    result = bash(
        f"source {Q(str(COMMON))}; {PRIVILEGE_STUBS}; atomic_install_file {Q(str(source))} {Q(str(target))} root root 0755"
    )
    assert result.returncode != 0
    assert original.read_bytes() == b"do not touch"


@pytest.mark.parametrize("mode", ["fresh", "reconfigure"])
@pytest.mark.parametrize(
    "failure", ["db", "migration", "local", "https-setup", "https", "none"]
)
def test_installer_launcher_is_last_and_requires_health(tmp_path, mode, failure):
    tail = (
        (ROOT / "install/install.sh")
        .read_text()
        .split('upgrade_production_env "$release_dir"', 1)[1]
    )
    events = tmp_path / "events"
    result = bash(f"""
source {Q(str(COMMON))}
release_dir={Q(str(tmp_path))}; ECR_DB_TYPE=external; INSTALL_MODE={mode}
ECR_SERVICE_NAME=ecr; ECR_HTTPS_ENABLED=true; ECR_DOMAIN=example.invalid
CREATE_INITIAL_ADMIN=false; deploy_commit=target; ECR_GIT_REF=release; previous_release=old
ECR_INSTALL_DIR={Q(str(tmp_path))}
run_db_check() {{ echo db >>{Q(str(events))}; {"return 50" if failure == "db" else ":"}; }}
run_migrations() {{ echo migrate >>{Q(str(events))}; {"return 50" if failure == "migration" else ":"}; }}
activate_release() {{ echo activate >>{Q(str(events))}; }}
write_deployment_state() {{ :; }}; append_deployment_history() {{ :; }}
render_systemd_service() {{ :; }}; render_nginx_site() {{ :; }}
systemctl() {{ :; }}
wait_for_local_health() {{ echo local >>{Q(str(events))}; {"return 50" if failure == "local" else ":"}; }}
configure_https() {{ echo tls-config >>{Q(str(events))}; HTTPS_SETUP_FAILED={"true" if failure == "https-setup" else "false"}; }}
https_health_check() {{ echo https >>{Q(str(events))}; {"return 50" if failure == "https" else ":"}; }}
curl() {{ :; }}
install_update_launcher() {{ echo launcher >>{Q(str(events))}; }}
git_as_deployer() {{ echo exact-target; }}
{tail}
""")
    sequence = events.read_text().splitlines()
    if failure == "none":
        assert result.returncode == 0, result.stderr
        assert sequence == [
            "db",
            "migrate",
            "activate",
            "local",
            "tls-config",
            "https",
            "launcher",
        ]
    else:
        assert result.returncode != 0
        assert "launcher" not in sequence


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_metadata(path, values):
    path.write_text(
        "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())
    )


@pytest.fixture
def recovery(deployment, tmp_path):
    base, _, config, *_ = deployment
    service = pwd.getpwuid(os.getuid()).pw_name
    config.write_text(
        config.read_text().replace(
            "ECR_SERVICE_USER=mock_service", f"ECR_SERVICE_USER={service}"
        )
        + "ECR_HTTPS_ENABLED=true\n"
    )
    executable(base / "current/.venv/bin/python", f'exec {Q(sys.executable)} "$@"\n')
    data = base / "shared/data"
    signature = data / "protected/signature.png"
    signature.write_bytes(b"backup-signature")
    sql = base / "backups/chosen.sql.gz"
    sql.write_bytes(gzip.compress(b"SELECT 'chosen recovery';\n"))
    files = base / "backups/chosen.files.tar.gz"
    with tarfile.open(files, "w:gz") as archive:
        archive.add(data, arcname="data")
    signature.write_bytes(b"newer-signature")
    (data / "newer-only.txt").write_bytes(b"newer data")
    meta_path = base / "backups/chosen.meta"
    values = {
        "DATABASE_NAME": "ecr",
        "SQL_SHA256": digest(sql),
        "FILES_SHA256": digest(files),
    }
    save_metadata(meta_path, values)
    (base / "shared/.env").write_text(
        f"DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_NAME=ecr\nDB_USER=ecr\nDB_PASSWORD='never-log-this-secret'\nSTORAGE_ROOT='{data / 'protected'}'\n"
    )
    tooling = tmp_path / "tooling"
    shutil.copytree(ROOT / "install", tooling)
    events = tmp_path / "events"
    common = tooling / "lib/common.sh"
    common.write_text(
        common.read_text()
        + PRIVILEGE_STUBS
        + f"""
acquire_deployment_lock() {{ :; }}
systemctl() {{ echo "service:$*" >>{Q(str(events))}; }}
run_db_check() {{ echo db-check >>{Q(str(events))}; }}
wait_for_local_health() {{ echo local-health >>{Q(str(events))}; [[ ${{TEST_FAILURE:-}} != health ]]; }}
https_health_check() {{ echo https-health >>{Q(str(events))}; [[ ${{TEST_FAILURE:-}} != https ]]; }}
"""
    )
    bins = tmp_path / "bin"
    executable(
        bins / "mysqldump",
        f"""
echo safety-backup >>{Q(str(events))}
[[ ${{TEST_FAILURE:-}} != backup ]] || exit 52
printf 'SAFETY CURRENT DATABASE\\n'
""",
    )
    captured = tmp_path / "restored.sql"
    executable(
        bins / "mysql",
        f"""
echo mysql-restore >>{Q(str(events))}
command tee {Q(str(captured))} >/dev/null
[[ ${{TEST_FAILURE:-}} != mysql ]] || exit 51
""",
    )
    env = dict(
        os.environ, ECR_CONFIG_FILE=str(config), PATH=f"{bins}:{os.environ['PATH']}"
    )
    return {
        "base": base,
        "sql": sql,
        "files": files,
        "meta": meta_path,
        "values": values,
        "data": data,
        "tooling": tooling,
        "events": events,
        "env": env,
        "captured": captured,
    }


def restore(case, answer="RESTORE ecr\n"):
    return subprocess.run(
        ["bash", str(case["tooling"] / "restore.sh"), str(case["sql"])],
        input=answer,
        env=case["env"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def custom_storage(case, tmp_path):
    custom = tmp_path / "custom signatures"
    custom.mkdir()
    (custom / "customer.png").write_bytes(b"backup-custom-signature")
    archive = case["base"] / "backups/chosen.storage.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(custom, arcname=".")
    (custom / "customer.png").write_bytes(b"newer-custom-signature")
    (custom / "newer-only.txt").write_bytes(b"newer custom data")
    env_file = case["base"] / "shared/.env"
    env_file.write_text(
        env_file.read_text().split("STORAGE_ROOT=")[0] + f"STORAGE_ROOT='{custom}'\n"
    )
    case["values"].update(
        STORAGE_ROOT=str(custom.resolve()), STORAGE_SHA256=digest(archive)
    )
    save_metadata(case["meta"], case["values"])
    return custom, archive


@pytest.mark.parametrize("custom", [False, True])
def test_restore_database_and_corresponding_persistent_files(
    recovery, tmp_path, custom
):
    case = recovery
    if custom:
        storage, _ = custom_storage(case, tmp_path)
    result = restore(case)
    assert result.returncode == 0, result.stderr
    assert "never-log-this-secret" not in result.stdout + result.stderr
    assert case["captured"].read_bytes() == gzip.decompress(case["sql"].read_bytes())
    assert (
        case["data"] / "protected/signature.png"
    ).read_bytes() == b"backup-signature"
    assert not (case["data"] / "newer-only.txt").exists()
    if custom:
        assert (storage / "customer.png").read_bytes() == b"backup-custom-signature"
        assert not (storage / "newer-only.txt").exists()
    events = case["events"].read_text().splitlines()
    assert events == [
        "service:is-active --quiet ecr.service",
        "service:stop ecr.service",
        "safety-backup",
        "mysql-restore",
        "db-check",
        "service:start ecr.service",
        "local-health",
        "https-health",
    ]
    safety = [path for path in (case["base"] / "backups").glob("ecr-*.sql.gz")]
    assert len(safety) == 1
    assert gzip.decompress(safety[0].read_bytes()) == b"SAFETY CURRENT DATABASE\n"
    base_name = str(safety[0]).removesuffix(".sql.gz")
    with tarfile.open(base_name + ".files.tar.gz") as archive:
        assert (
            archive.extractfile("data/protected/signature.png").read()
            == b"newer-signature"
        )
        assert "data/newer-only.txt" in archive.getnames()
    assert list(case["data"].parent.glob(".ecr-before-restore.*"))
    assert not list(case["data"].parent.glob(".ecr-restore-stage.*"))
    assert not list((case["base"] / "state").glob("restore.*"))


@pytest.mark.parametrize(
    "failure",
    [
        "sql-checksum",
        "files-checksum",
        "storage-checksum",
        "storage-mismatch",
        "missing-archive",
        "incomplete",
        "release-root",
        "metadata-code",
    ],
)
def test_restore_rejects_invalid_backup_sets_before_mutation(
    recovery, tmp_path, failure
):
    case = recovery
    if failure in ("storage-checksum", "storage-mismatch", "release-root"):
        custom_storage(case, tmp_path)
    if failure == "sql-checksum":
        case["values"]["SQL_SHA256"] = "0" * 64
    elif failure == "files-checksum":
        case["values"]["FILES_SHA256"] = "0" * 64
    elif failure == "storage-checksum":
        case["values"]["STORAGE_SHA256"] = "0" * 64
    elif failure == "storage-mismatch":
        case["values"]["STORAGE_ROOT"] = str(tmp_path / "other root")
    elif failure == "missing-archive":
        case["files"].unlink()
    elif failure == "incomplete":
        case["values"].update(BACKUP_FORMAT="2")
    elif failure == "release-root":
        env_file = case["base"] / "shared/.env"
        env_file.write_text(
            env_file.read_text().split("STORAGE_ROOT=")[0]
            + f"STORAGE_ROOT={case['base'] / 'releases/old/private'}\n"
        )
    elif failure == "metadata-code":
        case["values"]["STORAGE_ROOT"] = f"$(touch {tmp_path / 'executed'})"
        case["values"]["SQL_SHA256"] = "0" * 64
    save_metadata(case["meta"], case["values"])
    result = restore(case)
    assert result.returncode != 0
    assert not case["events"].exists()
    assert not case["captured"].exists()
    assert not (tmp_path / "executed").exists()
    assert (case["data"] / "protected/signature.png").read_bytes() == b"newer-signature"
    assert not list(case["data"].parent.glob(".ecr-restore-stage.*"))


@pytest.mark.parametrize(
    "kind", ["traversal", "absolute", "symlink", "hardlink", "wrong-root"]
)
def test_restore_rejects_unsafe_tar_members(recovery, kind):
    case = recovery
    with tarfile.open(case["files"], "w:gz") as archive:
        name = {
            "traversal": "data/../../outside",
            "absolute": "/tmp/outside",
            "wrong-root": "unrelated/file",
        }.get(kind, "data/link")
        info = tarfile.TarInfo(name)
        if kind in ("symlink", "hardlink"):
            info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        else:
            info.size = 4
            archive.addfile(info, io.BytesIO(b"evil"))
    case["values"]["FILES_SHA256"] = digest(case["files"])
    save_metadata(case["meta"], case["values"])
    result = restore(case)
    assert result.returncode != 0
    assert "archive" in result.stderr.lower()
    assert not case["events"].exists()
    assert (case["data"] / "newer-only.txt").exists()


@pytest.mark.parametrize("acknowledge", [False, True])
def test_older_db_only_backup_requires_explicit_acknowledgement(recovery, acknowledge):
    case = recovery
    case["files"].unlink()
    case["values"].pop("FILES_SHA256")
    save_metadata(case["meta"], case["values"])
    answer = (
        "ACKNOWLEDGE MISSING FILES ecr\n" if acknowledge else ""
    ) + "RESTORE ecr\n"
    result = restore(case, answer)
    assert "DB-only recovery" in result.stdout
    assert (case["data"] / "newer-only.txt").exists()
    if acknowledge:
        assert result.returncode == 0, result.stderr
        assert case["captured"].exists()
    else:
        assert result.returncode != 0
        assert not case["captured"].exists()


@pytest.mark.parametrize("failure", ["mysql", "health", "https", "backup"])
def test_restore_failures_retain_safety_backup_and_safe_service_state(
    recovery, failure
):
    case = recovery
    case["env"]["TEST_FAILURE"] = failure
    result = restore(case)
    assert result.returncode != 0
    events = case["events"].read_text().splitlines()
    if failure == "backup":
        assert "mysql-restore" not in events
        assert "service:start ecr.service" in events
        assert (case["data"] / "newer-only.txt").exists()
    else:
        safety = list((case["base"] / "backups").glob("ecr-*.sql.gz"))
        assert len(safety) == 1
        assert gzip.decompress(safety[0].read_bytes()) == b"SAFETY CURRENT DATABASE\n"
        assert str(safety[0]) in result.stderr
        assert "Application remains stopped" in result.stderr
        if failure == "mysql":
            assert "service:start ecr.service" not in events
            assert (case["data"] / "newer-only.txt").exists()
        else:
            assert events[-1] == "service:stop ecr.service"
