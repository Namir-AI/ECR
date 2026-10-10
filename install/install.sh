#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
COMMON_FILE="$SCRIPT_DIR/lib/common.sh"
temporary_common=""
temporary_installer=""
temporary_tools=""
BOOTSTRAP_REF=""
new_install_release=""
installation_migration_started=false

cleanup_bootstrap() {
    local exit_code=$?
    trap - EXIT ERR
    set +e
    if (( exit_code != 0 )) && [[ "$installation_migration_started" == false && \
            -n "$new_install_release" ]]; then
        cleanup_failed_release "$new_install_release" || true
    fi
    if [[ -n "$temporary_common" && -f "$temporary_common" ]]; then
        rm -f -- "$temporary_common" || printf '[ECR] WARNING: Common bootstrap cleanup failed.\n' >&2
    fi
    if [[ -n "$temporary_installer" && -f "$temporary_installer" ]]; then
        rm -f -- "$temporary_installer" || printf '[ECR] WARNING: Reference installer cleanup failed.\n' >&2
    fi
    if [[ -n "$temporary_tools" ]]; then
        # Exact private mktemp bootstrap directory, never an installation path.
        rm -rf -- "$temporary_tools" || printf '[ECR] WARNING: Installer bootstrap cleanup failed.\n' >&2
    fi
    exit "$exit_code"
}
trap cleanup_bootstrap EXIT

bootstrap_error() {
    printf 'ECR bootstrap error: %s\n' "$*" >&2
    exit 1
}

validate_bootstrap_ref() {
    local value=$1
    [[ -n "$value" && "$value" != -* ]] || bootstrap_error "Git ref is invalid."
    [[ "$value" =~ ^[A-Za-z0-9._/-]+$ ]] || \
        bootstrap_error "Git ref contains unsupported characters."
    [[ "$value" != *..* ]] || bootstrap_error "Git ref must not contain '..'."
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bootstrap-ref)
            [[ $# -ge 2 ]] || bootstrap_error "--bootstrap-ref requires a Git ref."
            BOOTSTRAP_REF=$2
            shift 2
            ;;
        --bootstrap-ref=*)
            BOOTSTRAP_REF=${1#*=}
            shift
            ;;
        *)
            bootstrap_error "Usage: $0 [--bootstrap-ref GIT_REF]"
            ;;
    esac
done
[[ -z "$BOOTSTRAP_REF" ]] || validate_bootstrap_ref "$BOOTSTRAP_REF"

download_bootstrap_file() {
    local url=$1
    local destination=$2
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --proto '=https' --tlsv1.2 "$url" -o "$destination"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --https-only -O "$destination" "$url"
    else
        bootstrap_error "Install curl or wget, or run install.sh from a complete repository checkout."
    fi
}

if [[ ! -r "$COMMON_FILE" ]]; then
    [[ -n "$BOOTSTRAP_REF" ]] || bootstrap_error \
        "Standalone installation requires --bootstrap-ref so install.sh and common.sh use the same Git ref."
    command -v cmp >/dev/null 2>&1 || bootstrap_error \
        "The standard cmp utility is required for bootstrap verification."
    temporary_tools=$(mktemp -d /tmp/ecr-installer.XXXXXXXX)
    mkdir "$temporary_tools/lib"
    temporary_common="$temporary_tools/lib/common.sh"
    temporary_installer="$temporary_tools/reference-install.sh"
    bootstrap_base_url="https://raw.githubusercontent.com/Namir-AI/ECR/${BOOTSTRAP_REF}/install"
    download_bootstrap_file "$bootstrap_base_url/install.sh" "$temporary_installer"
    cmp -s -- "$temporary_installer" "${BASH_SOURCE[0]}" || bootstrap_error \
        "The local install.sh does not match the explicitly selected Git ref '$BOOTSTRAP_REF'."
    download_bootstrap_file "$bootstrap_base_url/lib/common.sh" "$temporary_common"
    download_bootstrap_file "$bootstrap_base_url/lib/env_tools.py" "$temporary_tools/lib/env_tools.py"
    download_bootstrap_file "$bootstrap_base_url/lib/runtime_tools.py" "$temporary_tools/lib/runtime_tools.py"
    download_bootstrap_file "${bootstrap_base_url%/install}/app/storage/configuration.py" "$temporary_tools/lib/storage_configuration.py"
    download_bootstrap_file "$bootstrap_base_url/ecr-update.sh" "$temporary_tools/ecr-update.sh"
    SCRIPT_DIR=$temporary_tools
    COMMON_FILE=$temporary_common
fi

# shellcheck source=install/lib/common.sh
source "$COMMON_FILE"

trap 'error_report "$LINENO"' ERR

verify_ubuntu() {
    [[ -r /etc/os-release ]] || die "Unable to identify the operating system."
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ ${ID:-} == "ubuntu" ]] || die "This installer supports Ubuntu only."
    case ${VERSION_ID:-unknown} in
        24.04|26.04)
            log "Detected supported Ubuntu ${VERSION_ID}."
            ;;
        *)
            warn "Ubuntu ${VERSION_ID:-unknown} has not been validated; supported releases are 24.04 and 26.04."
            ;;
    esac
}

install_system_packages() {
    log "Updating apt package indexes."
    apt-get update
    local -a packages=(
        ca-certificates curl wget git openssh-client
        nginx default-mysql-client gzip tar util-linux fonts-dejavu-core
    )
    if [[ "$ECR_DB_TYPE" == "local" ]]; then
        packages+=(mysql-server)
    fi
    if [[ "$ECR_HTTPS_ENABLED" == "true" ]]; then
        packages+=(certbot python3-certbot-nginx openssl)
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
}

install_uv() {
    if command -v uv >/dev/null 2>&1; then
        log "uv is already installed: $(uv --version)"
        return
    fi
    local uv_installer
    uv_installer=$(mktemp /tmp/ecr-uv-installer.XXXXXX)
    curl -fsSL --proto '=https' --tlsv1.2 https://astral.sh/uv/install.sh \
        -o "$uv_installer"
    UV_INSTALL_DIR=/usr/local/bin sh "$uv_installer"
    rm -f -- "$uv_installer"
    require_command uv
    log "Installed $(uv --version)."
}

ensure_service_account() {
    if id "$ECR_SERVICE_USER" >/dev/null 2>&1; then
        [[ $(id -u "$ECR_SERVICE_USER") -ne 0 ]] || die "Service account must not be root."
    else
        useradd --system --create-home --home-dir /var/lib/ecr \
            --shell /usr/sbin/nologin "$ECR_SERVICE_USER"
    fi
}

configure_deploy_key() {
    install -d -o root -g root -m 0700 /etc/ecr
    if [[ "$ECR_REPOSITORY_URL" != git@github.com:* ]]; then
        ECR_DEPLOY_KEY_FILE=""
        return
    fi

    local destination=/etc/ecr/github_deploy_key
    [[ -n "$DEPLOY_KEY_SOURCE" ]] || die "A private repository requires a Deploy Key file."
    [[ -f "$DEPLOY_KEY_SOURCE" ]] || die "Deploy Key file not found: $DEPLOY_KEY_SOURCE"
    if [[ $(readlink -f "$DEPLOY_KEY_SOURCE") != $(readlink -m "$destination") ]]; then
        install -o root -g root -m 0600 "$DEPLOY_KEY_SOURCE" "$destination"
    else
        chown root:root "$destination"
        chmod 0600 "$destination"
    fi
    ECR_DEPLOY_KEY_FILE=$destination

    local known_hosts=/etc/ecr/github_known_hosts
    if [[ ! -s "$known_hosts" ]]; then
        ssh-keyscan -t rsa,ecdsa,ed25519 github.com >"$known_hosts"
        chown root:root "$known_hosts"
        chmod 0644 "$known_hosts"
        printf 'GitHub SSH host-key fingerprints discovered for this server:\n'
        ssh-keygen -lf "$known_hosts"
        confirm "Do these match GitHub's independently published fingerprints?" N || \
            die "GitHub SSH host-key verification was not confirmed."
    fi
}

validate_storage_values() {
    # Prompts precede uv installation. Validate with its protected CPython as
    # soon as available, BEFORE any saved deployment configuration is written.
    # The standalone bootstrap carries the same dependency-free application
    # validator; no duplicated Bash regexes or system Python are needed.
    local helper="$SCRIPT_DIR/../app/storage/configuration.py"
    if [[ -f "$SCRIPT_DIR/lib/storage_configuration.py" ]]; then
        helper="$SCRIPT_DIR/lib/storage_configuration.py"
    fi
    [[ -f "$helper" && ! -L "$helper" ]] || die "Storage configuration validator is unavailable."
    ensure_production_python
    local -a values=()
    mapfile -d '' -t values < <("$ECR_PRODUCTION_PYTHON" "$helper" \
        "$ECR_STORAGE_BACKEND" "$ECR_S3_BUCKET" "$ECR_S3_REGION" "$ECR_S3_PREFIX")
    (( ${#values[@]} == 4 )) || die "Storage configuration was not saved. Correct fresh-install inputs, or choose 'Reconfigure and resume incomplete installation' to correct saved values when no production .env exists."
    ECR_STORAGE_BACKEND=${values[0]}
    ECR_S3_BUCKET=${values[1]}
    ECR_S3_REGION=${values[2]}
    ECR_S3_PREFIX=${values[3]}
}

write_deployment_config() {
    validate_storage_values
    install -d -o root -g root -m 0700 /etc/ecr
    local temporary_config
    temporary_config=$(mktemp /etc/ecr/deployment.conf.XXXXXX)
    {
        printf 'ECR_INSTALL_DIR=%q\n' "$ECR_INSTALL_DIR"
        printf 'ECR_DOMAIN=%q\n' "$ECR_DOMAIN"
        printf 'ECR_GIT_REF=%q\n' "$ECR_GIT_REF"
        printf 'ECR_REPOSITORY_URL=%q\n' "$ECR_REPOSITORY_URL"
        printf 'ECR_DEPLOY_KEY_FILE=%q\n' "$ECR_DEPLOY_KEY_FILE"
        printf 'ECR_HTTPS_ENABLED=%q\n' "$ECR_HTTPS_ENABLED"
        printf 'ECR_SSL_EMAIL=%q\n' "$ECR_SSL_EMAIL"
        printf 'ECR_DB_TYPE=%q\n' "$ECR_DB_TYPE"
        printf 'ECR_DB_HOST=%q\n' "$DB_HOST"
        printf 'ECR_DB_NAME=%q\n' "$DB_NAME"
        printf 'ECR_SERVICE_USER=%q\n' "$ECR_SERVICE_USER"
        printf 'ECR_SERVICE_NAME=%q\n' "$ECR_SERVICE_NAME"
        printf 'ECR_APP_PORT=%q\n' "$ECR_APP_PORT"
        printf 'ECR_STORAGE_BACKEND=%q\n' "$ECR_STORAGE_BACKEND"
        printf 'ECR_S3_BUCKET=%q\n' "$ECR_S3_BUCKET"
        printf 'ECR_S3_REGION=%q\n' "$ECR_S3_REGION"
        printf 'ECR_S3_PREFIX=%q\n' "$ECR_S3_PREFIX"
    } >"$temporary_config"
    chmod 0600 "$temporary_config"
    chown root:root "$temporary_config"
    mv -f "$temporary_config" "$ECR_CONFIG_FILE"
}

configure_local_mysql() {
    systemctl enable --now mysql
    local bind_config=/etc/mysql/mysql.conf.d/99-ecr-local-bind.cnf
    if [[ ! -e "$bind_config" ]]; then
        {
            printf '[mysqld]\n'
            printf 'bind-address = 127.0.0.1\n'
            printf 'mysqlx-bind-address = 127.0.0.1\n'
        } >"$bind_config"
        chmod 0644 "$bind_config"
        systemctl restart mysql
    fi

    if [[ "$INSTALL_MODE" == "repair" ]]; then
        return
    fi

    local user_exists password_hex
    user_exists=$(mysql --protocol=socket --batch --skip-column-names \
        -e "SELECT COUNT(*) FROM mysql.user WHERE User='${DB_USER}' AND Host='localhost';")
    mysql --protocol=socket <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\`
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
SQL

    if [[ "$user_exists" == "0" ]]; then
        password_hex=$(printf '%s' "$DB_PASSWORD" | od -An -v -tx1 | tr -d ' \n')
        mysql --protocol=socket <<SQL
SET @ecr_password = CONVERT(0x${password_hex} USING utf8mb4);
SET @ecr_create_user = CONCAT(
    'CREATE USER ''${DB_USER}''@''localhost'' IDENTIFIED BY ',
    QUOTE(@ecr_password)
);
PREPARE ecr_statement FROM @ecr_create_user;
EXECUTE ecr_statement;
DEALLOCATE PREPARE ecr_statement;
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES
    ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
SQL
    elif [[ "$CHANGE_LOCAL_DB_PASSWORD" == "true" ]]; then
        password_hex=$(printf '%s' "$DB_PASSWORD" | od -An -v -tx1 | tr -d ' \n')
        mysql --protocol=socket <<SQL
SET @ecr_password = CONVERT(0x${password_hex} USING utf8mb4);
SET @ecr_alter_user = CONCAT(
    'ALTER USER ''${DB_USER}''@''localhost'' IDENTIFIED BY ',
    QUOTE(@ecr_password)
);
PREPARE ecr_statement FROM @ecr_alter_user;
EXECUTE ecr_statement;
DEALLOCATE PREPARE ecr_statement;
SQL
    fi

    mysql --protocol=socket <<SQL
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES,
    TRIGGER, SHOW VIEW ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
SQL
}

write_production_env() {
    local release_dir=$1
    local env_file="$ECR_INSTALL_DIR/shared/.env"
    local storage_root
    umask 0077
    if [[ -e "$env_file" ]]; then
        if [[ "$INSTALL_MODE" == "fresh" ]]; then
            die "A production .env already exists; refusing to overwrite it."
        fi
        local env_backup="$ECR_INSTALL_DIR/state/.env.before-reconfigure.$(date -u +%Y%m%dT%H%M%SZ)"
        cp -a "$env_file" "$env_backup"
        chown root:root "$env_backup"
        chmod 0600 "$env_backup"
    fi
    storage_root=$("$release_dir/.venv/bin/python" "$release_dir/install/lib/env_tools.py" \
        backup-storage "$env_file" "$ECR_INSTALL_DIR")

    {
        printf '%s\0%s\0' APP_ENV production
        printf '%s\0%s\0' APP_DEBUG false
        printf '%s\0%s\0' APP_HOST 127.0.0.1
        printf '%s\0%s\0' APP_PORT "$ECR_APP_PORT"
        printf '%s\0%s\0' STORAGE_ROOT "$storage_root"
        printf '%s\0%s\0' STORAGE_BACKEND "$ECR_STORAGE_BACKEND"
        printf '%s\0%s\0' S3_BUCKET "$ECR_S3_BUCKET"
        printf '%s\0%s\0' S3_REGION "$ECR_S3_REGION"
        printf '%s\0%s\0' S3_PREFIX "$ECR_S3_PREFIX"
        printf '%s\0%s\0' SESSION_SECURE_COOKIE "$ECR_HTTPS_ENABLED"
        printf '%s\0%s\0' DB_HOST "$DB_HOST"
        printf '%s\0%s\0' DB_PORT "$DB_PORT"
        printf '%s\0%s\0' DB_NAME "$DB_NAME"
        printf '%s\0%s\0' DB_USER "$DB_USER"
        printf '%s\0%s\0' DB_PASSWORD "$DB_PASSWORD"
        printf '%s\0%s\0' DB_CHARSET utf8mb4
    } | "$release_dir/.venv/bin/python" \
        "$release_dir/install/lib/env_tools.py" write "$env_file" "$release_dir/.env.example"
    chown root:"$ECR_SERVICE_USER" "$env_file"
    chmod 0640 "$env_file"
}

render_systemd_service() {
    local release_dir=$1
    local helper="$release_dir/install/lib/env_tools.py"
    local candidate
    candidate=$(mktemp --suffix=.service /tmp/ecr.XXXXXX)
    {
        printf '%s\0%s\0' SERVICE_USER "$ECR_SERVICE_USER"
        printf '%s\0%s\0' INSTALL_DIR "$ECR_INSTALL_DIR"
        printf '%s\0%s\0' APP_PORT "$ECR_APP_PORT"
    } | "$release_dir/.venv/bin/python" "$helper" render \
        "$release_dir/install/templates/ecr.service.in" "$candidate"

    systemd-analyze verify "$candidate"
    local target="/etc/systemd/system/${ECR_SERVICE_NAME}.service"
    [[ ! -L "$target" ]] || die "Refusing to replace a symlinked systemd unit: $target"
    if [[ -e "$target" ]] && ! cmp -s "$candidate" "$target"; then
        cp -a "$target" "${target}.before-ecr-$(date -u +%Y%m%dT%H%M%SZ)"
    fi
    install -o root -g root -m 0644 "$candidate" "$target"
    rm -f -- "$candidate"
    systemctl daemon-reload
    systemctl enable "$ECR_SERVICE_NAME.service"
}

render_nginx_site() {
    local release_dir=$1
    local helper="$release_dir/install/lib/env_tools.py"
    local candidate target enabled backup=""
    candidate=$(mktemp /tmp/ecr-nginx.XXXXXX)
    {
        printf '%s\0%s\0' DOMAIN "$ECR_DOMAIN"
        printf '%s\0%s\0' APP_PORT "$ECR_APP_PORT"
    } | "$release_dir/.venv/bin/python" "$helper" render \
        "$release_dir/install/templates/nginx-http.conf.in" "$candidate"

    target=/etc/nginx/sites-available/ecr.conf
    enabled=/etc/nginx/sites-enabled/ecr.conf
    [[ ! -L "$target" ]] || die "Refusing to replace an unexpected Nginx configuration symlink."
    if [[ -e "$enabled" && ! -L "$enabled" ]]; then
        die "Refusing to replace a non-symlink Nginx enabled-site file: $enabled"
    fi
    if [[ -e "$target" && ! -L "$target" ]] && ! cmp -s "$candidate" "$target"; then
        if [[ "$INSTALL_MODE" == "repair" ]]; then
            if ! confirm "Managed Nginx configuration differs. Back it up and repair it?" N; then
                rm -f -- "$candidate"
                nginx -t
                return
            fi
        elif [[ "$INSTALL_MODE" == "fresh" ]]; then
            confirm "An existing ECR Nginx configuration was found. Back it up and replace it?" N || \
                die "Nginx configuration replacement declined."
        fi
        backup="${target}.before-ecr-$(date -u +%Y%m%dT%H%M%SZ)"
        cp -a "$target" "$backup"
    fi

    install -o root -g root -m 0644 "$candidate" "$target"
    rm -f -- "$candidate"
    ln -sfn "$target" "$enabled"
    if ! nginx -t; then
        if [[ -n "$backup" ]]; then
            cp -a "$backup" "$target"
        else
            rm -f -- "$enabled" "$target"
        fi
        nginx -t || true
        die "Nginx configuration validation failed; the prior configuration was restored."
    fi
    systemctl enable --now nginx
    systemctl reload nginx
}

configure_https() {
    [[ "$ECR_HTTPS_ENABLED" == "true" ]] || return 0
    local nginx_site=/etc/nginx/sites-available/ecr.conf
    local nginx_backup
    nginx_backup=$(mktemp /tmp/ecr-nginx-before-certbot.XXXXXX)
    cp -a "$nginx_site" "$nginx_backup"

    local certificate_file="/etc/letsencrypt/live/${ECR_DOMAIN}/fullchain.pem"
    if [[ -r "$certificate_file" ]] && \
            openssl x509 -checkend 86400 -noout -in "$certificate_file" >/dev/null 2>&1 && \
            grep -Fq "$certificate_file" "$nginx_site" && nginx -t; then
        log "Preserving the existing valid certificate for $ECR_DOMAIN."
    else
        log "Configuring or obtaining the Let's Encrypt certificate for $ECR_DOMAIN."
        if ! certbot --nginx --non-interactive --agree-tos \
            --email "$ECR_SSL_EMAIL" --redirect --keep-until-expiring \
            --cert-name "$ECR_DOMAIN" -d "$ECR_DOMAIN"; then
            cp -a "$nginx_backup" "$nginx_site"
            nginx -t && systemctl reload nginx
            rm -f -- "$nginx_backup"
            warn "Certificate issuance/configuration failed. The working HTTP configuration was restored."
            warn "Check DNS, ports 80/443, Cloudflare proxy rules, and the Certbot log."
            HTTPS_SETUP_FAILED=true
            return 0
        fi
    fi
    rm -f -- "$nginx_backup"

    install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy
    local renewal_hook=/etc/letsencrypt/renewal-hooks/deploy/ecr-reload-nginx
    {
        printf '#!/usr/bin/env bash\n'
        printf 'set -Eeuo pipefail\n'
        printf 'systemctl reload nginx\n'
    } >"$renewal_hook"
    chmod 0755 "$renewal_hook"
    systemctl enable --now certbot.timer

    local renewal_marker="$ECR_INSTALL_DIR/state/certbot-renewal-tested"
    if [[ ! -e "$renewal_marker" ]]; then
        if certbot renew --cert-name "$ECR_DOMAIN" --dry-run \
                --deploy-hook "systemctl reload nginx"; then
            touch "$renewal_marker"
            chmod 0640 "$renewal_marker"
            chown root:"$ECR_SERVICE_USER" "$renewal_marker"
        else
            warn "The certificate is installed, but the renewal dry-run failed. Review it before production use."
        fi
    fi
    nginx -t
    systemctl reload nginx
}

prompt_deployment_values() {
    local old_install_dir=${ECR_INSTALL_DIR:-$ECR_DEFAULT_INSTALL_DIR}
    ECR_DOMAIN=$(prompt_value "ECR Server Domain" "${ECR_DOMAIN:-}")
    validate_domain "$ECR_DOMAIN"
    if [[ -n "$BOOTSTRAP_REF" ]]; then
        ECR_GIT_REF=$BOOTSTRAP_REF
        printf 'Deployment Git ref: %s (fixed by --bootstrap-ref)\n' "$ECR_GIT_REF"
    else
        ECR_GIT_REF=$(prompt_value "Deployment Git ref" "${ECR_GIT_REF:-main}")
    fi
    validate_git_ref "$ECR_GIT_REF"
    ECR_INSTALL_DIR=$(prompt_value "Installation directory" "$old_install_dir")
    validate_install_dir "$ECR_INSTALL_DIR"
    if [[ "$INSTALL_MODE" == "reconfigure" || \
            "$INSTALL_MODE" == "recover-reconfigure" ]] && \
            [[ "$ECR_INSTALL_DIR" != "$old_install_dir" ]]; then
        die "Changing the installation directory in place is unsafe. Use a separate fresh installation."
    fi

    local access_default=1
    [[ ${ECR_REPOSITORY_URL:-} == git@github.com:* ]] && access_default=2
    local access_choice
    access_choice=$(prompt_value "GitHub access: 1) Public HTTPS  2) Private read-only Deploy Key" "$access_default")
    case "$access_choice" in
        1)
            ECR_REPOSITORY_URL=$(prompt_value "Repository URL" \
                "${ECR_REPOSITORY_URL:-$ECR_DEFAULT_REPOSITORY_URL}")
            DEPLOY_KEY_SOURCE=""
            ;;
        2)
            ECR_REPOSITORY_URL=$(prompt_value "Private repository SSH URL" \
                "git@github.com:Namir-AI/ECR.git")
            DEPLOY_KEY_SOURCE=$(prompt_value "Read-only GitHub Deploy Key file" \
                "${ECR_DEPLOY_KEY_FILE:-}")
            ;;
        *) die "Select repository access option 1 or 2." ;;
    esac
    validate_repository_url "$ECR_REPOSITORY_URL"

    local db_default=1
    [[ ${ECR_DB_TYPE:-local} == "external" ]] && db_default=2
    local db_choice
    db_choice=$(prompt_value "Database: 1) Local MySQL  2) External MySQL / AWS RDS" "$db_default")
    CHANGE_LOCAL_DB_PASSWORD=false
    case "$db_choice" in
        1)
            ECR_DB_TYPE=local
            DB_HOST=127.0.0.1
            DB_PORT=3306
            DB_NAME=$(prompt_value "Database Name" "${DB_NAME:-ecr}")
            DB_USER=$(prompt_value "Database User" "${DB_USER:-ecr_app}")
            ;;
        2)
            ECR_DB_TYPE=external
            DB_HOST=$(prompt_value "Database Host" "${DB_HOST:-}")
            [[ "$DB_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || die "Database Host is invalid."
            DB_PORT=$(prompt_value "Database Port" "${DB_PORT:-3306}")
            validate_port "$DB_PORT"
            DB_NAME=$(prompt_value "Database Name" "${DB_NAME:-}")
            DB_USER=$(prompt_value "Database User" "${DB_USER:-}")
            ;;
        *) die "Select database option 1 or 2." ;;
    esac
    validate_db_identifier "Database Name" "$DB_NAME"
    validate_db_identifier "Database User" "$DB_USER"

    local entered_password confirmation
    if [[ "$INSTALL_MODE" == "reconfigure" ]]; then
        entered_password=$(prompt_secret "Database Password (leave blank to retain current password)")
        if [[ -z "$entered_password" ]]; then
            DB_PASSWORD=$EXISTING_DB_PASSWORD
        else
            confirmation=$(prompt_secret "Confirm Database Password")
            [[ "$entered_password" == "$confirmation" ]] || die "Database passwords do not match."
            DB_PASSWORD=$entered_password
            [[ "$ECR_DB_TYPE" == "local" ]] && CHANGE_LOCAL_DB_PASSWORD=true
        fi
    else
        DB_PASSWORD=$(prompt_secret "Database Password")
        confirmation=$(prompt_secret "Confirm Database Password")
        [[ "$DB_PASSWORD" == "$confirmation" ]] || die "Database passwords do not match."
        if [[ "$INSTALL_MODE" == "recover-reconfigure" && \
                "$ECR_DB_TYPE" == "local" ]]; then
            CHANGE_LOCAL_DB_PASSWORD=true
        fi
    fi
    validate_secret_value "Database Password" "$DB_PASSWORD"

    prompt_storage_values

    ECR_SSL_EMAIL=$(prompt_value "Let's Encrypt / SSL email address" "${ECR_SSL_EMAIL:-}")
    ECR_HTTPS_ENABLED=false
    if confirm "Enable HTTPS?" Y; then
        ECR_HTTPS_ENABLED=true
        [[ "$ECR_SSL_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || \
            die "A valid email address is required for Let's Encrypt."
    else
        warn "HTTP-only mode sets SESSION_SECURE_COOKIE=false and is not suitable for production credentials."
    fi
    CREATE_INITIAL_ADMIN=false
    if confirm "Create initial ECR Admin after installation?" Y; then
        CREATE_INITIAL_ADMIN=true
    fi
}

prompt_storage_values() {
    if [[ -f "$ECR_INSTALL_DIR/shared/.env" ]]; then
        # Protected production env, not possibly stale deployment.conf, wins.
        if valid_current_release_path >/dev/null; then
            read_storage_environment
        fi
        log "Preserving the existing storage backend and namespace; backend switching is not supported by reconfiguration."
        return
    fi
    if [[ "$INSTALL_MODE" != fresh && "$INSTALL_MODE" != recover-reconfigure ]]; then
        log "Preserving saved storage configuration during recovery."
        return
    fi
    local choice
    choice=$(prompt_value "File/object storage backend: 1) Local protected storage  2) AWS S3" "1")
    case "$choice" in
        1) ECR_STORAGE_BACKEND=local; ECR_S3_BUCKET=""; ECR_S3_REGION=""; ECR_S3_PREFIX="" ;;
        2)
            ECR_STORAGE_BACKEND=s3
            ECR_S3_BUCKET=$(prompt_value "S3 bucket name" "")
            ECR_S3_REGION=$(prompt_value "AWS region" "")
            ECR_S3_PREFIX=$(prompt_value "ECR S3 prefix" "")
            [[ -n "$ECR_S3_BUCKET" && -n "$ECR_S3_REGION" && -n "$ECR_S3_PREFIX" ]] || die "S3 bucket, region and prefix are required."
            log "AWS SDK credential chain / EC2 IAM role is required. No AWS keys are requested or stored."
            ;;
        *) die "Select storage option 1 or 2." ;;
    esac
}

prompt_recovery_database_values() {
    DB_HOST=${ECR_DB_HOST:-127.0.0.1}
    DB_NAME=${ECR_DB_NAME:-ecr}
    case "$ECR_DB_TYPE" in
        local)
            DB_HOST=127.0.0.1
            DB_PORT=3306
            DB_USER=$(prompt_value "Database User" "ecr_app")
            ;;
        external)
            [[ "$DB_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || die "Saved Database Host is invalid."
            DB_PORT=$(prompt_value "Database Port" "3306")
            validate_port "$DB_PORT"
            DB_USER=$(prompt_value "Database User" "")
            ;;
        *) die "Saved database type is invalid: $ECR_DB_TYPE" ;;
    esac
    validate_db_identifier "Database Name" "$DB_NAME"
    validate_db_identifier "Database User" "$DB_USER"

    local confirmation
    DB_PASSWORD=$(prompt_secret "Database Password")
    confirmation=$(prompt_secret "Confirm Database Password")
    [[ "$DB_PASSWORD" == "$confirmation" ]] || die "Database passwords do not match."
    validate_secret_value "Database Password" "$DB_PASSWORD"
}

print_summary() {
    printf '\nDeployment summary (passwords omitted)\n'
    printf '%-20s %s\n' 'Domain:' "$ECR_DOMAIN"
    printf '%-20s %s\n' 'Git ref/version:' "$ECR_GIT_REF"
    printf '%-20s %s\n' 'Installation path:' "$ECR_INSTALL_DIR"
    printf '%-20s %s\n' 'Database type:' "$ECR_DB_TYPE"
    printf '%-20s %s\n' 'Database host:' "$DB_HOST"
    printf '%-20s %s\n' 'Database name:' "$DB_NAME"
    printf '%-20s %s\n' 'HTTPS:' "$ECR_HTTPS_ENABLED"
    printf '%-20s %s\n' 'Repository:' "$ECR_REPOSITORY_URL"
    printf '%-20s %s\n' 'Object storage:' "$ECR_STORAGE_BACKEND"
    printf '\n'
}

require_root
acquire_deployment_lock
verify_ubuntu

INSTALL_MODE=fresh
ECR_SERVICE_USER=$ECR_DEFAULT_SERVICE_USER
ECR_SERVICE_NAME=$ECR_DEFAULT_SERVICE_NAME
ECR_APP_PORT=$ECR_DEFAULT_APP_PORT
ECR_INSTALL_DIR=$ECR_DEFAULT_INSTALL_DIR
ECR_GIT_REF=${BOOTSTRAP_REF:-main}
ECR_REPOSITORY_URL=$ECR_DEFAULT_REPOSITORY_URL
ECR_DEPLOY_KEY_FILE=""
DEPLOY_KEY_SOURCE=""
ECR_SSL_EMAIL=""
ECR_DB_TYPE=local
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=ecr
DB_USER=ecr_app
DB_PASSWORD=""
CREATE_INITIAL_ADMIN=false
CHANGE_LOCAL_DB_PASSWORD=false
RECOVER_USE_EXISTING_ENV=false
ECR_STORAGE_BACKEND=local
ECR_S3_BUCKET=""
ECR_S3_REGION=""
ECR_S3_PREFIX=""

if [[ -r "$ECR_CONFIG_FILE" ]]; then
    load_deployment_config
    validate_install_dir "$ECR_INSTALL_DIR"
    if current_release=$(valid_current_release_path); then
        printf 'Completed ECR installation detected.\n\n'
        printf '1) Verify installation\n'
        printf '2) Repair installation\n'
        printf '3) Reconfigure deployment\n'
        printf '4) Exit\n'
        read -r -p "Select an option [1-4]: " existing_choice
        case "$existing_choice" in
            1)
                status_script="$current_release/install/status.sh"
                [[ -x "$status_script" ]] || die "Installed status script is unavailable. Choose repair."
                exec "$status_script"
                ;;
            2)
                INSTALL_MODE=repair
                DB_HOST=${ECR_DB_HOST:-127.0.0.1}
                DB_NAME=${ECR_DB_NAME:-ecr}
                DB_PORT=3306
                read_database_environment
                read_storage_environment
                DB_PORT=$ECR_DB_PORT_VALUE
                DB_USER=$ECR_DB_USER_VALUE
                DEPLOY_KEY_SOURCE=${ECR_DEPLOY_KEY_FILE:-}
                CREATE_INITIAL_ADMIN=false
                confirm "Run the existing initial-Admin command after repair?" N && CREATE_INITIAL_ADMIN=true
                print_summary
                confirm "Proceed with installation repair?" Y || die "Repair cancelled."
                ;;
            3)
                INSTALL_MODE=reconfigure
                old_install_dir=$ECR_INSTALL_DIR
                read_database_environment
                DB_HOST=$ECR_DB_HOST_VALUE
                DB_PORT=$ECR_DB_PORT_VALUE
                DB_NAME=$ECR_DB_NAME_VALUE
                DB_USER=$ECR_DB_USER_VALUE
                EXISTING_DB_PASSWORD=$ECR_DB_PASSWORD_VALUE
                ECR_ALLOW_REPOSITORY_RECONFIGURE=true
                prompt_deployment_values
                [[ "$ECR_INSTALL_DIR" == "$old_install_dir" ]] || \
                    die "Installation directory cannot be changed during reconfiguration."
                print_summary
                confirm "Proceed with deployment reconfiguration?" Y || die "Reconfiguration cancelled."
                ;;
            4) exit 0 ;;
            *) die "Invalid selection." ;;
        esac
    else
        printf 'Incomplete ECR installation detected.\n'
        printf 'No valid active release exists; saved data will not be deleted.\n\n'
        printf '1) Resume/recover using saved deployment configuration\n'
        printf '2) Reconfigure and resume incomplete installation\n'
        printf '3) Exit\n'
        read -r -p "Select an option [1-3]: " incomplete_choice
        case "$incomplete_choice" in
            1)
                INSTALL_MODE=recover
                DB_HOST=${ECR_DB_HOST:-127.0.0.1}
                DB_NAME=${ECR_DB_NAME:-ecr}
                DB_PORT=3306
                DEPLOY_KEY_SOURCE=${ECR_DEPLOY_KEY_FILE:-}
                if [[ "$ECR_REPOSITORY_URL" == git@github.com:* && \
                        ! -r "$DEPLOY_KEY_SOURCE" ]]; then
                    DEPLOY_KEY_SOURCE=$(prompt_value \
                        "Read-only GitHub Deploy Key file" "$DEPLOY_KEY_SOURCE")
                fi
                if [[ -f "$ECR_INSTALL_DIR/shared/.env" ]]; then
                    RECOVER_USE_EXISTING_ENV=true
                    log "The existing protected production .env will be preserved."
                else
                    warn "No production .env exists; database credentials must be entered to resume."
                    prompt_recovery_database_values
                fi
                CREATE_INITIAL_ADMIN=false
                confirm "Create initial ECR Admin after recovery?" N && CREATE_INITIAL_ADMIN=true
                print_summary
                confirm "Resume the incomplete installation?" Y || die "Recovery cancelled."
                ;;
            2)
                INSTALL_MODE=recover-reconfigure
                old_install_dir=$ECR_INSTALL_DIR
                DB_HOST=${ECR_DB_HOST:-127.0.0.1}
                DB_NAME=${ECR_DB_NAME:-ecr}
                DB_PORT=3306
                DB_USER=ecr_app
                ECR_ALLOW_REPOSITORY_RECONFIGURE=true
                prompt_deployment_values
                [[ "$ECR_INSTALL_DIR" == "$old_install_dir" ]] || \
                    die "Installation directory cannot be changed during recovery."
                print_summary
                confirm "Reconfigure and resume the incomplete installation?" Y || \
                    die "Recovery cancelled."
                ;;
            3) exit 0 ;;
            *) die "Invalid selection." ;;
        esac
    fi
else
    prompt_deployment_values
    print_summary
    confirm "Proceed with installation?" Y || die "Installation cancelled."
fi

install_system_packages
install_uv
ensure_service_account
ensure_install_layout
configure_deploy_key
write_deployment_config

previous_commit=""
previous_ref=""
if [[ "$INSTALL_MODE" == "repair" ]]; then
    ensure_repository
    release_dir=$(valid_current_release_path)
    sync_release_dependencies "$release_dir"
else
    if [[ "$INSTALL_MODE" == "reconfigure" ]]; then
        previous_release=$(current_release_path)
        previous_commit=$(git_as_deployer -C "$previous_release" rev-parse HEAD) || \
            die "Cannot verify the pre-reconfiguration application commit."
        load_deployment_state || true
        previous_ref=${ECR_CURRENT_REF:-unknown}
        log "Creating pre-reconfiguration database backup."
        "$previous_release/install/backup.sh" >/dev/null
    fi
    ensure_repository
    fetch_repository
    deploy_commit=$(resolve_git_ref "$ECR_GIT_REF") || \
        die "Requested Git ref was not found: $ECR_GIT_REF"
    release_dir=$(prepare_release "$deploy_commit")
    new_install_release=$release_dir
    if [[ -f "$ECR_INSTALL_DIR/shared/.env" ]]; then
        read_storage_environment "$release_dir"
    fi
    if [[ "$INSTALL_MODE" == "recover" && \
            "$RECOVER_USE_EXISTING_ENV" == "true" ]]; then
        link_release_env "$release_dir"
        read_database_environment_from_release "$release_dir"
        DB_HOST=$ECR_DB_HOST_VALUE
        DB_PORT=$ECR_DB_PORT_VALUE
        DB_NAME=$ECR_DB_NAME_VALUE
        DB_USER=$ECR_DB_USER_VALUE
        DB_PASSWORD=$ECR_DB_PASSWORD_VALUE
        write_deployment_config
    else
        write_production_env "$release_dir"
        link_release_env "$release_dir"
    fi
fi

read_storage_environment "$release_dir"
write_deployment_config
upgrade_production_env "$release_dir"

if [[ "$ECR_DB_TYPE" == "local" ]]; then
    configure_local_mysql
fi

run_db_check "$release_dir"
run_application_import_check "$release_dir"
run_storage_check "$release_dir"
installation_migration_started=true
run_migrations "$release_dir"

if [[ "$INSTALL_MODE" != "repair" ]]; then
    activate_release "$release_dir"
fi

render_systemd_service "$release_dir"
render_nginx_site "$release_dir"
systemctl restart "$ECR_SERVICE_NAME.service"
wait_for_local_health 30 || die "ECR service did not pass its local health check."

ssl_failed=false
HTTPS_SETUP_FAILED=false
configure_https
ssl_failed=$HTTPS_SETUP_FAILED

if [[ "$CREATE_INITIAL_ADMIN" == "true" ]]; then
    (cd "$release_dir" && run_as_service \
        "$release_dir/.venv/bin/python" -m app.users.create_admin)
fi

if [[ "$ECR_HTTPS_ENABLED" == "true" && "$ssl_failed" == "false" ]]; then
    https_health_check || die "HTTPS is configured, but the HTTPS health check failed."
    final_url="https://${ECR_DOMAIN}"
else
    curl -fsS --max-time 15 -H "Host: $ECR_DOMAIN" \
        "http://127.0.0.1/health" >/dev/null || die "Nginx HTTP health check failed."
    final_url="http://${ECR_DOMAIN}"
fi

if [[ "$ssl_failed" == "true" ]]; then
    warn "Deployment is running over HTTP, but HTTPS setup failed and requires administrator action."
    exit 1
fi
if [[ "$INSTALL_MODE" != "repair" ]]; then
    write_deployment_state "$deploy_commit" "$ECR_GIT_REF" "$previous_commit" "$previous_ref"
    append_deployment_history "$INSTALL_MODE" "$ECR_GIT_REF" "$deploy_commit" "$release_dir"
fi
install_update_launcher "$release_dir"
deployed_commit=$(git_as_deployer -C "$release_dir" rev-parse HEAD)
printf '\nECR deployment completed.\n'
printf 'URL              : %s\n' "$final_url"
printf 'Git ref          : %s\n' "$ECR_GIT_REF"
printf 'Installed commit : %s\n' "$deployed_commit"
printf 'Install directory: %s\n' "$ECR_INSTALL_DIR"
