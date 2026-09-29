#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=install/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
trap 'error_report "$LINENO"' ERR

require_root
load_deployment_config
acquire_deployment_lock
load_deployment_state || die "Deployment state is unavailable."

current_release=$(current_release_path) || die "Current release is unavailable."
if [[ $# -gt 1 ]]; then
    die "Usage: $0 [RELEASE_DIRECTORY_OR_NAME]"
fi

target=${1:-${ECR_PREVIOUS_RELEASE:-}}
[[ -n "$target" ]] || die "No previous release is recorded."
[[ "$target" == /* ]] || target="$ECR_INSTALL_DIR/releases/$target"
target_release=$(readlink -f "$target") || die "Rollback release does not exist."
release_root=$(readlink -f "$ECR_INSTALL_DIR/releases")
[[ "$target_release" == "$release_root"/* ]] || die "Rollback target is outside the releases directory."
[[ -x "$target_release/.venv/bin/uvicorn" && -f "$target_release/pyproject.toml" ]] || \
    die "Rollback target is not a complete ECR release."

target_commit=$(git_as_deployer -C "$target_release" rev-parse HEAD)
printf 'Current release : %s\n' "$current_release"
printf 'Target release  : %s\n' "$target_release"
printf 'Target commit   : %s\n' "$target_commit"
printf 'Database migrations will NOT be downgraded.\n'
confirm "Proceed with application-code rollback?" Y || die "Rollback cancelled."

activate_release "$target_release"
if ! systemctl restart "$ECR_SERVICE_NAME.service" || ! wait_for_local_health 30; then
    warn "Rollback health check failed; restoring the prior application release."
    activate_release "$current_release"
    systemctl restart "$ECR_SERVICE_NAME.service" || true
    die "Application rollback failed. Database was not changed."
fi

if [[ ${ECR_HTTPS_ENABLED:-false} == "true" ]] && ! https_health_check; then
    warn "Application is locally healthy, but HTTPS health verification failed."
fi

write_deployment_state "$target_commit" "$target_commit" "$current_release"
append_deployment_history "rollback" "$target_commit" \
    "$target_commit" "$target_release"
log "Application rollback complete at commit ${target_commit:0:12}."
log "For schema rollback, use a reviewed database backup with restore.sh."
