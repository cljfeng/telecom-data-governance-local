from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Connection, text

from governance_app.adapters.sqlite_database import _metadata
from governance_app.identity_store import identity_metadata

POSTGRES_SCHEMA_VERSION = 7


@dataclass(frozen=True)
class PostgresMigration:
    version: int
    apply: Callable[[Connection], None]


def apply_postgres_migrations(connection: Connection) -> None:
    connection.execute(
        text(
            """
            create table if not exists schema_migrations (
                version integer primary key,
                applied_at timestamp with time zone not null
                    default current_timestamp
            )
            """
        )
    )
    connection.execute(
        text(
            "select pg_advisory_xact_lock("
            "hashtext('governance-schema-migrations'))"
        )
    )
    current = connection.execute(
        text("select coalesce(max(version), 0) from schema_migrations")
    ).scalar_one()
    if int(current) > POSTGRES_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current} 高于应用支持版本 "
            f"{POSTGRES_SCHEMA_VERSION}，请使用更新版本的程序"
        )
    for migration in POSTGRES_MIGRATIONS:
        if migration.version <= int(current):
            continue
        migration.apply(connection)
        connection.execute(
            text(
                "insert into schema_migrations(version) "
                "values (:version)"
            ),
            {"version": migration.version},
        )


def _create_initial_schema(connection: Connection) -> None:
    _metadata.create_all(connection)


def _add_identity_and_runtime_schema(connection: Connection) -> None:
    identity_metadata.create_all(connection)
    for statement in (
        "alter table recent_files add column if not exists "
        "organization_id integer",
        "alter table operation_logs add column if not exists "
        "user_id integer",
        "alter table operation_logs add column if not exists "
        "organization_id integer",
        "alter table operation_logs add column if not exists "
        "request_id varchar",
        "alter table operation_logs add column if not exists "
        "source_ip varchar",
        "alter table operation_logs add column if not exists "
        "task_id integer",
        "create index if not exists idx_recent_files_organization "
        "on recent_files(organization_id, last_used_at)",
    ):
        connection.execute(text(statement))


def _add_site_jurisdiction_events(connection: Connection) -> None:
    _metadata.tables["site_jurisdiction_events"].create(connection, checkfirst=True)
    _metadata.tables["site_evidence_files"].create(connection, checkfirst=True)


def _add_authoritative_site_schema(connection: Connection) -> None:
    for name in ("authoritative_sites", "authoritative_site_sources", "authoritative_site_versions"):
        _metadata.tables[name].create(connection, checkfirst=True)


def _backfill_related_ledger_jurisdiction(connection: Connection) -> None:
    connection.execute(text("""
        with site_scope as (
            select batch_id, telecom_site_code, count(*) as site_count,
                   min(id) as site_row_id, min(city) as source_city,
                   min(district) as source_district
            from ledger_rows
            where ledger_type = 'site'
            group by batch_id, telecom_site_code
        ), effective_scope as (
            select site_scope.*,
                   case when exists (
                            select 1 from site_jurisdiction_events
                            where ledger_row_id = site_scope.site_row_id
                        ) then site_scope.source_city
                        else authoritative_sites.current_json::jsonb ->> '地市' end as city,
                   case when exists (
                            select 1 from site_jurisdiction_events
                            where ledger_row_id = site_scope.site_row_id
                        ) then site_scope.source_district
                        else authoritative_sites.current_json::jsonb ->> '区县' end as district
            from site_scope
            left join authoritative_site_sources
              on authoritative_site_sources.ledger_row_id = site_scope.site_row_id
            left join authoritative_sites
              on authoritative_sites.id = authoritative_site_sources.site_id
        )
        update ledger_rows as related
        set city = case when effective_scope.site_count = 1
                             and effective_scope.city is not null
                             and effective_scope.district is not null
                        then effective_scope.city else null end,
            district = case when effective_scope.site_count = 1
                                 and effective_scope.city is not null
                                 and effective_scope.district is not null
                            then effective_scope.district else null end
        from effective_scope
        where related.ledger_type != 'site'
          and related.batch_id = effective_scope.batch_id
          and related.telecom_site_code = effective_scope.telecom_site_code
    """))
    connection.execute(text("""
        update ledger_rows as related
        set city = null, district = null
        where related.ledger_type != 'site'
          and not exists (
              select 1 from ledger_rows as site
              where site.ledger_type = 'site'
                and site.batch_id = related.batch_id
                and site.telecom_site_code = related.telecom_site_code
          )
    """))
    connection.execute(text("""
        update issues
        set city = ledger_rows.city, district = ledger_rows.district
        from audit_results, ledger_rows
        where issues.audit_result_id = audit_results.id
          and audit_results.ledger_row_id = ledger_rows.id
          and ledger_rows.ledger_type != 'site'
    """))


def _add_site_change_requests(connection: Connection) -> None:
    connection.execute(text(
        "alter table authoritative_site_versions add column if not exists confirmer varchar"
    ))
    _metadata.tables["site_change_requests"].create(connection, checkfirst=True)


def _add_tower_rent_authority(connection: Connection) -> None:
    for name in ("authoritative_tower_rents", "authoritative_tower_rent_sources",
                 "authoritative_tower_rent_versions", "tower_rent_change_requests"):
        _metadata.tables[name].create(connection, checkfirst=True)
    connection.execute(text("""alter table authoritative_tower_rent_sources
        add column if not exists frozen_json varchar"""))
    connection.execute(text("""alter table authoritative_tower_rent_sources
        add column if not exists frozen_version integer"""))
    rows = connection.execute(text("""select ledger_rows.id, ledger_rows.batch_id, raw_rows.row_json
        from ledger_rows join raw_rows on raw_rows.id = ledger_rows.raw_row_id
        where ledger_rows.ledger_type = 'tower_rent' order by ledger_rows.id""")).all()
    import json

    from governance_app.tower_rent_identity import business_key
    keys = [(row.id, row.batch_id, business_key(json.loads(row.row_json)) or f"row:{row.id}")
            for row in rows]
    counts: dict[tuple[int, str], int] = {}
    values_by_key: dict[str, set[str]] = {}
    for row, (_, _, key) in zip(rows, keys, strict=True):
        values_by_key.setdefault(key, set()).add(json.dumps(json.loads(row.row_json),
                                                    ensure_ascii=False, sort_keys=True))
    for _, batch_id, key in keys:
        counts[(batch_id, key)] = counts.get((batch_id, key), 0) + 1
    for row, (_, batch_id, key) in zip(rows, keys, strict=True):
        if counts[(batch_id, key)] > 1 or len(values_by_key[key]) > 1:
            key = f"row:{row.id}"
        connection.execute(text("""insert into authoritative_tower_rents(business_key, current_json, current_version)
            values (:key, :value, 0) on conflict (business_key) do nothing"""),
            {"key": key, "value": row.row_json})
        connection.execute(text("""insert into authoritative_tower_rent_sources(ledger_row_id, rent_id)
            select :row_id, id from authoritative_tower_rents where business_key = :key
            on conflict (ledger_row_id) do nothing"""),
            {"row_id": row.id, "key": key})
    connection.execute(text("""update authoritative_tower_rent_sources as source
        set frozen_json = raw.row_json, frozen_version = 0
        from ledger_rows as ledger join raw_rows as raw on raw.id = ledger.raw_row_id,
             import_batches as batch
        where source.ledger_row_id = ledger.id
          and ledger.batch_id = batch.id and batch.is_archived = 1
          and source.frozen_json is null"""))


POSTGRES_MIGRATIONS = (
    PostgresMigration(1, _create_initial_schema),
    PostgresMigration(2, _add_identity_and_runtime_schema),
    PostgresMigration(3, _add_site_jurisdiction_events),
    PostgresMigration(4, _add_authoritative_site_schema),
    PostgresMigration(5, _backfill_related_ledger_jurisdiction),
    PostgresMigration(6, _add_site_change_requests),
    PostgresMigration(7, _add_tower_rent_authority),
)
