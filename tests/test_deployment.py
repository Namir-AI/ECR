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
    shutil.copyfile(ROOT / ".gitignore", source / ".gitignore")
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


def seed_active_runtime(base):
    active = (base / "current").resolve()
    executable(active / ".venv/bin/python", f'exec {Q(sys.executable)} "$@"\n')
    (active / ".env").symlink_to(base / "shared/.env")
    return active


@pytest.mark.parametrize("ref_kind", ["commit", "tag"])
@pytest.mark.parametrize(
    "relationship",
    ["direct", "later", "same", "older", "divergent", "unrelated", "git_error"],
)
def test_normal_update_is_forward_only(deployment, tmp_path, relationship, ref_kind):
    base, source, config, old, target = deployment
    current = target if relationship in ("same", "older", "divergent") else old
    requested_commit = target
    if relationship == "older":
        requested_commit = old
    elif relationship == "later":
        (source / "revision").write_text("later descendant")
        git("add", ".", cwd=source)
        git("commit", "-qm", "later deployment", cwd=source)
        requested_commit = git("rev-parse", "HEAD", cwd=source)
    elif relationship == "divergent":
        git("checkout", "-qb", "divergent", old, cwd=source)
        (source / "revision").write_text("divergent deployment")
        git("add", ".", cwd=source)
        git("commit", "-qm", "divergent deployment", cwd=source)
        requested_commit = git("rev-parse", "HEAD", cwd=source)
    elif relationship == "unrelated":
        # Same valid application tree, but an entirely unrelated root commit.
        tree = git("rev-parse", "HEAD^{tree}", cwd=source)
        requested_commit = git("commit-tree", tree, "-m", "unrelated root", cwd=source)
    git("tag", "requested-update", requested_commit, cwd=source)
    requested = requested_commit if ref_kind == "commit" else "requested-update"
    active = seed_active_runtime(base)
    git("-C", active, "checkout", "--detach", current)
    assert (
        Path(git("-C", active, "rev-parse", "--git-common-dir")).resolve()
        == (base / "repository.git").resolve()
    )
    state = base / "state/deployment.env"
    state.write_text(f"ECR_CURRENT_COMMIT={current}\nECR_CURRENT_REF=current-tag\n")
    history = base / "state/deployment-history.tsv"
    history.write_text("previous history\n")
    runtime = active / ".venv/runtime-commit"
    runtime.write_text(current)
    env_file = base / "shared/.env"
    env_file.chmod(0o640)
    signature = base / "shared/data/protected/signature.png"
    installed = tmp_path / "ecr-update"
    installed.write_text("known-good launcher")
    service = tmp_path / "service-state"
    service.write_text("running")
    preserved = {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in (state, history, runtime, env_file, signature, installed, service)
    }
    events = tmp_path / "events"
    tooling = tmp_path / "tooling"
    shutil.copytree(ROOT / "install", tooling)
    with config.open("a") as stream:
        stream.write("ECR_HTTPS_ENABLED=true\n")
    with (tooling / "lib/common.sh").open("a") as stream:
        stream.write(
            PRIVILEGE_STUBS
            + f"""
acquire_deployment_lock() {{ :; }}
git_as_deployer() {{
    # A linked worktree already sees objects fetched into its common mirror.
    # Fail the test if redundant active-worktree self-fetch is reintroduced.
    [[ $1 != -C || $3 != fetch ]] || return 91
    {"if [[ $1 == -C && $3 == merge-base ]]; then return 128; fi" if relationship == "git_error" else ":"}
    command git "$@"
}}
systemctl() {{
    echo "$1" >>{Q(str(events))}
    case "$1" in
        is-active) [[ $(<{Q(str(service))}) == running ]];;
        stop) printf stopped >{Q(str(service))};;
        start) printf running >{Q(str(service))};;
        *) return 92;;
    esac
}}
wait_for_local_health() {{ echo local >>{Q(str(events))}; }}
https_health_check() {{ echo https >>{Q(str(events))}; }}
sync_application_dependencies() {{ echo sync >>{Q(str(events))}; git -C "$1" rev-parse HEAD >"$1/.venv/runtime-commit"; }}
upgrade_production_env() {{ echo env >>{Q(str(events))}; echo UPGRADED=1 >>"$ECR_INSTALL_DIR/shared/.env"; }}
run_db_check() {{ echo db >>{Q(str(events))}; }}
run_migrations() {{ echo migration >>{Q(str(events))}; }}
original_state=$(declare -f write_deployment_state)
eval "${{original_state/write_deployment_state/real_write_deployment_state}}"
write_deployment_state() {{ echo state >>{Q(str(events))}; real_write_deployment_state "$@"; }}
original_history=$(declare -f append_deployment_history)
eval "${{original_history/append_deployment_history/real_append_deployment_history}}"
append_deployment_history() {{ echo history >>{Q(str(events))}; real_append_deployment_history "$@"; }}
install_update_launcher() {{ echo launcher >>{Q(str(events))}; cp -- "$1/install/ecr-update.sh" {Q(str(installed))}; }}
"""
        )
    executable(
        tooling / "backup.sh",
        f"""
echo backup >>{Q(str(events))}
printf backup >{Q(str(base / "backups/pre.sql.gz"))}
printf '%s' {Q(str(base / "backups/pre.sql.gz"))}
""",
    )
    executable(tooling / "status.sh", f"echo status >>{Q(str(events))}\n")
    result = subprocess.run(
        ["bash", str(tooling / "update.sh"), requested],
        check=False,
        env=dict(os.environ, ECR_CONFIG_FILE=str(config)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "never-log-this-secret" not in result.stdout + result.stderr
    assert (base / "current").resolve() == active
    assert list((base / "releases").iterdir()) == [active]
    log = events.read_text().splitlines()
    rejected = relationship in ("older", "divergent", "unrelated", "git_error")
    if rejected:
        assert result.returncode != 0
        assert "forward-only" in result.stderr
        assert "reviewed operator recovery" in result.stderr.lower()
        assert log == ["is-active", "local", "https"]
        assert git("-C", active, "rev-parse", "HEAD") == current
        assert not list((base / "backups").iterdir())
        assert not list((base / "state").glob(".env.before-update.*"))
        for path, fingerprint in preserved.items():
            assert (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_mtime_ns,
            ) == fingerprint
    else:
        assert result.returncode == 0, result.stderr
        assert git("-C", active, "rev-parse", "HEAD") == requested_commit
        assert dotenv_values(state)["ECR_CURRENT_COMMIT"] == requested_commit
        assert service.read_text() == "running"
        if relationship == "same":
            assert log == [
                "is-active",
                "local",
                "https",
                "state",
                "history",
                "launcher",
                "status",
            ]
            assert not list((base / "backups").iterdir())
            assert not list((base / "state").glob(".env.before-update.*"))
        else:
            assert log == [
                "is-active",
                "local",
                "https",
                "backup",
                "stop",
                "sync",
                "env",
                "db",
                "migration",
                "start",
                "local",
                "https",
                "state",
                "history",
                "launcher",
            ]
            assert dotenv_values(state)["ECR_PREVIOUS_COMMIT"] == current
            assert runtime.read_text().strip() == requested_commit


@pytest.mark.parametrize("ref_kind", ["commit", "tag"])
@pytest.mark.parametrize("relationship", ["older", "divergent"])
def test_launcher_rejects_unguarded_older_or_divergent_tooling(
    deployment, tmp_path, ref_kind, relationship
):
    base, source, config, *_ = deployment
    active = seed_active_runtime(base)
    executed = tmp_path / "unguarded-updater-executed"
    executable(source / "install/update.sh", f"printf unsafe >{Q(str(executed))}\n")
    git("add", ".", cwd=source)
    git("commit", "-qm", "old tooling without forward-only protection", cwd=source)
    ancestor = git("rev-parse", "HEAD", cwd=source)
    (source / "revision").write_text("healthy newer application")
    git("add", ".", cwd=source)
    git("commit", "-qm", "newer application", cwd=source)
    current = git("rev-parse", "HEAD", cwd=source)
    git(f"--git-dir={base / 'repository.git'}", "fetch", "origin")
    git("-C", active, "checkout", "--detach", current)
    requested_commit = ancestor
    if relationship == "divergent":
        git("checkout", "-qb", "divergent", ancestor, cwd=source)
        (source / "revision").write_text("divergent application")
        git("add", ".", cwd=source)
        git("commit", "-qm", "divergent unguarded tooling", cwd=source)
        requested_commit = git("rev-parse", "HEAD", cwd=source)
    git("tag", "unsafe-target", requested_commit, cwd=source)
    requested = requested_commit if ref_kind == "commit" else "unsafe-target"
    state = base / "state/deployment.env"
    state.write_text(f"ECR_CURRENT_COMMIT={current}\nECR_CURRENT_REF=healthy\n")
    history = base / "state/deployment-history.tsv"
    history.write_text("healthy history\n")
    signature = base / "shared/data/protected/signature.png"
    env_file = base / "shared/.env"
    env_file.chmod(0o640)
    preserved = {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in (state, history, signature, env_file)
    }
    bootstrap_called = tmp_path / "bootstrap-called"
    result = bash(f"""
source {Q(str(LAUNCHER))}
launcher_config_file={Q(str(config))}
launcher_lock_file={Q(str(tmp_path / "lock"))}
launcher_require_root() {{ :; }}
launcher_trusted_file() {{ :; }}
stat() {{ if [[ $2 == %u ]]; then echo 0; else command stat "$@"; fi; }}
mktemp() {{ printf unsafe >{Q(str(bootstrap_called))}; return 93; }}
launcher_main {Q(requested)}
""")
    assert result.returncode != 0
    assert "forward-only" in result.stderr
    assert "reviewed operator recovery" in result.stderr
    assert not executed.exists()
    assert not bootstrap_called.exists()
    assert git("-C", active, "rev-parse", "HEAD") == current
    assert (base / "current").resolve() == active
    assert list((base / "releases").iterdir()) == [active]
    assert not list((base / "backups").iterdir())
    assert not list((base / "state").glob(".env.before-update.*"))
    for path, fingerprint in preserved.items():
        assert (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_mtime_ns,
        ) == fingerprint


@pytest.mark.parametrize("requested", ["none", "directory", "absolute", "commit"])
def test_disabled_rollback_never_changes_code_service_or_persistent_state(
    deployment, tmp_path, requested
):
    base, _source, config, old, target = deployment
    active = seed_active_runtime(base)
    sentinel = tmp_path / "unexpected-operation"
    with config.open("a") as stream:
        stream.write(f"printf unsafe >{Q(str(sentinel))}\n")
    state = base / "state/deployment.env"
    state.write_text(
        f"ECR_CURRENT_COMMIT={old}\nECR_CURRENT_REF=current-tag\n"
        f"ECR_PREVIOUS_RELEASE={Q(str(active))}\n"
        f"ECR_PREVIOUS_COMMIT={target}\nECR_PREVIOUS_REF=older-tag\n"
    )
    history = base / "state/deployment-history.tsv"
    history.write_text(f"prior\tupdate\tcurrent-tag\t{old}\t{active}\n")
    runtime = active / ".venv/runtime-marker"
    runtime.write_bytes(b"current dependencies")
    protected = [
        state,
        history,
        runtime,
        base / "shared/.env",
        base / "shared/data/protected/signature.png",
    ]
    before = {path: (path.read_bytes(), path.stat().st_mode) for path in protected}
    before_git = git("-C", active, "status", "--porcelain")
    bins = tmp_path / "forbidden-commands"
    for name in ("git", "uv", "systemctl", "mysql", "mysqldump", "curl", "ln", "mv"):
        executable(bins / name, f"printf unsafe >{Q(str(sentinel))}\nexit 95\n")
    args = {
        "none": [],
        "directory": ["old"],
        "absolute": [str(active)],
        "commit": [target],
    }[requested]
    result = subprocess.run(
        ["bash", str(ROOT / "install/rollback.sh"), *args],
        env=dict(
            os.environ, ECR_CONFIG_FILE=str(config), PATH=f"{bins}:{os.environ['PATH']}"
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 1
    assert "rollback.sh is disabled for in-place deployments" in result.stderr
    assert "schema-compatibility review" in result.stderr
    assert "No automatic database downgrade" in result.stderr
    assert not sentinel.exists()
    assert (base / "current").resolve() == active
    assert git("-C", active, "rev-parse", "HEAD") == old
    assert git("-C", active, "status", "--porcelain") == before_git
    for path, contents_mode in before.items():
        assert (path.read_bytes(), path.stat().st_mode) == contents_mode
    assert not list((base / "backups").iterdir())


@pytest.mark.parametrize(
    "previous", ["empty", "commit", "directory", "bad_sha", "ref_without_commit"]
)
def test_state_is_commit_based_and_history_preserves_storage_locator(
    deployment, tmp_path, previous
):
    base, _source, _config, old, target = deployment
    active = (base / "current").resolve()
    state = base / "state/deployment.env"
    legacy = f"ECR_CURRENT_COMMIT={old}\nECR_CURRENT_REF=old-tag\nECR_PREVIOUS_RELEASE={active}\n"
    state.write_text(legacy)
    state.chmod(0o640)
    history = base / "state/deployment-history.tsv"
    earlier = f"earlier\tinstall\told-tag\t{old}\t{active}\n"
    history.write_text(earlier)
    previous_commit, previous_ref = {
        "empty": ("", ""),
        "commit": (old, "old-tag"),
        "directory": (str(active), "old-tag"),
        "bad_sha": ("f" * 39, "old-tag"),
        "ref_without_commit": ("", "old-tag"),
    }[previous]
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
ECR_INSTALL_DIR={Q(str(base))}; ECR_SERVICE_USER=ecr
write_deployment_state {Q(target)} new-tag {Q(previous_commit)} {Q(previous_ref)}
append_deployment_history update new-tag {Q(target)} {Q(str(active))}
""")
    if previous in ("directory", "bad_sha", "ref_without_commit"):
        assert result.returncode != 0
        assert state.read_text() == legacy
        assert history.read_text() == earlier
    else:
        assert result.returncode == 0, result.stderr
        values = dotenv_values(state)
        assert values["ECR_CURRENT_COMMIT"] == target
        assert values["ECR_CURRENT_REF"] == "new-tag"
        assert values["ECR_PREVIOUS_COMMIT"] == previous_commit
        assert values["ECR_PREVIOUS_REF"] == previous_ref
        assert "ECR_PREVIOUS_RELEASE" not in values
        assert state.stat().st_mode & 0o777 == 0o640
        assert history.read_text().startswith(earlier)
        fields = history.read_text().splitlines()[-1].split("\t")
        assert len(fields) == 5
        assert fields[1:] == ["update", "new-tag", target, str(active)]
        assert old not in active.name and target not in active.name
    assert (base / "current").resolve() == active
    assert git("-C", active, "rev-parse", "HEAD") == old


@pytest.mark.parametrize(
    "failure",
    [
        "uv",
        "db",
        "env",
        "recovery",
        "migration",
        "start",
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
    base, _source, config, old, target = deployment
    active = seed_active_runtime(base)
    (active / ".venv/runtime-commit").write_text(old)
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
    {'if [[ $(git -C "$1" rev-parse HEAD) == ' + Q(target) + ' ]]; then real_sync_release_dependencies "$@"; fi' if failure == "uv" else 'git -C "$1" rev-parse HEAD >"$1/.venv/runtime-commit"'}
}}
upgrade_production_env() {{ echo UPGRADED=1 >>"$ECR_INSTALL_DIR/shared/.env"; {"return 40" if failure == "env" else ":"}; }}
run_db_check() {{ echo db >>{Q(str(events))}; {'if [[ $(git -C "$1" rev-parse HEAD) == ' + Q(target) + " ]]; then return 42; fi" if failure in ("db", "recovery") else ":"}; }}
run_migrations() {{ echo migration >>{Q(str(events))}; {"return 43" if failure == "migration" else ":"}; }}
systemctl() {{ echo "$1" >>{Q(str(events))}; {"if [[ $1 == start ]]; then return 50; fi" if failure == "start" else ":"}; }}
wait_for_local_health() {{ echo local >>{Q(str(events))}; {'if [[ $(git -C "$application_dir" rev-parse HEAD) == ' + Q(target) + " ]]; then return 44; fi" if failure == "health" else ":"}; }}
https_health_check() {{ echo https >>{Q(str(events))}; {'if [[ $(git -C "$application_dir" rev-parse HEAD) == ' + Q(target) + " ]]; then return 45; fi" if failure == "https" else ":"}; }}
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
    if failure == "recovery":
        with common.open("a") as stream:
            stream.write(
                f"\nsync_release_dependencies() {{ echo sync >>{Q(str(events))}; "
                f'[[ $(git -C "$1" rev-parse HEAD) != {Q(old)} ]] || return 51; }}\n'
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
        candidate.unlink()
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
    assert log[0:6] == ["is-active", "local", "https", "backup", "stop", "sync"]
    worktrees = git(
        f"--git-dir={base / 'repository.git'}", "worktree", "list", "--porcelain"
    )
    dirs = list((base / "releases").iterdir())
    assert dirs == [active]
    assert (base / "current").resolve() == active
    if failure in ("uv", "db", "env", "recovery"):
        assert (
            result.returncode
            == {"uv": 41, "db": 42, "env": 40, "recovery": 42}[failure]
        ), result.stderr
        assert dirs == [base / "releases/old"]
        assert "migration" not in log
        assert (base / "current").resolve() == base / "releases/old"
        assert (base / "shared/.env").read_bytes() == before
        if failure == "db":
            assert (base / "shared/.env").stat().st_mode & 0o777 == 0o640
            assert "-o root -g ecr -m 0640" in ownership.read_text()
            assert os.access(base / "shared/.env", os.R_OK)
        assert (
            "before migrations" in result.stderr
            or "Pre-migration recovery" in result.stderr
        )
        assert target not in worktrees
        assert git("-C", active, "rev-parse", "HEAD") == old
        assert (active / ".venv/runtime-commit").read_text().strip() == old
        if failure == "recovery":
            assert log[-1] == "stop"
            assert "must remain stopped for operator recovery" in result.stderr
            assert "application health verified" not in result.stderr
        else:
            assert log[-3:] == ["start", "local", "https"]
    elif failure in ("migration", "start", "health", "https"):
        assert result.returncode != 0
        assert "no automatic Git rollback or Alembic downgrade" in result.stderr
        assert "pre.sql.gz" in result.stderr
        assert target in worktrees
        if failure == "migration":
            assert (base / "current").resolve() == base / "releases/old"
        assert "downgrade" not in log
        assert log[-1] == "stop"
        assert git("-C", active, "rev-parse", "HEAD") == target
    elif failure in ("state", "history", "launcher"):
        assert (
            result.returncode == {"state": 47, "history": 48, "launcher": 46}[failure]
        )
        assert git("-C", base / "current", "rev-parse", "HEAD") == target
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
        assert log == [
            "is-active",
            "local",
            "https",
            "backup",
            "stop",
            "sync",
            "db",
            "migration",
            "start",
            "local",
            "https",
            "state",
            "history",
            "launcher",
        ]
        assert target in (base / "state/deployment.env").read_text()
        assert target in (base / "state/deployment-history.tsv").read_text()
        assert (
            dotenv_values(base / "state/deployment.env")["ECR_PREVIOUS_COMMIT"] == old
        )
        values = dotenv_values(base / "state/deployment.env")
        assert "ECR_PREVIOUS_RELEASE" not in values
        assert values["ECR_PREVIOUS_REF"] == (
            "stale-ref" if failure == "stale" else "unknown"
        )
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
    snapshots = list((base / "state").glob(".env.before-update.*"))
    assert len(snapshots) == 1
    assert snapshots[0].stat().st_mode & 0o777 == 0o600
    assert snapshots[0].read_bytes() == before


@pytest.mark.parametrize(
    "finding",
    [
        "dirty",
        "env_link",
        "service",
        "local",
        "https",
        "backup",
        "target_env",
        "target_venv",
        "missing_helper",
        "ignored_collision",
    ],
)
def test_in_place_update_preflight_and_checkout_safety(deployment, tmp_path, finding):
    base, source, config, old, target = deployment
    active = seed_active_runtime(base)
    if finding == "dirty":
        (active / "app/main.py").write_text("# operator changes\n")
    elif finding == "env_link":
        (active / ".env").unlink()
        (active / ".env").write_text("private config must not be replaced\n")
    elif finding in (
        "target_env",
        "target_venv",
        "missing_helper",
        "ignored_collision",
    ):
        if finding == "missing_helper":
            git("rm", "install/lib/env_tools.py", cwd=source)
        else:
            filename = {
                "target_env": ".env",
                "target_venv": ".venv/tracked",
                "ignored_collision": "var/operator.txt",
            }[finding]
            path = source / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unwanted overwrite\n")
            git("add", "-f", filename, cwd=source)
        git("commit", "-qm", "unsafe target", cwd=source)
        target = git("rev-parse", "HEAD", cwd=source)
        if finding == "ignored_collision":
            (active / "var").mkdir()
            (active / "var/operator.txt").write_text("operator data\n")
    with config.open("a") as stream:
        stream.write("ECR_HTTPS_ENABLED=true\n")
    tooling = tmp_path / "guard-tooling"
    shutil.copytree(ROOT / "install", tooling)
    events = tmp_path / "guard-events"
    common = tooling / "lib/common.sh"
    common.write_text(
        common.read_text()
        + PRIVILEGE_STUBS
        + f"""
acquire_deployment_lock() {{ :; }}
systemctl() {{ echo "$1" >>{Q(str(events))}; {"return 39" if finding == "service" else ":"}; }}
wait_for_local_health() {{ echo local >>{Q(str(events))}; {"return 40" if finding == "local" else ":"}; }}
https_health_check() {{ echo https >>{Q(str(events))}; {"return 41" if finding == "https" else ":"}; }}
sync_release_dependencies() {{ echo sync >>{Q(str(events))}; }}
run_db_check() {{ echo db >>{Q(str(events))}; }}
run_migrations() {{ echo forbidden-migration >>{Q(str(events))}; return 95; }}
"""
    )
    executable(
        tooling / "backup.sh",
        f"echo backup >>{Q(str(events))}\n"
        + (
            "exit 42\n"
            if finding == "backup"
            else f"printf '%s' {Q(str(base / 'backups/safety.sql.gz'))}\n"
        ),
    )
    before_env = (base / "shared/.env").read_bytes()
    result = subprocess.run(
        ["bash", str(tooling / "update.sh"), target],
        env=dict(os.environ, ECR_CONFIG_FILE=str(config)),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert "update successful" not in result.stdout
    assert "never-log-this-secret" not in result.stdout + result.stderr
    assert git("-C", active, "rev-parse", "HEAD") == old
    assert (base / "current").resolve() == active
    assert list((base / "releases").iterdir()) == [active]
    assert (base / "shared/.env").read_bytes() == before_env
    assert (
        base / "shared/data/protected/signature.png"
    ).read_bytes() == b"protected data"
    assert not (base / "state/deployment.env").exists()
    assert not (base / "state/deployment-history.tsv").exists()
    log = events.read_text().splitlines() if events.exists() else []
    assert "forbidden-migration" not in log
    if finding in ("target_env", "target_venv", "missing_helper", "ignored_collision"):
        assert log[:5] == ["is-active", "local", "https", "backup", "stop"]
        assert log[-3:] == ["start", "local", "https"]
        assert "application health verified" in result.stderr
        if finding == "ignored_collision":
            assert (active / "var/operator.txt").read_text() == "operator data\n"
    else:
        assert "stop" not in log and "start" not in log
        assert not list((base / "state").glob(".env.before-update.*"))


@pytest.mark.parametrize(
    "failure", ["none", "blank", "legacy", "launcher", "health", "https", "git"]
)
def test_same_commit_reconciles_from_active_worktree_without_new_release(
    deployment, tmp_path, failure
):
    base, _source, config, old, target = deployment
    active = seed_active_runtime(base)
    git("-C", active, "checkout", "--detach", target)
    state = base / "state/deployment.env"
    stale = (
        f"ECR_CURRENT_COMMIT={'' if failure == 'blank' else old}\nECR_CURRENT_REF=stale-ref\n"
        f"ECR_PREVIOUS_RELEASE={Q(str(active))}\n"
    )
    if failure != "legacy":
        stale += f"ECR_PREVIOUS_COMMIT={old}\nECR_PREVIOUS_REF=prior-tag\n"
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
systemctl() {{ if [[ $1 == is-active ]]; then echo active >>{Q(str(events))}; else echo forbidden-restart >>{Q(str(events))}; return 94; fi; }}
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
        assert result.returncode == (49 if failure == "git" else 1)
        if failure == "git":
            assert "before application mutation" in result.stderr
        else:
            assert "update cancelled" in result.stderr
        assert state.read_text() == stale
        assert installed.read_bytes() == b"stale launcher"
        assert not (base / "state/deployment-history.tsv").exists()
    else:
        assert "Deployment metadata was stale" in result.stderr
        assert dotenv_values(state)["ECR_CURRENT_COMMIT"] == target
        assert dotenv_values(state)["ECR_CURRENT_REF"] == "main"
        assert dotenv_values(state)["ECR_PREVIOUS_COMMIT"] == (
            "" if failure == "legacy" else old
        )
        assert dotenv_values(state)["ECR_PREVIOUS_REF"] == (
            "" if failure == "legacy" else "prior-tag"
        )
        assert "ECR_PREVIOUS_RELEASE" not in dotenv_values(state)
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
    if failure in ("none", "blank", "legacy"):
        assert log == ["active", "local", "https", "launcher", "status"]
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


@pytest.mark.parametrize("caller_umask", ["0077", "0022"])
def test_release_build_permissions_are_independent_of_caller_umask(
    deployment, tmp_path, caller_umask
):
    # Real Git and a native venv; uv/root/service identity are simulated.
    # Public permission bits model an account other than the build owner.
    base, source, _config, *_ = deployment
    (source / "app/__init__.py").write_text("")
    (source / "app/db").mkdir()
    (source / "app/db/__init__.py").write_text("")
    (source / "app/db/check.py").write_text(
        "import deployment_package_probe\n"
        "assert deployment_package_probe.VALUE == 'installed'\n"
    )
    git("add", ".", cwd=source)
    git("commit", "-qm", "permission probe application fixture", cwd=source)
    git(f"--git-dir={base / 'repository.git'}", "fetch", "origin", "main:main")
    target = git("rev-parse", "HEAD", cwd=source)
    cache_file = base / "shared/uv-cache/package-probe.py"
    cache_file.write_text("VALUE = 'installed'\n")
    cache_file.chmod(0o444)
    runtime = executable(
        base / "shared/uv-python/cpython/bin/python3.12",
        f'exec {Q(sys.executable)} "$@"\n',
    )
    fingerprints = {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in (cache_file, runtime)
    }
    (base / "shared/.env").chmod(0o640)
    sync_code = """
import os
import sys
import venv
from pathlib import Path

release, cached = map(Path, sys.argv[1:])
environment = release / '.venv'
venv.EnvBuilder(with_pip=False).create(environment)
site = next((environment / 'lib').glob('python*/site-packages'))
os.link(cached, site / 'deployment_package_probe.py')
for command in ('alembic', 'uvicorn'):
    entry = environment / 'bin' / command
    entry.write_text(
        '#!' + str(environment / 'bin/python') + '\\n'
        'import deployment_package_probe, app.main\\n'
        'assert deployment_package_probe.VALUE == "installed"\\n'
    )
    entry.chmod(entry.stat().st_mode | 0o111)
"""
    bins = tmp_path / "permission-bin"
    build_mask = tmp_path / "uv-build-mask"
    executable(
        bins / "uv",
        f"""
set -Eeuo pipefail
case "$1 ${{2:-}}" in
    'python install') exit 0;;
    'python find') printf '%s\\n' {Q(str(runtime))};;
    sync*)
        printf '%s' "$(umask)" >{Q(str(build_mask))}
        [[ $* == *'--frozen --no-dev'* ]]
        [[ $* == *'--managed-python --no-python-downloads'* ]]
        [[ $* == *{Q(str(runtime))}* ]]
        exec {Q(sys.executable)} -c {Q(sync_code)} "$3" {Q(str(cache_file))};;
    *) exit 91;;
esac
""",
    )
    result = bash(f"""
source {Q(str(COMMON))}
{PRIVILEGE_STUBS}
ECR_INSTALL_DIR={Q(str(base))}; ECR_SERVICE_USER=mock_service
export PATH={Q(str(bins))}:$PATH
umask {caller_umask}
mkdir "$ECR_INSTALL_DIR/state/private-before"
printf 'private' >"$ECR_INSTALL_DIR/state/private-before/token"
release=$(prepare_release {Q(target)})
[[ $(umask) == {caller_umask} ]]
mkdir "$ECR_INSTALL_DIR/state/private-after"
printf 'private' >"$ECR_INSTALL_DIR/state/private-after/token"
# Service identity is mocked, so do not create root-owned bytecode at runtime
# under the caller's private mask while inspecting build-time permissions.
export PYTHONPATH="$release" PYTHONDONTWRITEBYTECODE=1
run_db_check "$release"
run_migrations "$release"
(cd "$release" && run_as_service "$release/.venv/bin/uvicorn" app.main:app)
printf '%s' "$release"
""")
    assert result.returncode == 0, result.stderr
    release = Path(result.stdout)
    assert git("-C", release, "rev-parse", "HEAD") == target
    # Every newly built directory can be traversed/read by a non-owner, while
    # app/package source is readable and CLI entrypoints remain executable.
    for node in [release, *release.rglob("*")]:
        if node.is_symlink():
            continue
        mode = node.stat().st_mode
        if node.is_dir():
            assert mode & 0o005 == 0o005, node
        else:
            assert mode & 0o004, node
    for entry in ("python", "alembic", "uvicorn"):
        command = release / ".venv/bin" / entry
        assert command.stat().st_mode & 0o005 == 0o005
        assert os.access(command, os.R_OK | os.X_OK)
    assert release.stat().st_mode & 0o777 == 0o755
    assert (release / ".venv").stat().st_mode & 0o777 == 0o755
    assert build_mask.read_text() == "0022"
    assert (release / ".env").is_symlink()
    assert (release / ".env").resolve() == base / "shared/.env"
    assert (base / "shared/.env").stat().st_mode & 0o777 == 0o640
    package = next((release / ".venv/lib").glob("python*/site-packages"))
    assert (
        package / "deployment_package_probe.py"
    ).stat().st_ino == cache_file.stat().st_ino
    for path, fingerprint in fingerprints.items():
        assert (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_mtime_ns,
        ) == fingerprint
    for name in ("private-before", "private-after"):
        private = base / "state" / name
        assert private.stat().st_mode & 0o777 == (
            0o700 if caller_umask == "0077" else 0o755
        )
        assert (private / "token").stat().st_mode & 0o777 == (
            0o600 if caller_umask == "0077" else 0o644
        )


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
            f"""
[[ $(umask) == 0077 ]]
bootstrap=$(dirname -- "$(dirname -- "${{BASH_SOURCE[0]}}")")
[[ $(stat -c %a -- "$bootstrap") == 700 ]]
private_file=$(mktemp "$bootstrap/private.XXXXXXXX")
[[ $(stat -c %a -- "$private_file") == 600 ]]
printf '%s' "$ECR_BOOTSTRAP_COMMIT" >{Q(str(marker))}
""",
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
        assert marker.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "lock").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("ref", ["commit", "main", "release-tag"])
def test_launcher_updates_existing_worktree_with_no_private_service_paths(
    deployment, tmp_path, ref
):
    """Real launcher/archive/Git/env/backup; simulated uv, MySQL and systemd.

    The real target updater is run from a 0700 bootstrap. Service execution is
    intercepted at its boundary, but actual Python validates target settings.
    No production service/account or /run path is modified by this test.
    """
    base, source, config, _old, _target = deployment
    active = seed_active_runtime(base)
    events = tmp_path / "flow-events"
    commands = tmp_path / "service-commands"
    installed = tmp_path / "ecr-update"
    installed.write_text("previous launcher")
    bins = tmp_path / "bin"
    runtime = executable(
        base / "shared/uv-python/cpython/bin/python3.12",
        f'exec {Q(sys.executable)} "$@"\n',
    )
    (active / ".venv/bin/python").unlink()
    (active / ".venv/bin/python").symlink_to(runtime)
    # Simulate a healthy installation containing obsolete updater code.
    executable(source / "install/update.sh", "echo OLD-UPDATER; exit 99\n")
    git("add", ".", cwd=source)
    git("commit", "-qm", "legacy updater", cwd=source)
    old = git("rev-parse", "HEAD", cwd=source)
    git(f"--git-dir={base / 'repository.git'}", "fetch", "origin")
    git("-C", active, "checkout", "--detach", old)
    shutil.copyfile(ROOT / "install/update.sh", source / "install/update.sh")
    (source / "app/core").mkdir()
    (source / "app/__init__.py").write_text("")
    (source / "app/core/__init__.py").write_text("")
    shutil.copyfile(ROOT / "app/core/config.py", source / "app/core/config.py")
    (source / "app/db").mkdir()
    (source / "app/db/__init__.py").write_text("")
    (source / "app/db/check.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "assert Path('.venv/lib/service-readable').read_text() == 'runtime'\n"
        "with open(os.environ['ECR_TEST_EVENTS'], 'a') as stream:\n"
        "    stream.write('db-check\\n')\n"
    )
    alembic = executable(
        tmp_path / "simulated-alembic",
        f"""
[[ $* == 'upgrade head' ]]
[[ $PWD == {Q(str(active))} ]]
echo migration >>"$ECR_TEST_EVENTS"
""",
    )
    common = source / "install/lib/common.sh"
    common.write_text(
        common.read_text()
        + PRIVILEGE_STUBS
        + f"""
# Local-only substitutes for privileges and service management.
[[ $(umask) == 0077 ]]
[[ $(stat -c %a -- "${{SCRIPT_DIR%/install}}") == 700 ]]
acquire_deployment_lock() {{
    if [[ ${{1:-}} == /run/lock/ecr-backup.lock ]]; then return; fi
    [[ $ECR_DEPLOYMENT_LOCK_HELD == true ]]
    [[ $(readlink -f -- /proc/$$/fd/9) == {Q(str(tmp_path / "lock"))} ]]
    flock -n 9
    echo lock >>"$ECR_TEST_EVENTS"
}}
run_as_service() {{
    printf '%s\\n' "$*" >>{Q(str(commands))}
    [[ $* != *'/run/ecr-update.'* && $* != *{Q(str(tmp_path / "bootstrap."))}* ]] || return 95
    [[ -x {Q(str(active / ".venv/bin/python"))} ]]
    "$@"
}}
systemctl() {{ echo "$1" >>"$ECR_TEST_EVENTS"; }}
original_atomic=$(declare -f atomic_install_file)
eval "${{original_atomic/atomic_install_file/real_atomic_install_file}}"
atomic_install_file() {{
    if [[ $2 == /usr/local/sbin/ecr-update ]]; then
        [[ $1 == {Q(str(active / "install/ecr-update.sh"))} ]]
        echo launcher >>"$ECR_TEST_EVENTS"
        real_atomic_install_file "$1" {Q(str(installed))} "$3" "$4" "$5"
    else real_atomic_install_file "$@"; fi
}}
"""
    )
    git("add", ".", cwd=source)
    git("commit", "-qm", "target in-place tooling", cwd=source)
    target = git("rev-parse", "HEAD", cwd=source)
    git("tag", "release-tag", cwd=source)
    executable(
        bins / "uv",
        f"""
set -Eeuo pipefail
case "$1 ${{2:-}}" in
    'python install') [[ $* == 'python install --no-bin 3.12' ]];;
    'python find') printf '%s\\n' {Q(str(active / ".venv/bin/python"))};;
    sync*)
        [[ $(umask) == 0022 ]]
        [[ $* == *'--frozen --no-dev'* ]]
        [[ $* == *'--managed-python --no-python-downloads'* ]]
        [[ $* == *'--directory '{Q(str(active))}* ]]
        [[ $* == *'--python '{Q(str(runtime))}* ]]
        [[ $(git -C {Q(str(active))} rev-parse HEAD) == {Q(target)} ]]
        echo uv-sync >>"$ECR_TEST_EVENTS"
        mkdir -p {Q(str(active / ".venv/lib"))}
        printf runtime >{Q(str(active / ".venv/lib/service-readable"))}
        cp {Q(str(alembic))} {Q(str(active / ".venv/bin/alembic"))}
        ;;
    *) exit 91;;
esac
""",
    )
    executable(
        bins / "mysqldump",
        "echo backup >>\"$ECR_TEST_EVENTS\"\nprintf 'CREATE TABLE recovery_fixture(id INT);\\n'\n",
    )
    executable(
        bins / "curl",
        'if [[ $* == *https://* ]]; then echo https >>"$ECR_TEST_EVENTS"; '
        'else echo local >>"$ECR_TEST_EVENTS"; fi\nprintf \'{"status":"ok"}\'\n',
    )
    with config.open("a") as stream:
        stream.write(
            f"export ECR_TEST_EVENTS={Q(str(events))}\n"
            f"export PATH={Q(str(bins))}:$PATH\nECR_HTTPS_ENABLED=true\n"
        )
    env_file = base / "shared/.env"
    env_file.write_text(
        "APP_ENV=production\nAPP_DEBUG=false\nDB_HOST=127.0.0.1\nDB_PORT=3306\n"
        "DB_NAME=ecr\nDB_USER=ecr\nDB_PASSWORD='never-log-this-secret'\n"
        f"STORAGE_ROOT={base / 'shared/data/protected'}\n"
    )
    env_file.chmod(0o640)
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
    assert result.returncode == 0, result.stderr
    assert "OLD-UPDATER" not in result.stdout + result.stderr
    assert "never-log-this-secret" not in result.stdout + result.stderr
    assert events.read_text().splitlines() == [
        "lock",
        "is-active",
        "local",
        "https",
        "backup",
        "stop",
        "uv-sync",
        "db-check",
        "migration",
        "start",
        "local",
        "https",
        "launcher",
    ]
    service_calls = commands.read_text().splitlines()
    assert any("env_tools.py validate" in call for call in service_calls)
    assert any("-m app.db.check" in call for call in service_calls)
    assert any("alembic upgrade head" in call for call in service_calls)
    assert all(
        "/run/ecr-update." not in call and "/bootstrap." not in call
        for call in service_calls
    )
    assert (base / "current").resolve() == active
    assert list((base / "releases").iterdir()) == [active]
    assert git("-C", active, "rev-parse", "HEAD") == target
    assert "??" not in git("-C", active, "status", "--porcelain")
    assert dotenv_values(env_file)["DB_PASSWORD"] == "never-log-this-secret"
    assert env_file.stat().st_mode & 0o777 == 0o640
    assert (active / ".venv/lib/service-readable").stat().st_mode & 0o444 == 0o444
    assert (active / ".venv/bin/alembic").stat().st_mode & 0o111 == 0o111
    assert not list(tmp_path.glob("bootstrap.*"))
    assert (tmp_path / "lock").stat().st_mode & 0o777 == 0o600
    backups = list((base / "backups").glob("*.meta"))
    assert len(backups) == 1
    metadata = dotenv_values(backups[0])
    assert metadata["APPLICATION_COMMIT"] == old
    assert metadata["BACKUP_COMPLETE"] == "true"
    files = backups[0].with_suffix(".files.tar.gz")
    with tarfile.open(files) as archive:
        assert (
            archive.extractfile("data/protected/signature.png").read()
            == b"protected data"
        )
    state = dotenv_values(base / "state/deployment.env")
    assert state["ECR_CURRENT_COMMIT"] == target
    assert state["ECR_PREVIOUS_COMMIT"] == old
    assert state["ECR_PREVIOUS_REF"] == "unknown"
    assert "ECR_PREVIOUS_RELEASE" not in state
    assert target in (base / "state/deployment-history.tsv").read_text()
    assert installed.read_bytes() == (active / "install/ecr-update.sh").read_bytes()


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
