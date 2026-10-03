#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=install/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

migration_started=false
application_healthy=false
state_recorded=false
history_recorded=false
launcher_refresh_started=false
launcher_refreshed=false
new_release=""
env_backup=""
backup_path=""
update_exit() {
    local exit_code=$?
    trap - EXIT
    (( exit_code != 0 )) || return 0
    set +e
    if [[ "$application_healthy" == true ]]; then
        warn "Target application is active at $new_commit and passed all required health checks."
        if [[ "$state_recorded" == true ]]; then
            warn "Deployment state records the target commit: $new_commit"
        else
            warn "Deployment state reconciliation failed; administrator must repair deployment metadata."
        fi
        if [[ "$history_recorded" != true ]]; then
            warn "Successful deployment history was not recorded; administrator must repair history."
        fi
        if [[ "$launcher_refresh_started" == true && "$launcher_refreshed" != true ]]; then
            warn "Stable launcher refresh failed. The current launcher remains unchanged because publication is atomic."
            warn "Administrator must repair launcher publication; repeat ecr-update for the same exact commit."
        fi
        printf '[ECR] ERROR: post-health deployment maintenance failed (exit %s); no application or schema rollback attempted.\n' "$exit_code" >&2
        exit "$exit_code"
    elif [[ "$migration_started" == false ]]; then
        if [[ -n "$new_release" ]]; then
            cleanup_failed_release "$new_release" || true
        fi
        if [[ -n "$env_backup" && -f "$env_backup" ]]; then
            restore_production_env "$env_backup" || \
                warn "Could not restore pre-update environment; review $env_backup."
        fi
        warn "Update failed before migrations. The previous release remains active."
    else
        warn "Update failed after migrations began. No automatic Alembic downgrade was attempted."
        warn "Database backup: ${backup_path:-not available}"
        warn "Environment snapshot: ${env_backup:-not available}"
        warn "Review the backup and use restore.sh only after assessing schema compatibility."
    fi
    printf '[ECR] ERROR: update failed (exit %s).\n' "$exit_code" >&2
    exit "$exit_code"
}
trap update_exit EXIT

require_root
load_deployment_config
acquire_deployment_lock
require_command uv
require_command git
require_command curl

load_deployment_state || true
deployment_ref=${1:-${ECR_GIT_REF:-main}}
requested_ref=${ECR_REQUESTED_REF:-$deployment_ref}
[[ $# -le 1 ]] || die "Usage: $0 [GIT_REF]"
validate_git_ref "$requested_ref"

old_release=$(current_release_path) || die "Current ECR release is unavailable."
old_commit=$(git_as_deployer -C "$old_release" rev-parse HEAD) || die "Cannot verify the active release Git commit."
if [[ -v ECR_CURRENT_COMMIT && "$ECR_CURRENT_COMMIT" != "$old_commit" ]]; then
    warn "Deployment metadata was stale: recorded commit $ECR_CURRENT_COMMIT differs from active worktree $old_commit. Using the actual current release."
fi
old_ref=${ECR_CURRENT_REF:-unknown}

ensure_repository
if [[ -z ${ECR_BOOTSTRAP_COMMIT:-} ]]; then
    fetch_repository
fi
new_commit=$(resolve_git_ref "$deployment_ref") || die "Requested Git ref was not found: $requested_ref"
[[ -z ${ECR_BOOTSTRAP_COMMIT:-} || "$new_commit" == "$ECR_BOOTSTRAP_COMMIT" ]] || \
    die "Target bootstrap commit differs from requested deployment."
if [[ "$new_commit" == "$old_commit" ]]; then
    wait_for_local_health 30 || die "Current application failed its local health check; reconciliation cancelled."
    if [[ ${ECR_HTTPS_ENABLED:-false} == "true" ]]; then
        https_health_check || die "Current application failed its HTTPS health check; reconciliation cancelled."
    fi
    application_healthy=true
    write_deployment_state "$old_commit" "$requested_ref" "${ECR_PREVIOUS_RELEASE:-}"
    state_recorded=true
    append_deployment_history "reconcile" "$requested_ref" "$old_commit" "$old_release"
    history_recorded=true
    launcher_refresh_started=true
    install_update_launcher "$old_release"
    launcher_refreshed=true
    log "ECR is already deployed at ${new_commit:0:12} for ref $requested_ref."
    "$SCRIPT_DIR/status.sh"
    exit 0
fi

log "Creating mandatory pre-migration database backup."
backup_path=$("$SCRIPT_DIR/backup.sh")
log "Pre-migration backup: $backup_path"

new_release=$(prepare_release "$new_commit")
link_release_env "$new_release"
pending_env_backup=$(mktemp "$ECR_INSTALL_DIR/state/.env.before-update.XXXXXX")
cp -a -- "$ECR_INSTALL_DIR/shared/.env" "$pending_env_backup"
env_backup=$pending_env_backup
chown root:root "$env_backup"
chmod 0600 "$env_backup"
upgrade_production_env "$new_release"
run_db_check "$new_release"

migration_started=true
run_migrations "$new_release"

activate_release "$new_release"
systemctl restart "$ECR_SERVICE_NAME.service"
wait_for_local_health 30 || die "Updated application failed its local health check."
if [[ ${ECR_HTTPS_ENABLED:-false} == "true" ]]; then
    https_health_check || die "Updated application failed its HTTPS health check."
fi
application_healthy=true

write_deployment_state "$new_commit" "$requested_ref" "$old_release"
state_recorded=true
append_deployment_history "update" "$requested_ref" "$new_commit" "$new_release"
history_recorded=true
launcher_refresh_started=true
install_update_launcher "$new_release"
launcher_refreshed=true

printf 'ECR update successful.\n'
printf 'Previous ref/commit : %s / %s\n' "$old_ref" "${old_commit:0:12}"
printf 'Current ref/commit  : %s / %s\n' "$requested_ref" "${new_commit:0:12}"
printf 'Database backup     : %s\n' "$backup_path"
