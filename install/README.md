# ECR deployment automation

These scripts install and operate the existing Paharpur ECR FastAPI application
on an already-created Ubuntu server. They do not create or modify AWS,
Cloudflare, DNS, IAM, Route53, RDS, Elastic IP, or security-group resources.

## Production layout

The default installation uses:

```text
/opt/ecr/
├── current -> releases/<timestamp>-<commit>/
├── releases/                 immutable application releases and virtualenvs
├── repository.git/           deployment-only Git mirror
├── shared/
│   ├── .env                  protected production configuration
│   ├── data/                 persistent/upload-ready data location
│   ├── logs/                 persistent application-managed logs if introduced
│   ├── uv-cache/             shared uv package/download cache
│   └── uv-python/            uv-managed CPython 3.12 runtime
├── backups/                  protected SQL and persistent-data backups
└── state/                    deployment history and rollback metadata
```

Root owns deployment code, metadata, the Git mirror, and `.env`. The dedicated
`ecr` runtime account can read the current release and configuration, but can
write only under the designated shared runtime directories. The application is
served by Uvicorn on `127.0.0.1:8000`; Nginx is the only public HTTP server.

## A. AWS EC2 prerequisites

- An existing EC2 instance or company Ubuntu server with `sudo` access.
- Ubuntu 26.04 LTS (amd64) and Ubuntu 24.04 LTS are supported. The installer
  does not depend on either release providing Python 3.12 through apt. It uses
  uv to install and manage CPython 3.12 in the protected ECR shared directory,
  then creates every release virtual environment from that interpreter.
- At least the capacity appropriate for Python, Nginx, and MySQL when the local
  database option is selected.
- Working outbound HTTPS/DNS access for Ubuntu packages, the uv-managed Python
  download, Python dependencies, GitHub, and Let's Encrypt.
- A domain/subdomain already controlled by the administrator.

An Elastic IP is strongly recommended so the public address does not change
when an EC2 instance is stopped and started.

## B. Network and security groups

Configure these rules manually in AWS/company infrastructure:

| Port | Purpose | Recommended source |
|---|---|---|
| 22 | SSH administration | Administrator/VPN IPs only |
| 80 | HTTP and ACME challenge | Internet |
| 443 | HTTPS application | Internet/company ranges as required |

Do not expose port 8000. Do not expose local MySQL port 3306. For RDS, allow
3306 only from the ECR server's security group or approved private network.

## C. Cloudflare DNS and TLS

Create an `A` record such as:

```text
service.example.com -> EC2 Elastic IP
```

Cloudflare-proxied records resolve to Cloudflare addresses, so the installer
does not attempt to compare public DNS with the server IP. HTTP-01 certificate
issuance must still reach ports 80/443 on the origin. If Cloudflare rules block
the challenge, temporarily use DNS-only mode, issue the certificate, then
restore the proxy.

After a valid Let's Encrypt origin certificate is installed, Cloudflare SSL/TLS
mode should normally be **Full (strict)**. Never use Flexible mode for ECR.

## D. First installation

For the public repository development branch, explicitly select `main` for
both downloaded deployment files:

```bash
mkdir -p ~/ecr-install
cd ~/ecr-install
ECR_BOOTSTRAP_REF=main
wget -O install.sh "https://raw.githubusercontent.com/Namir-AI/ECR/${ECR_BOOTSTRAP_REF}/install/install.sh"
chmod +x install.sh
sudo ./install.sh --bootstrap-ref "$ECR_BOOTSTRAP_REF"
```

For a tagged release, use the tag in both the download and installer argument:

```bash
mkdir -p ~/ecr-install-v1.2.0
cd ~/ecr-install-v1.2.0
ECR_BOOTSTRAP_REF=v1.2.0
wget -O install.sh "https://raw.githubusercontent.com/Namir-AI/ECR/${ECR_BOOTSTRAP_REF}/install/install.sh"
chmod +x install.sh
sudo ./install.sh --bootstrap-ref "$ECR_BOOTSTRAP_REF"
```

When run as a standalone file, `install.sh` requires the explicit bootstrap
ref. It downloads both a reference copy of `install.sh` and `common.sh` from
that same ref, verifies the running installer matches the reference copy, and
only then loads the helper. It will not silently fall back to `main`. The
selected bootstrap ref becomes the fixed deployment ref shown by the installer
and cannot be changed by an interactive answer. This guarantees the installer,
shared helper, and deployed application all use the same ref. The ref may be
`main`, a release tag, or a commit SHA.

A complete repository checkout always uses its local `install/lib/common.sh`;
it does not download a replacement helper. This is the required approach for a
private repository and is also valid for public tagged releases. When a
complete checkout is launched without `--bootstrap-ref`, the interactive
deployment-ref prompt remains available.

On repeated execution, a completed installation offers:

1. verification;
2. repair;
3. explicit reconfiguration; or
4. exit.

Repair preserves `.env`, records, backups, persistent data, certificates, and
the current Git release. Reconfiguration backs up the current database and
the prior `.env` before applying explicitly entered settings.

If `/etc/ecr/deployment.conf` exists but no valid active release exists, the
installer identifies an incomplete installation instead. It offers a resume
using the saved configuration or an explicit reconfiguration-and-resume. A
normal resume reuses an existing protected `.env`; if `.env` was not reached
during the failed attempt, credentials are requested again using hidden input.
The recovery creates a new isolated release and safely reconciles missing
components without deleting or recreating the database, certificates,
backups, uploaded data, or other persistent files. Explicit recovery
reconfiguration backs up an existing `.env` before replacing its settings.

## E. Local MySQL

Select **Local MySQL** during installation. The installer installs the Ubuntu
MySQL 8-compatible server, binds it to loopback, creates the database only when
absent, creates a localhost-only least-privilege account only when absent, and
uses `utf8mb4`. Existing databases are never dropped or recreated.

If an existing MySQL account is selected during fresh installation, its
password is not silently changed. Reconfiguration can change it only when the
administrator explicitly enters a new password.

## F. External MySQL / AWS RDS

Create the database and least-privilege application account through company IT
or AWS before running the installer. Select **External MySQL / AWS RDS** and
enter its host, port, database, user, and password. The scripts do not create or
alter RDS infrastructure. They run `python -m app.db.check` before Alembic and
stop clearly if routing, security groups, TLS/network policy, credentials, or
database permissions prevent access.

## G. Private GitHub repository and Deploy Key

Use a read-only GitHub Deploy Key; no Personal Access Token is required.

1. On the server, create a dedicated key without reusing a personal key:

   ```bash
   sudo ssh-keygen -t ed25519 -f /root/ecr_github_deploy_key -C ecr-deploy
   ```

2. Add `/root/ecr_github_deploy_key.pub` in GitHub under repository
   **Settings → Deploy keys**. Do **not** enable write access.
3. Clone the complete requested branch or tag with the Deploy Key. For example,
   for tag `v1.2.0`:

   ```bash
   GIT_SSH_COMMAND="ssh -i /root/ecr_github_deploy_key -o IdentitiesOnly=yes" \
       git clone --branch v1.2.0 --single-branch git@github.com:Namir-AI/ECR.git
   sudo ./ECR/install/install.sh --bootstrap-ref v1.2.0
   ```

   For `main` or a commit SHA, replace both occurrences of `v1.2.0` with the
   same desired ref. Supplying `--bootstrap-ref` fixes the deployed ref to that
   value. Omit the option only when intentionally using the complete checkout's
   interactive Git-ref selection.
4. In the installer, select **Private read-only Deploy Key**, enter
   `git@github.com:Namir-AI/ECR.git`, and provide the private-key path.

The installer copies the private key to `/etc/ecr/github_deploy_key` with root
read-only permissions. It is never committed, printed, embedded in a URL, or
written to `.env`. Future updates use the same key and require only read access.
Administrators should independently verify GitHub's published SSH host-key
fingerprints when first configuring the server.

## H. Initial Admin and Admin recovery

If requested, installation invokes the application's existing command after
database checks and migrations:

```bash
python -m app.users.create_admin
```

The deployment scripts do not duplicate authentication logic. Existing Admin
self-recovery remains available from the active release:

```bash
cd /opt/ecr/current
sudo -u ecr .venv/bin/python -m app.users.reset_admin_password
```

## I. Updating

Run from the active checkout, with a branch, tag, or commit:

```bash
cd /opt/ecr/current
sudo ./install/update.sh main
```

For a release tag:

```bash
sudo ./install/update.sh v1.2.0
```

The updater validates and fetches the ref, creates a database backup, prepares
a separate frozen `uv.lock` release and virtual environment using the same
uv-managed CPython 3.12 runtime as installation, checks database connectivity,
runs Alembic, atomically switches `current`, restarts systemd, and verifies
local and HTTPS health. It never uses `git reset --hard` and never changes
`.env` or persistent data.

Before migrations, failures leave the old release active. Once migrations have
started, the tool deliberately does not guess that an Alembic downgrade is
safe; it reports the backup location for reviewed recovery.

## J. Backups and retention

Create a backup:

```bash
cd /opt/ecr/current
sudo ./install/backup.sh
```

Backups use a consistent transactional `mysqldump`, gzip verification, SHA-256
metadata, deployed ref/commit metadata, and mode `0600`. If `shared/data/`
contains persistent files, it is archived alongside the SQL dump.

Backups are retained indefinitely by default. Optional retention is explicit
and always preserves the newest five SQL backups:

```bash
sudo ./install/backup.sh --prune-older-than 30
```

Copy backups to an encrypted, access-controlled off-instance destination using
company backup policy. Local copies alone do not protect against instance loss.

## K. Restore

Restore is never run by installation. Select a specific backup:

```bash
cd /opt/ecr/current
sudo ./install/restore.sh /opt/ecr/backups/ecr-DATABASE-TIMESTAMP-REF.sql.gz
```

With no argument, the script lists available SQL backups. It validates the
gzip/checksum, displays the target database and backup metadata, requires the
exact confirmation `RESTORE <database>`, and creates another safety backup
before restoring. It does not automatically change application code or
downgrade Alembic; match the application release to backup metadata when schema
compatibility requires it.

Persistent-file archives are intentionally not overwritten automatically.
Restore those only after reviewing their archive and destination.

## L. Application rollback

Roll back to the previously recorded release:

```bash
cd /opt/ecr/current
sudo ./install/rollback.sh
```

Or name a retained directory under `/opt/ecr/releases`:

```bash
sudo ./install/rollback.sh 20260929T120000Z-abc123def456
```

Rollback changes application code only, restarts ECR, and performs a health
check. If that check fails it restores the prior code symlink. It never runs an
automatic Alembic downgrade. Use the verified database restore procedure when
the schema also must be reverted.

## M. Status and diagnostics

```bash
cd /opt/ecr/current
sudo ./install/status.sh
```

The report checks the ECR systemd unit, Nginx, application database command,
local health endpoint, HTTPS health endpoint, certificate validity/expiry,
Certbot timer, deployed ref/commit, and install directory. It never displays
database credentials.

Useful read-only diagnostics:

```bash
sudo systemctl status ecr --no-pager
sudo journalctl -u ecr -n 100 --no-pager
sudo nginx -t
sudo certbot certificates
```

## N. SSL renewal

The installer enables `certbot.timer`, installs an Nginx reload deploy hook,
and performs one renewal dry-run after successful HTTPS setup. Verify later:

```bash
sudo systemctl status certbot.timer --no-pager
sudo certbot renew --dry-run
```

Existing valid certificates under `/etc/letsencrypt` are preserved. If initial
issuance fails, the installer restores the working HTTP Nginx configuration and
prints DNS/firewall/Cloudflare troubleshooting guidance.

## O. Troubleshooting

- **Database check fails:** verify host/port, RDS security group/private route,
  database existence, account grants, and the protected `/opt/ecr/shared/.env`.
  Do not paste its password into logs or support messages.
- **Certificate issuance fails:** verify the DNS record, Elastic IP, inbound
  ports 80/443, Nginx, and whether Cloudflare/WAF rules block `/.well-known/`.
- **Service fails:** inspect `journalctl -u ecr`, then run the database check as
  the runtime user from `/opt/ecr/current`.
- **Nginx fails validation:** run `nginx -t`; installer-created prior copies are
  retained beside the managed config.
- **Private Git fetch fails:** verify Deploy Key read permission and GitHub SSH
  host keys; never replace it with an embedded token URL.
- **Migration/update fails:** do not improvise a downgrade. Preserve the error,
  application ref, and named pre-migration backup for review.

These scripts have local static/syntax validation in the repository. Successful
execution on a particular AWS/company environment must still be verified by its
administrator; the repository does not claim to provision or test AWS itself.
