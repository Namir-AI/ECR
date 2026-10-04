#!/usr/bin/env bash
set -Eeuo pipefail

# Deliberately standalone: do not source legacy helpers, configuration or state.
# In-place updates mean old directory names are not immutable code snapshots.
# We cannot certify an older application's compatibility with a migrated DB.
printf '%s\n' \
    '[ECR] ERROR: rollback.sh is disabled for in-place deployments.' \
    '[ECR] No Git checkout, dependency sync, service operation or data change was performed.' \
    '[ECR] Directory-based rollback and ECR_PREVIOUS_RELEASE are no longer supported.' \
    '[ECR] An older application may be incompatible with the current migrated database.' \
    '[ECR] Operator recovery requires an explicitly verified Git SHA and schema-compatibility review.' \
    '[ECR] Before changing code/dependencies, create a verified database/persistent-files backup and preserve the protected environment.' \
    '[ECR] Restore matching code/dependencies while stopped, then verify DB connectivity and local/HTTPS health before recording success.' \
    '[ECR] restore.sh recovers database and persistent files only; it never checks out code or runs Alembic.' \
    '[ECR] No automatic database downgrade or schema recovery is provided. See install/README.md.' >&2
exit 1
