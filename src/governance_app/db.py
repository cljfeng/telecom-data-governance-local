import sqlite3
from contextlib import contextmanager
from typing import Iterator

from governance_app.config import AppConfig

SCHEMA_VERSION = 1


@contextmanager
def connect(config: AppConfig) -> Iterator[sqlite3.Connection]:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(config: AppConfig) -> None:
    with connect(config) as conn:
        conn.executescript(
            """
            create table if not exists import_batches (
                id integer primary key autoincrement,
                source_file text not null,
                template_version text not null default '2026-05-05',
                created_at text not null default current_timestamp,
                status text not null default 'imported'
            );

            create table if not exists raw_rows (
                id integer primary key autoincrement,
                batch_id integer not null references import_batches(id) on delete cascade,
                ledger_type text not null,
                sheet_name text not null,
                row_number integer not null,
                row_json text not null
            );

            create table if not exists ledger_rows (
                id integer primary key autoincrement,
                batch_id integer not null references import_batches(id) on delete cascade,
                ledger_type text not null,
                city text,
                district text,
                telecom_site_code text,
                telecom_site_name text,
                tower_site_code text,
                tower_site_name text,
                raw_row_id integer references raw_rows(id) on delete cascade,
                row_json text not null
            );

            create table if not exists audit_runs (
                id integer primary key autoincrement,
                batch_id integer not null references import_batches(id) on delete cascade,
                created_at text not null default current_timestamp,
                rule_count integer not null
            );

            create table if not exists audit_results (
                id integer primary key autoincrement,
                audit_run_id integer not null references audit_runs(id) on delete cascade,
                ledger_row_id integer references ledger_rows(id) on delete cascade,
                rule_id text not null,
                severity text not null,
                message text not null,
                field_name text,
                result_json text not null
            );

            create table if not exists issues (
                id integer primary key autoincrement,
                issue_code text not null unique,
                audit_result_id integer not null references audit_results(id) on delete cascade,
                batch_id integer not null references import_batches(id) on delete cascade,
                city text,
                district text,
                telecom_site_code text,
                telecom_site_name text,
                ledger_type text not null,
                rule_id text not null,
                severity text not null,
                status text not null default 'pending_export',
                message text not null,
                suggestion text not null,
                correction_value text,
                correction_note text,
                updated_at text not null default current_timestamp
            );

            create table if not exists analysis_opportunities (
                id integer primary key autoincrement,
                batch_id integer not null references import_batches(id) on delete cascade,
                ledger_row_id integer references ledger_rows(id) on delete cascade,
                domain text not null,
                opportunity_code text not null unique,
                opportunity_type text not null,
                severity text not null,
                city text,
                district text,
                telecom_site_code text,
                telecom_site_name text,
                period text,
                meter_no text,
                current_amount real not null default 0,
                reference_amount real not null default 0,
                recoverable_amount real not null default 0,
                saving_opportunity_amount real not null default 0,
                confidence text not null,
                source_rule_ids_json text not null default '[]',
                message text not null,
                suggestion text not null,
                created_at text not null default current_timestamp
            );

            create table if not exists correction_returns (
                id integer primary key autoincrement,
                source_file text not null,
                imported_at text not null default current_timestamp,
                matched_count integer not null,
                error_count integer not null,
                errors_json text not null
            );

            create table if not exists settings (
                key text primary key,
                value_json text not null
            );

            create table if not exists operation_logs (
                id integer primary key autoincrement,
                batch_id integer references import_batches(id) on delete cascade,
                operation text not null,
                message text not null,
                created_at text not null default current_timestamp
            );

            create table if not exists recent_files (
                path text primary key,
                kind text not null,
                ok integer not null,
                ledger_counts_json text not null,
                error_count integer not null,
                last_used_at text not null default current_timestamp
            );

            create table if not exists audit_rule_settings (
                rule_id text primary key,
                enabled integer not null default 1,
                config_json text not null default '{}',
                updated_at text not null default current_timestamp
            );

            create table if not exists schema_migrations (
                version integer primary key,
                applied_at text not null default current_timestamp
            );

            create index if not exists idx_ledger_rows_batch_type_city_site
                on ledger_rows(batch_id, ledger_type, city, telecom_site_code);

            create index if not exists idx_issues_batch_city_status_rule
                on issues(batch_id, city, status, rule_id);

            create index if not exists idx_analysis_opportunities_batch_domain_type
                on analysis_opportunities(batch_id, domain, opportunity_type);

            create index if not exists idx_analysis_opportunities_batch_city
                on analysis_opportunities(batch_id, city);
            """
        )
        _ensure_column(conn, "import_batches", "name", "text")
        _ensure_column(conn, "import_batches", "batch_code", "text")
        _ensure_column(conn, "import_batches", "is_archived", "integer not null default 0")
        _ensure_column(conn, "import_batches", "archived_at", "text")
        _ensure_column(conn, "ledger_rows", "sheet_name", "text")
        _ensure_column(conn, "ledger_rows", "row_number", "integer")
        _ensure_column(conn, "ledger_rows", "raw_row_id", "integer references raw_rows(id) on delete cascade")
        _ensure_column(conn, "correction_returns", "warning_count", "integer not null default 0")
        _ensure_column(conn, "correction_returns", "warnings_json", "text not null default '[]'")
        conn.execute("insert or ignore into schema_migrations(version) values (?)", (SCHEMA_VERSION,))


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"pragma table_info({table_name})")}
    if column_name not in columns:
        conn.execute(f"alter table {table_name} add column {column_name} {definition}")
