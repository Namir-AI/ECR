"""Runtime-only permissions and real imports; privileges/uv are simulated."""

import importlib.util
import os
import sys
import venv

import pytest

from tests.test_deployment import COMMON, PRIVILEGE_STUBS, ROOT, Q, bash, executable


def runtime_tools():
    spec = importlib.util.spec_from_file_location(
        "runtime_tools", ROOT / "install/lib/runtime_tools.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("hardlinked", [False, True])
def test_sync_hardens_restrictive_packages_without_widening_private_objects(
    tmp_path, hardlinked
):
    app = tmp_path / "application"
    (app / "app").mkdir(parents=True)
    (app / "app/__init__.py").write_text("")
    (app / "app/main.py").write_text("import PIL\nassert PIL.VALUE == 'installed'\n")
    environment = app / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    site = next((environment / "lib").glob("python*/site-packages"))
    package = site / "PIL"
    package.mkdir()
    base = tmp_path / "installation"
    runtime = executable(
        base / "shared/uv-python/cpython/bin/python3.12",
        f'exec {Q(sys.executable)} "$@"\n',
    )
    cached = base / "shared/uv-cache/PIL.py"
    cached.parent.mkdir(parents=True)
    cached.write_text("VALUE = 'installed'\n")
    cached.chmod(0o600)
    init = package / "__init__.py"
    if hardlinked:
        os.link(cached, init)
    else:
        init.write_bytes(cached.read_bytes())
    init.chmod(0o600)
    entry = executable(environment / "bin/probe", "exit 0\n")
    entry.chmod(0o700)
    for directory in (environment, environment / "lib", site, package):
        directory.chmod(0o700)
    secrets = []
    for path in (
        base / "shared/.env",
        base / "deploy-key",
        base / "backups/dump.sql.gz",
        base / "shared/data/protected/signatures/sign.png",
        tmp_path / "run-bootstrap/token",
        base / "state/deployment.env",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private")
        path.chmod(0o640 if path.name == ".env" else 0o600)
        if path.parent.name in {"run-bootstrap", "signatures", "backups"}:
            path.parent.chmod(0o700)
        secrets.extend([path, path.parent])
    (app / ".env").symlink_to(base / "shared/.env")
    before = {
        p: (p.stat().st_mode, p.stat().st_ino)
        for p in [cached, cached.parent, runtime, *secrets]
    }
    bins = tmp_path / "bin"
    executable(
        bins / "uv",
        f"""
case "$1 ${{2:-}}" in
  'python install') :;;
  'python find') printf '%s\\n' {Q(str(runtime))};;
  sync*) [[ $* == *'--frozen --no-dev'* && $* == *'--link-mode copy'* ]];;
  *) exit 91;;
esac
""",
    )
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
SCRIPT_DIR={Q(str(ROOT / "install"))}
ECR_INSTALL_DIR={Q(str(base))}; ECR_SERVICE_USER=mock_service
export PATH={Q(str(bins))}:$PATH
umask 0077
sync_application_dependencies {Q(str(app))}
[[ $(umask) == 0077 ]]
export PYTHONDONTWRITEBYTECODE=1
run_application_import_check {Q(str(app))}
{Q(str(entry))}
""")
    assert result.returncode == 0, result.stderr
    assert init.stat().st_mode & 0o777 == 0o644
    assert entry.stat().st_mode & 0o777 == 0o755
    for directory in (environment, environment / "lib", site, package):
        assert directory.stat().st_mode & 0o777 == 0o755
    assert all(
        (p.stat().st_mode, p.stat().st_ino) == fingerprint
        for p, fingerprint in before.items()
    )
    assert not list(environment.rglob(".ecr-runtime-*"))
    if hardlinked:
        assert init.stat().st_ino != cached.stat().st_ino


@pytest.mark.parametrize("kind", ["venv", "file", "directory"])
def test_runtime_hardening_rejects_external_symlinks(tmp_path, kind):
    app = tmp_path / "application"
    app.mkdir()
    outside = tmp_path / "private"
    outside.mkdir(mode=0o700)
    secret = outside / "secret"
    secret.write_bytes(b"private")
    secret.chmod(0o600)
    root = app / ".venv"
    if kind == "venv":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir()
        (root / "substituted").symlink_to(secret if kind == "file" else outside)
    with pytest.raises(ValueError):
        runtime_tools().harden_runtime(app, outside)
    assert outside.stat().st_mode & 0o777 == 0o700
    assert secret.stat().st_mode & 0o777 == 0o600


def test_runtime_hardening_keeps_managed_interpreter_symlink(tmp_path):
    app = tmp_path / "application"
    (app / ".venv/bin").mkdir(parents=True)
    python_root = tmp_path / "shared/uv-python"
    target = executable(python_root / "cpython/bin/python3.12", "exit 0\n")
    original_mode = target.stat().st_mode
    link = app / ".venv/bin/python"
    link.symlink_to(target)
    runtime_tools().harden_runtime(app, python_root)
    assert link.is_symlink() and target.stat().st_mode == original_mode


def test_import_smoke_test_uses_target_environment_and_service_boundary(tmp_path):
    commands = tmp_path / "commands"
    result = bash(f"""
source {Q(str(COMMON))}
run_as_service() {{ printf '%s\\n%s\\n' "$PWD" "$*" >{Q(str(commands))}; }}
run_application_import_check {Q(str(tmp_path))}
""")
    assert result.returncode == 0, result.stderr
    cwd, command = commands.read_text().splitlines()
    assert cwd == str(tmp_path)
    assert command == f"{tmp_path}/.venv/bin/python -c import app.main"
    assert "/run/ecr-update." not in command


def test_recovery_uses_bootstrap_helper_when_restored_application_predates_it(tmp_path):
    app = tmp_path / "older-application"
    package = app / ".venv/lib/site-packages/package.py"
    package.parent.mkdir(parents=True)
    package.write_text("VALUE = 'restored'\n")
    package.chmod(0o600)
    assert not (app / "install/lib/runtime_tools.py").exists()
    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir(mode=0o700)
    helper = bootstrap / "install/lib/runtime_tools.py"
    helper.parent.mkdir(parents=True)
    helper.write_bytes((ROOT / "install/lib/runtime_tools.py").read_bytes())
    helper.chmod(0o600)
    base = tmp_path / "installation"
    (base / "shared/uv-python").mkdir(parents=True)
    result = bash(f"""
source {Q(str(COMMON))}
SCRIPT_DIR={Q(str(bootstrap / "install"))}
ECR_INSTALL_DIR={Q(str(base))}
ECR_PRODUCTION_PYTHON={Q(sys.executable)}
run_as_service() {{ echo 'Bootstrap helper must not run as service' >&2; return 99; }}
harden_application_runtime {Q(str(app))}
""")
    assert result.returncode == 0, result.stderr
    assert package.stat().st_mode & 0o777 == 0o644
    assert bootstrap.stat().st_mode & 0o777 == 0o700
    assert helper.stat().st_mode & 0o777 == 0o600


def test_readiness_check_imports_real_application(tmp_path):
    # Actual FastAPI/Pillow/attachment imports, with only account switching mocked.
    (tmp_path / "app").symlink_to(ROOT / "app", target_is_directory=True)
    executable(tmp_path / ".venv/bin/python", f'exec {Q(sys.executable)} "$@"\n')
    result = bash(f"""
source {Q(str(COMMON))}
run_as_service() {{
    [[ $PWD == {Q(str(tmp_path))} && $1 == {Q(str(tmp_path / ".venv/bin/python"))} ]]
    "$@"
}}
run_application_import_check {Q(str(tmp_path))}
""")
    assert result.returncode == 0, result.stderr


def test_hardlink_copy_failure_preserves_original_and_cache(tmp_path, monkeypatch):
    app = tmp_path / "application"
    (app / ".venv").mkdir(parents=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    original = cache / "private.py"
    original.write_bytes(b"unchanged")
    original.chmod(0o600)
    installed = app / ".venv/private.py"
    os.link(original, installed)
    module = runtime_tools()

    def fail(*args, **kwargs):
        raise OSError("simulated copy error")

    monkeypatch.setattr(module.shutil, "copyfileobj", fail)
    with pytest.raises(OSError):
        module.harden_runtime(app, cache)
    assert original.read_bytes() == installed.read_bytes() == b"unchanged"
    assert original.stat().st_mode & 0o777 == 0o600
    assert original.stat().st_ino == installed.stat().st_ino
    assert not list((app / ".venv").glob(".ecr-runtime-*"))
