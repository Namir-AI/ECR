"""Non-destructive installation preflight using the service account SDK chain."""

import secrets
from uuid import uuid4

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    CredentialRetrievalError,
    NoCredentialsError,
    PartialCredentialsError,
)

from app.core.config import get_app_settings
from app.storage.s3 import create_s3_client


class StorageCheckError(OSError):
    pass


def explain_failure(stage, error):
    if isinstance(
        error, (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError)
    ):
        return "AWS authentication unavailable: attach an EC2 IAM role / instance profile accessible to the service account."
    code = (
        error.response.get("Error", {}).get("Code")
        if isinstance(error, ClientError)
        else None
    )
    if code in (
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "InvalidToken",
    ):
        return "AWS authentication failed: check the service account credential chain and IAM role."
    if code in ("NoSuchBucket", "404", "NotFound"):
        return "S3 bucket not found: verify the installation bucket name."
    if code in ("AccessDenied", "403"):
        return f"S3 AccessDenied during {stage}: check IAM/bucket policy for the configured namespace."
    if code in (
        "AuthorizationHeaderMalformed",
        "PermanentRedirect",
        "IllegalLocationConstraintException",
    ):
        return (
            "S3 region mismatch: verify S3_REGION against the bucket's actual region."
        )
    return (
        f"S3 {stage} failed: check connectivity, IAM/bucket policy and configuration."
    )


def check_s3(settings, *, client=None):
    stage = "authentication"
    key = f"{settings.s3_prefix}/.install-test/{uuid4().hex}"
    payload = secrets.token_bytes(32)
    attempted = False
    deleted = False
    multipart_key = f"{settings.s3_prefix}/.install-test/{uuid4().hex}"
    upload_id = None
    try:
        client = client if client is not None else create_s3_client(settings.s3_region)
        stage = "bucket location check"
        location = client.get_bucket_location(Bucket=settings.s3_bucket).get(
            "LocationConstraint"
        )
        # AWS legacy location semantics, NOT regional installation defaults.
        actual = (
            "us-east-1"
            if location in (None, "")
            else "eu-west-1"
            if location == "EU"
            else location
        )
        if actual != settings.s3_region:
            raise StorageCheckError(
                "S3 region mismatch: supplied region differs from the actual bucket region."
            )
        stage = "write"
        attempted = True
        try:
            client.put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=payload,
                ContentType="application/octet-stream",
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in (
                "PreconditionFailed",
                "ConditionalRequestConflict",
            ):
                attempted = False  # Never delete a pre-existing colliding object.
            raise
        stage = "read"
        body = client.get_object(Bucket=settings.s3_bucket, Key=key)["Body"]
        try:
            if body.read(len(payload) + 1) != payload:
                raise StorageCheckError(
                    "S3 read verification failed: test object contents differ."
                )
        finally:
            body.close()
        stage = "delete"
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
        deleted = True
        stage = "multipart initiation"
        upload_id = client.create_multipart_upload(
            Bucket=settings.s3_bucket,
            Key=multipart_key,
            ContentType="application/octet-stream",
        )["UploadId"]
        # No parts, listing, completion or object publication. Only abort the
        # exact upload ID returned for this fresh private test key.
        stage = "multipart abort (s3:AbortMultipartUpload)"
        client.abort_multipart_upload(
            Bucket=settings.s3_bucket, Key=multipart_key, UploadId=upload_id
        )
        upload_id = None
    except (BotoCoreError, ClientError) as exc:
        raise StorageCheckError(explain_failure(stage, exc)) from None
    finally:
        if upload_id is not None and client is not None:
            try:
                client.abort_multipart_upload(
                    Bucket=settings.s3_bucket, Key=multipart_key, UploadId=upload_id
                )
            except (BotoCoreError, ClientError):
                print(
                    "WARNING: S3 preflight multipart cleanup failed; IT must reconcile incomplete uploads in the configured .install-test namespace."
                )
        if attempted and not deleted and client is not None:
            try:
                client.delete_object(Bucket=settings.s3_bucket, Key=key)
            except (BotoCoreError, ClientError):
                # No secrets/keys logged. This namespace is deliberately scoped
                # for IT lifecycle cleanup if connectivity/permissions fail.
                print(
                    "WARNING: S3 preflight cleanup failed; IT must reconcile the configured .install-test namespace."
                )


def main():
    settings = get_app_settings()
    if settings.storage_backend == "local":
        print("Local protected storage selected; no AWS preflight required.")
        return
    try:
        check_s3(settings)
    except OSError as exc:
        raise SystemExit(str(exc)) from None
    print(
        "S3 credential-chain, bucket region, private put/read/delete and multipart create/abort preflight passed."
    )


if __name__ == "__main__":
    main()
