#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=install/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

require_root
load_deployment_config

overall_status=0
application_status="Stopped"
nginx_status="Stopped"
database_status="Disconnected"
health_status="FAIL"
https_status="Disabled"
certificate_status="Not configured"
certificate_expiry="N/A"
renewal_status="N/A"

if systemctl is-active --quiet "$ECR_SERVICE_NAME.service"; then
    application_status="Running"
else
    overall_status=1
fi

if systemctl is-active --quiet nginx; then
    nginx_status="Running"
else
    overall_status=1
fi

current_release=$(current_release_path 2>/dev/null || true)
if [[ -n "$current_release" ]] && run_db_check "$current_release" >/dev/null 2>&1; then
    database_status="Connected"
else
    overall_status=1
fi

if local_health_check >/dev/null 2>&1; then
    health_status="PASS"
else
    overall_status=1
fi

if [[ ${ECR_HTTPS_ENABLED:-false} == "true" ]]; then
    https_status="Inactive"
    certificate_file="/etc/letsencrypt/live/${ECR_DOMAIN}/fullchain.pem"
    if [[ -r "$certificate_file" ]]; then
        raw_expiry=$(openssl x509 -enddate -noout -in "$certificate_file" | cut -d= -f2-)
        certificate_expiry=$(date -u -d "$raw_expiry" +%Y-%m-%d)
        if openssl x509 -checkend 86400 -noout -in "$certificate_file" >/dev/null 2>&1; then
            certificate_status="Valid"
        else
            certificate_status="Expired or expiring"
            overall_status=1
        fi
    else
        certificate_status="Missing"
        overall_status=1
    fi
    if https_health_check >/dev/null 2>&1; then
        https_status="Active"
    else
        overall_status=1
    fi
    if systemctl is-enabled --quiet certbot.timer 2>/dev/null && \
            systemctl is-active --quiet certbot.timer; then
        renewal_status="Active"
    else
        renewal_status="Inactive"
        overall_status=1
    fi
fi

load_deployment_state || true
git_ref=${ECR_CURRENT_REF:-unknown}
commit=${ECR_CURRENT_COMMIT:-unknown}
[[ "$commit" == "unknown" ]] || commit=${commit:0:12}

printf 'Paharpur ECR Status\n'
printf '%s\n' '---------------------------------------'
printf '%-19s : %s\n' 'Application' "$application_status"
printf '%-19s : %s\n' 'Domain' "$ECR_DOMAIN"
printf '%-19s : %s\n' 'HTTPS' "$https_status"
printf '%-19s : %s\n' 'Certificate' "$certificate_status"
printf '%-19s : %s\n' 'Certificate Expiry' "$certificate_expiry"
printf '%-19s : %s\n' 'Auto Renewal' "$renewal_status"
printf '%-19s : %s\n' 'Nginx' "$nginx_status"
printf '%-19s : %s\n' 'Database' "$database_status"
printf '%-19s : %s\n' 'Health Check' "$health_status"
printf '%-19s : %s\n' 'Git Ref' "$git_ref"
printf '%-19s : %s\n' 'Commit' "$commit"
printf '%-19s : %s\n' 'Install Directory' "$ECR_INSTALL_DIR"

exit "$overall_status"
