import sqlite3
from contextlib import contextmanager
from typing import Iterator

from governance_app.config import AppConfig


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
            """
        )


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {row["name"] for row in rows}
