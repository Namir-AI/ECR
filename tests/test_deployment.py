"""Deployment integration checks: real Git/files; mocked root/services/uv/MySQL."""

from __future__ import annotations

import gzip
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "install/lib/common.sh"
HELPER = ROOT / "install/lib/env_tools.py"
LAUNCHER = ROOT / "install/ecr-update.sh"
Q = shlex.quote

# No test escalates privileges or changes /etc, /run, systemd, or production.
PRIVILEGE_STUBS = """
require_root() { :; }
chown() { :; }
install() {
    if [[ -n ${ECR_TEST_OWNERSHIP_LOG:-} ]]; then printf '%s\n' "$*" >>"$ECR_TEST_OWNERSHIP_LOG"; fi
    local -a args=()
    while (( $# )); do
        case "$1" in -o|-g) shift 2;; *) args+=("$1"); shift;; esac
    done
    command install "${args[@]}"
}
run_as_service() { "$@"; }
"""


def bash(code: str, *, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", code],
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )


def executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + content)
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("kind", ["direct", "release_symlink", "root_symlink"])
def test_uv_canonical_valid(tmp_path, kind):
    runtime = tmp_path / "shared/uv-python/cpython/bin/python3.12"
    executable(runtime, f'exec {Q(sys.executable)} "$@"\n')
    candidate = runtime
    if kind == "release_symlink":
        candidate = tmp_path / "releases/old/.venv/bin/python"
        candidate.parent.mkdir(parents=True)
        candidate.symlink_to(runtime)
    if kind == "root_symlink":
        root_link = tmp_path / "installation"
        root_link.symlink_to(tmp_path, target_is_directory=True)
        candidate = root_link / "shared/uv-python/cpython/bin/python3.12"
    executable(
        tmp_path / "bin/uv",
        f"""
[[ $UV_PYTHON_INSTALL_DIR == {Q(str(tmp_path / "shared/uv-python"))} ]]
[[ $UV_CACHE_DIR == {Q(str(tmp_path / "shared/uv-cache"))} ]]
if [[ $2 == find ]]; then printf '%s\\n' {Q(str(candidate))}; fi
""",
    )
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
ECR_INSTALL_DIR={Q(str(tmp_path))}
ECR_SERVICE_USER=mock_service
export PATH={Q(str(tmp_path / "bin"))}:$PATH
ensure_production_python
printf '%s' "$ECR_PRODUCTION_PYTHON"
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(runtime.resolve())


@pytest.mark.parametrize(
    "kind", ["system", "sibling", "escape", "missing", "nonexec", "version", "pypy"]
)
def test_uv_canonical_rejected(tmp_path, kind):
    root = tmp_path / "shared/uv-python"
    root.mkdir(parents=True)
    outside = executable(tmp_path / "outside/python", "exit 0\n")
    candidates = {
        "system": Path("/usr/bin/python3"),
        "sibling": executable(
            tmp_path / "shared/uv-python-evil/bin/python", "exit 0\n"
        ),
        "escape": root / "escaped-python",
        "missing": root / "missing",
        "nonexec": root / "nonexec",
        "version": executable(root / "wrong-version", "exit 1\n"),
        "pypy": executable(
            root / "pypy",
            f'exec {Q(sys.executable)} -c \'import sys; sys.implementation.name="pypy"; exec(sys.argv[1])\' "$2"\n',
        ),
    }
    candidates["escape"].symlink_to(outside)
    candidates["nonexec"].write_text("not executable")
    executable(
        tmp_path / "bin/uv",
        f"if [[ $2 == find ]]; then printf '%s\\n' {Q(str(candidates[kind]))}; fi\n",
    )
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
ECR_INSTALL_DIR={Q(str(tmp_path))}; ECR_SERVICE_USER=mock_service
export PATH={Q(str(tmp_path / "bin"))}:$PATH
ensure_production_python
""")
    assert result.returncode != 0
    assert "[ECR] ERROR" in result.stderr


def git(*args, cwd=None):
    return subprocess.check_output(["git", *map(str, args)], cwd=cwd, text=True).strip()


@pytest.fixture
def deployment(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git("init", "-q", "-b", "main", cwd=source)
    git("config", "user.name", "Deployment test", cwd=source)
    git("config", "user.email", "deployment@example.invalid", cwd=source)
    shutil.copytree(ROOT / "install", source / "install")
    (source / "app").mkdir()
    (source / "app/main.py").write_text("# fixture only\n")
    (source / "pyproject.toml").write_text("# fixture only\n")
    (source / "uv.lock").write_text("# fixture only\n")
    (source / "alembic.ini").write_text("# fixture only\n")
    shutil.copyfile(ROOT / ".env.example", source / ".env.example")
    git("add", ".", cwd=source)
    git("commit", "-qm", "old deployment", cwd=source)
    old = git("rev-parse", "HEAD", cwd=source)
    (source / "revision").write_text("new")
    git("add", ".", cwd=source)
    git("commit", "-qm", "target deployment", cwd=source)
    target = git("rev-parse", "HEAD", cwd=source)
    base = tmp_path / "ecr"
    base.mkdir()
    git("clone", "--mirror", source, base / "repository.git")
    releases = base / "releases"
    releases.mkdir()
    active = releases / "old"
    git(
        f"--git-dir={base / 'repository.git'}",
        "worktree",
        "add",
        "--detach",
        active,
        old,
    )
    (base / "current").symlink_to(active, target_is_directory=True)
    for path in (
        "shared/data/protected",
        "shared/uv-python",
        "shared/uv-cache",
        "state",
        "backups",
    ):
        (base / path).mkdir(parents=True, exist_ok=True)
    (base / "shared/data/protected/signature.png").write_bytes(b"protected data")
    (base / "shared/.env").write_text("DB_PASSWORD='never-log-this-secret'\n")
    config = tmp_path / "deployment.conf"
    config.write_text(
        f"ECR_INSTALL_DIR={Q(str(base))}\nECR_REPOSITORY_URL={Q(str(source))}\n"
        "ECR_DOMAIN=example.invalid\nECR_SERVICE_USER=mock_service\n"
        "ECR_SERVICE_NAME=ecr\nECR_APP_PORT=8000\nECR_GIT_REF=main\n"
    )
    return base, source, config, old, target


@pytest.mark.parametrize(
    "failure",
    [
        "uv",
        "db",
        "migration",
        "health",
        "https",
        "state",
        "history",
        "launcher",
        "stale",
        "none",
    ],
)
def test_update_failure_boundaries(deployment, tmp_path, failure):
    base, _source, config, _old, target = deployment
    tooling = tmp_path / "tooling"
    shutil.copytree(ROOT / "install", tooling)
    events = tmp_path / "events"
    installed = tmp_path / "ecr-update"
    ownership = tmp_path / "ownership"
    installed.write_bytes(b"known-good launcher")
    if failure == "stale":
        # Metadata falsely says the target is already deployed, but the active
        # worktree is still old. This must not take the same-commit shortcut.
        (base / "state/deployment.env").write_text(
            f"ECR_CURRENT_COMMIT={target}\nECR_CURRENT_REF=stale-ref\n"
        )
    with config.open("a") as stream:
        stream.write("ECR_HTTPS_ENABLED=true\n")
        if failure == "db":
            stream.write("ECR_SERVICE_USER=ecr\n")
    common = tooling / "lib/common.sh"
    common.write_text(
        common.read_text()
        + PRIVILEGE_STUBS
        + f"""
acquire_deployment_lock() {{ :; }}
original_atomic=$(declare -f atomic_install_file)
eval "${{original_atomic/atomic_install_file/real_atomic_install_file}}"
atomic_install_file() {{
    if [[ $2 == /usr/local/sbin/ecr-update ]]; then
        echo launcher >>{Q(str(events))}
        {"install() { return 46; }" if failure == "launcher" else ":"}
        real_atomic_install_file "$1" {Q(str(installed))} "$3" "$4" "$5"
    else real_atomic_install_file "$@"; fi
}}
original_sync=$(declare -f sync_release_dependencies)
eval "${{original_sync/sync_release_dependencies/real_sync_release_dependencies}}"
sync_release_dependencies() {{
    echo sync >>{Q(str(events))}
    {'real_sync_release_dependencies "$@"' if failure == "uv" else ":"}
}}
upgrade_production_env() {{ echo UPGRADED=1 >>"$ECR_INSTALL_DIR/shared/.env"; }}
run_db_check() {{ echo db >>{Q(str(events))}; {"return 42" if failure == "db" else ":"}; }}
run_migrations() {{ echo migration >>{Q(str(events))}; {"return 43" if failure == "migration" else ":"}; }}
systemctl() {{ echo restart >>{Q(str(events))}; }}
wait_for_local_health() {{ echo local >>{Q(str(events))}; {"return 44" if failure == "health" else ":"}; }}
https_health_check() {{ echo https >>{Q(str(events))}; {"return 45" if failure == "https" else ":"}; }}
original_state=$(declare -f write_deployment_state)
eval "${{original_state/write_deployment_state/real_write_deployment_state}}"
write_deployment_state() {{
    echo state >>{Q(str(events))}
    {"return 47" if failure == "state" else 'real_write_deployment_state "$@"'}
}}
original_history=$(declare -f append_deployment_history)
eval "${{original_history/append_deployment_history/real_append_deployment_history}}"
append_deployment_history() {{
    echo history >>{Q(str(events))}
    {"return 48" if failure == "history" else 'real_append_deployment_history "$@"'}
}}
"""
    )
    executable(
        tooling / "backup.sh",
        f"echo backup >>{Q(str(events))}\nprintf '%s' {Q(str(base / 'backups/pre.sql.gz'))}\n",
    )
    env = dict(
        os.environ, ECR_CONFIG_FILE=str(config), ECR_TEST_OWNERSHIP_LOG=str(ownership)
    )
    if failure == "uv":
        runtime = executable(
            base / "shared/uv-python/cpython/bin/python3.12",
            f'exec {Q(sys.executable)} "$@"\n',
        )
        candidate = base / "current/.venv/bin/python"
        candidate.parent.mkdir(parents=True)
        candidate.symlink_to(runtime)
        bins = tmp_path / "bin"
        executable(
            bins / "uv",
            f"""
case "$1 ${{2:-}}" in
    'python install') exit 0;;
    'python find') printf '%s\\n' {Q(str(candidate))};;
    sync*)
        [[ $* == *'--frozen --no-dev'* ]]
        [[ $* == *'--managed-python --no-python-downloads'* ]]
        [[ $* == *{Q(str(runtime))}* ]]
        exit 41;;
    *) exit 91;;
esac
""",
        )
        env["PATH"] = f"{bins}:{os.environ['PATH']}"
    before = (base / "shared/.env").read_bytes()
    result = subprocess.run(
        ["bash", str(tooling / "update.sh"), target],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "never-log-this-secret" not in result.stdout + result.stderr
    assert (
        base / "shared/data/protected/signature.png"
    ).read_bytes() == b"protected data"
    log = events.read_text().splitlines()
    assert log[0:2] == ["backup", "sync"]
    worktrees = git(
        f"--git-dir={base / 'repository.git'}", "worktree", "list", "--porcelain"
    )
    dirs = list((base / "releases").iterdir())
    if failure in ("uv", "db"):
        assert result.returncode == (41 if failure == "uv" else 42), result.stderr
        assert dirs == [base / "releases/old"]
        assert "migration" not in log
        assert (base / "current").resolve() == base / "releases/old"
        assert (base / "shared/.env").read_bytes() == before
        if failure == "db":
            assert (base / "shared/.env").stat().st_mode & 0o777 == 0o640
            assert "-o root -g ecr -m 0640" in ownership.read_text()
            assert os.access(base / "shared/.env", os.R_OK)
        assert "before migrations" in result.stderr
        assert target not in worktrees
    elif failure in ("migration", "health", "https"):
        assert result.returncode != 0
        assert len(dirs) == 2
        assert "No automatic Alembic downgrade" in result.stderr
        assert "pre.sql.gz" in result.stderr
        assert target in worktrees
        if failure == "migration":
            assert (base / "current").resolve() == base / "releases/old"
        assert "downgrade" not in log
    elif failure in ("state", "history", "launcher"):
        assert (
            result.returncode == {"state": 47, "history": 48, "launcher": 46}[failure]
        )
        assert git("-C", base / "current", "rev-parse", "HEAD") == target
        assert len(dirs) == 2
        assert "Target application is active" in result.stderr
        assert "passed all required health checks" in result.stderr
        assert "post-health deployment maintenance failed" in result.stderr
        assert "Review the backup" not in result.stderr
        assert "Update failed after migrations" not in result.stderr
        assert "downgrade" not in log
        assert (base / "shared/.env").read_bytes() != before
        if failure == "state":
            assert not (base / "state/deployment.env").exists()
            assert "state reconciliation failed" in result.stderr
            assert "history" not in log and "launcher" not in log
        else:
            assert target in (base / "state/deployment.env").read_text()
            assert "Deployment state records the target commit" in result.stderr
            if failure == "history":
                assert not (base / "state/deployment-history.tsv").exists()
                assert "launcher" not in log
            else:
                assert target in (base / "state/deployment-history.tsv").read_text()
                assert log[-3:] == ["state", "history", "launcher"]
                assert "Stable launcher refresh failed" in result.stderr
                assert "launcher remains unchanged" in result.stderr
                assert "repair launcher publication" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert (base / "current").resolve() != base / "releases/old"
        assert log == [
            "backup",
            "sync",
            "db",
            "migration",
            "restart",
            "local",
            "https",
            "state",
            "history",
            "launcher",
        ]
        assert target in (base / "state/deployment.env").read_text()
        assert target in (base / "state/deployment-history.tsv").read_text()
        if failure == "stale":
            assert "Deployment metadata was stale" in result.stderr
            assert (
                "ECR_CURRENT_REF=" + target
                in (base / "state/deployment.env").read_text()
            )
    if failure not in ("none", "stale"):
        assert installed.read_bytes() == b"known-good launcher"
    else:
        assert (
            installed.read_bytes()
            == (base / "current/install/ecr-update.sh").read_bytes()
        )
        assert installed.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize(
    "failure", ["none", "blank", "launcher", "health", "https", "git"]
)
def test_same_commit_reconciles_from_active_worktree_without_new_release(
    deployment, tmp_path, failure
):
    base, _source, config, old, target = deployment
    active = (base / "current").resolve()
    git("-C", active, "checkout", "--detach", target)
    state = base / "state/deployment.env"
    stale = f"ECR_CURRENT_COMMIT={'' if failure == 'blank' else old}\nECR_CURRENT_REF=stale-ref\n"
    state.write_text(stale)
    with config.open("a") as stream:
        stream.write("ECR_HTTPS_ENABLED=true\n")
    tooling = tmp_path / "same-tooling"
    shutil.copytree(ROOT / "install", tooling)
    events = tmp_path / "same-events"
    installed = tmp_path / "ecr-update"
    installed.write_bytes(b"stale launcher")
    common = tooling / "lib/common.sh"
    common.write_text(
        common.read_text()
        + PRIVILEGE_STUBS
        + f"""
acquire_deployment_lock() {{ :; }}
prepare_release() {{ echo forbidden-release >>{Q(str(events))}; return 91; }}
run_migrations() {{ echo forbidden-migration >>{Q(str(events))}; return 92; }}
upgrade_production_env() {{ echo forbidden-env >>{Q(str(events))}; return 93; }}
systemctl() {{ echo forbidden-restart >>{Q(str(events))}; return 94; }}
wait_for_local_health() {{ echo local >>{Q(str(events))}; {"return 44" if failure == "health" else ":"}; }}
https_health_check() {{ echo https >>{Q(str(events))}; {"return 45" if failure == "https" else ":"}; }}
original_atomic=$(declare -f atomic_install_file)
eval "${{original_atomic/atomic_install_file/real_atomic_install_file}}"
atomic_install_file() {{
    [[ $1 == {Q(str(active / "install/ecr-update.sh"))} ]]
    echo launcher >>{Q(str(events))}
    {"install() { return 46; }" if failure == "launcher" else ":"}
    real_atomic_install_file "$1" {Q(str(installed))} "$3" "$4" "$5"
}}
"""
    )
    if failure == "git":
        with common.open("a") as stream:
            stream.write("git_as_deployer() { return 49; }\n")
    executable(
        tooling / "backup.sh", f"echo forbidden-backup >>{Q(str(events))}; exit 95\n"
    )
    executable(
        tooling / "status.sh",
        f"echo status >>{Q(str(events))}\nprintf 'Status: PASS\\n'\n",
    )
    before_env = (base / "shared/.env").read_bytes()
    before_worktrees = git(
        f"--git-dir={base / 'repository.git'}", "worktree", "list", "--porcelain"
    )

    def run():
        return subprocess.run(
            ["bash", str(tooling / "update.sh"), "main"],
            check=False,
            env=dict(os.environ, ECR_CONFIG_FILE=str(config)),
            capture_output=True,
            text=True,
            timeout=30,
        )

    result = run()
    if failure in ("health", "https", "git"):
        assert result.returncode == 1
        if failure == "git":
            assert "Cannot verify the active release Git commit" in result.stderr
        else:
            assert "reconciliation cancelled" in result.stderr
        assert state.read_text() == stale
        assert installed.read_bytes() == b"stale launcher"
        assert not (base / "state/deployment-history.tsv").exists()
    else:
        assert "Deployment metadata was stale" in result.stderr
        assert dotenv_values(state)["ECR_CURRENT_COMMIT"] == target
        assert dotenv_values(state)["ECR_CURRENT_REF"] == "main"
        history = (base / "state/deployment-history.tsv").read_text()
        assert f"\treconcile\tmain\t{target}\t{active}" in history
        if failure == "launcher":
            assert result.returncode == 46
            assert installed.read_bytes() == b"stale launcher"
            assert "Stable launcher refresh failed" in result.stderr
            assert "Deployment state records the target commit" in result.stderr
            assert "Update failed before migrations" not in result.stderr
            # Repeat the same command after repairing publication permissions.
            common.write_text(
                common.read_text().replace("install() { return 46; }", ":")
            )
            result = run()
        assert result.returncode == 0, result.stderr
        assert "Status: PASS" in result.stdout
        assert installed.read_bytes() == (active / "install/ecr-update.sh").read_bytes()
        assert installed.stat().st_mode & 0o777 == 0o755
    log = events.read_text().splitlines() if events.exists() else []
    assert not any(item.startswith("forbidden-") for item in log)
    if failure in ("none", "blank"):
        assert log == ["local", "https", "launcher", "status"]
    assert (base / "current").resolve() == active
    assert (base / "shared/.env").read_bytes() == before_env
    assert not list((base / "backups").iterdir())
    assert list((base / "releases").iterdir()) == [active]
    assert (
        git(f"--git-dir={base / 'repository.git'}", "worktree", "list", "--porcelain")
        == before_worktrees
    )


def test_cleanup_rejects_active_previous_and_shared(deployment):
    base, *_ = deployment
    for path in (
        base / "current",
        base / "releases/old",
        base / "shared",
        base / "repository.git",
        base,
    ):
        result = bash(
            f"source {Q(str(COMMON))}; ECR_INSTALL_DIR={Q(str(base))}; cleanup_failed_release {Q(str(path))}"
        )
        assert result.returncode != 0
        assert path.exists()


def test_cleanup_warning_preserves_original_failure(deployment):
    base, _, _, _, target = deployment
    result = bash(f"""
source {Q(str(COMMON))}
ECR_INSTALL_DIR={Q(str(base))}
sync_release_dependencies() {{ return 51; }}
cleanup_failed_release() {{ warn 'cleanup deliberately failed'; return 52; }}
new_release=$(prepare_release {Q(target)})
""")
    assert result.returncode == 51
    assert "cleanup deliberately failed" in result.stderr
    assert (base / "current").resolve() == base / "releases/old"


@pytest.mark.parametrize("ref", ["commit", "main", "release-tag"])
@pytest.mark.parametrize("broken", [False, True])
def test_launcher_uses_target_not_current(deployment, tmp_path, broken, ref):
    base, source, config, _old, _ = deployment
    marker = tmp_path / "executed"
    executable(base / "current/install/update.sh", "echo OLD; exit 99\n")
    if broken:
        (source / "install/lib/common.sh").unlink()
    else:
        executable(
            source / "install/update.sh",
            f"printf '%s' \"$ECR_BOOTSTRAP_COMMIT\" >{Q(str(marker))}\n",
        )
    git("add", ".", cwd=source)
    git("commit", "-qm", "new target tooling", cwd=source)
    target = git("rev-parse", "HEAD", cwd=source)
    git("tag", "release-tag", cwd=source)
    requested = target if ref == "commit" else ref
    result = bash(f"""
source {Q(str(LAUNCHER))}
launcher_config_file={Q(str(config))}
launcher_lock_file={Q(str(tmp_path / "lock"))}
launcher_require_root() {{ :; }}
launcher_trusted_file() {{ :; }}
stat() {{ if [[ $2 == %u ]]; then echo 0; else command stat "$@"; fi; }}
mktemp() {{ command mktemp -d {Q(str(tmp_path / "bootstrap.XXXXXXXX"))}; }}
launcher_main {Q(requested)}
""")
    assert "OLD" not in result.stdout + result.stderr
    assert (base / "current").resolve() == base / "releases/old"
    assert not list(tmp_path.glob("bootstrap.*"))
    if broken:
        assert result.returncode != 0
        assert not marker.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert marker.read_text() == target


def test_launcher_requires_root():
    if os.geteuid() == 0:
        pytest.skip("This check exercises the unprivileged execution path.")
    result = bash(f"bash {Q(str(LAUNCHER))} main")
    assert result.returncode != 0
    assert "sudo or as root" in result.stderr


def test_launcher_rejects_user_owned_configuration(deployment):
    if os.geteuid() == 0:
        pytest.skip("Configuration fixture is intentionally non-root-owned.")
    _, _, config, *_ = deployment
    result = bash(f"""
source {Q(str(LAUNCHER))}
launcher_config_file={Q(str(config))}
launcher_require_root() {{ :; }}
launcher_main main
""")
    assert result.returncode != 0
    assert "root-owned regular" in result.stderr


def test_inherited_deployment_lock_stays_held(tmp_path):
    lock = tmp_path / "lock"
    result = bash(f"""
source {Q(str(COMMON))}
exec 9>{Q(str(lock))}
flock -n 9
export ECR_DEPLOYMENT_LOCK_HELD=true
# Simulate the production lock path; the descriptor and flock are real.
readlink() {{
    if [[ $3 == /proc/$$/fd/9 ]]; then
        printf '%s\\n' /run/lock/ecr-deployment.lock
    else command readlink "$@"; fi
}}
acquire_deployment_lock
if flock -n {Q(str(lock))} true; then exit 77; fi
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("kind", ["symlink", "unprotected"])
def test_lock_rejects_unsafe_file_without_truncation(tmp_path, kind):
    protected = tmp_path / "untouched"
    protected.write_text("keep this file")
    lock = tmp_path / "lock"
    if kind == "symlink":
        lock.symlink_to(protected)
    else:
        lock.write_text("keep this file")
        lock.chmod(0o666)
    result = bash(f"source {Q(str(COMMON))}; acquire_deployment_lock {Q(str(lock))}")
    assert result.returncode != 0
    assert protected.read_text() == "keep this file"
    assert lock.read_text() == "keep this file"


def test_lock_opens_without_truncating_existing_file(tmp_path):
    lock = tmp_path / "lock"
    lock.write_text("retained lock metadata")
    lock.chmod(0o600)
    result = bash(f"""
source {Q(str(COMMON))}
stat() {{ if [[ $2 == %u ]]; then echo 0; else command stat "$@"; fi; }}
acquire_deployment_lock {Q(str(lock))}
if flock -n {Q(str(lock))} true; then exit 77; fi
""")
    assert result.returncode == 0, result.stderr
    assert lock.read_text() == "retained lock metadata"


def test_launcher_installation_is_automatic_and_exact(tmp_path):
    installed = tmp_path / "ecr-update"
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
original_atomic=$(declare -f atomic_install_file)
eval "${{original_atomic/atomic_install_file/real_atomic_install_file}}"
atomic_install_file() {{
    [[ $2 == /usr/local/sbin/ecr-update && $3 == root && $4 == root && $5 == 0755 ]]
    real_atomic_install_file "$1" {Q(str(installed))} "$3" "$4" "$5"
}}
install_update_launcher {Q(str(ROOT))}
""")
    assert result.returncode == 0, result.stderr
    assert installed.read_bytes() == LAUNCHER.read_bytes()
    assert installed.stat().st_mode & 0o777 == 0o755


def env_command(*args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, str(HELPER), *map(str, args)],
        check=False,
        cwd=cwd,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("storage", [None, "var/protected", "custom"])
def test_env_upgrade_preserves_secrets_and_custom_values(tmp_path, storage):
    env = tmp_path / ".env"
    original = (
        "DB_PASSWORD='production-secret'\nSESSION_TTL_DAYS=60\nCUSTOM_SETTING=keep\n"
    )
    if storage:
        original += f"STORAGE_ROOT='{tmp_path / 'custom' if storage == 'custom' else storage}'\n"
    env.write_text(original)
    env.chmod(0o640)
    example = tmp_path / "example"
    example.write_text(
        (ROOT / ".env.example").read_text()
        + "\nUNMANAGED_NEW=must-not-copy\nNEW_SECRET=must-not-copy\n"
    )
    for _ in range(2):
        result = env_command("upgrade", env, example, tmp_path)
        assert result.returncode == 0, result.stderr
        assert "production-secret" not in result.stdout + result.stderr
    values = dotenv_values(env, interpolate=False)
    assert values["DB_PASSWORD"] == "production-secret"
    assert values["SESSION_TTL_DAYS"] == "60"
    assert values["CUSTOM_SETTING"] == "keep"
    assert "NEW_SECRET" not in values and "UNMANAGED_NEW" not in values
    assert values["STORAGE_ROOT"] == str(
        tmp_path / ("custom" if storage == "custom" else "shared/data/protected")
    )
    assert values["ARGON2_TIME_COST"] == "3"
    assert env.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".env.upgrade.*"))


@pytest.mark.parametrize("storage", ["relative/custom", "/", "release"])
def test_env_upgrade_rejects_unsafe_storage(tmp_path, storage):
    if storage == "release":
        storage = str(tmp_path / "releases/old/private")
    env = tmp_path / ".env"
    env.write_text(f"STORAGE_ROOT='{storage}'\nDB_PASSWORD=secret\n")
    before = env.read_bytes()
    result = env_command("upgrade", env, ROOT / ".env.example", tmp_path)
    assert result.returncode != 0
    assert env.read_bytes() == before
    assert "secret" not in result.stderr


def test_production_config_validation_hides_secrets(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "APP_ENV=production\nDB_HOST=127.0.0.1\nDB_NAME=ecr\nDB_USER=ecr\n"
        f"DB_PASSWORD=never-log-secret\nSTORAGE_ROOT={tmp_path / 'protected'}\nDB_PORT=not-a-port\n"
    )
    result = env_command("validate", env)
    assert result.returncode != 0
    assert "db_port" in result.stderr
    assert "never-log-secret" not in result.stderr
    env.write_text(env.read_text().replace("not-a-port", "3306"))
    assert env_command("validate", env).returncode == 0


def test_fresh_install_storage_is_private_absolute_and_writable(tmp_path):
    release = tmp_path / "releases/new"
    shutil.copytree(ROOT / "install", release / "install")
    shutil.copytree(
        ROOT / "app", release / "app", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copyfile(ROOT / ".env.example", release / ".env.example")
    (release / ".venv/bin").mkdir(parents=True)
    executable(release / ".venv/bin/python", f'exec {Q(sys.executable)} "$@"\n')
    # Exercise the real installer env writer plus real shared layout/upgrade.
    installer = (ROOT / "install/install.sh").read_text()
    writer = (
        "write_production_env() {"
        + installer.split("write_production_env() {", 1)[1].split(
            "render_systemd_service()", 1
        )[0]
    )
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
{writer}
ECR_INSTALL_DIR={Q(str(tmp_path))}; ECR_SERVICE_USER=mock_service
ECR_APP_PORT=8000; ECR_HTTPS_ENABLED=true; INSTALL_MODE=fresh
DB_HOST=127.0.0.1; DB_PORT=3306; DB_NAME=ecr; DB_USER=ecr; DB_PASSWORD=secret
ensure_install_layout
write_production_env {Q(str(release))}
upgrade_production_env {Q(str(release))}
""")
    assert result.returncode == 0, result.stderr
    protected = tmp_path / "shared/data/protected"
    assert dotenv_values(tmp_path / "shared/.env")["STORAGE_ROOT"] == str(protected)
    assert protected.stat().st_mode & 0o777 == 0o700
    # The root/service ownership switch is mocked; actual current UID writes.
    (protected / "writable-probe").write_text("ok")
    assert (protected / "writable-probe").read_text() == "ok"


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("recorded", ["absent", "stale", "matching", "blank"])
def test_backup_verifies_mysql_files_and_active_commit(
    deployment, tmp_path, external, recorded
):
    base, _, config, old, target = deployment
    active = base / "current"
    git("-C", active, "checkout", "--detach", target)
    (active / ".venv/bin").mkdir(parents=True)
    executable(active / ".venv/bin/python", f'exec {Q(sys.executable)} "$@"\n')
    state = base / "state/deployment.env"
    if recorded != "absent":
        state.write_text(
            f"ECR_CURRENT_COMMIT={old if recorded == 'stale' else '' if recorded == 'blank' else target}\n"
            "ECR_CURRENT_REF=owner-release-tag\n"
        )
    before_state = state.read_bytes() if state.exists() else None
    before_mtime = state.stat().st_mtime_ns if state.exists() else None
    # Target backup code must not depend on an old deployment reader.
    (active / "install/lib/env_tools.py").write_text(
        'raise SystemExit("OLD_ENV_HELPER_USED")\n'
    )
    storage = (
        tmp_path / "custom-signatures" if external else base / "shared/data/protected"
    )
    storage.mkdir(exist_ok=True)
    (storage / "signature.png").write_bytes(b"private-signature")
    (base / "shared/.env").write_text(
        f"DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_NAME=ecr\nDB_USER=ecr\n"
        f"DB_PASSWORD=private-secret\nSTORAGE_ROOT={storage}\n"
    )
    tooling = tmp_path / "backup-tooling"
    shutil.copytree(ROOT / "install", tooling)
    common = tooling / "lib/common.sh"
    common.write_text(
        common.read_text() + PRIVILEGE_STUBS + "\nacquire_deployment_lock() { :; }\n"
    )
    bins = tmp_path / "bin"
    executable(
        bins / "mysqldump",
        "[[ $MYSQL_PWD == private-secret ]] || exit 2\nprintf 'CREATE TABLE test(id INT);\\n'\n",
    )
    env = dict(
        os.environ, PATH=f"{bins}:{os.environ['PATH']}", ECR_CONFIG_FILE=str(config)
    )
    result = subprocess.run(
        ["bash", str(tooling / "backup.sh")],
        check=False,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "private-secret" not in result.stdout + result.stderr
    sql = Path(result.stdout.strip())
    assert gzip.decompress(sql.read_bytes()).startswith(b"CREATE TABLE")
    base_name = str(sql).removesuffix(".sql.gz")
    meta = Path(base_name + ".meta").read_text()
    values = dotenv_values(base_name + ".meta")
    assert values["APPLICATION_COMMIT"] == target
    assert values["APPLICATION_REF"] == (
        "unknown" if recorded == "absent" else "owner-release-tag"
    )
    assert ("Deployment metadata was stale" in result.stderr) == (
        recorded in ("stale", "blank")
    )
    if recorded == "stale":
        assert old in result.stderr and target in result.stderr
    if before_state is None:
        assert not state.exists()
    else:
        assert state.read_bytes() == before_state
        assert state.stat().st_mtime_ns == before_mtime
    assert not (base / "state/deployment-history.tsv").exists()
    assert hashlib.sha256(sql.read_bytes()).hexdigest() in meta
    with tarfile.open(base_name + ".files.tar.gz") as archive:
        assert "data/protected/signature.png" in archive.getnames()
    files = Path(base_name + ".files.tar.gz")
    assert hashlib.sha256(files.read_bytes()).hexdigest() in meta
    if external:
        with tarfile.open(base_name + ".storage.tar.gz") as archive:
            assert "./signature.png" in archive.getnames()
        assert "STORAGE_SHA256=" in meta
    assert sql.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "recorded",
    ["stale", "matching", "absent", "blank", "invalid_release", "git_failure"],
)
def test_status_reports_active_commit_without_mutating_state(
    deployment, tmp_path, recorded
):
    base, _, config, old, target = deployment
    active = (base / "current").resolve()
    git("-C", active, "checkout", "--detach", target)
    if recorded != "invalid_release":
        executable(active / ".venv/bin/python", f'exec {Q(sys.executable)} "$@"\n')
    state = base / "state/deployment.env"
    if recorded != "absent":
        state.write_text(
            f"ECR_CURRENT_COMMIT={target if recorded == 'matching' else '' if recorded == 'blank' else old}\n"
            "ECR_CURRENT_REF=owner-release-tag\n"
        )
    before_state = state.read_bytes() if state.exists() else None
    before_mtime = state.stat().st_mtime_ns if state.exists() else None
    tooling = tmp_path / "status-tooling"
    shutil.copytree(ROOT / "install", tooling)
    common = tooling / "lib/common.sh"
    common.write_text(
        common.read_text()
        + PRIVILEGE_STUBS
        + f"""
systemctl() {{ :; }}
run_db_check() {{ [[ $1 == {Q(str(active))} ]]; }}
local_health_check() {{ :; }}
{"git_as_deployer() { return 49; }" if recorded == "git_failure" else ":"}
"""
    )
    result = subprocess.run(
        ["bash", str(tooling / "status.sh")],
        check=False,
        env=dict(os.environ, ECR_CONFIG_FILE=str(config)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    invalid = recorded in ("invalid_release", "git_failure")
    assert result.returncode == int(invalid), result.stderr
    values = {
        key.strip(): value.strip()
        for line in result.stdout.splitlines()
        if ":" in line
        for key, value in [line.split(":", 1)]
    }
    assert values["Commit"] == ("unknown" if invalid else target[:12])
    assert values["Git Ref"] == (
        "unknown" if recorded == "absent" else "owner-release-tag"
    )
    assert values["Application"] == "Running"
    assert values["Nginx"] == "Running"
    assert values["Health Check"] == "PASS"
    assert values["Database"] == (
        "Disconnected" if recorded == "invalid_release" else "Connected"
    )
    assert values["HTTPS"] == "Disabled"
    assert values["Install Directory"] == str(base)
    assert ("Deployment metadata was stale" in result.stderr) == (
        recorded in ("stale", "blank")
    )
    if recorded == "stale":
        assert old in result.stderr and target in result.stderr
    if recorded == "git_failure":
        assert "Cannot verify the active release Git commit" in result.stderr
    assert "never-log-this-secret" not in result.stdout + result.stderr
    if before_state is None:
        assert not state.exists()
    else:
        assert state.read_bytes() == before_state
        assert state.stat().st_mtime_ns == before_mtime
    assert not (base / "state/deployment-history.tsv").exists()
    assert not list((base / "backups").iterdir())


def test_standalone_installer_bootstraps_matching_helpers(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("Use an unprivileged invocation to stop before installation.")
    standalone = tmp_path / "install.sh"
    shutil.copyfile(ROOT / "install/install.sh", standalone)
    downloaded = tmp_path / "downloaded"
    bins = tmp_path / "bin"
    executable(
        bins / "curl",
        f"""
url=$1
for arg in "$@"; do
    if [[ $arg == https://* ]]; then url=$arg; fi
done
destination=${{@: -1}}
path=${{url#*/approved-ref/install/}}
printf '%s\\n' "$path" >>{Q(str(downloaded))}
cp {Q(str(ROOT / "install"))}/"$path" "$destination"
""",
    )
    env = dict(os.environ, PATH=f"{bins}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", str(standalone), "--bootstrap-ref", "approved-ref"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "sudo or as root" in result.stderr
    assert downloaded.read_text().splitlines() == [
        "install.sh",
        "lib/common.sh",
        "lib/env_tools.py",
        "ecr-update.sh",
    ]
