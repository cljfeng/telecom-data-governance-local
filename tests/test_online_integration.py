import json
import os
import time
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from governance_app.adapters.object_file_storage import ObjectFileStorage
from governance_app.adapters.postgres_database import PostgresDatabase
from governance_app.audit_engine import run_audit
from governance_app.config import AppConfig
from governance_app.db import initialize_database
from governance_app.identity_store import IdentityStore
from governance_app.importer import import_workbook
from governance_app.online_migration import migrate_sqlite_to_postgres
from governance_app.request_context import principal_context
from governance_app.server import create_app
from governance_app.workflow import create_batch, list_issues


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


@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_province_account_import_audit_export_survives_restart(
    tmp_path: Path,
    sample_workbook: Path,
):
    boto3 = pytest.importorskip("boto3")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["TEST_OBJECT_STORAGE_ENDPOINT"],
        region_name=os.environ.get("TEST_OBJECT_STORAGE_REGION", "us-east-1"),
    )
    bucket = os.environ["TEST_OBJECT_STORAGE_BUCKET"]
    try:
        client.create_bucket(Bucket=bucket)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass

    workbook = load_workbook(sample_workbook)
    workbook["电费台账"][2][7].value = 9.9
    workbook.save(sample_workbook)
    local = AppConfig.for_workspace(tmp_path / "local")
    initialize_database(local)
    local_import = import_workbook(local, sample_workbook)
    assert local_import.batch_id is not None
    expected_count = run_audit(local, local_import.batch_id).issue_count
    expected_rules = sorted(
        issue["rule_id"]
        for issue in list_issues(local, local_import.batch_id)
    )
    assert expected_count > 0
    local_database_digest = sha256(local.database_path.read_bytes()).digest()

    config = AppConfig.for_online_workspace(
        tmp_path / "first-server",
        database_url=os.environ["TEST_POSTGRES_URL"],
        object_store_bucket=bucket,
        object_store_endpoint=os.environ["TEST_OBJECT_STORAGE_ENDPOINT"],
        object_store_region=os.environ.get("TEST_OBJECT_STORAGE_REGION", "us-east-1"),
    )
    initialize_database(config)
    store = IdentityStore(config)
    store.initialize()
    store.bootstrap(username="integration-admin", password="integration-admin-password")
    admin = store.authenticate(
        username="integration-admin",
        password="integration-admin-password",
        source_ip="127.0.0.1",
        user_agent="pytest",
        ttl_seconds=3600,
    ).principal
    unique = uuid4().hex[:10]
    province_id = store.create_organization(code=f"province-{unique}", name="省公司")
    store.create_user(
        actor=admin,
        organization_id=province_id,
        username=f"province-{unique}",
        display_name="省级业务人员",
        password="province-test-password",
        role_codes=["organization_admin"],
    )
    app = create_app(config)
    status, _, body = app.handle_test_request(
        "POST", "/api/auth/login",
        json.dumps({"username": f"province-{unique}", "password": "province-test-password"}),
    )
    assert status == 200
    token = json.loads(body)["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    def request(method: str, path: str, payload: dict | None = None):
        status, _, raw = app.handle_test_request(
            method, path, json.dumps(payload or {}), headers=headers,
        )
        return status, json.loads(raw)

    status, created = request("POST", "/api/batches", {"name": f"全省批次-{unique}"})
    assert status == 200
    batch_id = created["batch_id"]
    boundary = f"test-{unique}"
    upload = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"strategy\"\r\n\r\nappend\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"batch_id\"\r\n\r\n{batch_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sample.xlsx\"\r\n"
        "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode() + sample_workbook.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    status, _, raw = app.handle_test_upload_request(
        "/api/import/upload", f"multipart/form-data; boundary={boundary}", upload,
        headers=headers,
    )
    assert status == 202
    import_task_id = json.loads(raw)["task"]["id"]

    def completed(task_id: int) -> dict:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, response = request("GET", f"/api/tasks/{task_id}")
            assert status == 200
            task = response["task"]
            if task["status"] == "completed":
                return task["result"]
            if task["status"] == "failed":
                pytest.fail(f"online task failed: {task['error']}")
            time.sleep(0.1)
        pytest.fail(f"online task {task_id} did not complete")

    imported = completed(import_task_id)
    assert imported["batch_id"] == batch_id
    assert set(imported["ledger_counts"]) == {"site", "tower_rent", "electricity", "generator"}
    status, queued = request("POST", "/api/audit", {"batch_id": batch_id})
    assert status == 202
    audited = completed(queued["task"]["id"])
    assert audited["issue_count"] == expected_count
    status, issues = request("GET", f"/api/issues?batch_id={batch_id}")
    assert status == 200
    assert sorted(issue["rule_id"] for issue in issues["issues"]) == expected_rules
    status, queued = request("POST", "/api/export", {"batch_id": batch_id})
    assert status == 202
    exported = completed(queued["task"]["id"])
    assert exported["files"]

    restarted = AppConfig.for_online_workspace(
        tmp_path / "second-server",
        database_url=os.environ["TEST_POSTGRES_URL"],
        object_store_bucket=bucket,
        object_store_endpoint=os.environ["TEST_OBJECT_STORAGE_ENDPOINT"],
        object_store_region=os.environ.get("TEST_OBJECT_STORAGE_REGION", "us-east-1"),
    )
    restarted_app = create_app(restarted)
    status, _, raw = restarted_app.handle_test_request(
        "GET", f"/api/issues?batch_id={batch_id}", headers=headers,
    )
    assert status == 200
    assert len(json.loads(raw)["issues"]) == expected_count
    status, _, raw = restarted_app.handle_test_request(
        "GET", f"/api/tasks/{queued['task']['id']}", headers=headers,
    )
    assert status == 200
    assert json.loads(raw)["task"]["status"] == "completed"
    status, _, raw = restarted_app.handle_test_request(
        "GET", "/api/import/recent", headers=headers,
    )
    assert status == 200
    original_file_id = json.loads(raw)["files"][0]["file"]["file_id"]
    status, _, original_bytes = restarted_app.handle_test_request(
        "GET", f"/api/files/{original_file_id}", headers=headers,
    )
    assert status == 200
    assert original_bytes == sample_workbook.read_bytes()
    principal = IdentityStore(restarted).session_principal(token, auth_method="bearer")
    assert principal is not None
    with principal_context(principal):
        stored = ObjectFileStorage(
            bucket=bucket,
            staging_dir=restarted.data_dir / "object-staging",
            endpoint_url=restarted.object_store_endpoint,
            region_name=restarted.object_store_region,
            client=client,
        ).resolve(exported["files"][0]["file_id"])
    assert stored.local_path.read_bytes().startswith(b"PK")
    assert not restarted.database_path.exists()
    assert sha256(local.database_path.read_bytes()).digest() == local_database_digest
