"""AWS-free adapters, configuration, preflight and real authorized routes."""

from io import BytesIO
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import AppSettings
from app.ecr.attachment_models import EcrPackageAttachment
from app.storage.attachments import (
    LocalAttachmentStorage,
    get_attachment_storage,
    iter_chunks,
)
from app.storage.check import StorageCheckError, check_s3
from app.storage.protected import LocalProtectedStorage, get_protected_storage
from app.storage.s3 import S3AttachmentStorage, S3ProtectedStorage
from app.users.models import UserRole
from tests.conftest import csrf_from, login
from tests.test_attachments import image_bytes, pdf_bytes, send
from tests.test_page3 import sign
from tests.test_phase3 import _create_report


def aws_error(code):
    return ClientError(
        {"Error": {"Code": code, "Message": "PRIVATE AWS DETAIL NEVER EXPOSE"}}, "test"
    )


class FakeS3:
    def __init__(self, *, location="ap-south-1", fail=None):
        self.objects = {}
        self.calls = []
        self.location = location
        self.fail = fail
        self.readers = []
        self.uploads = {}
        self.upload_sequence = 0

    def operation(self, operation, fields):
        self.calls.append((operation, fields))
        if self.fail == operation:
            raise aws_error("InternalError")
        if self.fail == "denied":
            raise aws_error("AccessDenied")
        if self.fail == "auth":
            raise NoCredentialsError()

    def get_bucket_location(self, **fields):
        self.operation("location", fields)
        return {"LocationConstraint": self.location}

    def put_object(self, **fields):
        self.operation("put", fields)
        if fields.get("IfNoneMatch") and fields["Key"] in self.objects:
            raise aws_error("PreconditionFailed")
        self.objects[fields["Key"]] = fields["Body"]

    def upload_fileobj(self, source, bucket, key, *, ExtraArgs, Config):
        self.operation(
            "upload", {"Bucket": bucket, "Key": key, **ExtraArgs, "Config": Config}
        )
        chunks = []
        while chunk := source.read(65536):
            chunks.append(chunk)
        self.objects[key] = b"".join(chunks)

    def get_object(self, **fields):
        self.operation("get", fields)
        if fields["Key"] not in self.objects:
            raise aws_error("NoSuchKey")
        body = BytesIO(self.objects[fields["Key"]])
        self.readers.append(body)
        return {"Body": body}

    def delete_object(self, **fields):
        self.operation("delete", fields)
        self.objects.pop(fields["Key"], None)

    def create_multipart_upload(self, **fields):
        self.operation("multipart-create", fields)
        self.upload_sequence += 1
        upload_id = f"synthetic-upload-{self.upload_sequence}"
        self.uploads[(fields["Key"], upload_id)] = True
        return {"UploadId": upload_id}

    def abort_multipart_upload(self, **fields):
        self.operation("multipart-abort", fields)
        if self.fail == "abort-denied":
            raise aws_error("AccessDenied")
        self.uploads.pop((fields["Key"], fields["UploadId"]), None)


def settings(**overrides):
    return AppSettings(
        _env_file=None,
        storage_backend="s3",
        s3_bucket="test-private-bucket",
        s3_region="ap-south-1",
        s3_prefix="training/ecr",
        **overrides,
    )


@pytest.mark.parametrize("field", ["s3_bucket", "s3_region", "s3_prefix"])
def test_required_s3_config(field):
    values = {
        "storage_backend": "s3",
        "s3_bucket": "test-private-bucket",
        "s3_region": "ap-south-1",
        "s3_prefix": "ecr",
    }
    values.pop(field)
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, **values)
    assert AppSettings.model_fields[field].default is None


@pytest.mark.parametrize(
    "prefix", ["", "/ecr", "../ecr", "ecr/..", "ecr//a", "ecr\\x", "ecr/", "ecr/jobs"]
)
def test_prefix_validation(prefix):
    if prefix in ("ecr/", "ecr/jobs"):
        values = settings().model_dump()
        values["s3_prefix"] = prefix
        assert AppSettings(_env_file=None, **values).s3_prefix == prefix.rstrip("/")
    else:
        values = settings().model_dump()
        values["s3_prefix"] = prefix
        with pytest.raises(ValidationError):
            AppSettings(_env_file=None, **values)


def test_factories_preserve_local_and_overrides(tmp_path):
    local = AppSettings(_env_file=None, storage_root=tmp_path)
    assert local.storage_backend == "local" and local.s3_region is None
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=local))
    )
    assert isinstance(get_attachment_storage(request), LocalAttachmentStorage)
    assert isinstance(get_protected_storage(request), LocalProtectedStorage)
    request.app.state.settings = settings()
    assert isinstance(get_attachment_storage(request), S3AttachmentStorage)
    assert isinstance(get_protected_storage(request), S3ProtectedStorage)
    request.app.state.attachment_storage = "injected"
    request.app.state.protected_storage = "signature-injected"
    assert get_attachment_storage(request) == "injected"
    assert get_protected_storage(request) == "signature-injected"


class BoundedStream(BytesIO):
    def read(self, size=-1):
        assert 0 < size <= 65536, "unbounded application read"
        return super().read(size)


def test_attachment_stream_and_metadata():
    client = FakeS3()
    store = S3AttachmentStorage(
        "test-private-bucket", "ap-south-1", "ecr/", client=client
    )
    content = b"pdf" * 3_000_000
    key = store.put_stream(BoundedStream(content), "application/pdf")
    assert store.physical_key(key) == f"ecr/attachments/{key}"
    assert b"".join(iter_chunks(store.open_reader(key))) == content
    assert client.readers[-1].closed
    upload = client.calls[0][1]
    assert upload["ContentType"] == "application/pdf" and "ACL" not in upload
    assert not upload["Config"].use_threads and upload["Config"].max_concurrency == 1
    assert upload["Config"].preferred_transfer_client == "classic"
    store.delete(key)
    store.delete(key)
    with pytest.raises(FileNotFoundError):
        store.open_reader(key)


@pytest.mark.parametrize("adapter", [S3AttachmentStorage, S3ProtectedStorage])
@pytest.mark.parametrize(
    "key",
    [
        "../a.png",
        "s3://bucket/key",
        "/a.png",
        "ecr/attachments/" + "a" * 32 + ".png",
        "A" * 32 + ".png",
        "x.png",
    ],
)
def test_reject_arbitrary_keys(adapter, key):
    client = FakeS3()
    store = adapter("test-private-bucket", "ap-south-1", "ecr", client=client)
    for action in (store.open_reader, store.delete):
        with pytest.raises(ValueError):
            action(key)
    assert not client.calls


def test_signature_separate_namespace_and_png_only():
    client = FakeS3()
    store = S3ProtectedStorage(
        "test-private-bucket", "ap-south-1", "ecr", client=client
    )
    content = image_bytes()
    key = store.put(content)
    assert store.physical_key(key) == f"ecr/signatures/{key}"
    assert store.read(key) == content and client.readers[-1].closed
    with pytest.raises(ValueError):
        store.put(content, "image/jpeg")
    assert "ACL" not in client.calls[0][1]
    store.delete(key)
    store.delete(key)


@pytest.mark.parametrize(
    "location,region",
    [(None, "us-east-1"), ("EU", "eu-west-1"), ("ap-south-1", "ap-south-1")],
)
def test_preflight_region_semantics_and_cleanup(location, region):
    values = settings().model_dump()
    values["s3_region"] = region
    client = FakeS3(location=location)
    client.objects["training/ecr/existing"] = b"never delete"
    check_s3(AppSettings(_env_file=None, **values), client=client)
    assert client.objects == {"training/ecr/existing": b"never delete"}
    assert [c[0] for c in client.calls] == [
        "location",
        "put",
        "get",
        "delete",
        "multipart-create",
        "multipart-abort",
    ]
    assert not client.uploads
    created, aborted = client.calls[-2][1], client.calls[-1][1]
    assert created["Key"].startswith("training/ecr/.install-test/")
    assert created["Key"] != client.calls[1][1]["Key"]
    assert (aborted["Bucket"], aborted["Key"], aborted["UploadId"]) == (
        created["Bucket"],
        created["Key"],
        "synthetic-upload-1",
    )
    assert client.calls[1][1]["Key"].startswith("training/ecr/.install-test/")
    assert client.calls[1][1]["IfNoneMatch"] == "*"


@pytest.mark.parametrize(
    "failure,match",
    [
        ("auth", "authentication"),
        ("denied", "AccessDenied"),
        ("location", "bucket location"),
        ("put", "write"),
        ("get", "read"),
        ("delete", "delete"),
        ("multipart-create", "multipart initiation"),
        ("multipart-abort", "multipart abort"),
    ],
)
def test_preflight_failures_safe_and_cleanup(failure, match, capsys):
    client = FakeS3(fail=failure)
    with pytest.raises(StorageCheckError, match=match):
        check_s3(settings(), client=client)
    assert "PRIVATE AWS DETAIL" not in capsys.readouterr().out
    if failure in ("put", "get", "delete"):
        assert client.calls[-1][0] == "delete"
    if failure != "delete":
        assert not client.objects


def test_preflight_abort_denied_preserves_existing_objects_and_uploads(capsys):
    client = FakeS3(fail="abort-denied")
    existing = ("training/ecr/.install-test/existing", "prior-upload-id")
    client.uploads[existing] = True
    client.objects[existing[0]] = b"pre-existing content"
    with pytest.raises(
        StorageCheckError, match="AccessDenied.*s3:AbortMultipartUpload"
    ) as error:
        check_s3(settings(), client=client)
    assert "PRIVATE AWS DETAIL" not in str(error.value) + capsys.readouterr().out
    assert client.objects == {existing[0]: b"pre-existing content"}
    assert existing in client.uploads
    aborts = [fields for op, fields in client.calls if op == "multipart-abort"]
    assert len(aborts) == 2 and aborts[0] == aborts[1]
    assert aborts[0]["UploadId"] == "synthetic-upload-1"
    assert all("list" not in op and "part-upload" not in op for op, _ in client.calls)


def test_preflight_retries_cleanup_of_only_its_created_multipart_upload():
    class Client(FakeS3):
        def abort_multipart_upload(self, **fields):
            if not any(op == "multipart-abort" for op, _ in self.calls):
                self.operation("multipart-abort", fields)
                raise aws_error("InternalError")
            super().abort_multipart_upload(**fields)

    client = Client()
    existing = ("training/ecr/.install-test/prior", "prior-id")
    client.uploads[existing] = True
    with pytest.raises(StorageCheckError, match="multipart abort"):
        check_s3(settings(), client=client)
    assert client.uploads == {existing: True}


def test_preflight_multipart_key_collision_does_not_modify_existing_content(
    monkeypatch,
):
    from uuid import UUID

    identifiers = iter((UUID(int=1), UUID(int=2)))
    monkeypatch.setattr("app.storage.check.uuid4", lambda: next(identifiers))
    client = FakeS3()
    key = f"training/ecr/.install-test/{UUID(int=2).hex}"
    client.objects[key] = b"pre-existing content"
    client.uploads[(key, "pre-existing-upload-id")] = True
    check_s3(settings(), client=client)
    assert client.objects == {key: b"pre-existing content"}
    assert client.uploads == {(key, "pre-existing-upload-id"): True}
    assert all(
        fields.get("UploadId") != "pre-existing-upload-id" for _, fields in client.calls
    )


def test_preflight_region_mismatch_before_writes():
    client = FakeS3(location="eu-west-1")
    with pytest.raises(StorageCheckError, match="region mismatch"):
        check_s3(settings(), client=client)
    assert [c[0] for c in client.calls] == ["location"]


@pytest.mark.parametrize("adapter", [S3AttachmentStorage, S3ProtectedStorage])
def test_sdk_errors_are_safe_oserrors(adapter):
    store = adapter(
        "test-private-bucket", "ap-south-1", "ecr", client=FakeS3(fail="denied")
    )
    with pytest.raises(OSError) as error:
        store.open_reader("a" * 32 + ".png")
    assert "PRIVATE AWS DETAIL" not in str(error.value)


def test_client_uses_default_chain_and_cached_sdk_configuration(monkeypatch):
    from app.storage.s3 import s3_client

    calls = []
    client = object()

    class Session:
        def __init__(self, **kwargs):
            assert kwargs == {}  # No credentials/profile forced by application.

        def client(self, service, **kwargs):
            calls.append((service, kwargs))
            return client

    s3_client.cache_clear()
    monkeypatch.setattr("app.storage.s3.boto3.session.Session", Session)
    try:
        assert s3_client("eu-west-1") is client
        assert s3_client("eu-west-1") is client
        assert len(calls) == 1 and calls[0][0] == "s3"
        assert set(calls[0][1]) == {"region_name", "config"}
        assert calls[0][1]["region_name"] == "eu-west-1"
    finally:
        s3_client.cache_clear()


@pytest.mark.parametrize(
    "code,match",
    [
        ("NoSuchBucket", "bucket not found"),
        ("ExpiredToken", "authentication"),
        ("AuthorizationHeaderMalformed", "region mismatch"),
    ],
)
def test_preflight_aws_failure_classification(code, match):
    class Client(FakeS3):
        def get_bucket_location(self, **fields):
            raise aws_error(code)

    with pytest.raises(StorageCheckError, match=match):
        check_s3(settings(), client=Client())


def test_preflight_sdk_credential_construction_failure(monkeypatch):
    from botocore.exceptions import CredentialRetrievalError

    def fail(region):
        raise CredentialRetrievalError(
            provider="iam-role", error_msg="PRIVATE TOKEN DETAILS"
        )

    monkeypatch.setattr("app.storage.check.create_s3_client", fail)
    with pytest.raises(StorageCheckError, match="authentication unavailable") as error:
        check_s3(settings())
    assert "PRIVATE TOKEN" not in str(error.value)


def test_preflight_collision_is_never_deleted(monkeypatch):
    from uuid import UUID

    monkeypatch.setattr("app.storage.check.uuid4", lambda: UUID(int=1))
    client = FakeS3()
    key = f"training/ecr/.install-test/{UUID(int=1).hex}"
    client.objects[key] = b"preexisting"
    with pytest.raises(StorageCheckError):
        check_s3(settings(), client=client)
    assert client.objects[key] == b"preexisting"
    assert not any(op == "delete" for op, _ in client.calls)


def test_preflight_read_mismatch_closes_body_and_cleans_test_object():
    class Client(FakeS3):
        def get_object(self, **fields):
            response = super().get_object(**fields)
            response["Body"].close()
            body = BytesIO(b"unexpected")
            self.readers.append(body)
            return {"Body": body}

    client = Client()
    with pytest.raises(StorageCheckError, match="contents differ"):
        check_s3(settings(), client=client)
    assert all(body.closed for body in client.readers) and not client.objects


def test_stream_read_errors_close_sdk_body():
    from botocore.exceptions import ReadTimeoutError

    from app.storage.s3 import S3Reader

    class Body:
        closed = False

        def read(self, size):
            raise ReadTimeoutError(endpoint_url="https://private-detail.invalid")

        def close(self):
            self.closed = True

    body = Body()
    with pytest.raises(OSError) as error:
        list(iter_chunks(S3Reader(body)))
    assert body.closed and "private-detail" not in str(error.value)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_s3_image_media_types(fmt):
    client = FakeS3()
    store = S3AttachmentStorage(
        "test-private-bucket", "ap-south-1", "ecr", client=client
    )
    media = "image/jpeg" if fmt == "JPEG" else "image/png"
    data = image_bytes(fmt)
    key = store.put_stream(BytesIO(data), media)
    assert client.calls[0][1]["ContentType"] == media
    assert key.endswith(".jpg" if fmt == "JPEG" else ".png")
    assert b"".join(iter_chunks(store.open_reader(key))) == data


def test_s3_failed_upload_cleanup_does_not_remove_previous_object():
    class Client(FakeS3):
        def upload_fileobj(self, source, bucket, key, **options):
            self.objects[key] = b"uncertain upload"
            raise aws_error("InternalError")

    client = Client()
    previous = "ecr/attachments/" + "a" * 32 + ".pdf"
    client.objects[previous] = b"old valid attachment"
    store = S3AttachmentStorage(
        "test-private-bucket", "ap-south-1", "ecr", client=client
    )
    with pytest.raises(OSError):
        store.put_stream(BytesIO(b"new"), "application/pdf")
    assert client.objects == {previous: b"old valid attachment"}


@pytest.mark.parametrize(
    "role", [UserRole.SUPERVISOR, UserRole.BRANCH_ADMIN, UserRole.SUPERADMIN]
)
@pytest.mark.parametrize("same_branch", [False, True])
def test_s3_real_attachment_route_authorization(
    client, db_session, user_factory, branch_factory, monkeypatch, role, same_branch
):
    aws = FakeS3()
    monkeypatch.setattr("app.storage.s3.s3_client", lambda region: aws)
    for key in ("storage_backend", "s3_bucket", "s3_region", "s3_prefix"):
        setattr(client.app.state.settings, key, getattr(settings(), key))
    owner = user_factory()
    report = _create_report(db_session, owner)
    login(client, owner.employee_id)
    token = csrf_from(
        client.get(f"/ecr/packages/{report.tower.package_id}/attachments").text
    )
    assert (
        send(
            client, report, token, files=[("customer-name.pdf", pdf_bytes(3))]
        ).status_code
        == 200
    )
    row = db_session.scalar(
        select(EcrPackageAttachment).where(
            EcrPackageAttachment.package_id == report.tower.package_id
        )
    )
    physical = f"training/ecr/attachments/{row.storage_key}"
    assert "customer-name" not in physical and physical in aws.objects
    path = f"/ecr/packages/{row.package_id}/attachments/{row.id}"
    response = client.get(path)
    assert response.status_code == 200 and response.content == aws.objects[physical]
    assert response.headers["cache-control"] == "private, no-store"
    viewer = user_factory(
        role=role,
        branch=owner.branch if same_branch else branch_factory(code="OTHER-S3"),
    )
    client.cookies.clear()
    login(client, viewer.employee_id)
    aws.calls.clear()
    response = client.get(path)
    if role == UserRole.SUPERADMIN or (role == UserRole.BRANCH_ADMIN and same_branch):
        assert response.status_code == 200
    else:
        assert response.status_code in (403, 404) and not aws.calls
    client.cookies.clear()
    login(client, owner.employee_id)
    aws.objects.clear()
    assert client.get(path).status_code == 404


def test_s3_real_adobe_photos_failures_and_anonymous_access(
    client, db_session, user_factory, monkeypatch
):
    from app.ecr.attachments import file_records
    from tests.test_adobe_scan_jcc import adobe_pdf, assert_sanitized

    aws = FakeS3()
    monkeypatch.setattr("app.storage.s3.s3_client", lambda region: aws)
    for key in ("storage_backend", "s3_bucket", "s3_region", "s3_prefix"):
        setattr(client.app.state.settings, key, getattr(settings(), key))
    owner = user_factory()
    report = _create_report(db_session, owner)
    login(client, owner.employee_id)
    token = csrf_from(
        client.get(f"/ecr/packages/{report.tower.package_id}/attachments").text
    )
    original = adobe_pdf(pages=3)
    assert (
        send(client, report, token, files=[("adobe.pdf", original)]).status_code == 200
    )
    row = file_records(db_session, report.tower.package_id)[0]
    content = aws.objects[f"training/ecr/attachments/{row.storage_key}"]
    assert_sanitized(content, 3)
    assert content != original and len(content) == row.byte_size
    before = dict(aws.objects)
    aws.fail = "upload"
    response = send(
        client, report, token, action="JCC_REPLACE", files=[("new.pdf", pdf_bytes())]
    )
    assert response.status_code == 503 and "PRIVATE AWS DETAIL" not in response.text
    assert aws.objects == before
    aws.fail = None
    assert (
        send(
            client,
            report,
            token,
            action="TOWER_PHOTO_ADD",
            files=[(f"photo{i}.png", image_bytes()) for i in range(5)],
        ).status_code
        == 200
    )
    before = dict(aws.objects)
    assert (
        send(
            client,
            report,
            token,
            action="TOWER_PHOTO_ADD",
            files=[("sixth.png", image_bytes())],
        ).status_code
        == 422
    )
    assert aws.objects == before
    path = f"/ecr/packages/{row.package_id}/attachments/{row.id}"
    aws.fail = "denied"
    response = client.get(path)
    assert response.status_code == 404 and "PRIVATE AWS DETAIL" not in response.text
    aws.calls.clear()
    client.cookies.clear()
    assert client.get(path).status_code in (303, 401, 403) and not aws.calls


def test_s3_real_signature_route_validation_and_authorization(
    client, db_session, user_factory, monkeypatch
):
    aws = FakeS3()
    monkeypatch.setattr("app.storage.s3.s3_client", lambda region: aws)
    for key in ("storage_backend", "s3_bucket", "s3_region", "s3_prefix"):
        setattr(client.app.state.settings, key, getattr(settings(), key))
    owner = user_factory()
    report = _create_report(db_session, owner)
    login(client, owner.employee_id)
    token = csrf_from(client.get(f"/ecr/reports/{report.id}/edit").text)
    path = f"/ecr/reports/{report.id}/signature"
    response = sign(client, report, token)
    assert response.status_code == 200, response.text
    assert all(key.startswith("training/ecr/signatures/") for key in aws.objects)
    assert client.get(path).status_code == 200
    intruder = user_factory()
    client.cookies.clear()
    login(client, intruder.employee_id)
    aws.calls.clear()
    assert client.get(path).status_code in (403, 404) and not aws.calls
    client.cookies.clear()
    login(client, owner.employee_id)
    aws.objects.clear()
    assert client.get(path).status_code == 404


def test_browser_print_reads_s3_customer_sign_through_storage_factory(
    client, db_session, user_factory, monkeypatch
):
    from tests.test_phase5 import complete_report

    aws = FakeS3()
    monkeypatch.setattr("app.storage.s3.s3_client", lambda region: aws)
    for key in ("storage_backend", "s3_bucket", "s3_region", "s3_prefix"):
        setattr(client.app.state.settings, key, getattr(settings(), key))
    owner = user_factory()
    report = complete_report(db_session, _create_report(db_session, owner))
    login(client, owner.employee_id)
    token = csrf_from(client.get(f"/ecr/reports/{report.id}/edit").text)
    assert sign(client, report, token).status_code == 200
    storage_key = report.page3.customer_signature_storage_key
    before = (report.status, report.submitted_at, report.approved_at)
    aws.calls.clear()
    response = client.get(f"/ecr/reports/{report.id}/print")
    assert response.status_code == 200, response.text
    assert response.text.count('class="customer-sign"') == 1
    assert "data:image/png;base64," in response.text
    assert (
        storage_key not in response.text
        and "training/ecr/signatures/" not in response.text
    )
    assert [fields["Key"] for op, fields in aws.calls if op == "get"] == [
        f"training/ecr/signatures/{storage_key}"
    ]
    assert all(body.closed for body in aws.readers)
    db_session.refresh(report)
    assert before == (report.status, report.submitted_at, report.approved_at)
    aws.objects.clear()
    assert client.get(f"/ecr/reports/{report.id}/print").status_code == 503
