from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    case,
    create_engine,
    delete,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.pool import NullPool

from governance_app.models import IssueStatus
from governance_app.ports.database import (
    AuditFindingRecord,
    AuditRepository,
    BatchRecord,
    BatchRepository,
    ImportedLedgerRow,
    IssueGroupQuery,
    IssueGroupSelector,
    IssueQuery,
    IssueRecord,
    IssueRepository,
    LedgerQuery,
    LedgerRecord,
    LedgerRepository,
    UnitOfWork,
)

_metadata = MetaData()
_CLOSED_ISSUE_STATUSES = ("closed", "not_required", "resolved_by_reaudit")

_import_batches = Table(
    "import_batches",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("source_file", String, nullable=False),
    Column("name", String),
    Column("batch_code", String),
    Column("template_version", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("status", String, nullable=False),
    Column("is_archived", Integer, nullable=False),
    Column("archived_at", String),
)

_settings = Table(
    "settings",
    _metadata,
    Column("key", String, primary_key=True),
    Column("value_json", String, nullable=False),
)

_operation_logs = Table(
    "operation_logs",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer),
    Column("operation", String, nullable=False),
    Column("message", String, nullable=False),
    Column("created_at", String, nullable=False),
)

_audit_results = Table(
    "audit_results",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("audit_run_id", Integer, nullable=False),
    Column("ledger_row_id", Integer),
    Column("rule_id", String, nullable=False),
    Column("severity", String, nullable=False),
    Column("message", String, nullable=False),
    Column("field_name", String),
    Column("result_json", String, nullable=False),
)

_issues = Table(
    "issues",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("issue_code", String, nullable=False),
    Column("audit_result_id", Integer, nullable=False),
    Column("batch_id", Integer, nullable=False),
    Column("city", String),
    Column("district", String),
    Column("telecom_site_code", String),
    Column("telecom_site_name", String),
    Column("ledger_type", String, nullable=False),
    Column("rule_id", String, nullable=False),
    Column("severity", String, nullable=False),
    Column("status", String, nullable=False),
    Column("message", String, nullable=False),
    Column("suggestion", String, nullable=False),
    Column("correction_value", String),
    Column("correction_note", String),
    Column("last_seen_audit_run_id", Integer),
    Column("resolved_at", String),
    Column("updated_at", String, nullable=False),
)

_issue_events = Table(
    "issue_events",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("issue_id", Integer, nullable=False),
    Column("from_status", String),
    Column("to_status", String, nullable=False),
    Column("source", String, nullable=False),
    Column("note", String),
    Column("created_at", String, nullable=False),
)

_raw_rows = Table(
    "raw_rows",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer, nullable=False),
    Column("ledger_type", String, nullable=False),
    Column("sheet_name", String, nullable=False),
    Column("row_number", Integer, nullable=False),
    Column("row_json", String, nullable=False),
)

_ledger_rows = Table(
    "ledger_rows",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer, nullable=False),
    Column("ledger_type", String, nullable=False),
    Column("city", String),
    Column("district", String),
    Column("telecom_site_code", String),
    Column("telecom_site_name", String),
    Column("tower_site_code", String),
    Column("tower_site_name", String),
    Column("raw_row_id", Integer),
    Column("row_json", String, nullable=False),
    Column("sheet_name", String),
    Column("row_number", Integer),
)

_audit_runs = Table(
    "audit_runs",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer, nullable=False),
    Column("rule_count", Integer, nullable=False),
)

_analysis_opportunities = Table(
    "analysis_opportunities",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer, nullable=False),
)


class SqliteBatchRepository(BatchRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def create(self, *, name: str, batch_code: str) -> int:
        result = self._connection.execute(
            insert(_import_batches).values(
                source_file="",
                name=name,
                batch_code=batch_code,
                status="created",
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return a batch id")
        return int(primary_key[0])

    def create_imported(self, *, source_file: str, name: str, batch_code: str) -> int:
        result = self._connection.execute(
            insert(_import_batches).values(
                source_file=source_file,
                name=name,
                batch_code=batch_code,
                status="imported",
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return a batch id")
        return int(primary_key[0])

    def get(self, batch_id: int) -> BatchRecord | None:
        statement = (
            select(
                _import_batches.c.id,
                func.coalesce(
                    _import_batches.c.name,
                    _import_batches.c.source_file,
                    "未命名批次",
                ).label("name"),
                _import_batches.c.source_file,
                _import_batches.c.template_version,
                _import_batches.c.batch_code,
                _import_batches.c.created_at,
                _import_batches.c.status,
                _import_batches.c.is_archived,
                _import_batches.c.archived_at,
            )
            .where(_import_batches.c.id == batch_id)
        )
        row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else dict(row)

    def list_all(self) -> list[BatchRecord]:
        statement = select(
            _import_batches.c.id,
            func.coalesce(
                _import_batches.c.name,
                _import_batches.c.source_file,
                "未命名批次",
            ).label("name"),
            _import_batches.c.batch_code,
            _import_batches.c.source_file,
            _import_batches.c.template_version,
            _import_batches.c.created_at,
            _import_batches.c.status,
            _import_batches.c.is_archived,
            _import_batches.c.archived_at,
        ).order_by(_import_batches.c.id.desc())
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def current_id(self) -> int | None:
        value = self._connection.execute(
            select(_settings.c.value_json).where(_settings.c.key == "current_batch_id")
        ).scalar_one_or_none()
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def set_current(self, batch_id: int) -> None:
        result = self._connection.execute(
            update(_settings)
            .where(_settings.c.key == "current_batch_id")
            .values(value_json=str(batch_id))
        )
        if not result.rowcount:
            self._connection.execute(
                insert(_settings).values(key="current_batch_id", value_json=str(batch_id))
            )

    def update_status(self, batch_id: int, status: str, *, archive: bool = False) -> None:
        values: dict[str, Any] = {"status": status}
        if archive:
            values.update(is_archived=1, archived_at=func.current_timestamp())
        self._connection.execute(
            update(_import_batches).where(_import_batches.c.id == batch_id).values(**values)
        )

    def update_source(self, batch_id: int, *, source_file: str, fallback_name: str) -> None:
        self._connection.execute(
            update(_import_batches)
            .where(_import_batches.c.id == batch_id)
            .values(
                source_file=source_file,
                name=func.coalesce(_import_batches.c.name, fallback_name),
            )
        )

    def add_operation(self, batch_id: int, operation: str, message: str) -> None:
        self._connection.execute(
            insert(_operation_logs).values(
                batch_id=batch_id,
                operation=operation,
                message=message,
            )
        )


class SqliteIssueRepository(IssueRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def query(self, query: IssueQuery) -> tuple[list[IssueRecord], int]:
        conditions = _issue_conditions(query)
        grouped_issues = _issues.alias("grouped_issues")
        same_site_rule_count = (
            select(func.count())
            .where(
                grouped_issues.c.batch_id == _issues.c.batch_id,
                grouped_issues.c.rule_id == _issues.c.rule_id,
                func.coalesce(grouped_issues.c.telecom_site_code, "")
                == func.coalesce(_issues.c.telecom_site_code, ""),
            )
            .correlate(_issues)
            .scalar_subquery()
            .label("same_site_rule_count")
        )
        statement = (
            select(
                _issues.c.issue_code,
                func.coalesce(_issues.c.city, "未填地市").label("city"),
                _issues.c.district,
                _issues.c.telecom_site_code,
                _issues.c.telecom_site_name,
                _issues.c.ledger_type,
                _issues.c.rule_id,
                _issues.c.severity,
                _issues.c.status,
                _issues.c.message,
                _issues.c.suggestion,
                _issues.c.correction_value,
                _issues.c.correction_note,
                _issues.c.updated_at,
                _audit_results.c.result_json,
                same_site_rule_count,
            )
            .select_from(
                _issues.outerjoin(
                    _audit_results,
                    _audit_results.c.id == _issues.c.audit_result_id,
                )
            )
            .where(*conditions)
            .order_by(_issues.c.updated_at.desc(), _issues.c.issue_code)
            .limit(query.limit)
            .offset(query.offset)
        )
        total_statement = select(func.count()).select_from(_issues).where(*conditions)
        rows: list[IssueRecord] = [
            dict(row) for row in self._connection.execute(statement).mappings()
        ]
        total = int(self._connection.execute(total_statement).scalar_one())
        return rows, total

    def rule_counts(self, batch_id: int) -> list[IssueRecord]:
        statement = (
            select(
                _issues.c.rule_id,
                func.count().label("issue_count"),
            )
            .where(_issues.c.batch_id == batch_id)
            .group_by(_issues.c.rule_id)
            .order_by(func.count().desc(), _issues.c.rule_id)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def groups(self, query: IssueGroupQuery) -> list[IssueRecord]:
        conditions = _issue_group_conditions(query)
        city = func.coalesce(_issues.c.city, "未填地市")
        site_code = func.coalesce(_issues.c.telecom_site_code, "")
        open_count = func.sum(
            case((~_issues.c.status.in_(_CLOSED_ISSUE_STATUSES), 1), else_=0)
        ).label("open_count")
        statement = (
            select(
                city.label("city"),
                _issues.c.ledger_type,
                _issues.c.rule_id,
                _issues.c.severity,
                site_code.label("telecom_site_code"),
                func.max(_issues.c.telecom_site_name).label("telecom_site_name"),
                func.count().label("issue_count"),
                open_count,
                func.sum(case((_issues.c.status == "needs_review", 1), else_=0)).label(
                    "review_count"
                ),
                func.sum(case((_issues.c.status == "still_invalid", 1), else_=0)).label(
                    "still_invalid_count"
                ),
                func.sum(case((_issues.c.status == "closed", 1), else_=0)).label(
                    "closed_count"
                ),
                func.sum(case((_issues.c.status == "not_required", 1), else_=0)).label(
                    "not_required_count"
                ),
                func.min(_issues.c.issue_code).label("representative_issue_code"),
                func.max(_issues.c.updated_at).label("updated_at"),
            )
            .where(*conditions)
            .group_by(
                city,
                _issues.c.ledger_type,
                _issues.c.rule_id,
                _issues.c.severity,
                site_code,
            )
            .order_by(
                open_count.desc(),
                func.count().desc(),
                city,
                site_code,
                _issues.c.rule_id,
            )
            .limit(query.limit)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def get_with_batch(self, issue_code: str) -> IssueRecord | None:
        statement = (
            select(
                _issues.c.id,
                _issues.c.batch_id,
                _issues.c.status,
                _import_batches.c.is_archived,
            )
            .select_from(
                _issues.join(
                    _import_batches,
                    _import_batches.c.id == _issues.c.batch_id,
                )
            )
            .where(_issues.c.issue_code == issue_code)
        )
        row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else dict(row)

    def update_status(
        self,
        issue: IssueRecord,
        status: IssueStatus,
        *,
        source: str,
        event_note: str,
    ) -> None:
        self._connection.execute(
            update(_issues)
            .where(_issues.c.id == issue["id"])
            .values(status=status, updated_at=func.current_timestamp())
        )
        self._connection.execute(
            insert(_issue_events).values(
                issue_id=issue["id"],
                from_status=issue["status"],
                to_status=status,
                source=source,
                note=event_note,
            )
        )

    def update_group_status(
        self,
        selector: IssueGroupSelector,
        status: IssueStatus,
        *,
        source: str,
        event_note: str,
    ) -> int:
        conditions = _issue_group_selector_conditions(selector)
        affected = list(
            self._connection.execute(
                select(_issues.c.id, _issues.c.status).where(*conditions)
            ).mappings()
        )
        if not affected:
            return 0
        self._connection.execute(
            update(_issues)
            .where(*conditions)
            .values(status=status, updated_at=func.current_timestamp())
        )
        self._connection.execute(
            insert(_issue_events),
            [
                {
                    "issue_id": row["id"],
                    "from_status": row["status"],
                    "to_status": status,
                    "source": source,
                    "note": event_note,
                }
                for row in affected
            ],
        )
        return len(affected)


class SqliteLedgerRepository(LedgerRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def query(self, query: LedgerQuery) -> list[LedgerRecord]:
        effective_row_json = case(
            (_ledger_rows.c.row_json != "{}", _ledger_rows.c.row_json),
            else_=func.coalesce(_raw_rows.c.row_json, _ledger_rows.c.row_json),
        ).label("row_json")
        statement = (
            select(
                _ledger_rows.c.id,
                _ledger_rows.c.ledger_type,
                func.coalesce(_ledger_rows.c.city, "未填地市").label("city"),
                _ledger_rows.c.district,
                _ledger_rows.c.telecom_site_code,
                _ledger_rows.c.telecom_site_name,
                _ledger_rows.c.tower_site_code,
                _ledger_rows.c.tower_site_name,
                effective_row_json,
            )
            .select_from(
                _ledger_rows.outerjoin(
                    _raw_rows,
                    _raw_rows.c.id == _ledger_rows.c.raw_row_id,
                )
            )
            .where(*_ledger_conditions(query))
            .order_by(
                _ledger_rows.c.ledger_type,
                _ledger_rows.c.city,
                _ledger_rows.c.telecom_site_code,
                _ledger_rows.c.id,
            )
            .limit(query.limit)
            .offset(query.offset)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def count(self, query: LedgerQuery) -> int:
        statement = (
            select(func.count())
            .select_from(_ledger_rows)
            .where(*_ledger_conditions(query))
        )
        return int(self._connection.execute(statement).scalar_one())

    def add_imported_row(self, batch_id: int, row: ImportedLedgerRow) -> None:
        raw_result = self._connection.execute(
            insert(_raw_rows).values(
                batch_id=batch_id,
                ledger_type=row.ledger_type,
                sheet_name=row.sheet_name,
                row_number=row.row_number,
                row_json=row.row_json,
            )
        )
        primary_key = raw_result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return a raw row id")
        self._connection.execute(
            insert(_ledger_rows).values(
                batch_id=batch_id,
                ledger_type=row.ledger_type,
                city=row.city,
                district=row.district,
                telecom_site_code=row.telecom_site_code,
                telecom_site_name=row.telecom_site_name,
                tower_site_code=row.tower_site_code,
                tower_site_name=row.tower_site_name,
                raw_row_id=primary_key[0],
                row_json="{}",
                sheet_name=row.sheet_name,
                row_number=row.row_number,
            )
        )

    def clear_batch_data(self, batch_id: int) -> None:
        run_ids = select(_audit_runs.c.id).where(_audit_runs.c.batch_id == batch_id)
        self._connection.execute(delete(_issues).where(_issues.c.batch_id == batch_id))
        self._connection.execute(
            delete(_audit_results).where(_audit_results.c.audit_run_id.in_(run_ids))
        )
        self._connection.execute(delete(_audit_runs).where(_audit_runs.c.batch_id == batch_id))
        self._connection.execute(delete(_ledger_rows).where(_ledger_rows.c.batch_id == batch_id))
        self._connection.execute(delete(_raw_rows).where(_raw_rows.c.batch_id == batch_id))

    def audit_rows(self, batch_id: int) -> list[LedgerRecord]:
        effective_row_json = case(
            (_ledger_rows.c.row_json != "{}", _ledger_rows.c.row_json),
            else_=func.coalesce(_raw_rows.c.row_json, _ledger_rows.c.row_json),
        ).label("effective_row_json")
        statement = (
            select(
                _ledger_rows.c.id,
                _ledger_rows.c.ledger_type,
                _ledger_rows.c.city,
                _ledger_rows.c.district,
                _ledger_rows.c.telecom_site_code,
                _ledger_rows.c.telecom_site_name,
                effective_row_json,
            )
            .select_from(
                _ledger_rows.outerjoin(
                    _raw_rows,
                    _raw_rows.c.id == _ledger_rows.c.raw_row_id,
                )
            )
            .where(_ledger_rows.c.batch_id == batch_id)
            .order_by(_ledger_rows.c.id)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]


class SqliteAuditRepository(AuditRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def create_run(self, batch_id: int, rule_count: int) -> int:
        result = self._connection.execute(
            insert(_audit_runs).values(batch_id=batch_id, rule_count=rule_count)
        )
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return an audit run id")
        return int(primary_key[0])

    def save_finding(self, finding: AuditFindingRecord) -> None:
        result = self._connection.execute(
            insert(_audit_results).values(
                audit_run_id=finding.audit_run_id,
                ledger_row_id=finding.ledger_row_id,
                rule_id=finding.rule_id,
                severity=finding.severity,
                message=finding.message,
                field_name=finding.field_name,
                result_json=finding.result_json,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return an audit result id")
        existing = self._connection.execute(
            select(_issues.c.id, _issues.c.status).where(
                _issues.c.issue_code == finding.issue_code
            )
        ).mappings().one_or_none()
        if existing is None:
            issue_result = self._connection.execute(
                insert(_issues).values(
                    issue_code=finding.issue_code,
                    audit_result_id=primary_key[0],
                    last_seen_audit_run_id=finding.audit_run_id,
                    batch_id=finding.batch_id,
                    city=finding.city,
                    district=finding.district,
                    telecom_site_code=finding.telecom_site_code,
                    telecom_site_name=finding.telecom_site_name,
                    ledger_type=finding.ledger_type,
                    rule_id=finding.rule_id,
                    severity=finding.severity,
                    message=finding.message,
                    suggestion=finding.suggestion,
                )
            )
            issue_primary_key = issue_result.inserted_primary_key
            if issue_primary_key is None or issue_primary_key[0] is None:
                raise RuntimeError("database did not return an issue id")
            self._record_event(
                int(issue_primary_key[0]),
                None,
                "pending_export",
                "audit",
                "首次命中稽核规则",
            )
            return

        reopened = existing["status"] == "resolved_by_reaudit"
        target_status = "pending_export" if reopened else existing["status"]
        self._connection.execute(
            update(_issues)
            .where(_issues.c.id == existing["id"])
            .values(
                audit_result_id=primary_key[0],
                last_seen_audit_run_id=finding.audit_run_id,
                city=finding.city,
                district=finding.district,
                telecom_site_code=finding.telecom_site_code,
                telecom_site_name=finding.telecom_site_name,
                severity=finding.severity,
                message=finding.message,
                suggestion=finding.suggestion,
                status=target_status,
                resolved_at=None,
                updated_at=func.current_timestamp(),
            )
        )
        if reopened:
            self._record_event(
                int(existing["id"]),
                "resolved_by_reaudit",
                "pending_export",
                "reaudit_reopen",
                "重复稽核再次命中",
            )

    def resolve_missing(
        self,
        batch_id: int,
        audit_run_id: int,
        seen_issue_codes: set[str],
    ) -> int:
        rows = list(
            self._connection.execute(
                select(_issues.c.id, _issues.c.issue_code, _issues.c.status).where(
                    _issues.c.batch_id == batch_id,
                    _issues.c.status != "resolved_by_reaudit",
                )
            ).mappings()
        )
        missing = [row for row in rows if row["issue_code"] not in seen_issue_codes]
        for row in missing:
            self._connection.execute(
                update(_issues)
                .where(_issues.c.id == row["id"])
                .values(
                    status="resolved_by_reaudit",
                    resolved_at=func.current_timestamp(),
                    updated_at=func.current_timestamp(),
                )
            )
            self._record_event(
                int(row["id"]),
                str(row["status"]),
                "resolved_by_reaudit",
                "reaudit_resolve",
                f"稽核运行 {audit_run_id} 未再次命中",
            )
        return len(missing)

    def clear_analysis_opportunities(self, batch_id: int) -> None:
        self._connection.execute(
            delete(_analysis_opportunities).where(
                _analysis_opportunities.c.batch_id == batch_id
            )
        )

    def _record_event(
        self,
        issue_id: int,
        from_status: str | None,
        to_status: str,
        source: str,
        note: str,
    ) -> None:
        self._connection.execute(
            insert(_issue_events).values(
                issue_id=issue_id,
                from_status=from_status,
                to_status=to_status,
                source=source,
                note=note,
            )
        )


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: Connection) -> None:
        self.batches = SqliteBatchRepository(connection)
        self.issues = SqliteIssueRepository(connection)
        self.ledgers = SqliteLedgerRepository(connection)
        self.audits = SqliteAuditRepository(connection)


class SqliteDatabase:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = URL.create("sqlite+pysqlite", database=str(database_path))
        self._engine: Engine = create_engine(url, poolclass=NullPool)
        event.listen(self._engine, "connect", _configure_connection)

    @contextmanager
    def unit_of_work(self) -> Iterator[UnitOfWork]:
        with self._engine.begin() as connection:
            yield SqliteUnitOfWork(connection)

    def dispose(self) -> None:
        self._engine.dispose()


def _configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("pragma foreign_keys = on")
        cursor.execute("pragma busy_timeout = 5000")
    finally:
        cursor.close()


def _issue_conditions(query: IssueQuery) -> list[Any]:
    conditions = [_issues.c.batch_id == query.batch_id]
    for value, column in (
        (query.city, func.coalesce(_issues.c.city, "未填地市")),
        (query.ledger_type, _issues.c.ledger_type),
        (query.severity, _issues.c.severity),
        (query.status, _issues.c.status),
        (query.rule_id, _issues.c.rule_id),
    ):
        if value:
            conditions.append(column == value)
    if query.closure == "open":
        conditions.append(~_issues.c.status.in_(_CLOSED_ISSUE_STATUSES))
    elif query.closure == "closed":
        conditions.append(_issues.c.status.in_(_CLOSED_ISSUE_STATUSES))
    return conditions


def _issue_group_conditions(query: IssueGroupQuery) -> list[Any]:
    conditions = [_issues.c.batch_id == query.batch_id]
    for value, column in (
        (query.city, func.coalesce(_issues.c.city, "未填地市")),
        (query.ledger_type, _issues.c.ledger_type),
        (query.rule_id, _issues.c.rule_id),
    ):
        if value:
            conditions.append(column == value)
    if query.closure == "open":
        conditions.append(~_issues.c.status.in_(_CLOSED_ISSUE_STATUSES))
    elif query.closure == "closed":
        conditions.append(_issues.c.status.in_(_CLOSED_ISSUE_STATUSES))
    return conditions


def _issue_group_selector_conditions(selector: IssueGroupSelector) -> list[Any]:
    return [
        _issues.c.batch_id == selector.batch_id,
        _issues.c.rule_id == selector.rule_id,
        _issues.c.ledger_type == selector.ledger_type,
        func.coalesce(_issues.c.city, "未填地市") == selector.city,
        func.coalesce(_issues.c.telecom_site_code, "") == selector.telecom_site_code,
    ]


def _ledger_conditions(query: LedgerQuery) -> list[Any]:
    conditions = [_ledger_rows.c.batch_id == query.batch_id]
    for value, column in (
        (query.ledger_type, _ledger_rows.c.ledger_type),
        (query.city, func.coalesce(_ledger_rows.c.city, "未填地市")),
        (query.district, func.coalesce(_ledger_rows.c.district, "")),
        (query.site_code, func.coalesce(_ledger_rows.c.telecom_site_code, "")),
    ):
        if value:
            conditions.append(column == value)
    return conditions
