#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=install/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

migration_started=false
update_error() {
    local exit_code=$?
    local line_number=${1:-unknown}
    if [[ "$migration_started" == false ]]; then
        warn "Update failed before migrations. The previous release remains active."
    else
        warn "Update failed after migrations began. No automatic Alembic downgrade was attempted."
        warn "Review the database backup and use restore.sh only after assessing schema compatibility."
    fi
    printf '[ECR] ERROR: update failed near line %s (exit %s).\n' \
        "$line_number" "$exit_code" >&2
    exit "$exit_code"
}
trap 'update_error "$LINENO"' ERR

require_root
load_deployment_config
acquire_deployment_lock
require_command uv
require_command git
require_command curl

load_deployment_state || true
requested_ref=${1:-${ECR_CURRENT_REF:-${ECR_GIT_REF:-main}}}
[[ $# -le 1 ]] || die "Usage: $0 [GIT_REF]"
validate_git_ref "$requested_ref"

old_release=$(current_release_path) || die "Current ECR release is unavailable."
old_commit=${ECR_CURRENT_COMMIT:-$(git_as_deployer -C "$old_release" rev-parse HEAD)}
old_ref=${ECR_CURRENT_REF:-unknown}

ensure_repository
fetch_repository
new_commit=$(resolve_git_ref "$requested_ref") || die "Requested Git ref was not found: $requested_ref"
if [[ "$new_commit" == "$old_commit" ]]; then
    write_deployment_state "$old_commit" "$requested_ref" "${ECR_PREVIOUS_RELEASE:-}"
    log "ECR is already deployed at ${new_commit:0:12} for ref $requested_ref."
    "$SCRIPT_DIR/status.sh"
    exit 0
fi

log "Creating mandatory pre-migration database backup."
backup_path=$("$SCRIPT_DIR/backup.sh")
log "Pre-migration backup: $backup_path"

new_release=$(prepare_release "$new_commit")
link_release_env "$new_release"
run_db_check "$new_release"

migration_started=true
run_migrations "$new_release"

activate_release "$new_release"
systemctl restart "$ECR_SERVICE_NAME.service"
wait_for_local_health 30 || die "Updated application failed its local health check."
if [[ ${ECR_HTTPS_ENABLED:-false} == "true" ]]; then
    https_health_check || die "Updated application failed its HTTPS health check."
fi

write_deployment_state "$new_commit" "$requested_ref" "$old_release"
append_deployment_history "update" "$requested_ref" "$new_commit" "$new_release"

printf 'ECR update successful.\n'
printf 'Previous ref/commit : %s / %s\n' "$old_ref" "${old_commit:0:12}"
printf 'Current ref/commit  : %s / %s\n' "$requested_ref" "${new_commit:0:12}"
printf 'Database backup     : %s\n' "$backup_path"
