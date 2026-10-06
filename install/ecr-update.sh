#!/usr/bin/env bash
# Stable, self-contained bootstrap. Never source helpers from current or cwd.
set -Eeuo pipefail

launcher_config_file=/etc/ecr/deployment.conf
launcher_lock_file=/run/lock/ecr-deployment.lock

launcher_die() { printf '[ECR] ERROR: %s\n' "$*" >&2; exit 1; }

launcher_require_root() {
    [[ $EUID -eq 0 ]] || launcher_die "Run ecr-update with sudo or as root."
}

launcher_trusted_file() {
    local file=$1 mode
    [[ -f "$file" && ! -L "$file" && $(stat -c %u -- "$file") == 0 ]] || \
        launcher_die "Expected a root-owned regular configuration/key file."
    mode=$(stat -c %a -- "$file")
    (( (8#$mode & 0022) == 0 )) || launcher_die "Configuration/key must not be writable by other users."
}

launcher_git() {
    if [[ -n ${ECR_DEPLOY_KEY_FILE:-} ]]; then
        env GIT_SSH_COMMAND="ssh -i $ECR_DEPLOY_KEY_FILE -o IdentitiesOnly=yes -o UserKnownHostsFile=/etc/ecr/github_known_hosts" git "$@"
    else
        git "$@"
    fi
}

launcher_main() (
    launcher_require_root
    export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    umask 0077
    launcher_trusted_file "$launcher_config_file"
    # The containing directory must also be protected from file replacement.
    local config_parent=${launcher_config_file%/*} parent_mode
    [[ $(stat -c %u -- "$config_parent") == 0 ]] || launcher_die "Unprotected configuration directory."
    parent_mode=$(stat -c %a -- "$config_parent")
    (( (8#$parent_mode & 0022) == 0 )) || launcher_die "Unprotected configuration directory."
    # shellcheck disable=SC1090
    source "$launcher_config_file"
    [[ ${ECR_INSTALL_DIR:-} =~ ^/[A-Za-z0-9._/-]+$ && \
        "$ECR_INSTALL_DIR" != / && "$ECR_INSTALL_DIR" != /opt ]] || launcher_die "Invalid installation directory."
    local install_only=false
    if [[ ${1:-} == --install-launcher ]]; then
        install_only=true
        shift
    fi
    [[ $# -le 1 ]] || launcher_die "Usage: ecr-update [GIT_REF]"
    local requested_ref=${1:-${ECR_GIT_REF:-main}}
    [[ "$requested_ref" =~ ^[A-Za-z0-9._/-]+$ && "$requested_ref" != -* && \
        "$requested_ref" != *..* ]] || launcher_die "Invalid Git ref."
    local repository="$ECR_INSTALL_DIR/repository.git" mode origin commit bootstrap_dir="" staged_launcher=""
    [[ -d "$repository" && ! -L "$repository" && $(stat -c %u -- "$repository") == 0 ]] || \
        launcher_die "Protected deployment repository mirror is unavailable."
    mode=$(stat -c %a -- "$repository")
    (( (8#$mode & 0022) == 0 )) || launcher_die "Deployment repository is writable by other users."
    if [[ -n ${ECR_DEPLOY_KEY_FILE:-} ]]; then
        launcher_trusted_file "$ECR_DEPLOY_KEY_FILE"
    fi
    if [[ ! -e "$launcher_lock_file" && ! -L "$launcher_lock_file" ]]; then
        (umask 0077; set -o noclobber; : >"$launcher_lock_file") || true
    fi
    launcher_trusted_file "$launcher_lock_file"
    exec 9<"$launcher_lock_file"
    flock -n 9 || launcher_die "Another ECR deployment operation is already running."
    origin=$(launcher_git --git-dir="$repository" remote get-url origin)
    [[ "$origin" == "${ECR_REPOSITORY_URL:?Missing repository URL}" ]] || launcher_die "Repository origin differs from deployment configuration."
    launcher_git --git-dir="$repository" fetch --prune origin \
        '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*'
    commit=$(launcher_git --git-dir="$repository" rev-parse --verify "$requested_ref^{commit}") || \
        launcher_die "Requested Git ref was not found."
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || launcher_die "Unable to resolve an exact commit."
    if [[ "$install_only" != true ]]; then
        # Check BEFORE running target-version tooling: an older/divergent
        # commit could contain an updater that predates the forward-only rule.
        local application_dir active_commit ancestry_status
        [[ -L "$ECR_INSTALL_DIR/current" ]] || launcher_die "Current application link is unavailable."
        application_dir=$(readlink -f -- "$ECR_INSTALL_DIR/current") || launcher_die "Cannot resolve the active application."
        active_commit=$(launcher_git -C "$application_dir" rev-parse --verify 'HEAD^{commit}') || launcher_die "Cannot verify the active application commit."
        [[ "$active_commit" =~ ^[0-9a-f]{40}$ ]] || launcher_die "Invalid active application commit."
        if [[ "$active_commit" != "$commit" ]]; then
            if launcher_git --git-dir="$repository" merge-base --is-ancestor "$active_commit" "$commit"; then
                :
            else
                ancestry_status=$?
                (( ancestry_status != 1 )) || launcher_die "Normal ECR updates are forward-only. Older or divergent commits are rejected; rollback/recovery requires reviewed operator recovery."
                launcher_die "Cannot verify forward-only Git ancestry (exit $ancestry_status); update cancelled without application changes. Reviewed operator recovery is required."
            fi
        fi
    fi
    bootstrap_dir=$(mktemp -d /run/ecr-update.XXXXXXXX)
    # Only this exact root-private mktemp directory is disposable.
    trap 'code=$?; if [[ -n "$bootstrap_dir" ]]; then
        rm -rf -- "$bootstrap_dir" || printf "[ECR] WARNING: Bootstrap cleanup failed.\n" >&2
    fi; if [[ -n "$staged_launcher" ]]; then rm -f -- "$staged_launcher" || true; fi; exit "$code"' EXIT
    launcher_git --git-dir="$repository" archive "$commit" install | tar -xf - -C "$bootstrap_dir"
    [[ ! -L "$bootstrap_dir/install" && ! -L "$bootstrap_dir/install/lib" ]] || \
        launcher_die "Target deployment directories must not be symlinks."
    local file
    for file in update.sh backup.sh lib/common.sh lib/env_tools.py lib/runtime_tools.py ecr-update.sh; do
        [[ -f "$bootstrap_dir/install/$file" && ! -L "$bootstrap_dir/install/$file" ]] || \
            launcher_die "Target commit lacks deployment tooling: $file"
    done
    if [[ "$install_only" == true ]]; then
        [[ ! -e /usr/local/sbin/ecr-update && ! -L /usr/local/sbin/ecr-update ]] || \
            launcher_die "Launcher already installed; use ecr-update <ref> for a health-verified refresh."
        staged_launcher=$(mktemp /usr/local/sbin/.ecr-update.XXXXXXXX)
        install -o root -g root -m 0755 "$bootstrap_dir/install/ecr-update.sh" "$staged_launcher"
        mv -T -- "$staged_launcher" /usr/local/sbin/ecr-update
        printf '[ECR] Stable launcher installed. Use sudo ecr-update <release-tag-or-commit>.\n'
        exit 0
    fi
    printf '[ECR] Running target deployment tooling at %s.\n' "$commit" >&2
    ECR_CONFIG_FILE="$launcher_config_file" ECR_DEPLOYMENT_LOCK_HELD=true \
        ECR_BOOTSTRAP_COMMIT="$commit" ECR_REQUESTED_REF="$requested_ref" \
        bash "$bootstrap_dir/install/update.sh" "$commit"
)

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    launcher_main "$@"
fi
