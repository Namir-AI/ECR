#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=install/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

migration_started=false
service_stopped=false
worktree_mutation_started=false
application_healthy=false
state_recorded=false
history_recorded=false
launcher_refresh_started=false
launcher_refreshed=false
application_dir=""
env_backup=""
backup_path=""
recover_before_migrations() {
    local restored=true
    if [[ "$worktree_mutation_started" == true ]]; then
        checkout_application_commit "$application_dir" "$old_commit" || restored=false
    fi
    if [[ -n "$env_backup" && -f "$env_backup" ]]; then
        restore_production_env "$env_backup" || restored=false
    fi
    if [[ "$worktree_mutation_started" == true && "$restored" == true ]]; then
        sync_application_dependencies "$application_dir" || restored=false
        if [[ "$restored" == true ]]; then
            run_db_check "$application_dir" || restored=false
            if [[ "$restored" == true ]]; then
                run_application_import_check "$application_dir" || restored=false
            fi
        fi
    fi
    if [[ "$restored" == true ]]; then
        systemctl start "$ECR_SERVICE_NAME.service" || restored=false
        if [[ "$restored" == true ]]; then
            wait_for_local_health 30 || restored=false
        fi
        if [[ "$restored" == true && ${ECR_HTTPS_ENABLED:-false} == true ]]; then
            https_health_check || restored=false
        fi
    fi
    if [[ "$restored" == true ]]; then
        warn "Update failed before migrations. Previous Git commit, environment and runtime restored; application health verified."
    else
        systemctl stop "$ECR_SERVICE_NAME.service" || warn "Could not stop ECR; operator intervention required."
        warn "Pre-migration recovery could not be verified. ECR must remain stopped for operator recovery."
        warn "Restore commit $old_commit and the saved environment, sync its frozen dependencies, then verify DB connectivity and health before starting ECR."
    fi
}

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
    elif [[ "$migration_started" == true ]]; then
        systemctl stop "$ECR_SERVICE_NAME.service" || warn "Could not stop ECR; operator intervention required."
        warn "Update failed after migrations began. ECR is stopped; no automatic Git rollback or Alembic downgrade was attempted."
        warn "Assess schema compatibility before restoring code or using restore.sh for the database and paired files."
    elif [[ "$service_stopped" == true ]]; then
        recover_before_migrations
    else
        warn "Update failed before application mutation. Current worktree, service and database were not changed."
    fi
    warn "Pre-update commit: ${old_commit:-not verified}"
    warn "Database/files backup: ${backup_path:-not available}"
    warn "Environment snapshot: ${env_backup:-not available}"
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

ensure_repository
if [[ -z ${ECR_BOOTSTRAP_COMMIT:-} ]]; then
    fetch_repository
fi
new_commit=$(resolve_git_ref "$deployment_ref") || die "Requested Git ref was not found: $requested_ref"
[[ "$new_commit" =~ ^[0-9a-f]{40}$ ]] || die "Requested ref did not resolve to an exact Git commit."
[[ -z ${ECR_BOOTSTRAP_COMMIT:-} || "$new_commit" == "$ECR_BOOTSTRAP_COMMIT" ]] || \
    die "Target bootstrap commit differs from requested deployment."

application_dir=$(valid_current_release_path) || die "Current ECR application is unavailable."
old_commit=$(git_as_deployer -C "$application_dir" rev-parse HEAD) || die "Cannot verify the active release Git commit."
application_top=$(git_as_deployer -C "$application_dir" rev-parse --show-toplevel)
[[ $(readlink -f -- "$application_top") == "$application_dir" ]] || die "Current application is not a Git worktree root."
case "$SCRIPT_DIR/" in
    "$application_dir/"*) die "Use ecr-update: updater tooling must run outside the worktree it changes." ;;
esac
if [[ -v ECR_CURRENT_COMMIT && "$ECR_CURRENT_COMMIT" != "$old_commit" ]]; then
    warn "Deployment metadata was stale: recorded commit $ECR_CURRENT_COMMIT differs from active worktree $old_commit. Using the actual current release."
fi
old_ref=${ECR_CURRENT_REF:-unknown}
worktree_status=$(git_as_deployer -C "$application_dir" status --porcelain) || die "Cannot verify application worktree cleanliness."
[[ -z "$worktree_status" ]] || die "Current application worktree has local changes; operator review required."
[[ -L "$application_dir/.env" && $(readlink -f -- "$application_dir/.env") == "$ECR_INSTALL_DIR/shared/.env" ]] || die "Application .env must link to protected shared/.env."
storage_root=$("$application_dir/.venv/bin/python" "$SCRIPT_DIR/lib/env_tools.py" backup-storage "$ECR_INSTALL_DIR/shared/.env" "$ECR_INSTALL_DIR")
storage_root=$(readlink -f -- "$storage_root")
case "$storage_root/" in
    "$application_dir/"*) die "STORAGE_ROOT must be outside the Git-controlled application worktree." ;;
esac
systemctl is-active --quiet "$ECR_SERVICE_NAME.service" || die "Current ECR service is not active."
wait_for_local_health 30 || die "Current application failed its local health check; update cancelled."
if [[ ${ECR_HTTPS_ENABLED:-false} == true ]]; then
    https_health_check || die "Current application failed its HTTPS health check; update cancelled."
fi
if [[ "$new_commit" == "$old_commit" ]]; then
    application_healthy=true
    write_deployment_state "$old_commit" "$requested_ref" "${ECR_PREVIOUS_COMMIT:-}" "${ECR_PREVIOUS_REF:-}"
    state_recorded=true
    append_deployment_history "reconcile" "$requested_ref" "$old_commit" "$application_dir"
    history_recorded=true
    launcher_refresh_started=true
    install_update_launcher "$application_dir"
    launcher_refreshed=true
    log "ECR is already deployed at ${new_commit:0:12} for ref $requested_ref."
    "$SCRIPT_DIR/status.sh"
    exit 0
fi

# Only normal deployment is forward-only. Keep checkout_application_commit
# unrestricted so pre-migration recovery can restore the verified old SHA.
if git_as_deployer -C "$application_dir" merge-base --is-ancestor "$old_commit" "$new_commit"; then
    :
else
    ancestry_status=$?
    if (( ancestry_status == 1 )); then
        die "Normal ECR updates are forward-only. Older or divergent commits are rejected; rollback/recovery requires reviewed operator recovery."
    fi
    die "Cannot verify forward-only Git ancestry (exit $ancestry_status); update cancelled without application changes. Reviewed operator recovery is required."
fi

log "Creating mandatory pre-migration database and persistent-files backup."
backup_path=$("$SCRIPT_DIR/backup.sh")
log "Pre-migration backup: $backup_path"

pending_env_backup=$(mktemp "$ECR_INSTALL_DIR/state/.env.before-update.XXXXXX")
cp -- "$ECR_INSTALL_DIR/shared/.env" "$pending_env_backup"
env_backup=$pending_env_backup
chown root:root "$env_backup"
chmod 0600 "$env_backup"

log "Stopping ECR and updating the existing application worktree."
service_stopped=true
systemctl stop "$ECR_SERVICE_NAME.service"
# Resolve first, then fetch again while stopped. Deploy the pinned SHA even if
# a branch/tag moves; never re-resolve a moving ref after the safety backup.
fetch_repository
# Linked worktrees share the mirror's object database; no self-fetch is needed.
verify_application_target "$application_dir" "$new_commit"
worktree_mutation_started=true
checkout_application_commit "$application_dir" "$new_commit"
sync_application_dependencies "$application_dir"
upgrade_production_env "$application_dir"
run_db_check "$application_dir"
run_application_import_check "$application_dir"

migration_started=true
run_migrations "$application_dir"

systemctl start "$ECR_SERVICE_NAME.service"
wait_for_local_health 30 || die "Updated application failed its local health check."
if [[ ${ECR_HTTPS_ENABLED:-false} == "true" ]]; then
    https_health_check || die "Updated application failed its HTTPS health check."
fi
application_healthy=true

# The mutable worktree is not a previous-release rollback destination.
write_deployment_state "$new_commit" "$requested_ref" "$old_commit" "$old_ref"
state_recorded=true
append_deployment_history "update" "$requested_ref" "$new_commit" "$application_dir"
history_recorded=true
launcher_refresh_started=true
install_update_launcher "$application_dir"
launcher_refreshed=true

printf 'ECR update successful.\n'
printf 'Previous ref/commit : %s / %s\n' "$old_ref" "${old_commit:0:12}"
printf 'Current ref/commit  : %s / %s\n' "$requested_ref" "${new_commit:0:12}"
printf 'Application worktree: %s\n' "$application_dir"
printf 'Database backup     : %s\n' "$backup_path"
