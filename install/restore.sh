#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=install/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

workspace=""
safety_backup=""
service_stopped=false
service_was_active=false
restore_started=false
restore_exit() {
    local code=$?
    trap - EXIT ERR
    set +e
    if (( code != 0 )); then
        warn "Restore failed. Safety backup: ${safety_backup:-not created; no restore attempted}"
        if [[ "$restore_started" == true ]]; then
            # MySQL DDL may already have committed. Never restart a potentially
            # mixed DB/files state or guess that automatic recovery is safe.
            systemctl stop "$ECR_SERVICE_NAME.service" || warn "Could not confirm application shutdown."
            warn "Database/files may be partially restored. Application remains stopped for operator recovery."
        elif [[ "$service_stopped" == true && "$service_was_active" == true ]]; then
            systemctl start "$ECR_SERVICE_NAME.service" && wait_for_local_health 30 || \
                warn "Original service could not be restored to health."
        fi
    fi
    if [[ -n "$workspace" ]]; then
        if [[ -f "$workspace/plan.json" ]]; then
            "$python" "$helper" cleanup "$workspace" || warn "Restore staging retained for operator review."
            rm -f -- "$workspace/plan.json"
        fi
        rm -f -- "$workspace/database.sql.gz"
        rmdir -- "$workspace" || warn "Restore workspace retained: $workspace"
    fi
    exit "$code"
}
trap restore_exit EXIT

require_root
load_deployment_config
acquire_deployment_lock
require_command mysql
require_command gzip
read_database_environment

[[ $# -le 1 ]] || die "Usage: $0 [BACKUP.sql.gz]"
if [[ $# -eq 0 ]]; then
    log "Available SQL backups:"
    find "$ECR_INSTALL_DIR/backups" -maxdepth 1 -type f -name 'ecr-*.sql.gz' -printf '  %f\n' | sort -r
    read -r -p "Backup filename to restore: " requested_backup
    requested_backup="$ECR_INSTALL_DIR/backups/$requested_backup"
else
    requested_backup=$1
    [[ "$requested_backup" == /* ]] || requested_backup="$PWD/$requested_backup"
fi
backup_file=$(safe_backup_path "$requested_backup") || die "Backup must be a .sql.gz file inside backups."
current_release=$(valid_current_release_path)
read_storage_environment "$current_release"
python="$current_release/.venv/bin/python"
helper="$SCRIPT_DIR/lib/restore_tools.py"
storage_root=$("$python" "$SCRIPT_DIR/lib/env_tools.py" storage "$ECR_INSTALL_DIR/shared/.env")
umask 0077
workspace=$(mktemp -d "$ECR_INSTALL_DIR/state/restore.XXXXXXXX")
"$python" "$helper" prepare "$backup_file" "$ECR_INSTALL_DIR" "$storage_root" \
    "$ECR_DB_NAME_VALUE" "$ECR_SERVICE_USER" "$workspace" \
    "$ECR_STORAGE_BACKEND" "$ECR_S3_BUCKET" "$ECR_S3_REGION" "$ECR_S3_PREFIX"
if [[ "$ECR_STORAGE_BACKEND" == s3 ]]; then
    warn "This restores MySQL only. It does NOT restore or delete S3 objects. IT must review matching S3 object versions/retention before proceeding."
    read -r -p "Type ACKNOWLEDGE S3 RECOVERY ${ECR_DB_NAME_VALUE} to confirm external object recovery has been reviewed: " acknowledgement
    [[ "$acknowledgement" == "ACKNOWLEDGE S3 RECOVERY ${ECR_DB_NAME_VALUE}" ]] || die "S3 recovery review was not acknowledged."
fi
if [[ $("$python" "$helper" missing "$workspace") == yes ]]; then
    read -r -p "Type ACKNOWLEDGE MISSING FILES ${ECR_DB_NAME_VALUE} to allow recovery without those files: " acknowledgement
    [[ "$acknowledgement" == "ACKNOWLEDGE MISSING FILES ${ECR_DB_NAME_VALUE}" ]] || die "Missing-files recovery declined."
fi
printf 'Restore target database : %s\n' "$ECR_DB_NAME_VALUE"
printf 'Backup set              : %s\n' "$backup_file"
printf 'Application code and Alembic are NOT rolled back by this operation.\n'
read -r -p "Type RESTORE ${ECR_DB_NAME_VALUE} to continue: " confirmation
[[ "$confirmation" == "RESTORE ${ECR_DB_NAME_VALUE}" ]] || die "Restore cancelled."

if systemctl is-active --quiet "$ECR_SERVICE_NAME.service"; then
    service_was_active=true
fi
systemctl stop "$ECR_SERVICE_NAME.service"
service_stopped=true
log "Creating mandatory safety backup while the application is stopped."
safety_backup=$("$SCRIPT_DIR/backup.sh")
log "Safety backup retained: $safety_backup"

restore_started=true
log "Restoring reviewed database and persistent-file backup set."
gzip -dc "$workspace/database.sql.gz" | env MYSQL_PWD="$ECR_DB_PASSWORD_VALUE" mysql \
    --host="$ECR_DB_HOST_VALUE" --port="$ECR_DB_PORT_VALUE" --user="$ECR_DB_USER_VALUE" \
    --protocol=TCP --database="$ECR_DB_NAME_VALUE"
"$python" "$helper" publish "$workspace"
run_db_check "$current_release"
systemctl start "$ECR_SERVICE_NAME.service"
wait_for_local_health 30 || {
    systemctl stop "$ECR_SERVICE_NAME.service" || true
    die "Restored application failed its local health check; service stopped."
}
if [[ ${ECR_HTTPS_ENABLED:-false} == true ]] && ! https_health_check; then
    systemctl stop "$ECR_SERVICE_NAME.service" || true
    die "Restored application failed its HTTPS health check; service stopped."
fi
log "Restore completed. Safety backup: $safety_backup"
