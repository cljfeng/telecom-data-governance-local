import os
from pathlib import Path

import pytest

from governance_app.adapters.object_file_storage import ObjectFileStorage
from governance_app.adapters.postgres_database import PostgresDatabase
from governance_app.config import AppConfig
from governance_app.db import initialize_database
from governance_app.identity_store import IdentityStore
from governance_app.online_migration import migrate_sqlite_to_postgres
from governance_app.request_context import principal_context
from governance_app.workflow import create_batch


@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_live_postgres_transaction_and_s3_object_round_trip(tmp_path: Path):
    boto3 = pytest.importorskip("boto3")
    postgres_url = os.environ["TEST_POSTGRES_URL"]
    bucket = os.environ["TEST_OBJECT_STORAGE_BUCKET"]
    endpoint = os.environ["TEST_OBJECT_STORAGE_ENDPOINT"]
    region = os.environ.get("TEST_OBJECT_STORAGE_REGION", "us-east-1")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
    )
    try:
        client.create_bucket(Bucket=bucket)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    config = AppConfig.for_online_workspace(
        tmp_path,
        database_url=postgres_url,
        object_store_bucket=bucket,
        object_store_endpoint=endpoint,
        object_store_region=region,
    )
    local_config = AppConfig.for_workspace(tmp_path / "local-source")
    initialize_database(local_config)
    source_batch_id = create_batch(local_config, "迁移演练批次")
    migration = migrate_sqlite_to_postgres(
        local_config.database_path,
        postgres_url,
    )
    database = PostgresDatabase(postgres_url)
    database.initialize()
    with database.unit_of_work() as unit_of_work:
        assert unit_of_work.batches.get(source_batch_id)["name"] == "迁移演练批次"
        batch_id = unit_of_work.batches.create(
            name="integration",
            batch_code="INTEGRATION",
        )
    with pytest.raises(RuntimeError, match="rollback"):
        with database.unit_of_work() as unit_of_work:
            unit_of_work.batches.create(
                name="rollback",
                batch_code="ROLLBACK",
            )
            raise RuntimeError("rollback")
    identity = IdentityStore(config)
    identity.initialize()
    identity.bootstrap(
        username="integration-admin",
        password="integration-admin-password",
    )
    grant = identity.authenticate(
        username="integration-admin",
        password="integration-admin-password",
        source_ip="127.0.0.1",
        user_agent="pytest",
        ttl_seconds=3600,
    )
    storage = ObjectFileStorage(
        bucket=bucket,
        staging_dir=tmp_path / "staging",
        endpoint_url=endpoint,
        region_name=region,
        client=client,
    )

    with principal_context(grant.principal):
        uploaded = storage.save_upload("integration.xlsx", b"integration")
        resolved = storage.resolve(uploaded.file_id)

    assert resolved.local_path.read_bytes() == b"integration"
    assert migration.source_counts == migration.target_counts
    with database.unit_of_work() as unit_of_work:
        assert unit_of_work.batches.get(batch_id)["name"] == "integration"
