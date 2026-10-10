"""Installation namespace validation shared by settings and S3 adapters."""

import re
import sys


def normalize_prefix(value: str) -> str:
    # One optional trailing delimiter is harmless; all other ambiguous forms fail.
    value = value.removesuffix("/")
    if (
        not value
        or len(value) > 256
        or not all(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", part)
            for part in value.split("/")
        )
    ):
        raise ValueError("S3_PREFIX must contain safe, nonempty namespace segments")
    return value


def validate_bucket(value: str) -> str:
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value)
        or ".." in value
        or ".-" in value
        or "-." in value
        or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", value)
        or value.startswith(("xn--", "sthree-", "amzn-s3-demo-"))
        or value.endswith(("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3"))
    ):
        raise ValueError("S3_BUCKET must be a valid general-purpose bucket name")
    return value


def validate_region(value: str) -> str:
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-\d+", value):
        raise ValueError("S3_REGION must be an explicitly supplied AWS region")
    return value


def installation_values(backend, bucket, region, prefix):
    """Dependency-free installer entrypoint using the exact application rules."""
    if backend == "local":
        return ("local", "", "", "")
    if backend != "s3":
        raise ValueError("STORAGE_BACKEND must be local or s3")
    if not all((bucket, region, prefix)):
        raise ValueError("S3_BUCKET, S3_REGION and S3_PREFIX are required")
    return (
        "s3",
        validate_bucket(bucket),
        validate_region(region),
        normalize_prefix(prefix),
    )


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("Invalid installer storage validation invocation.")
    try:
        values = installation_values(*sys.argv[1:])
    except ValueError as exc:
        raise SystemExit(f"Invalid storage configuration: {exc}.") from None
    for value in values:
        sys.stdout.buffer.write(value.encode("utf-8") + b"\0")
