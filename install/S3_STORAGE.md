# Private ECR object storage

Local protected storage remains supported and is the default. A **fresh** install
may select S3 for every JCC PDF/image page, Tower Photo and Customer Sign PNG.
MySQL retains metadata and opaque logical keys, never binary BLOBs, credentials
or URLs. No object migration or schema migration is introduced.

## IT inputs / installer

IT supplies the production domain/DNS, HTTPS email, local or external MySQL/RDS
details and network connectivity. For S3, also
provide an existing **private general-purpose bucket**, its exact region, a
dedicated ECR prefix and an EC2 IAM role / instance profile attached to the host.
The SDK credential chain must be accessible to the configured service account.
Allow outbound HTTPS/DNS to regional S3 (or an approved VPC endpoint) and EC2
metadata access for role credentials, alongside existing GitHub/uv/Ubuntu/ACME
and private DB connectivity. No AWS CLI or `aws configure` is required.

Bucket/region/prefix, IAM and network access are installation prerequisites.
S3 versioning, lifecycle, backup and retention are **post-install IT operational
responsibilities**, not interactive installer inputs or requirements for running
the installer. IT should establish the operational policy before live evidence
is entrusted to the installation.

Keep Block Public Access enabled; prefer bucket-owner-enforced ownership (ACLs
disabled). Bucket policy/default encryption governs encryption. If IT requires
SSE-KMS, grant narrowly scoped KMS/key-policy permissions separately.

Run the existing `sudo ./install.sh --bootstrap-ref <tag-or-exact-commit>`.
Choose **File/object storage backend**:

1. **Local protected storage**: existing persistent absolute STORAGE_ROOT,
   normally `/opt/ecr/shared/data/protected`.
2. **AWS S3**: enter S3 bucket name, AWS region and ECR S3 prefix. All three
   have **no defaults**. No AWS access/secret key is requested or written.

The root:service-user 0640 production `.env` records:

```dotenv
STORAGE_BACKEND=s3
S3_BUCKET=<IT-supplied-private-bucket>
S3_REGION=<IT-supplied-region>
S3_PREFIX=<IT-supplied-ecr-prefix>
```

STORAGE_ROOT remains persistent for local runtime compatibility, but is not the
binary store in S3 mode. Non-secret deployment configuration retains storage
selection for incomplete-install recovery. A live `.env` is authoritative.
The installer validates syntax with the same application configuration module
before persisting values. Without `.env`, resume preserves valid saved settings;
**Reconfigure and resume incomplete installation** allows re-entering/correcting
storage settings. Invalid saved settings fail with that actionable recovery path;
no manual protected-file editing is necessary.
Prefixes use safe letters/numbers/underscore/hyphen segments separated by `/`;
one trailing slash is normalized away. Leading/doubled slashes, dot/traversal
segments, whitespace and backslashes are rejected.

Repair/reconfiguration and `sudo ecr-update <ref>` preserve the backend and S3
namespace without ordinary-update prompts. Installer env writes reject a change
to an existing backend/bucket/region/prefix. Direct manual env edits are **not**
object migration and can orphan evidence. Use a separate fresh installation or
a separately reviewed recovery/migration plan, not a silent backend switch.

## Service-user preflight

Before migrations/installation success, the application SDK verifies signed
credential access, bucket location and tiny private put/read/delete under
`<prefix>/.install-test/<random-id>`. It verifies exact bytes and deletes only
its newly generated object; conditional creation protects even a random-key
collision. It also initiates and immediately aborts a separate test multipart
upload to verify `s3:AbortMultipartUpload`, without uploading parts or listing.
Only its own returned upload ID is eligible for best-effort cleanup.
Authentication, region, access-denied and operation errors have safe
diagnostics. Cleanup is best-effort on failure; IT must reconcile the dedicated
test namespace if cleanup is denied. A short lifecycle for it is advisable.

AWS legacy location responses `None` (us-east-1) and `EU` (eu-west-1) are handled;
these are **response semantics, not installation region defaults**. Normal
updates also preflight before migrations. From the active app directory, as the
configured service user using its environment, the standalone check is
`.venv/bin/python -m app.storage.check`. No pre-existing objects are read/modified
by this check.

## Least-privilege IAM example

IT replaces BUCKET_NAME and ECR_PREFIX, adjusting ARN partition if needed. This
is a policy example only, not a runtime value or automatic IAM change:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "VerifyBucketRegion",
      "Effect": "Allow",
      "Action": "s3:GetBucketLocation",
      "Resource": "arn:aws:s3:::BUCKET_NAME"
    },
    {
      "Sid": "PrivateEcrObjectsOnly",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::BUCKET_NAME/ECR_PREFIX/*"
    }
  ]
}
```

Do not grant AmazonS3FullAccess. Neither application nor preflight lists the
bucket; **ListBucket is not required**. Bucket location checks reachability
without an unrestricted HeadBucket/list grant. Missing objects can produce 403
without ListBucket; protected routes report unavailable content safely under
existing behavior. AbortMultipartUpload enables failed multipart cleanup;
configure incomplete-multipart lifecycle as defense in depth. IT console/backup
permissions belong in separate policies, not the application role.

References: [SDK credential chain](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html),
[managed transfers](https://docs.aws.amazon.com/boto3/latest/reference/customizations/s3.html),
[bucket location](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketLocation.html),
[operation permissions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html).

## Keys / streaming / security

```text
<prefix>/attachments/<32-lowercase-hex>.<pdf|jpg|png>
<prefix>/signatures/<32-lowercase-hex>.png
```

No customer/site/serial/filename/employee names enter physical keys. DB keys
remain logical (`<hex>.png`, etc.). There are no public or presigned URLs or ACL
writes. Existing routes authorize before reads and retain private/no-store,
safe MIME/filename/security headers. Browser Print reads the authorized Customer
Sign through the same abstraction. Validation/Adobe sanitization, locks, audits
and commit-before-cleanup/ambiguous-commit safeguards are unchanged.

JCC uses disk-backed uploads and classic single-threaded SDK multipart transfer
(8 MiB initial chunk/threshold; SDK may scale parts for exceptionally large
objects to meet S3 constraints). Downloads use 64 KiB reads and close the SDK
body. No application JCC size cap is added; AWS's own limits still apply.
Signatures retain bounded PNG validation and their existing byte interface.
Errors become safe storage exceptions. Cleanup failures may leave an orphan;
there is no broad collector, historical object scan or migration utility.
Versioned buckets retain old versions on normal DeleteObject; the application
does not delete historical versions.

## Backups / recovery

**Local**: existing mandatory verified MySQL + shared/data + custom STORAGE_ROOT
archives and checksum/restore protections are unchanged.

**S3**: backup.sh verifies MySQL backup and records backend/bucket/region/prefix
in metadata. It does **not** archive S3 objects or local object directories in
this mode. S3 Versioning, lifecycle, AWS Backup, replication and retention are IT
responsibilities, not automatically provisioned by this patch.

restore.sh rejects backend/namespace mismatches and unexpected local archives
in an S3 set. It requires **ACKNOWLEDGE S3 RECOVERY <database>**, plus existing
**RESTORE <database>**, before stopping ECR and making the mandatory safety
backup. It restores **DB only**, never changes/deletes S3 objects, never checks
out application code and never downgrades Alembic.

Coordinate SQL recovery with evidence object versions: DB stores keys, not S3
VersionIds. Older SQL may reference subsequently deleted/replaced objects. IT
must review/restore required versions before returning ECR to use. Health checks
do not prove every historical object is present. Failed restore remains stopped
for operator review under existing safety rules. No destructive S3 restore or
cross-backend conversion is implemented.
