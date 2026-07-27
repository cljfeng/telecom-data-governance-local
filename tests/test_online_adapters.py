from io import BytesIO

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from governance_app.adapters.object_file_storage import (
    ObjectFileStorage,
)
from governance_app.adapters.postgres_database import (
    PostgresDatabase,
    _psycopg_url,
)
from governance_app.adapters.sqlite_database import (
    _import_batches,
    _metadata,
)


class FakeObjectClient:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, Metadata):
        self.objects[(Bucket, Key)] = (bytes(Body), dict(Metadata))

    def head_object(self, *, Bucket, Key):
        content, metadata = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(content),
            "Metadata": metadata,
        }

    def get_object(self, *, Bucket, Key):
        content, _metadata = self.objects[(Bucket, Key)]
        return {"Body": BytesIO(content)}

    def list_objects_v2(
        self,
        *,
        Bucket,
        Prefix,
        ContinuationToken=None,
    ):
        del ContinuationToken
        return {
            "Contents": [
                {"Key": key}
                for bucket, key in self.objects
                if bucket == Bucket and key.startswith(Prefix)
            ],
            "IsTruncated": False,
        }

    def delete_objects(self, *, Bucket, Delete):
        for item in Delete["Objects"]:
            self.objects.pop((Bucket, item["Key"]))


def _storage(tmp_path, client=None):
    return ObjectFileStorage(
        bucket="governance-files",
        staging_dir=tmp_path / "staging",
        prefix="tenant",
        client=client or FakeObjectClient(),
    )


def test_object_storage_upload_resolve_publish_and_clear(tmp_path):
    client = FakeObjectClient()
    storage = _storage(tmp_path, client)

    upload = storage.save_upload("../台账.xlsx", b"workbook")
    resolved = storage.resolve(upload.file_id)
    export_path = storage.prepare_export("reports/result.xlsx")
    export_path.write_bytes(b"report")
    report = storage.publish(export_path)

    assert upload.file_id.startswith("uploads:")
    assert resolved.local_path.read_bytes() == b"workbook"
    assert upload.url.startswith("/api/files/")
    assert report.file_id == "exports:reports/result.xlsx"
    assert storage.resolve(report.file_id).local_path.read_bytes() == b"report"
    assert storage.clear(
        "uploads",
        keep_file_ids={upload.file_id},
    ) == 0
    assert storage.clear("exports") == 1


def test_object_storage_rejects_traversal_and_checksum_mismatch(tmp_path):
    client = FakeObjectClient()
    storage = _storage(tmp_path, client)
    uploaded = storage.save_upload("台账.xlsx", b"workbook")

    with pytest.raises(ValueError, match="文件引用无效"):
        storage.prepare_export("../outside.xlsx")

    key = next(key for key in client.objects if key[1].startswith("tenant/uploads/"))
    _content, metadata = client.objects[key]
    client.objects[key] = (b"tampered", metadata)
    with pytest.raises(ValueError, match="校验失败"):
        storage.resolve(uploaded.file_id)


def test_postgres_adapter_reuses_transactional_repository_contract():
    engine = create_engine(
        "sqlite+pysqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _metadata.create_all(engine)
    database = PostgresDatabase(
        "postgresql://unused/test",
        engine=engine,
    )

    with database.unit_of_work() as unit_of_work:
        batch_id = unit_of_work.batches.create(
            name="online",
            batch_code="ONLINE-1",
        )

    with pytest.raises(RuntimeError, match="rollback"):
        with database.unit_of_work() as unit_of_work:
            unit_of_work.batches.create(
                name="rolled back",
                batch_code="ONLINE-2",
            )
            raise RuntimeError("rollback")

    with engine.connect() as connection:
        rows = connection.execute(
            select(_import_batches.c.id, _import_batches.c.name)
        ).all()

    assert rows == [(batch_id, "online")]
    assert _psycopg_url("postgresql://db/app").startswith(
        "postgresql+psycopg://"
    )
