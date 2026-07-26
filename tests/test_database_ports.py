import pytest

from governance_app.adapters.sqlite_database import SqliteDatabase
from governance_app.db import initialize_database
from governance_app.workflow import (
    create_batch,
    list_batches,
    set_current_batch,
    transition_batch,
)


def test_sqlite_database_commits_batch_unit_of_work(app_config):
    initialize_database(app_config)
    database = SqliteDatabase(app_config.database_path)

    try:
        batch_id = create_batch(app_config, "接口迁移批次", database=database)
        transition_batch(app_config, batch_id, "import", database=database)

        batches = list_batches(app_config, database=database)
    finally:
        database.dispose()

    assert batches == [
        {
            "id": batch_id,
            "name": "接口迁移批次",
            "batch_code": batches[0]["batch_code"],
            "source_file": "",
            "template_version": "2026-05-05",
            "created_at": batches[0]["created_at"],
            "status": "imported",
            "is_archived": False,
            "archived_at": None,
            "is_current": True,
        }
    ]


def test_sqlite_database_rolls_back_failed_unit_of_work(app_config):
    initialize_database(app_config)
    database = SqliteDatabase(app_config.database_path)

    try:
        with pytest.raises(RuntimeError, match="force rollback"):
            with database.unit_of_work() as unit_of_work:
                unit_of_work.batches.create(name="不会提交", batch_code="rollback")
                raise RuntimeError("force rollback")

        assert list_batches(app_config, database=database) == []
    finally:
        database.dispose()


def test_batch_services_reject_unknown_batch_through_database_port(app_config):
    initialize_database(app_config)
    database = SqliteDatabase(app_config.database_path)

    try:
        with pytest.raises(ValueError, match="batch not found"):
            set_current_batch(app_config, 999, database=database)
        with pytest.raises(ValueError, match="batch not found"):
            transition_batch(app_config, 999, "import", database=database)
    finally:
        database.dispose()
