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
