import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    apply: Callable[[sqlite3.Connection], None]


SCHEMA_VERSION = 9


def current_schema_version(conn: sqlite3.Connection) -> int:
    table = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
    ).fetchone()
    if table is None:
        return 0
    row = conn.execute("select max(version) as version from schema_migrations").fetchone()
    return int(row[0] or 0)


def apply_migrations(
    conn: sqlite3.Connection,
    migrations: tuple[Migration, ...] | None = None,
) -> None:
    selected = MIGRATIONS if migrations is None else migrations
    if not selected:
        return
    current = current_schema_version(conn)
    latest = selected[-1].version
    if current > latest:
        raise RuntimeError(f"数据库版本 {current} 高于应用支持版本 {latest}，请使用更新版本的程序")
    for migration in selected:
        if migration.version <= current:
            continue
        try:
            conn.execute("begin")
            migration.apply(conn)
            conn.execute("insert into schema_migrations(version) values (?)", (migration.version,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        current = migration.version


def _create_version_1_schema(conn: sqlite3.Connection) -> None:
    _execute_script(
        conn,
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
        create table if not exists settings (key text primary key, value_json text not null);
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
        """,
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


def _upgrade_to_version_2(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "issues", "resolved_at", "text")
    _ensure_column(conn, "issues", "last_seen_audit_run_id", "integer references audit_runs(id)")
    _execute_script(
        conn,
        """
        create table if not exists issue_events (
            id integer primary key autoincrement,
            issue_id integer not null references issues(id) on delete cascade,
            from_status text,
            to_status text not null,
            source text not null,
            note text,
            created_at text not null default current_timestamp
        );
        create index if not exists idx_issue_events_issue_created
            on issue_events(issue_id, created_at);
        create index if not exists idx_issues_batch_status
            on issues(batch_id, status);
        """,
    )


def _upgrade_to_version_3(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "analysis_opportunities", "source_issue_code", "text")
    _execute_script(
        conn,
        """
        create index if not exists idx_analysis_opportunities_source_issue
            on analysis_opportunities(source_issue_code);
        create table if not exists analysis_opportunity_reviews (
            id integer primary key autoincrement,
            batch_id integer not null references import_batches(id) on delete cascade,
            domain text not null,
            opportunity_code text not null unique,
            opportunity_type text not null,
            source_issue_code text not null references issues(issue_code) on delete cascade,
            estimated_recoverable_amount real not null default 0 check (estimated_recoverable_amount >= 0),
            estimated_saving_amount real not null default 0 check (estimated_saving_amount >= 0),
            verified_recoverable_amount real check (verified_recoverable_amount is null or verified_recoverable_amount >= 0),
            realized_saving_amount real check (realized_saving_amount is null or realized_saving_amount >= 0),
            review_note text,
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );
        create index if not exists idx_analysis_reviews_batch_domain
            on analysis_opportunity_reviews(batch_id, domain);
        create index if not exists idx_analysis_reviews_source_issue
            on analysis_opportunity_reviews(source_issue_code);
        """,
    )


def _upgrade_to_version_4(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "recent_files", "organization_id", "integer")
    _ensure_column(conn, "operation_logs", "user_id", "integer")
    _ensure_column(conn, "operation_logs", "organization_id", "integer")
    _ensure_column(conn, "operation_logs", "request_id", "text")
    _ensure_column(conn, "operation_logs", "source_ip", "text")
    _ensure_column(conn, "operation_logs", "task_id", "integer")
    _execute_script(
        conn,
        """
        create table if not exists organizations (
            id integer primary key autoincrement,
            parent_id integer references organizations(id),
            code text not null unique,
            name text not null,
            domain_path text not null,
            active integer not null default 1,
            created_at integer not null
        );
        create table if not exists users (
            id integer primary key autoincrement,
            organization_id integer not null references organizations(id),
            username text not null unique,
            display_name text not null,
            password_hash text not null,
            active integer not null default 1,
            failed_attempts integer not null default 0,
            locked_until integer,
            created_at integer not null
        );
        create table if not exists roles (
            id integer primary key autoincrement,
            code text not null unique,
            name text not null,
            data_scope text not null
        );
        create table if not exists role_permissions (
            role_id integer not null references roles(id) on delete cascade,
            permission text not null,
            primary key(role_id, permission)
        );
        create table if not exists user_roles (
            user_id integer not null references users(id) on delete cascade,
            role_id integer not null references roles(id) on delete cascade,
            primary key(user_id, role_id)
        );
        create table if not exists sessions (
            id integer primary key autoincrement,
            user_id integer not null references users(id) on delete cascade,
            token_hash text not null unique,
            csrf_hash text not null,
            expires_at integer not null,
            last_seen_at integer not null,
            source_ip text not null,
            user_agent text not null,
            created_at integer not null
        );
        create table if not exists batch_organizations (
            batch_id integer primary key references import_batches(id) on delete cascade,
            organization_id integer not null references organizations(id),
            created_by integer not null references users(id),
            created_at integer not null
        );
        create table if not exists request_audit_logs (
            id integer primary key autoincrement,
            request_id text not null unique,
            user_id integer,
            organization_id integer,
            method text not null,
            path text not null,
            status integer not null,
            source_ip text not null,
            user_agent text not null,
            task_id integer,
            duration_ms integer not null,
            created_at integer not null
        );
        create table if not exists background_tasks (
            id integer primary key autoincrement,
            organization_id integer not null references organizations(id),
            user_id integer not null references users(id),
            kind text not null,
            status text not null,
            payload_json text not null,
            result_json text,
            error text,
            progress integer not null default 0,
            attempts integer not null default 0,
            max_attempts integer not null default 3,
            idempotency_key text not null,
            created_at integer not null,
            started_at integer,
            finished_at integer,
            unique(organization_id, kind, idempotency_key)
        );
        create index if not exists idx_users_organization
            on users(organization_id, active);
        create index if not exists idx_sessions_expiry
            on sessions(expires_at);
        create index if not exists idx_request_audit_created
            on request_audit_logs(created_at);
        create index if not exists idx_tasks_org_status
            on background_tasks(organization_id, status, created_at);
        create index if not exists idx_recent_files_organization
            on recent_files(organization_id, last_used_at);
        """,
    )


def _execute_script(conn: sqlite3.Connection, script: str) -> None:
    statement = ""
    for line in script.splitlines():
        statement += f"{line}\n"
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            if sql:
                conn.execute(sql)
            statement = ""
    if statement.strip():
        raise ValueError("incomplete migration statement")


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"pragma table_info({table_name})")}
    if column_name not in columns:
        conn.execute(f"alter table {table_name} add column {column_name} {definition}")


def _upgrade_to_version_5(conn: sqlite3.Connection) -> None:
    conn.execute("""create table if not exists site_jurisdiction_events (
        id integer primary key autoincrement,
        ledger_row_id integer not null references ledger_rows(id) on delete cascade,
        batch_id integer not null, old_city text, old_district text,
        new_city text not null, new_district text not null,
        reason text not null, actor_user_id integer not null,
        created_at text not null default current_timestamp
    )""")
    conn.execute("""create table if not exists site_evidence_files (
        id integer primary key autoincrement,
        ledger_row_id integer not null references ledger_rows(id) on delete cascade,
        file_id text not null, actor_user_id integer not null,
        created_at text not null default current_timestamp
    )""")


def _upgrade_to_version_6(conn: sqlite3.Connection) -> None:
    conn.execute("""create table authoritative_sites (
        id integer primary key autoincrement,
        site_code text not null unique,
        current_json text not null,
        current_version integer not null default 0
    )""")
    conn.execute("""create table authoritative_site_sources (
        ledger_row_id integer primary key references ledger_rows(id) on delete cascade,
        site_id integer not null references authoritative_sites(id)
    )""")
    conn.execute("""create table authoritative_site_versions (
        id integer primary key autoincrement,
        site_id integer not null references authoritative_sites(id),
        version integer not null,
        old_json text not null, new_json text not null,
        evidence text not null, operator text not null,
        error_cause text not null, source text not null,
        idempotency_key text not null, request_json text not null,
        effective_at text not null default current_timestamp,
        unique(site_id, version), unique(site_id, idempotency_key)
    )""")
    rows = conn.execute("""select ledger_rows.id, ledger_rows.batch_id,
        ledger_rows.telecom_site_code, ledger_rows.city, ledger_rows.district,
        case when ledger_rows.row_json != '{}' then ledger_rows.row_json
             else coalesce(raw_rows.row_json, ledger_rows.row_json) end
        from ledger_rows left join raw_rows on raw_rows.id = ledger_rows.raw_row_id
        where ledger_rows.ledger_type = 'site' and ledger_rows.telecom_site_code is not null
        order by ledger_rows.id""").fetchall()
    grouped: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        code = str(row[2]).strip()
        if code:
            grouped[code].append(tuple(row))
    for code, group in grouped.items():
        locations = {(row[3], row[4]) for row in group}
        batches = [row[1] for row in group]
        if len(locations) != 1 or len(batches) != len(set(batches)):
            continue  # Ambiguous old records remain source-only until reconciled.
        result = conn.execute("insert into authoritative_sites(site_code, current_json) values (?, ?)",
                              (code, group[-1][5]))
        conn.executemany("insert into authoritative_site_sources(ledger_row_id, site_id) values (?, ?)",
                         [(row[0], result.lastrowid) for row in group])


def _upgrade_to_version_7(conn: sqlite3.Connection) -> None:
    rows = conn.execute("""select id, batch_id, telecom_site_code
        from ledger_rows where ledger_type != 'site'""").fetchall()
    for row_id, batch_id, site_code in rows:
        sites = conn.execute("""select id, city, district from ledger_rows
            where batch_id = ? and ledger_type = 'site'
              and telecom_site_code = ?""", (batch_id, site_code)).fetchall()
        city, district = (None, None)
        if len(sites) == 1:
            site_row_id, source_city, source_district = sites[0]
            override = conn.execute("""select 1 from site_jurisdiction_events
                where ledger_row_id = ? limit 1""", (site_row_id,)).fetchone()
            if override is not None:
                city, district = source_city, source_district
            else:
                authority = conn.execute("""select authoritative_sites.current_json
                    from authoritative_site_sources join authoritative_sites
                      on authoritative_site_sources.site_id = authoritative_sites.id
                    where authoritative_site_sources.ledger_row_id = ?""",
                    (site_row_id,)).fetchone()
                if authority is not None:
                    current = json.loads(authority[0])
                    city, district = current.get("地市"), current.get("区县")
            if not city or not district:
                city, district = (None, None)
        conn.execute(
            "update ledger_rows set city = ?, district = ? where id = ?",
            (city, district, row_id),
        )
        conn.execute("""update issues set city = ?, district = ?
            where audit_result_id in (
                select id from audit_results where ledger_row_id = ?
            )""", (city, district, row_id))


def _upgrade_to_version_8(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "authoritative_site_versions", "confirmer", "text")
    conn.execute("""create table if not exists site_change_requests (
        id integer primary key autoincrement,
        batch_id integer not null references import_batches(id) on delete cascade,
        ledger_row_id integer not null references ledger_rows(id) on delete cascade,
        issue_code text,
        replaces_request_id integer references site_change_requests(id),
        kind text not null, changes_json text not null, evidence text not null,
        error_cause text not null, source text not null, note text not null,
        proposer_user_id integer not null, proposer_organization_id integer not null,
        proposer_username text not null, reviewer_organization_id integer,
        reviewer_user_id integer, reviewer_username text, status text not null,
        review_note text, applied_version integer, idempotency_key text not null,
        request_json text not null, created_at text not null default current_timestamp,
        updated_at text not null default current_timestamp,
        unique(proposer_user_id, idempotency_key)
    )""")


def _upgrade_to_version_9(conn: sqlite3.Connection) -> None:
    conn.execute("""create table if not exists authoritative_tower_rents (
        id integer primary key autoincrement, business_key text not null unique,
        current_json text not null, current_version integer not null default 0
    )""")
    conn.execute("""create table if not exists authoritative_tower_rent_sources (
        ledger_row_id integer primary key references ledger_rows(id) on delete cascade,
        rent_id integer not null references authoritative_tower_rents(id),
        frozen_json text, frozen_version integer
    )""")
    _ensure_column(conn, "authoritative_tower_rent_sources", "frozen_json", "text")
    _ensure_column(conn, "authoritative_tower_rent_sources", "frozen_version", "integer")
    conn.execute("""create table if not exists authoritative_tower_rent_versions (
        id integer primary key autoincrement,
        rent_id integer not null references authoritative_tower_rents(id),
        version integer not null, old_json text not null, new_json text not null,
        evidence text not null, operator text not null, confirmer text,
        error_cause text not null, source text not null, idempotency_key text not null,
        request_json text not null, effective_at text not null default current_timestamp,
        unique(rent_id, version), unique(rent_id, idempotency_key)
    )""")
    conn.execute("""create table if not exists tower_rent_change_requests (
        id integer primary key autoincrement,
        batch_id integer not null references import_batches(id) on delete cascade,
        ledger_row_id integer not null references ledger_rows(id) on delete cascade,
        replaces_request_id integer references tower_rent_change_requests(id),
        changes_json text not null, evidence text not null, error_cause text not null,
        source text not null, note text not null, proposer_user_id integer not null,
        proposer_organization_id integer not null, proposer_username text not null,
        reviewer_organization_id integer not null, reviewer_user_id integer,
        reviewer_username text, status text not null, review_note text,
        applied_version integer, idempotency_key text not null, request_json text not null,
        created_at text not null default current_timestamp,
        updated_at text not null default current_timestamp,
        unique(proposer_user_id, idempotency_key)
    )""")
    from governance_app.tower_rent_identity import business_key
    rows = conn.execute("""select ledger_rows.id, ledger_rows.batch_id,
        raw_rows.row_json from ledger_rows join raw_rows on raw_rows.id = ledger_rows.raw_row_id
        where ledger_rows.ledger_type = 'tower_rent' order by ledger_rows.id""").fetchall()
    keys = [(row[0], row[1], business_key(json.loads(row[2])) or f"row:{row[0]}") for row in rows]
    counts: dict[tuple[int, str], int] = {}
    values_by_key: dict[str, set[str]] = {}
    for row, (_, _, key) in zip(rows, keys, strict=True):
        values_by_key.setdefault(key, set()).add(json.dumps(json.loads(row[2]),
                                                    ensure_ascii=False, sort_keys=True))
    for _, batch_id, key in keys:
        counts[(batch_id, key)] = counts.get((batch_id, key), 0) + 1
    for row, (_, batch_id, key) in zip(rows, keys, strict=True):
        if counts[(batch_id, key)] > 1 or len(values_by_key[key]) > 1:
            key = f"row:{row[0]}"
        conn.execute("""insert or ignore into authoritative_tower_rents(business_key, current_json)
            values (?, ?)""", (key, row[2]))
        rent_id = conn.execute("select id from authoritative_tower_rents where business_key = ?", (key,)).fetchone()[0]
        conn.execute("insert or ignore into authoritative_tower_rent_sources(ledger_row_id, rent_id) values (?, ?)",
                     (row[0], rent_id))
    conn.execute("""update authoritative_tower_rent_sources
        set frozen_json = (select raw_rows.row_json from ledger_rows join raw_rows
                           on raw_rows.id = ledger_rows.raw_row_id
                           where ledger_rows.id = authoritative_tower_rent_sources.ledger_row_id),
            frozen_version = 0
        where ledger_row_id in (select ledger_rows.id from ledger_rows join import_batches
                                on import_batches.id = ledger_rows.batch_id
                                where import_batches.is_archived = 1)
          and frozen_json is null""")


MIGRATIONS = (
    Migration(1, _create_version_1_schema),
    Migration(2, _upgrade_to_version_2),
    Migration(3, _upgrade_to_version_3),
    Migration(4, _upgrade_to_version_4),
    Migration(5, _upgrade_to_version_5),
    Migration(6, _upgrade_to_version_6),
    Migration(7, _upgrade_to_version_7),
    Migration(8, _upgrade_to_version_8),
    Migration(9, _upgrade_to_version_9),
)
