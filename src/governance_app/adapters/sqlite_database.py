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
    BatchRecord,
    BatchRepository,
    IssueGroupQuery,
    IssueGroupSelector,
    IssueQuery,
    IssueRecord,
    IssueRepository,
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


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: Connection) -> None:
        self.batches = SqliteBatchRepository(connection)
        self.issues = SqliteIssueRepository(connection)


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
