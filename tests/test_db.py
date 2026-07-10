import sqlite3

import pytest

from governance_app.db import SCHEMA_VERSION, connect, initialize_database, table_names
from governance_app.migrations import MIGRATIONS, Migration, apply_migrations


def test_initialize_database_creates_core_tables(app_config):
    initialize_database(app_config)

    with connect(app_config) as conn:
        assert {
            "import_batches",
            "raw_rows",
            "ledger_rows",
            "audit_runs",
            "audit_results",
            "issues",
            "correction_returns",
            "settings",
        }.issubset(table_names(conn))


def test_initialize_database_is_idempotent(app_config):
    initialize_database(app_config)
    initialize_database(app_config)

    with connect(app_config) as conn:
        rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
        assert rows


def test_initialize_database_records_schema_version(app_config):
    initialize_database(app_config)

    with connect(app_config) as conn:
        row = conn.execute(
            "select version from schema_migrations order by version desc limit 1"
        ).fetchone()

    assert row["version"] == SCHEMA_VERSION


def test_initialize_database_applies_current_schema(app_config):
    initialize_database(app_config)

    with connect(app_config) as conn:
        versions = [row["version"] for row in conn.execute("select version from schema_migrations order by version")]
        issue_columns = {row["name"] for row in conn.execute("pragma table_info(issues)")}
        event_columns = {row["name"] for row in conn.execute("pragma table_info(issue_events)")}

    assert versions == list(range(1, SCHEMA_VERSION + 1))
    assert {"resolved_at", "last_seen_audit_run_id"}.issubset(issue_columns)
    assert {"issue_id", "from_status", "to_status", "source", "note", "created_at"}.issubset(event_columns)


def test_initialize_database_creates_analysis_opportunities(app_config):
    initialize_database(app_config)

    with connect(app_config) as conn:
        columns = {row["name"] for row in conn.execute("pragma table_info(analysis_opportunities)")}
        indexes = {row["name"] for row in conn.execute("pragma index_list(analysis_opportunities)")}

    assert {
        "id",
        "batch_id",
        "ledger_row_id",
        "domain",
        "opportunity_code",
        "opportunity_type",
        "severity",
        "city",
        "district",
        "telecom_site_code",
        "telecom_site_name",
        "period",
        "meter_no",
        "current_amount",
        "reference_amount",
        "recoverable_amount",
        "saving_opportunity_amount",
        "confidence",
        "source_rule_ids_json",
        "message",
        "suggestion",
        "created_at",
    }.issubset(columns)
    assert "idx_analysis_opportunities_batch_domain_type" in indexes
    assert "idx_analysis_opportunities_batch_city" in indexes


def test_initialize_database_upgrades_version_one_without_losing_data(app_config):
    app_config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(app_config.database_path)
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, MIGRATIONS[:1])
    conn.execute("insert into import_batches(source_file, name) values ('legacy.xlsx', '历史批次')")
    conn.commit()
    conn.close()

    initialize_database(app_config)

    with connect(app_config) as conn:
        batch = conn.execute("select name from import_batches").fetchone()
        version = conn.execute("select max(version) as version from schema_migrations").fetchone()["version"]
    assert batch["name"] == "历史批次"
    assert version == SCHEMA_VERSION


def test_initialize_database_backs_up_version_one_before_upgrade(app_config):
    app_config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(app_config.database_path)
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, MIGRATIONS[:1])
    conn.close()

    initialize_database(app_config)

    backups = list((app_config.workspace_dir / "backups").glob("*.sqlite3"))
    assert len(backups) == 1


def test_initialize_database_does_not_backup_fresh_database(app_config):
    initialize_database(app_config)

    assert not (app_config.workspace_dir / "backups").exists()


def test_failed_migration_rolls_back_schema_and_version(tmp_path):
    path = tmp_path / "failed.sqlite3"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    def fail(connection):
        connection.execute("create table should_rollback(id integer primary key)")
        raise RuntimeError("migration failed")

    with pytest.raises(RuntimeError, match="migration failed"):
        apply_migrations(conn, (Migration(1, fail),))

    tables = {row["name"] for row in conn.execute("select name from sqlite_master where type = 'table'")}
    conn.close()
    assert "should_rollback" not in tables
    assert "schema_migrations" not in tables


def test_initialize_database_rejects_newer_schema(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        conn.execute("insert into schema_migrations(version) values (?)", (SCHEMA_VERSION + 1,))

    with pytest.raises(RuntimeError, match="高于应用支持版本"):
        initialize_database(app_config)
