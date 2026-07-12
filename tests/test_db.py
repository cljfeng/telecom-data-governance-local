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


def test_initialize_database_creates_analysis_review_schema(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        opportunity_columns = {row["name"] for row in conn.execute("pragma table_info(analysis_opportunities)")}
        review_columns = {row["name"] for row in conn.execute("pragma table_info(analysis_opportunity_reviews)")}
        review_indexes = {row["name"] for row in conn.execute("pragma index_list(analysis_opportunity_reviews)")}
    assert "source_issue_code" in opportunity_columns
    assert {
        "batch_id", "domain", "opportunity_code", "opportunity_type", "source_issue_code",
        "estimated_recoverable_amount", "estimated_saving_amount",
        "verified_recoverable_amount", "realized_saving_amount", "review_note",
        "created_at", "updated_at",
    }.issubset(review_columns)
    assert {"idx_analysis_reviews_batch_domain", "idx_analysis_reviews_source_issue"}.issubset(review_indexes)


def test_version_two_upgrade_preserves_existing_opportunity(app_config):
    app_config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(app_config.database_path)
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, MIGRATIONS[:2])
    conn.execute("insert into import_batches(id, source_file, status) values (1, 'legacy.xlsx', 'audited')")
    conn.execute(
        """insert into analysis_opportunities(
               batch_id, domain, opportunity_code, opportunity_type, severity, confidence,
               source_rule_ids_json, message, suggestion
           ) values (1, 'electricity', 'legacy-opp', '高电价', 'high', 'high', '[]', 'm', 's')"""
    )
    conn.commit()
    conn.close()
    initialize_database(app_config)
    with connect(app_config) as upgraded:
        row = upgraded.execute(
            "select opportunity_code, source_issue_code from analysis_opportunities where opportunity_code = 'legacy-opp'"
        ).fetchone()
    assert dict(row) == {"opportunity_code": "legacy-opp", "source_issue_code": None}


def test_analysis_review_amounts_must_be_nonnegative(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        conn.execute("pragma foreign_keys = off")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                """insert into analysis_opportunity_reviews(
                       batch_id, domain, opportunity_code, opportunity_type, source_issue_code,
                       verified_recoverable_amount
                   ) values (1, 'electricity', 'opp', '高电价', 'issue', -1)"""
            )


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


def test_apply_migrations_accepts_standard_sqlite_connection():
    conn = sqlite3.connect(":memory:")

    apply_migrations(conn)

    version = conn.execute("select max(version) from schema_migrations").fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION


def test_initialize_database_rejects_newer_schema(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        conn.execute("insert into schema_migrations(version) values (?)", (SCHEMA_VERSION + 1,))

    with pytest.raises(RuntimeError, match="高于应用支持版本"):
        initialize_database(app_config)
