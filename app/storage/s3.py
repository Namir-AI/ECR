"""Private S3 implementations; logical DB keys remain backend-independent.

The SDK default chain supplies refreshable instance-role credentials. No ACL,
public/presigned URL, custom endpoint or application credential setting exists.
"""

import re
from functools import lru_cache
from io import BytesIO
from uuid import uuid4

import boto3
from boto3.exceptions import Boto3Error
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.storage.attachments import CHUNK_SIZE, EXTENSIONS
from app.storage.configuration import normalize_prefix, validate_bucket, validate_region


def create_s3_client(region: str):
    """Shared SDK construction; preflight classifies errors, routes redact them."""
    return boto3.session.Session().client(
        "s3",
        region_name=validate_region(region),
        config=Config(
            connect_timeout=5,
            read_timeout=60,
            retries={"mode": "standard", "total_max_attempts": 3},
        ),
    )


@lru_cache(maxsize=8)
def s3_client(region: str):
    try:
        return create_s3_client(region)
    except (BotoCoreError, ClientError):
        raise OSError("Private object storage is unavailable") from None


class S3Reader:
    """Closing/context-manager facade; SDK read failures never expose internals."""

    def __init__(self, body):
        self.body = body

    def read(self, size=CHUNK_SIZE):
        try:
            return self.body.read(size)
        except (BotoCoreError, ClientError):
            raise OSError("Private object content is unavailable") from None

    def close(self):
        self.body.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class S3Objects:
    def __init__(self, bucket, region, prefix, *, client=None):
        self.bucket = validate_bucket(bucket)
        self.region = validate_region(region)
        self.prefix = normalize_prefix(prefix)
        self._client = client

    @property
    def client(self):
        return self._client if self._client is not None else s3_client(self.region)

    def physical_key(self, key):
        if not isinstance(key, str) or not re.fullmatch(self.key_pattern, key):
            raise ValueError("Invalid private object key")
        return f"{self.prefix}/{self.namespace}/{key}"

    def open_reader(self, key):
        physical = self.physical_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=physical)
            return S3Reader(response["Body"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in (
                "NoSuchKey",
                "404",
                "NotFound",
            ):
                raise FileNotFoundError("Private object not found") from None
            raise OSError("Private object content is unavailable") from None
        except BotoCoreError:
            raise OSError("Private object content is unavailable") from None

    def delete(self, key):
        physical = self.physical_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=physical)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in (
                "NoSuchKey",
                "404",
                "NotFound",
            ):
                raise OSError("Unable to remove private object") from None
        except BotoCoreError:
            raise OSError("Unable to remove private object") from None


class S3AttachmentStorage(S3Objects):
    namespace = "attachments"
    key_pattern = r"[0-9a-f]{32}\.(pdf|jpg|png)"

    def put_stream(self, source, media_type):
        if media_type not in EXTENSIONS:
            raise ValueError("Unsupported attachment media type")
        key = f"{uuid4().hex}.{EXTENSIONS[media_type]}"
        physical = self.physical_key(key)
        source.seek(0)
        try:
            # Classic single-threaded transfer buffers bounded multipart chunks,
            # never the whole JCC. Disallow automatic CRT/client policy changes.
            self.client.upload_fileobj(
                source,
                self.bucket,
                physical,
                ExtraArgs={"ContentType": media_type},
                Config=TransferConfig(
                    multipart_threshold=8 * 1024 * 1024,
                    multipart_chunksize=8 * 1024 * 1024,
                    max_concurrency=1,
                    use_threads=False,
                    preferred_transfer_client="classic",
                ),
            )
        except (BotoCoreError, ClientError, Boto3Error, OSError):
            # A lost acknowledgement can leave an unreferenced object. Never
            # mask the failure or broaden cleanup beyond this generated key.
            try:
                self.delete(key)
            except OSError:
                pass
            raise OSError("Unable to store private attachment") from None
        return key


class S3ProtectedStorage(S3Objects):
    namespace = "signatures"
    key_pattern = r"[0-9a-f]{32}\.png"

    def put(self, content, media_type="image/png"):
        if media_type != "image/png":
            raise ValueError("Signature storage accepts PNG drawings only")
        key = f"{uuid4().hex}.png"
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self.physical_key(key),
                Body=content,
                ContentType=media_type,
            )
        except (BotoCoreError, ClientError):
            raise OSError("Unable to store private sign") from None
        return key

    def read(self, key):
        # Signature bytes are bounded by existing PNG validation. JCC uses the
        # separate streaming interface and never this convenience method.
        with self.open_reader(key) as reader:
            result = BytesIO()
            while chunk := reader.read(CHUNK_SIZE):
                result.write(chunk)
            return result.getvalue()
