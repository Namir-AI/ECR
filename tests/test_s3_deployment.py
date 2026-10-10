"""Storage installation/persistence/backup checks; no AWS or root operations."""

import gzip
import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from app.core.config import AppSettings
from tests.test_deployment import (
    COMMON,
    HELPER,
    PRIVILEGE_STUBS,
    ROOT,
    Q,
    bash,
    executable,
)
from tests.test_deployment import deployment as deployment  # noqa: PLC0414


def installer_function(name, following):
    text = (ROOT / "install/install.sh").read_text()
    return name + "() {" + text.split(name + "() {", 1)[1].split(following + "()", 1)[0]


def storage_config_functions(tmp_path):
    return (
        installer_function("validate_storage_values", "configure_local_mysql").replace(
            "/etc/ecr", str(tmp_path / "config")
        )
        + f"\nSCRIPT_DIR={Q(str(ROOT / 'install'))}\n"
        + f"ensure_production_python() {{ ECR_PRODUCTION_PYTHON={Q(sys.executable)}; }}\n"
    )


def deployment_values(tmp_path, mode="fresh"):
    return f"""
ECR_CONFIG_FILE={Q(str(tmp_path / "config/deployment.conf"))}; INSTALL_MODE={mode}
ECR_INSTALL_DIR={Q(str(tmp_path))}; ECR_DOMAIN=example.invalid; ECR_GIT_REF=main
ECR_REPOSITORY_URL=https://github.com/Namir-AI/ECR.git; ECR_DEPLOY_KEY_FILE=""
ECR_HTTPS_ENABLED=true; ECR_SSL_EMAIL=it@example.invalid
ECR_DB_TYPE=external; DB_HOST=database.internal; DB_NAME=ecr
ECR_SERVICE_USER=ecr; ECR_SERVICE_NAME=ecr; ECR_APP_PORT=8000
"""


@pytest.mark.parametrize("backend", ["local", "s3"])
def test_installer_prompt_and_protected_env(tmp_path, backend):
    release = tmp_path / "releases/target"
    shutil.copytree(ROOT / "install", release / "install")
    shutil.copyfile(ROOT / ".env.example", release / ".env.example")
    executable(release / ".venv/bin/python", f'exec {Q(sys.executable)} "$@"\n')
    prompts = tmp_path / "prompts"
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
{storage_config_functions(tmp_path)}
{deployment_values(tmp_path)}
{installer_function("prompt_storage_values", "prompt_recovery_database_values")}
{installer_function("write_production_env", "render_systemd_service")}
ECR_INSTALL_DIR={Q(str(tmp_path))}; ECR_SERVICE_USER=ecr
ECR_APP_PORT=8000; ECR_HTTPS_ENABLED=true; INSTALL_MODE=fresh
DB_HOST=127.0.0.1; DB_PORT=3306; DB_NAME=ecr; DB_USER=ecr; DB_PASSWORD=never-log-secret
prompt_value() {{
    printf '%s|%s\\n' "$1" "$2" >>{Q(str(prompts))}
    case "$1" in
      File/object*) printf {Q("2" if backend == "s3" else "1")};;
      'S3 bucket name') printf test-private-bucket;;
      'AWS region') printf eu-west-1;;
      'ECR S3 prefix') printf training/ecr/;;
    esac
}}
ensure_install_layout
prompt_storage_values
write_deployment_config
write_production_env {Q(str(release))}
upgrade_production_env {Q(str(release))}
""")
    assert result.returncode == 0, result.stderr
    assert "never-log-secret" not in result.stdout + result.stderr
    values = dotenv_values(tmp_path / "shared/.env")
    assert values["STORAGE_BACKEND"] == backend
    saved = (tmp_path / "config/deployment.conf").read_text()
    assert f"ECR_STORAGE_BACKEND={backend}\n" in saved
    assert (tmp_path / "shared/.env").stat().st_mode & 0o777 == 0o640
    assert values["STORAGE_ROOT"] == str(tmp_path / "shared/data/protected")
    if backend == "s3":
        assert (values["S3_BUCKET"], values["S3_REGION"], values["S3_PREFIX"]) == (
            "test-private-bucket",
            "eu-west-1",
            "training/ecr",
        )
        assert "AWS region|\n" in prompts.read_text()
        assert "EC2 IAM role" in result.stderr
        assert "ECR_S3_PREFIX=training/ecr\n" in saved
    else:
        assert prompts.read_text().count("\n") == 1
    assert "access key" not in prompts.read_text().lower()
    assert "secret key" not in prompts.read_text().lower()


@pytest.mark.parametrize(
    "changed", ["STORAGE_BACKEND", "S3_BUCKET", "S3_REGION", "S3_PREFIX", None]
)
def test_upgrade_and_reconfigure_preserve_storage_and_secrets(tmp_path, changed):
    target = tmp_path / ".env"
    original = (
        "STORAGE_BACKEND=s3\nS3_BUCKET=test-private-bucket\nS3_REGION=eu-west-1\nS3_PREFIX=training/ecr\nDB_PASSWORD=retain-secret\nCUSTOM=keep\nSTORAGE_ROOT="
        + str(tmp_path / "data")
        + "\n"
    )
    target.write_text(original)
    target.chmod(0o640)
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "upgrade",
            str(target),
            str(ROOT / ".env.example"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    values = dotenv_values(target)
    assert values["DB_PASSWORD"] == "retain-secret" and values["CUSTOM"] == "keep"
    before = target.read_bytes()
    pairs = {
        k: values[k] for k in ("STORAGE_BACKEND", "S3_BUCKET", "S3_REGION", "S3_PREFIX")
    }
    if changed:
        pairs[changed] = (
            "local"
            if changed == "STORAGE_BACKEND"
            else "another-bucket"
            if changed == "S3_BUCKET"
            else "ap-south-1"
            if changed == "S3_REGION"
            else "other-prefix"
        )
    body = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in pairs.items())
    result = subprocess.run(
        [sys.executable, str(HELPER), "write", str(target), str(ROOT / ".env.example")],
        input=body,
        capture_output=True,
        check=False,
    )
    if changed:
        assert result.returncode != 0 and b"reviewed recovery" in result.stderr
        assert target.read_bytes() == before
    else:
        assert result.returncode == 0, result.stderr
    assert b"retain-secret" not in result.stdout + result.stderr
    assert target.stat().st_mode & 0o777 == 0o640


def test_reconfigure_prompt_preserves_env_no_new_storage_prompts(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/.env").write_text(
        "STORAGE_BACKEND=s3\nS3_BUCKET=test-private-bucket\nS3_REGION=eu-west-1\nS3_PREFIX=ecr\n"
    )
    result = bash(f"""
source {Q(str(COMMON))}
{installer_function("prompt_storage_values", "prompt_recovery_database_values")}
ECR_INSTALL_DIR={Q(str(tmp_path))}; INSTALL_MODE=reconfigure
valid_current_release_path() {{ printf valid; }}
read_storage_environment() {{ ECR_STORAGE_BACKEND=s3; ECR_S3_BUCKET=test-private-bucket; ECR_S3_REGION=eu-west-1; ECR_S3_PREFIX=ecr; }}
prompt_value() {{ echo UNEXPECTED; return 91; }}
prompt_storage_values
[[ $ECR_STORAGE_BACKEND == s3 && $ECR_S3_PREFIX == ecr ]]
""")
    assert result.returncode == 0 and "UNEXPECTED" not in result.stdout


def test_service_preflight_exact_application_path(tmp_path):
    executable(
        tmp_path / ".venv/bin/python",
        '[[ $* == "-m app.storage.check" ]] || exit 88\nprintf success\n',
    )
    result = bash(
        f'source {Q(str(COMMON))}; run_as_service() {{ "$@"; }}; run_storage_check {Q(str(tmp_path))}'
    )
    assert result.returncode == 0 and result.stdout == "success"
    for script, var in (
        ("install.sh", "release_dir"),
        ("update.sh", "application_dir"),
    ):
        text = (ROOT / "install" / script).read_text()
        import_pos = text.rindex(f'run_application_import_check "${var}"')
        check_pos = text.rindex(f'run_storage_check "${var}"')
        migration = text.rindex(f'run_migrations "${var}"')
        assert import_pos < check_pos < migration


def test_saved_s3_configuration_survives_incomplete_install(tmp_path):
    # Private test destination replaces the fixed root-only config directory.
    writer = storage_config_functions(tmp_path)
    config = tmp_path / "config/deployment.conf"
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
{writer}
{installer_function("prompt_storage_values", "prompt_recovery_database_values")}
ECR_CONFIG_FILE={Q(str(config))}; INSTALL_MODE=recover
ECR_INSTALL_DIR={Q(str(tmp_path))}; ECR_DOMAIN=example.invalid; ECR_GIT_REF=main
ECR_REPOSITORY_URL=https://github.com/Namir-AI/ECR.git; ECR_DEPLOY_KEY_FILE=""
ECR_HTTPS_ENABLED=true; ECR_SSL_EMAIL=it@example.invalid
ECR_DB_TYPE=external; DB_HOST=database.internal; DB_NAME=ecr
ECR_SERVICE_USER=ecr; ECR_SERVICE_NAME=ecr; ECR_APP_PORT=8000
ECR_STORAGE_BACKEND=s3; ECR_S3_BUCKET=test-private-bucket; ECR_S3_REGION=eu-west-1; ECR_S3_PREFIX=training/ecr
write_deployment_config
ECR_STORAGE_BACKEND=local; ECR_S3_BUCKET=""; ECR_S3_REGION=""; ECR_S3_PREFIX=""
load_deployment_config
prompt_value() {{ echo UNEXPECTED; return 90; }}
prompt_storage_values
[[ $ECR_STORAGE_BACKEND == s3 && $ECR_S3_BUCKET == test-private-bucket && $ECR_S3_REGION == eu-west-1 && $ECR_S3_PREFIX == training/ecr ]]
""")
    assert result.returncode == 0, result.stderr
    assert "UNEXPECTED" not in result.stdout and config.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "field,value",
    [("bucket", "Invalid_Bucket"), ("region", "unknown"), ("prefix", "../ecr")],
)
def test_fresh_invalid_storage_never_persists_deployment_configuration(
    tmp_path, field, value
):
    chosen = {"bucket": "test-private-bucket", "region": "eu-west-1", "prefix": "ecr"}
    chosen[field] = value
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
{storage_config_functions(tmp_path)}
{deployment_values(tmp_path)}
{installer_function("prompt_storage_values", "prompt_recovery_database_values")}
prompt_value() {{
    case "$1" in
        File/object*) printf 2;;
        'S3 bucket name') printf '%s' {Q(chosen["bucket"])};;
        'AWS region') printf '%s' {Q(chosen["region"])};;
        'ECR S3 prefix') printf '%s' {Q(chosen["prefix"])};;
    esac
}}
prompt_storage_values
write_deployment_config
""")
    assert result.returncode != 0 and "Invalid storage configuration" in result.stderr
    assert "Reconfigure and resume incomplete installation" in result.stderr
    assert not (tmp_path / "config/deployment.conf").exists()
    assert not (tmp_path / "shared/.env").exists()


@pytest.mark.parametrize("backend", ["s3", "local"])
def test_incomplete_reconfigure_can_correct_invalid_saved_storage(tmp_path, backend):
    config = tmp_path / "config/deployment.conf"
    config.parent.mkdir()
    original = "ECR_STORAGE_BACKEND=s3\nECR_S3_BUCKET=INVALID\nECR_S3_REGION=unknown\nECR_S3_PREFIX=../bad\n"
    config.write_text(original)
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
{storage_config_functions(tmp_path)}
{deployment_values(tmp_path, "recover-reconfigure")}
{installer_function("prompt_storage_values", "prompt_recovery_database_values")}
load_deployment_config
prompt_value() {{
    case "$1" in
        File/object*) printf {Q("2" if backend == "s3" else "1")};;
        'S3 bucket name') printf test-private-bucket;;
        'AWS region') printf eu-west-1;;
        'ECR S3 prefix') printf corrected/ecr/;;
    esac
}}
prompt_storage_values
write_deployment_config
""")
    assert result.returncode == 0, result.stderr
    saved = config.read_text()
    assert f"ECR_STORAGE_BACKEND={backend}\n" in saved
    assert "INVALID" not in saved and "../bad" not in saved and "unknown" not in saved
    if backend == "s3":
        assert "ECR_S3_PREFIX=corrected/ecr\n" in saved
    assert config.stat().st_mode & 0o777 == 0o600


def test_invalid_saved_recover_reports_supported_reconfiguration_without_writing(
    tmp_path,
):
    config = tmp_path / "config/deployment.conf"
    config.parent.mkdir()
    original = "ECR_STORAGE_BACKEND=s3\nECR_S3_BUCKET=INVALID\nECR_S3_REGION=unknown\nECR_S3_PREFIX=../bad\n"
    config.write_text(original)
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
{storage_config_functions(tmp_path)}
{deployment_values(tmp_path, "recover")}
{installer_function("prompt_storage_values", "prompt_recovery_database_values")}
load_deployment_config
prompt_value() {{ return 99; }}
prompt_storage_values
write_deployment_config
""")
    assert result.returncode != 0
    assert "Reconfigure and resume incomplete installation" in result.stderr
    assert config.read_text() == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("bucket", "valid-private-bucket"),
        ("bucket", "INVALID"),
        ("bucket", "a..b"),
        ("bucket", "192.168.1.1"),
        ("region", "eu-west-1"),
        ("region", "not-a-region"),
        ("region", ""),
        ("prefix", "jobs/ecr/"),
        ("prefix", "/ecr"),
        ("prefix", "ecr//"),
        ("prefix", "ecr/.."),
        ("prefix", ""),
    ],
)
def test_installer_and_application_storage_validation_parity(field, value):
    inputs = {
        "s3_bucket": "test-private-bucket",
        "s3_region": "eu-west-1",
        "s3_prefix": "ecr",
    }
    inputs[f"s3_{field}"] = value
    try:
        settings = AppSettings(_env_file=None, storage_backend="s3", **inputs)
    except ValidationError:
        settings = None
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "app/storage/configuration.py"),
            "s3",
            *inputs.values(),
        ],
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == (settings is not None)
    if settings:
        assert result.stdout.split(b"\0")[:-1] == [
            b"s3",
            settings.s3_bucket.encode(),
            settings.s3_region.encode(),
            settings.s3_prefix.encode(),
        ]


@pytest.mark.parametrize("db_type", ["local", "external"])
def test_fresh_installer_includes_official_print_font_without_fontconfig(
    tmp_path, db_type
):
    packages = tmp_path / "packages"
    result = bash(f"""
source {Q(str(COMMON))}
{installer_function("install_system_packages", "install_uv")}
ECR_DB_TYPE={db_type}; ECR_HTTPS_ENABLED=false
apt-get() {{ printf '%s\\n' "$*" >>{Q(str(packages))}; }}
install_system_packages
""")
    assert result.returncode == 0, result.stderr
    words = packages.read_text().split()
    assert (
        "fonts-dejavu-core" in words
        and "fontconfig" not in words
        and "fc-cache" not in words
    )


def test_s3_backup_records_namespace_without_local_archives(deployment, tmp_path):
    base, _source, config, old, _target = deployment
    active = (base / "current").resolve()
    executable(active / ".venv/bin/python", f'exec {Q(sys.executable)} "$@"\n')
    (base / "shared/.env").write_text(
        f"DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_NAME=ecr\nDB_USER=ecr\nDB_PASSWORD=private-secret\nSTORAGE_ROOT={base / 'shared/data/protected'}\nSTORAGE_BACKEND=s3\nS3_BUCKET=test-private-bucket\nS3_REGION=eu-west-1\nS3_PREFIX=ecr\n"
    )
    before = (base / "shared/data/protected/signature.png").read_bytes()
    tooling = tmp_path / "tooling"
    shutil.copytree(ROOT / "install", tooling)
    common = tooling / "lib/common.sh"
    common.write_text(
        common.read_text() + PRIVILEGE_STUBS + "\nacquire_deployment_lock() { :; }\n"
    )
    bins = tmp_path / "bin"
    executable(bins / "mysqldump", "printf 'CREATE TABLE demo(id INT);\\n'\n")
    result = subprocess.run(
        ["bash", str(tooling / "backup.sh")],
        env=dict(
            os.environ, PATH=f"{bins}:{os.environ['PATH']}", ECR_CONFIG_FILE=str(config)
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    sql = Path(result.stdout.strip())
    stem = str(sql).removesuffix(".sql.gz")
    meta = dotenv_values(stem + ".meta")
    assert meta["STORAGE_BACKEND"] == "s3" and meta["S3_PREFIX"] == "ecr"
    assert meta["APPLICATION_COMMIT"] == old and meta["BACKUP_COMPLETE"] == "true"
    assert gzip.decompress(sql.read_bytes()).startswith(b"CREATE")
    assert meta["SQL_SHA256"] == hashlib.sha256(sql.read_bytes()).hexdigest()
    assert (
        not Path(stem + ".files.tar.gz").exists()
        and not Path(stem + ".storage.tar.gz").exists()
    )
    assert (base / "shared/data/protected/signature.png").read_bytes() == before
    assert "S3 objects are NOT included" in result.stderr


@pytest.mark.parametrize("finding", [None, "backend", "namespace", "archives"])
def test_s3_restore_never_claims_to_restore_objects(tmp_path, finding):
    spec = importlib.util.spec_from_file_location(
        "restore_s3", ROOT / "install/lib/restore_tools.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = tmp_path / "test.sql.gz"
    sql.write_bytes(gzip.compress(b"SQL"))
    meta = {
        "DATABASE_NAME": "ecr",
        "SQL_SHA256": hashlib.sha256(sql.read_bytes()).hexdigest(),
        "STORAGE_BACKEND": "s3",
        "S3_BUCKET": "test-private-bucket",
        "S3_REGION": "eu-west-1",
        "S3_PREFIX": "ecr",
    }
    if finding == "backend":
        meta["STORAGE_BACKEND"] = "local"
    if finding == "namespace":
        meta["S3_PREFIX"] = "other"
    if finding == "archives":
        Path(str(sql).removesuffix(".sql.gz") + ".files.tar.gz").write_bytes(
            b"unexpected"
        )
    (tmp_path / "test.meta").write_text("\n".join(f"{k}={v}" for k, v in meta.items()))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    args = (
        sql,
        tmp_path,
        tmp_path / "data",
        "ecr",
        pwd.getpwuid(os.getuid()).pw_name,
        workspace,
        "s3",
        "test-private-bucket",
        "eu-west-1",
        "ecr",
    )
    if finding:
        with pytest.raises(SystemExit, match="mismatch|unexpectedly"):
            module.prepare(*args)
    else:
        module.prepare(*args)
        assert json.loads((workspace / "plan.json").read_text())["entries"] == []
    script = (ROOT / "install/restore.sh").read_text()
    assert script.index("ACKNOWLEDGE S3 RECOVERY") < script.index(
        'systemctl stop "$ECR_SERVICE_NAME.service"',
        script.index('read -r -p "Type RESTORE'),
    )
