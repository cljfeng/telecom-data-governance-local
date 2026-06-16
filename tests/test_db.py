from governance_app.db import connect, initialize_database, table_names


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

    assert row["version"] == 1
