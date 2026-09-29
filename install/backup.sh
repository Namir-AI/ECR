#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=install/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
trap 'error_report "$LINENO"' ERR

require_root
load_deployment_config
acquire_deployment_lock /run/lock/ecr-backup.lock
require_command mysqldump
require_command gzip
require_command sha256sum

retention_days=""
if [[ $# -gt 0 ]]; then
    [[ $# -eq 2 && $1 == "--prune-older-than" && $2 =~ ^[0-9]+$ ]] || \
        die "Usage: $0 [--prune-older-than DAYS]"
    retention_days=$2
fi

read_database_environment
umask 0077
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
load_deployment_state || true
commit=${ECR_CURRENT_COMMIT:-unknown}
ref=${ECR_CURRENT_REF:-unknown}
safe_ref=${ref//[^A-Za-z0-9._-]/_}
backup_base="$ECR_INSTALL_DIR/backups/ecr-${ECR_DB_NAME_VALUE}-${timestamp}-${safe_ref}"
sql_backup="${backup_base}.sql.gz"
temporary_backup="${sql_backup}.partial.$$"

cleanup_partial() {
    if [[ -f "$temporary_backup" ]]; then
        rm -f -- "$temporary_backup"
    fi
}
trap cleanup_partial EXIT

log "Creating consistent MySQL backup for ${ECR_DB_NAME_VALUE}."
env MYSQL_PWD="$ECR_DB_PASSWORD_VALUE" mysqldump \
    --host="$ECR_DB_HOST_VALUE" \
    --port="$ECR_DB_PORT_VALUE" \
    --user="$ECR_DB_USER_VALUE" \
    --protocol=TCP \
    --single-transaction \
    --quick \
    --hex-blob \
    --no-tablespaces \
    --set-gtid-purged=OFF \
    "$ECR_DB_NAME_VALUE" | gzip -9 >"$temporary_backup"
gzip -t "$temporary_backup"
mv -f "$temporary_backup" "$sql_backup"
chmod 0600 "$sql_backup"

checksum=$(sha256sum "$sql_backup" | awk '{print $1}')
metadata_file="${backup_base}.meta"
{
    printf 'BACKUP_TIMESTAMP=%q\n' "$timestamp"
    printf 'DATABASE_NAME=%q\n' "$ECR_DB_NAME_VALUE"
    printf 'DATABASE_HOST=%q\n' "$ECR_DB_HOST_VALUE"
    printf 'APPLICATION_REF=%q\n' "$ref"
    printf 'APPLICATION_COMMIT=%q\n' "$commit"
    printf 'SQL_SHA256=%q\n' "$checksum"
} >"$metadata_file"
chmod 0600 "$metadata_file"

if [[ -d "$ECR_INSTALL_DIR/shared/data" ]] && \
        [[ -n $(find "$ECR_INSTALL_DIR/shared/data" -mindepth 1 -print -quit) ]]; then
    files_backup="${backup_base}.files.tar.gz"
    tar -C "$ECR_INSTALL_DIR/shared" -czf "$files_backup" data
    chmod 0600 "$files_backup"
    printf 'FILES_SHA256=%q\n' "$(sha256sum "$files_backup" | awk '{print $1}')" \
        >>"$metadata_file"
fi

if [[ -n "$retention_days" ]]; then
    mapfile -t old_backups < <(
        find "$ECR_INSTALL_DIR/backups" -maxdepth 1 -type f -name 'ecr-*.sql.gz' \
            -mtime "+$retention_days" -print | sort
    )
    mapfile -t all_backups < <(
        find "$ECR_INSTALL_DIR/backups" -maxdepth 1 -type f -name 'ecr-*.sql.gz' \
            -print | sort -r
    )
    if (( ${#all_backups[@]} <= 5 )); then
        warn "Retention requested, but the newest five SQL backups are always preserved."
    else
        for old_backup in "${old_backups[@]}"; do
            keep=false
            for newest in "${all_backups[@]:0:5}"; do
                [[ "$old_backup" == "$newest" ]] && keep=true
            done
            if [[ "$keep" == false ]]; then
                old_base=${old_backup%.sql.gz}
                log "Pruning explicitly selected old backup: $(basename "$old_backup")"
                rm -f -- "$old_backup" "${old_base}.meta" "${old_base}.files.tar.gz"
            fi
        done
    fi
fi

trap - EXIT
log "Backup verified: $sql_backup"
printf '%s\n' "$sql_backup"
