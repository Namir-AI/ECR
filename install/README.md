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
│   ├── data/protected/       private persistent customer-signature storage
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
The installer also installs the root-owned `/usr/local/sbin/ecr-update`
launcher. Normal company operation needs no knowledge of release directories.
It is atomically installed/refreshed only after the exact deployed release
passes local and configured HTTPS health checks. Failed target deployments
leave the known-good launcher unchanged. Successful future updates therefore
deliver improvements to the launcher automatically.

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
ref. It downloads a reference copy of `install.sh`, `common.sh`, `env_tools.py`
and the stable launcher from that same ref into a private temporary directory,
verifies the running installer matches the reference copy, and only then loads
the helper. It will not silently fall back to `main`. The
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

Company handover: first install with
`sudo ./install.sh --bootstrap-ref <release-tag-or-commit>` (see section D),
then update from any directory with:

```bash
sudo ecr-update <release-tag-or-commit>
```

For example, or to use the configured default ref (`ECR_GIT_REF`):

```bash
sudo ecr-update v1.1.0
sudo ecr-update
```

Exact commits remain supported. Prefer approved immutable tags/full SHAs.
The small stable launcher requires root, reads the protected deployment config,
holds the deployment lock, fetches the configured mirror, and resolves an exact
SHA. It extracts **that commit's** deployment scripts into a root-private
temporary directory and executes them, handing off the same lock. It never
sources the current release's helpers. A bootstrap failure changes neither the
current release nor the database. Deployment state/history records the exact SHA.

The target updater creates a mandatory database/files backup, prepares a
separate `uv.lock` release with `uv sync --frozen --no-dev`, checks database
connectivity, runs Alembic, atomically switches `current`, restarts systemd,
and verifies local and configured HTTPS health. uv remains the runtime and
package manager: CPython 3.12 lives under `shared/uv-python`, and each release
has its own virtualenv. Interpreter containment is checked against canonical
paths, including when uv returns a release-venv symlink. No manual production
Python setup is needed.

The active `current` Git worktree is authoritative, not a potentially stale
commit in `deployment.env`. Stale metadata produces a warning and is reconciled
on a successful update. Backup metadata and status output also use the actual
active worktree commit and warn about stale metadata, retaining the recorded
ref as a descriptive label. Neither backup nor status modifies deployment state.
After all health checks pass, the updater records deployment state
and successful history **before** atomically refreshing the stable launcher.
A launcher-only failure returns non-zero but explicitly reports the healthy
active application and recorded target commit; it does not imply database
recovery or trigger rollback. Repair publication permissions and repeat
`sudo ecr-update <same-ref>`: the same-commit path verifies health, reconciles
state/ref, records a `reconcile` history event, refreshes the launcher from the
exact current release, and reports status, without a backup, migration, restart
or duplicate release.

Before migrations, failures leave the old release/symlink/database unchanged,
remove only the failed new Git worktree, and restore the pre-update `.env` if
configuration preparation had begun. Failed cleanup is a warning, not permission
to remove other releases or shared data. Once migrations begin there is **no
automatic Alembic downgrade**: retain the failed release and use the named
database backup/environment snapshot for reviewed recovery. A post-activation
health failure also needs operator review; code/schema are not blindly reverted.
Environment recovery restores exact contents atomically with live permissions
`root:<service user> 0640`; the retained snapshot stays `root:root 0600`.

### One-time adoption on installations with the old updater

The old `/opt/ecr/current/install/update.sh` cannot install this fix when its
Python check fails. After the owner publishes an approved commit containing
this hardening, download its **new launcher** (not the old updater) and install
only the stable command. For a public repository, as root:

```bash
sudo -i
ECR_TOOLING_REF=<full-approved-hardening-commit-sha>
ECR_BOOTSTRAP_DIR=$(mktemp -d /root/ecr-launcher.XXXXXXXX)
curl -fsSL --proto '=https' --tlsv1.2 \
  "https://raw.githubusercontent.com/Namir-AI/ECR/${ECR_TOOLING_REF}/install/ecr-update.sh" \
  -o "$ECR_BOOTSTRAP_DIR/ecr-update.sh"
bash "$ECR_BOOTSTRAP_DIR/ecr-update.sh" --install-launcher "$ECR_TOOLING_REF"
ecr-update "$ECR_TOOLING_REF"
exit
```

For private repositories, obtain this same file through the company's trusted
checkout/read-only Deploy Key process. `--install-launcher` fetches and verifies
the configured mirror and installs the launcher **from the selected commit**;
it performs no release activation, application migration, or secret changes.
This adoption option is only for an absent launcher. It will not replace an
already-installed command; use a normal healthy update to refresh that command.
Keep/review the small root-only downloaded bootstrap directory according to IT
policy. The accepted application SHA `bf29fc7` predates this deployment fix and
does not contain the new launcher. Future updates need only `sudo ecr-update`.

### Managed production settings

Fresh install creates `shared/data/protected` with service ownership and mode
`0700` and writes the absolute `STORAGE_ROOT` into production `.env`.
Updates preserve valid custom absolute roots; a missing/blank setting or the
old example `var/protected` becomes the persistent shared path automatically.
Other relative roots, `/`, or release-local storage are rejected for review.
Existing signature files are never moved/deleted by a configuration upgrade.

New non-secret defaults may be explicitly added to `MANAGED_DEFAULTS` in
`install/lib/env_tools.py` by release maintainers. Only missing opted-in values
are copied from the target `.env.example`. Existing custom values, passwords,
and secrets are never replaced with example values. `STORAGE_ROOT` is the
explicit production-path exception described above. Settings are validated
using the target application before migrations/activation, without logging
secret values. Invalid custom settings require deliberate administrator review,
not silent overwriting. Updates retain a root-only pre-update env snapshot.

## J. Backups and retention

Create a backup:

```bash
cd /opt/ecr/current
sudo ./install/backup.sh
```

Backups use a consistent transactional `mysqldump`, gzip verification, SHA-256
metadata, deployed ref/commit metadata, and mode `0600`. If `shared/data/`
exists, it is archived alongside the SQL dump, including when empty.
The archive includes `data/protected` customer signatures. A valid custom
`STORAGE_ROOT` outside `shared/data` receives a separate `.storage.tar.gz`
archive with its own checksum/location metadata. Both file archives undergo
gzip and tar readability checks. New backup metadata marks a completed backup
set; nanosecond timestamps avoid overwriting backups taken close together.

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

With no argument, the script lists available SQL backups. Recovery is one
reviewed operation for the database **and its paired persistent files**:

1. Validate SQL gzip/SHA-256 and paired archive checksums. Parse metadata as
   data, never as shell code. Reject incomplete sets, unsafe paths, symlinks,
   hardlinks, devices, duplicate paths and unreadable archives before mutation.
2. Stage verified SQL and extracted file content privately. Only the regular
   files/directories in the reviewed archives are accepted; archive ownership
   and executable modes are not trusted.
3. Require the existing exact `RESTORE <database>` confirmation.
4. Stop the application, then create the mandatory safety backup of the
   current database/files. No current data is replaced unless that succeeds.
5. Restore SQL and exchange the paired data directories. Previous directories
   are retained as `.ecr-before-restore.*` for reviewed recovery, not deleted.
6. Run the DB check, start the service, then check local/configured HTTPS health.

The `.files.tar.gz` restores into the current real `shared/data` directory.
Custom `.storage.tar.gz` recovery uses the **current configured** canonical
`STORAGE_ROOT` and requires an exact match with the backed-up canonical root.
It never extracts to an arbitrary metadata-selected path, application releases,
or protected deployment directories. Mismatch aborts before SQL restoration.
Restored content is service-owned; protected files use private permissions.
Directory exchange requires a normal directory beneath the storage volume;
a destination that itself is a mount point or contains nested mounts is
rejected before mutation for mount-aware operator recovery. Mounted parents
(for example a volume mounted at `shared` with `shared/data` beneath it) are
supported.

Legacy DB-only sets are clearly identified. Missing archives without paired
checksums require the additional explicit
`ACKNOWLEDGE MISSING FILES <database>` before the normal `RESTORE` confirmation;
those files remain untouched, not falsely reported as restored. A checksum
that refers to a missing archive is an incomplete set and is rejected. SQL
checksums/metadata remain mandatory; unverified SQL dumps need operator review.

MySQL DDL recovery cannot be an atomic transaction with filesystem changes.
If recovery fails after SQL/file mutation starts, the application stays stopped
and the safety-backup location is reported for operator recovery. There is no
automatic SQL restore or Alembic downgrade. If the safety backup itself fails,
no recovery mutation is attempted and the previously active service is restarted.
Match application code/schema to backup metadata before recovery. Retained
previous directories are reviewed/removed separately by company policy.

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

Deployment regression tests use real temporary Git mirrors/worktrees and
filesystem archives, with root/service/uv/MySQL operations simulated:
`python -m pytest tests/test_deployment.py tests/test_deployment_recovery.py`.
They never change production. Root/service ownership requests are simulated
where root privileges are unavailable; production permission checks still
need the company's privileged acceptance test.
