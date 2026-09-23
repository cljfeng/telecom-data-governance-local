from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    and_,
    case,
    cast,
    create_engine,
    delete,
    event,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.selectable import FromClause

from governance_app.models import IssueStatus, LedgerType
from governance_app.ports.database import (
    AnalysisOpportunityRecord,
    AnalysisQuery,
    AnalysisRepository,
    ArchiveRepository,
    AuditFindingRecord,
    AuditRepository,
    BatchRecord,
    BatchRepository,
    CorrectionRepository,
    DashboardRepository,
    ExportRepository,
    ImportedLedgerRow,
    IssueGroupQuery,
    IssueGroupSelector,
    IssueQuery,
    IssueRecord,
    IssueRepository,
    LedgerQuery,
    LedgerRecord,
    LedgerRepository,
    PersistenceError,
    RecentFileRepository,
    ReviewRecord,
    ReviewRepository,
    RuleSettingRepository,
    UnitOfWork,
)

_metadata = MetaData()
_CLOSED_ISSUE_STATUSES = ("closed", "not_required", "resolved_by_reaudit")
_TIMESTAMP_DEFAULT = cast(func.current_timestamp(), String)

_import_batches = Table(
    "import_batches",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("source_file", String, nullable=False),
    Column("name", String),
    Column("batch_code", String),
    Column(
        "template_version",
        String,
        nullable=False,
        server_default="2026-05-05",
    ),
    Column(
        "created_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
    Column("status", String, nullable=False, server_default="imported"),
    Column("is_archived", Integer, nullable=False, server_default="0"),
    Column("archived_at", String),
)

_settings = Table(
    "settings",
    _metadata,
    Column("key", String, primary_key=True),
    Column("value_json", String, nullable=False),
)

_recent_files = Table(
    "recent_files",
    _metadata,
    Column("path", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("ok", Integer, nullable=False),
    Column("ledger_counts_json", String, nullable=False),
    Column("error_count", Integer, nullable=False),
    Column("organization_id", Integer),
    Column(
        "last_used_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
)

_audit_rule_settings = Table(
    "audit_rule_settings",
    _metadata,
    Column("rule_id", String, primary_key=True),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("config_json", String, nullable=False, server_default="{}"),
    Column(
        "updated_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
)

_operation_logs = Table(
    "operation_logs",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer, ForeignKey("import_batches.id", ondelete="CASCADE")),
    Column("operation", String, nullable=False),
    Column("message", String, nullable=False),
    Column("user_id", Integer),
    Column("organization_id", Integer),
    Column("request_id", String),
    Column("source_ip", String),
    Column("task_id", Integer),
    Column(
        "created_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
)

_audit_results = Table(
    "audit_results",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "audit_run_id",
        Integer,
        ForeignKey("audit_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "ledger_row_id",
        Integer,
        ForeignKey("ledger_rows.id", ondelete="CASCADE"),
    ),
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
    Column("issue_code", String, nullable=False, unique=True),
    Column(
        "audit_result_id",
        Integer,
        ForeignKey("audit_results.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "batch_id",
        Integer,
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("city", String),
    Column("district", String),
    Column("telecom_site_code", String),
    Column("telecom_site_name", String),
    Column("ledger_type", String, nullable=False),
    Column("rule_id", String, nullable=False),
    Column("severity", String, nullable=False),
    Column("status", String, nullable=False, server_default="pending_export"),
    Column("message", String, nullable=False),
    Column("suggestion", String, nullable=False),
    Column("correction_value", String),
    Column("correction_note", String),
    Column("last_seen_audit_run_id", Integer, ForeignKey("audit_runs.id")),
    Column("resolved_at", String),
    Column(
        "updated_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
)

_issue_events = Table(
    "issue_events",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "issue_id",
        Integer,
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("from_status", String),
    Column("to_status", String, nullable=False),
    Column("source", String, nullable=False),
    Column("note", String),
    Column(
        "created_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
)

_raw_rows = Table(
    "raw_rows",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "batch_id",
        Integer,
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("ledger_type", String, nullable=False),
    Column("sheet_name", String, nullable=False),
    Column("row_number", Integer, nullable=False),
    Column("row_json", String, nullable=False),
)

_ledger_rows = Table(
    "ledger_rows",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "batch_id",
        Integer,
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("ledger_type", String, nullable=False),
    Column("city", String),
    Column("district", String),
    Column("telecom_site_code", String),
    Column("telecom_site_name", String),
    Column("tower_site_code", String),
    Column("tower_site_name", String),
    Column("raw_row_id", Integer, ForeignKey("raw_rows.id", ondelete="CASCADE")),
    Column("row_json", String, nullable=False),
    Column("sheet_name", String),
    Column("row_number", Integer),
)

_site_jurisdiction_events = Table(
    "site_jurisdiction_events", _metadata,
    Column("id", Integer, primary_key=True),
    Column("ledger_row_id", Integer, ForeignKey("ledger_rows.id", ondelete="CASCADE"), nullable=False),
    Column("batch_id", Integer, nullable=False),
    Column("old_city", String), Column("old_district", String),
    Column("new_city", String, nullable=False), Column("new_district", String, nullable=False),
    Column("reason", String, nullable=False), Column("actor_user_id", Integer, nullable=False),
    Column("created_at", String, nullable=False, server_default=_TIMESTAMP_DEFAULT),
)

_site_evidence_files = Table(
    "site_evidence_files", _metadata,
    Column("id", Integer, primary_key=True),
    Column("ledger_row_id", Integer, ForeignKey("ledger_rows.id", ondelete="CASCADE"), nullable=False),
    Column("file_id", String, nullable=False),
    Column("actor_user_id", Integer, nullable=False),
    Column("created_at", String, nullable=False, server_default=_TIMESTAMP_DEFAULT),
)

_authoritative_sites = Table(
    "authoritative_sites", _metadata,
    Column("id", Integer, primary_key=True),
    Column("site_code", String, nullable=False, unique=True),
    Column("current_json", String, nullable=False),
    Column("current_version", Integer, nullable=False, server_default="0"),
)

_authoritative_site_sources = Table(
    "authoritative_site_sources", _metadata,
    Column("ledger_row_id", Integer, ForeignKey("ledger_rows.id", ondelete="CASCADE"), primary_key=True),
    Column("site_id", Integer, ForeignKey("authoritative_sites.id"), nullable=False),
)

_authoritative_site_versions = Table(
    "authoritative_site_versions", _metadata,
    Column("id", Integer, primary_key=True),
    Column("site_id", Integer, ForeignKey("authoritative_sites.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("old_json", String, nullable=False),
    Column("new_json", String, nullable=False),
    Column("evidence", String, nullable=False),
    Column("operator", String, nullable=False),
    Column("confirmer", String),
    Column("error_cause", String, nullable=False),
    Column("source", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_json", String, nullable=False),
    Column("effective_at", String, nullable=False, server_default=_TIMESTAMP_DEFAULT),
    UniqueConstraint("site_id", "version"),
    UniqueConstraint("site_id", "idempotency_key"),
)

_site_change_requests = Table(
    "site_change_requests", _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False),
    Column("ledger_row_id", Integer, ForeignKey("ledger_rows.id", ondelete="CASCADE"), nullable=False),
    Column("issue_code", String),
    Column("replaces_request_id", Integer, ForeignKey("site_change_requests.id")),
    Column("kind", String, nullable=False),
    Column("changes_json", String, nullable=False),
    Column("evidence", String, nullable=False),
    Column("error_cause", String, nullable=False),
    Column("source", String, nullable=False),
    Column("note", String, nullable=False),
    Column("proposer_user_id", Integer, nullable=False),
    Column("proposer_organization_id", Integer, nullable=False),
    Column("proposer_username", String, nullable=False),
    Column("reviewer_organization_id", Integer),
    Column("reviewer_user_id", Integer),
    Column("reviewer_username", String),
    Column("status", String, nullable=False),
    Column("review_note", String),
    Column("applied_version", Integer),
    Column("idempotency_key", String, nullable=False),
    Column("request_json", String, nullable=False),
    Column("created_at", String, nullable=False, server_default=_TIMESTAMP_DEFAULT),
    Column("updated_at", String, nullable=False, server_default=_TIMESTAMP_DEFAULT),
    UniqueConstraint("proposer_user_id", "idempotency_key"),
)

_authoritative_tower_rents = Table(
    "authoritative_tower_rents", _metadata,
    Column("id", Integer, primary_key=True),
    Column("business_key", String, nullable=False, unique=True),
    Column("current_json", String, nullable=False),
    Column("current_version", Integer, nullable=False, server_default="0"),
)
_authoritative_tower_rent_sources = Table(
    "authoritative_tower_rent_sources", _metadata,
    Column("ledger_row_id", Integer, ForeignKey("ledger_rows.id", ondelete="CASCADE"), primary_key=True),
    Column("rent_id", Integer, ForeignKey("authoritative_tower_rents.id"), nullable=False),
    Column("frozen_json", String),
    Column("frozen_version", Integer),
)
_authoritative_tower_rent_versions = Table(
    "authoritative_tower_rent_versions", _metadata,
    Column("id", Integer, primary_key=True),
    Column("rent_id", Integer, ForeignKey("authoritative_tower_rents.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("old_json", String, nullable=False), Column("new_json", String, nullable=False),
    Column("evidence", String, nullable=False), Column("operator", String, nullable=False),
    Column("confirmer", String), Column("error_cause", String, nullable=False),
    Column("source", String, nullable=False), Column("idempotency_key", String, nullable=False),
    Column("request_json", String, nullable=False),
    Column("effective_at", String, nullable=False, server_default=_TIMESTAMP_DEFAULT),
    UniqueConstraint("rent_id", "version"), UniqueConstraint("rent_id", "idempotency_key"),
)
_tower_rent_change_requests = Table(
    "tower_rent_change_requests", _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False),
    Column("ledger_row_id", Integer, ForeignKey("ledger_rows.id", ondelete="CASCADE"), nullable=False),
    Column("replaces_request_id", Integer, ForeignKey("tower_rent_change_requests.id")),
    Column("changes_json", String, nullable=False), Column("evidence", String, nullable=False),
    Column("error_cause", String, nullable=False), Column("source", String, nullable=False),
    Column("note", String, nullable=False), Column("proposer_user_id", Integer, nullable=False),
    Column("proposer_organization_id", Integer, nullable=False),
    Column("proposer_username", String, nullable=False),
    Column("reviewer_organization_id", Integer, nullable=False),
    Column("reviewer_user_id", Integer), Column("reviewer_username", String),
    Column("status", String, nullable=False), Column("review_note", String),
    Column("applied_version", Integer), Column("idempotency_key", String, nullable=False),
    Column("request_json", String, nullable=False),
    Column("created_at", String, nullable=False, server_default=_TIMESTAMP_DEFAULT),
    Column("updated_at", String, nullable=False, server_default=_TIMESTAMP_DEFAULT),
    UniqueConstraint("proposer_user_id", "idempotency_key"),
)

_audit_runs = Table(
    "audit_runs",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "batch_id",
        Integer,
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("rule_count", Integer, nullable=False),
    Column(
        "created_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
)

_analysis_opportunities = Table(
    "analysis_opportunities",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "batch_id",
        Integer,
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "ledger_row_id",
        Integer,
        ForeignKey("ledger_rows.id", ondelete="CASCADE"),
    ),
    Column("domain", String, nullable=False),
    Column("opportunity_code", String, nullable=False, unique=True),
    Column("opportunity_type", String, nullable=False),
    Column("severity", String, nullable=False),
    Column("city", String),
    Column("district", String),
    Column("telecom_site_code", String),
    Column("telecom_site_name", String),
    Column("period", String),
    Column("meter_no", String),
    Column("current_amount", Float, nullable=False),
    Column("reference_amount", Float, nullable=False),
    Column("recoverable_amount", Float, nullable=False),
    Column("saving_opportunity_amount", Float, nullable=False),
    Column("confidence", String, nullable=False),
    Column("source_rule_ids_json", String, nullable=False, server_default="[]"),
    Column("message", String, nullable=False),
    Column("suggestion", String, nullable=False),
    Column(
        "source_issue_code",
        String,
        ForeignKey("issues.issue_code", ondelete="CASCADE"),
    ),
    Column(
        "created_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
)

_analysis_opportunity_reviews = Table(
    "analysis_opportunity_reviews",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "batch_id",
        Integer,
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("domain", String, nullable=False),
    Column("opportunity_code", String, nullable=False, unique=True),
    Column("opportunity_type", String, nullable=False),
    Column(
        "source_issue_code",
        String,
        ForeignKey("issues.issue_code", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "estimated_recoverable_amount",
        Float,
        nullable=False,
        server_default="0",
    ),
    Column(
        "estimated_saving_amount",
        Float,
        nullable=False,
        server_default="0",
    ),
    Column("verified_recoverable_amount", Float),
    Column("realized_saving_amount", Float),
    Column("review_note", String),
    Column(
        "created_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
    Column(
        "updated_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
)

_correction_returns = Table(
    "correction_returns",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("source_file", String, nullable=False),
    Column(
        "imported_at",
        String,
        nullable=False,
        server_default=_TIMESTAMP_DEFAULT,
    ),
    Column("matched_count", Integer, nullable=False),
    Column("error_count", Integer, nullable=False),
    Column("errors_json", String, nullable=False),
    Column("warning_count", Integer, nullable=False),
    Column("warnings_json", String, nullable=False),
)

Index(
    "idx_ledger_rows_batch_type_city_site",
    _ledger_rows.c.batch_id,
    _ledger_rows.c.ledger_type,
    _ledger_rows.c.city,
    _ledger_rows.c.telecom_site_code,
)
Index(
    "idx_issues_batch_city_status_rule",
    _issues.c.batch_id,
    _issues.c.city,
    _issues.c.status,
    _issues.c.rule_id,
)
Index(
    "idx_issues_batch_status",
    _issues.c.batch_id,
    _issues.c.status,
)
Index(
    "idx_issue_events_issue_created",
    _issue_events.c.issue_id,
    _issue_events.c.created_at,
)
Index(
    "idx_analysis_opportunities_batch_domain_type",
    _analysis_opportunities.c.batch_id,
    _analysis_opportunities.c.domain,
    _analysis_opportunities.c.opportunity_type,
)
Index(
    "idx_analysis_opportunities_batch_city",
    _analysis_opportunities.c.batch_id,
    _analysis_opportunities.c.city,
)
Index(
    "idx_analysis_opportunities_source_issue",
    _analysis_opportunities.c.source_issue_code,
)
Index(
    "idx_analysis_reviews_batch_domain",
    _analysis_opportunity_reviews.c.batch_id,
    _analysis_opportunity_reviews.c.domain,
)
Index(
    "idx_analysis_reviews_source_issue",
    _analysis_opportunity_reviews.c.source_issue_code,
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
            rents = self._connection.execute(select(
                _authoritative_tower_rent_sources.c.ledger_row_id,
                _authoritative_tower_rents.c.current_json,
                _authoritative_tower_rents.c.current_version,
            ).select_from(_authoritative_tower_rent_sources.join(
                _ledger_rows, _authoritative_tower_rent_sources.c.ledger_row_id == _ledger_rows.c.id
            ).join(_authoritative_tower_rents,
                _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id
            )).where(_ledger_rows.c.batch_id == batch_id)).all()
            for row_id, current_json, version in rents:
                self._connection.execute(update(_authoritative_tower_rent_sources).where(
                    _authoritative_tower_rent_sources.c.ledger_row_id == row_id).values(
                        frozen_json=current_json, frozen_version=version))
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
        from governance_app.request_context import (
            current_principal,
            current_request,
        )

        principal = current_principal()
        request = current_request()
        self._connection.execute(
            insert(_operation_logs).values(
                batch_id=batch_id,
                operation=operation,
                message=message,
                user_id=None if principal is None else principal.user_id,
                organization_id=(
                    None
                    if principal is None
                    else principal.organization_id
                ),
                request_id=(
                    None if request is None else request.request_id
                ),
                source_ip=(
                    None if request is None else request.source_ip
                ),
                task_id=None if request is None else request.task_id,
            )
        )

    def recent_operations(self, batch_id: int, limit: int = 10) -> list[BatchRecord]:
        statement = (
            select(
                _operation_logs.c.operation,
                _operation_logs.c.message,
                _operation_logs.c.created_at,
            )
            .where(_operation_logs.c.batch_id == batch_id)
            .order_by(_operation_logs.c.id.desc())
            .limit(limit)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]


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
                *_jurisdiction_conditions(grouped_issues, query.jurisdictions),
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

    def rule_counts(self, batch_id: int, jurisdictions: tuple[tuple[str, str], ...] | None = None) -> list[IssueRecord]:
        statement = (
            select(
                _issues.c.rule_id,
                func.count().label("issue_count"),
            )
            .where(*_jurisdiction_conditions(_issues, jurisdictions), _issues.c.batch_id == batch_id)
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
                _issues.c.severity,
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
        correction_value: str | None = None,
        correction_note: str | None = None,
        update_correction_value: bool = False,
        update_correction_note: bool = False,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "updated_at": func.current_timestamp(),
        }
        if update_correction_value:
            values["correction_value"] = correction_value
        if update_correction_note:
            values["correction_note"] = correction_note
        self._connection.execute(
            update(_issues)
            .where(_issues.c.id == issue["id"])
            .values(**values)
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

    def workflow_summary(self, batch_id: int) -> IssueRecord:
        statement = select(
            func.count().label("total_issue_count"),
            func.sum(
                case(
                    (~_issues.c.status.in_(_CLOSED_ISSUE_STATUSES), 1),
                    else_=0,
                )
            ).label("open_issue_count"),
            func.sum(
                case((_issues.c.status == "pending_correction", 1), else_=0)
            ).label("pending_count"),
            func.sum(
                case((_issues.c.status == "needs_review", 1), else_=0)
            ).label("review_count"),
            func.sum(
                case((_issues.c.status == "still_invalid", 1), else_=0)
            ).label("still_invalid_count"),
        ).where(_issues.c.batch_id == batch_id)
        return dict(self._connection.execute(statement).mappings().one())

    def city_progress(self, batch_id: int) -> list[IssueRecord]:
        city = func.coalesce(_issues.c.city, "未填地市")
        statement = (
            select(
                city.label("city"),
                func.count().label("total_count"),
                *[
                    func.sum(case((_issues.c.status == status, 1), else_=0)).label(label)
                    for status, label in (
                        ("pending_correction", "pending_count"),
                        ("returned", "returned_count"),
                        ("needs_review", "review_count"),
                        ("still_invalid", "still_invalid_count"),
                        ("closed", "closed_count"),
                        ("not_required", "not_required_count"),
                        ("resolved_by_reaudit", "resolved_count"),
                    )
                ],
            )
            .where(_issues.c.batch_id == batch_id)
            .group_by(city)
            .order_by(func.count().desc(), city)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def top_rules_by_city(self, batch_id: int) -> list[IssueRecord]:
        city = func.coalesce(_issues.c.city, "未填地市")
        count = func.count().label("count")
        severity_order = case(
            (_issues.c.severity == "high", 0),
            (_issues.c.severity == "medium", 1),
            else_=2,
        )
        statement = (
            select(
                city.label("city"),
                _issues.c.rule_id,
                _issues.c.severity,
                count,
            )
            .where(_issues.c.batch_id == batch_id)
            .group_by(city, _issues.c.rule_id, _issues.c.severity)
            .order_by(count.desc(), severity_order, _issues.c.rule_id)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]


class SqliteLedgerRepository(LedgerRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def query(self, query: LedgerQuery) -> list[LedgerRecord]:
        effective_row_json = case(
            (_ledger_rows.c.ledger_type == "tower_rent",
             func.coalesce(_authoritative_tower_rent_sources.c.frozen_json,
                           _authoritative_tower_rents.c.current_json,
                           _raw_rows.c.row_json, _ledger_rows.c.row_json)),
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
                ).outerjoin(_authoritative_tower_rent_sources,
                    _ledger_rows.c.id == _authoritative_tower_rent_sources.c.ledger_row_id
                ).outerjoin(_authoritative_tower_rents,
                    _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id)
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

    def reassign_site(self, batch_id: int, row_id: int, city: str, district: str,
                      reason: str, actor_user_id: int) -> bool:
        row = self._connection.execute(select(_ledger_rows, _raw_rows.c.row_json.label("source_json"))
            .select_from(_ledger_rows.outerjoin(_raw_rows, _ledger_rows.c.raw_row_id == _raw_rows.c.id))
            .where(_ledger_rows.c.id == row_id, _ledger_rows.c.batch_id == batch_id,
                   _ledger_rows.c.ledger_type == "site")).mappings().one_or_none()
        if row is None:
            raise ValueError("site record not found")
        if row["city"] == city and row["district"] == district:
            return False
        raw = json.loads(row["row_json"] if row["row_json"] != "{}" else row["source_json"] or "{}")
        raw["地市"] = city
        raw["区县"] = district
        self._connection.execute(update(_ledger_rows).where(_ledger_rows.c.id == row_id).values(
            city=city, district=district, row_json=json.dumps(raw, ensure_ascii=False)))
        self._connection.execute(insert(_site_jurisdiction_events).values(
            ledger_row_id=row_id, batch_id=batch_id, old_city=row["city"],
            old_district=row["district"], new_city=city, new_district=district,
            reason=reason, actor_user_id=actor_user_id))
        self._connection.execute(update(_issues).where(_issues.c.audit_result_id.in_(
            select(_audit_results.c.id).where(_audit_results.c.ledger_row_id == row_id)
        )).values(city=city, district=district))
        if row["telecom_site_code"]:
            self._refresh_related_jurisdiction(batch_id, row["telecom_site_code"])
        return True

    def attach_site_evidence(self, batch_id: int, row_id: int, file_id: str,
                             actor_user_id: int) -> int:
        return self.attach_record_evidence(batch_id, row_id, file_id, actor_user_id, ("site",))

    def attach_record_evidence(self, batch_id: int, row_id: int, file_id: str,
                               actor_user_id: int, ledger_types: tuple[LedgerType, ...]) -> int:
        row = self._connection.execute(select(_ledger_rows.c.id).where(
            _ledger_rows.c.id == row_id, _ledger_rows.c.batch_id == batch_id,
            _ledger_rows.c.ledger_type.in_(ledger_types))).scalar_one_or_none()
        if row is None:
            raise ValueError("record not found")
        result = self._connection.execute(insert(_site_evidence_files).values(
            ledger_row_id=row_id, file_id=file_id, actor_user_id=actor_user_id))
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return an evidence id")
        return int(primary_key[0])

    def site_evidence_file(self, batch_id: int, row_id: int, evidence_id: int) -> str | None:
        return self.record_evidence_file(batch_id, row_id, evidence_id, ("site",))

    def record_evidence_file(self, batch_id: int, row_id: int, evidence_id: int,
                             ledger_types: tuple[LedgerType, ...]) -> str | None:
        return self._connection.execute(select(_site_evidence_files.c.file_id).select_from(
            _site_evidence_files.join(_ledger_rows,
                                      _site_evidence_files.c.ledger_row_id == _ledger_rows.c.id))
            .where(_ledger_rows.c.batch_id == batch_id, _ledger_rows.c.id == row_id,
                   _ledger_rows.c.ledger_type.in_(ledger_types), _site_evidence_files.c.id == evidence_id)
        ).scalar_one_or_none()

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
        city, district = row.city, row.district
        if row.ledger_type != "site" and row.telecom_site_code:
            jurisdiction = self._unique_site_jurisdiction(batch_id, row.telecom_site_code)
            city, district = jurisdiction if jurisdiction is not None else (None, None)
        ledger_result = self._connection.execute(
            insert(_ledger_rows).values(
                batch_id=batch_id,
                ledger_type=row.ledger_type,
                city=city,
                district=district,
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
        if row.ledger_type == "site" and row.telecom_site_code and ledger_result.inserted_primary_key:
            row_id = ledger_result.inserted_primary_key[0]
            if row_id is not None:
                self._link_site_source(batch_id, int(row_id), row)
                self._refresh_related_jurisdiction(batch_id, row.telecom_site_code)
        if row.ledger_type == "tower_rent" and ledger_result.inserted_primary_key:
            row_id = ledger_result.inserted_primary_key[0]
            if row_id is not None:
                self._link_tower_rent_source(batch_id, int(row_id), row.row_json)

    def _link_tower_rent_source(self, batch_id: int, row_id: int, source_json: str) -> None:
        from governance_app.tower_rent_identity import business_key
        key = business_key(json.loads(source_json))
        if key is None:
            key = f"row:{row_id}"
        existing = self._connection.execute(select(_authoritative_tower_rents).where(
            _authoritative_tower_rents.c.business_key == key)).mappings().one_or_none()
        if (existing is not None and existing["current_version"] == 0
                and json.loads(existing["current_json"]) != json.loads(source_json)):
            key = f"row:{row_id}"  # Conflicting unverified sources need explicit identity resolution.
        duplicates = self._connection.execute(select(
            _authoritative_tower_rent_sources.c.ledger_row_id,
            _authoritative_tower_rent_sources.c.rent_id,
        ).select_from(_authoritative_tower_rent_sources.join(
            _ledger_rows, _authoritative_tower_rent_sources.c.ledger_row_id == _ledger_rows.c.id
        ).join(_authoritative_tower_rents,
            _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id
        )).where(_ledger_rows.c.batch_id == batch_id,
                 _authoritative_tower_rents.c.business_key == key)).all()
        for previous_row_id, _ in duplicates:
            self._connection.execute(delete(_authoritative_tower_rent_sources).where(
                _authoritative_tower_rent_sources.c.ledger_row_id == previous_row_id))
            previous_source = self._connection.execute(select(_raw_rows.c.row_json).select_from(
                _ledger_rows.join(_raw_rows, _ledger_rows.c.raw_row_id == _raw_rows.c.id)
            ).where(_ledger_rows.c.id == previous_row_id)).scalar_one()
            self._create_tower_rent_source(int(previous_row_id), f"row:{previous_row_id}", previous_source)
        if duplicates:
            key = f"row:{row_id}"
        self._create_tower_rent_source(row_id, key, source_json)

    def _create_tower_rent_source(self, row_id: int, key: str, source_json: str) -> None:
        rent_id = self._connection.execute(select(_authoritative_tower_rents.c.id).where(
            _authoritative_tower_rents.c.business_key == key)).scalar_one_or_none()
        if rent_id is None:
            result = self._connection.execute(insert(_authoritative_tower_rents).values(
                business_key=key, current_json=source_json, current_version=0))
            primary_key = result.inserted_primary_key
            if primary_key is None or primary_key[0] is None:
                raise RuntimeError("database did not return a tower rent authority id")
            rent_id = primary_key[0]
        self._connection.execute(insert(_authoritative_tower_rent_sources).values(
            ledger_row_id=row_id, rent_id=rent_id))

    def tower_rent_authorities(self, batch_id: int) -> list[LedgerRecord]:
        rows = self._connection.execute(select(
            _ledger_rows.c.id.label("row_id"), _ledger_rows.c.telecom_site_code,
            _ledger_rows.c.tower_site_code,
            func.coalesce(_authoritative_tower_rent_sources.c.frozen_version,
                          _authoritative_tower_rents.c.current_version).label("current_version"),
        ).select_from(_ledger_rows.outerjoin(_authoritative_tower_rent_sources,
            _ledger_rows.c.id == _authoritative_tower_rent_sources.c.ledger_row_id
        ).outerjoin(_authoritative_tower_rents,
            _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id
        )).where(_ledger_rows.c.batch_id == batch_id,
                 _ledger_rows.c.ledger_type == "tower_rent").order_by(_ledger_rows.c.id)).mappings()
        return [dict(row) for row in rows]

    def tower_rent_authority(self, batch_id: int, row_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(select(
            _ledger_rows.c.id, _raw_rows.c.row_json.label("source_json"),
            _authoritative_tower_rents.c.id.label("rent_id"),
            _authoritative_tower_rents.c.current_json,
            _authoritative_tower_rents.c.current_version,
            _authoritative_tower_rent_sources.c.frozen_json,
            _authoritative_tower_rent_sources.c.frozen_version,
        ).select_from(_ledger_rows.join(_raw_rows,
            _ledger_rows.c.raw_row_id == _raw_rows.c.id
        ).outerjoin(_authoritative_tower_rent_sources,
            _ledger_rows.c.id == _authoritative_tower_rent_sources.c.ledger_row_id
        ).outerjoin(_authoritative_tower_rents,
            _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id
        )).where(_ledger_rows.c.id == row_id, _ledger_rows.c.batch_id == batch_id,
                 _ledger_rows.c.ledger_type == "tower_rent")).mappings().one_or_none()
        if row is None:
            return None
        versions = []
        version = row["frozen_version"] if row["frozen_version"] is not None else row["current_version"]
        if row["rent_id"] is not None:
            versions = [{"version": item["version"], "old_value": json.loads(item["old_json"]),
                         "new_value": json.loads(item["new_json"]), "evidence": item["evidence"],
                         "operator": item["operator"], "confirmer": item["confirmer"],
                         "error_cause": item["error_cause"], "source": item["source"],
                         "effective_at": item["effective_at"]}
                        for item in self._connection.execute(select(_authoritative_tower_rent_versions)
                            .where(_authoritative_tower_rent_versions.c.rent_id == row["rent_id"],
                                   _authoritative_tower_rent_versions.c.version <= version)
                            .order_by(_authoritative_tower_rent_versions.c.version)).mappings()]
        return {"row_id": row_id, "source": json.loads(row["source_json"]),
                "current": json.loads(row["frozen_json"] or row["current_json"])
                if row["rent_id"] is not None else None,
                "version": version, "versions": versions}

    def revise_tower_rent(self, batch_id: int, row_id: int,
                          request: Mapping[str, Any]) -> tuple[int, bool]:
        detail = self.tower_rent_authority(batch_id, row_id)
        if detail is None or detail["current"] is None:
            raise ValueError("tower rent record not found")
        rent_id = self._connection.execute(select(_authoritative_tower_rent_sources.c.rent_id).where(
            _authoritative_tower_rent_sources.c.ledger_row_id == row_id)).scalar_one()
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        existing = self._connection.execute(select(_authoritative_tower_rent_versions).where(
            _authoritative_tower_rent_versions.c.rent_id == rent_id,
            _authoritative_tower_rent_versions.c.idempotency_key == request["idempotency_key"]
        )).mappings().one_or_none()
        if existing is not None:
            if existing["request_json"] != request_json:
                raise ValueError("idempotency key was used with different changes")
            return int(existing["version"]), False
        current = detail["current"]
        if any(field not in current for field in request["changes"]):
            raise ValueError("tower rent changes must reference existing fields")
        revised = dict(current)
        revised.update(request["changes"])
        if revised == current:
            return int(detail["version"]), False
        next_version = int(detail["version"]) + 1
        self._connection.execute(insert(_authoritative_tower_rent_versions).values(
            rent_id=rent_id, version=next_version,
            old_json=json.dumps(current, ensure_ascii=False, sort_keys=True),
            new_json=json.dumps(revised, ensure_ascii=False, sort_keys=True),
            evidence=request["evidence"], operator=request["operator"],
            confirmer=request.get("confirmer"), error_cause=request["error_cause"],
            source=request["source"], idempotency_key=request["idempotency_key"],
            request_json=request_json))
        self._connection.execute(update(_authoritative_tower_rents).where(
            _authoritative_tower_rents.c.id == rent_id).values(
                current_json=json.dumps(revised, ensure_ascii=False, sort_keys=True),
                current_version=next_version))
        return next_version, True

    def _unique_site_jurisdiction(self, batch_id: int, site_code: str) -> tuple[str, str] | None:
        sites = self._connection.execute(select(
            _ledger_rows.c.id, _ledger_rows.c.city, _ledger_rows.c.district,
        ).where(
            _ledger_rows.c.batch_id == batch_id,
            _ledger_rows.c.ledger_type == "site",
            _ledger_rows.c.telecom_site_code == site_code,
        )).all()
        if len(sites) != 1:
            return None
        row_id, source_city, source_district = sites[0]
        has_override = self._connection.execute(select(_site_jurisdiction_events.c.id).where(
            _site_jurisdiction_events.c.ledger_row_id == row_id).limit(1)).first()
        if has_override is not None:
            return ((str(source_city), str(source_district))
                    if source_city and source_district else None)
        current_json = self._connection.execute(select(_authoritative_sites.c.current_json)
            .select_from(_authoritative_site_sources.join(_authoritative_sites,
                _authoritative_site_sources.c.site_id == _authoritative_sites.c.id))
            .where(_authoritative_site_sources.c.ledger_row_id == row_id)
        ).scalar_one_or_none()
        if current_json is None:
            return None
        current = json.loads(current_json)
        city, district = current.get("地市"), current.get("区县")
        return (str(city), str(district)) if city and district else None

    def _refresh_related_jurisdiction(self, batch_id: int, site_code: str) -> None:
        jurisdiction = self._unique_site_jurisdiction(batch_id, site_code)
        city, district = jurisdiction if jurisdiction is not None else (None, None)
        related_row_ids = select(_ledger_rows.c.id).where(
            _ledger_rows.c.batch_id == batch_id,
            _ledger_rows.c.ledger_type != "site",
            _ledger_rows.c.telecom_site_code == site_code,
        )
        self._connection.execute(update(_ledger_rows).where(
            _ledger_rows.c.id.in_(related_row_ids)
        ).values(city=city, district=district))
        self._connection.execute(update(_issues).where(_issues.c.audit_result_id.in_(
            select(_audit_results.c.id).where(_audit_results.c.ledger_row_id.in_(related_row_ids))
        )).values(city=city, district=district))

    def _link_site_source(self, batch_id: int, row_id: int, row: ImportedLedgerRow) -> None:
        code = str(row.telecom_site_code).strip()
        existing = self._connection.execute(select(_authoritative_sites).where(
            _authoritative_sites.c.site_code == code)).mappings().one_or_none()
        if existing is None:
            result = self._connection.execute(insert(_authoritative_sites).values(
                site_code=code, current_json=row.row_json, current_version=0))
            primary_key = result.inserted_primary_key
            if primary_key is None or primary_key[0] is None:
                raise RuntimeError("database did not return a site authority id")
            site_id = primary_key[0]
        else:
            site_id = existing["id"]
            source_location = self._connection.execute(select(_ledger_rows.c.city, _ledger_rows.c.district)
                .select_from(_ledger_rows.join(_authoritative_site_sources,
                    _ledger_rows.c.id == _authoritative_site_sources.c.ledger_row_id))
                .where(_authoritative_site_sources.c.site_id == site_id)
                .order_by(_ledger_rows.c.id).limit(1)).first()
            current = json.loads(existing["current_json"])
            verified_location = (
                current.get("地市"), current.get("区县")
            ) if existing["current_version"] else None
            if (source_location is not None and source_location != (row.city, row.district)
                    and verified_location != (row.city, row.district)):
                return  # A conflicting identity needs province verification.
        already = self._connection.execute(select(_ledger_rows.c.id).where(
            _ledger_rows.c.batch_id == batch_id, _ledger_rows.c.ledger_type == "site",
            _ledger_rows.c.telecom_site_code == code, _ledger_rows.c.id != row_id)).scalars().all()
        if already:
            self._connection.execute(delete(_authoritative_site_sources).where(
                _authoritative_site_sources.c.ledger_row_id.in_(already)))
            return  # Duplicate identity in one batch is ambiguous.
        self._connection.execute(insert(_authoritative_site_sources).values(
            ledger_row_id=row_id, site_id=site_id))

    def site_authorities(self, batch_id: int) -> list[LedgerRecord]:
        statement = select(_ledger_rows.c.id.label("row_id"), _ledger_rows.c.telecom_site_code,
                           _authoritative_sites.c.current_version).select_from(
            _ledger_rows.outerjoin(_authoritative_site_sources,
                _ledger_rows.c.id == _authoritative_site_sources.c.ledger_row_id)
            .outerjoin(_authoritative_sites,
                _authoritative_site_sources.c.site_id == _authoritative_sites.c.id)
        ).where(_ledger_rows.c.batch_id == batch_id,
                _ledger_rows.c.ledger_type == "site").order_by(_ledger_rows.c.id)
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def site_authority(self, batch_id: int, row_id: int) -> dict[str, Any] | None:
        statement = select(_ledger_rows.c.id, _raw_rows.c.row_json.label("source_json"),
                           _ledger_rows.c.row_json.label("ledger_json"),
                           _authoritative_sites.c.id.label("site_id"),
                           _authoritative_sites.c.current_json,
                           _authoritative_sites.c.current_version).select_from(
            _ledger_rows.outerjoin(_raw_rows, _ledger_rows.c.raw_row_id == _raw_rows.c.id)
            .outerjoin(_authoritative_site_sources,
                _ledger_rows.c.id == _authoritative_site_sources.c.ledger_row_id)
            .outerjoin(_authoritative_sites,
                _authoritative_site_sources.c.site_id == _authoritative_sites.c.id)
        ).where(_ledger_rows.c.batch_id == batch_id, _ledger_rows.c.id == row_id,
                _ledger_rows.c.ledger_type == "site")
        row = self._connection.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        source = json.loads(row["source_json"] or row["ledger_json"])
        site_id = row["site_id"]
        versions = []
        if site_id is not None:
            versions = [
                {"version": version["version"], "old_value": json.loads(version["old_json"]),
                 "new_value": json.loads(version["new_json"]), "evidence": version["evidence"],
                 "operator": version["operator"], "confirmer": version["confirmer"],
                 "error_cause": version["error_cause"],
                 "source": version["source"], "effective_at": version["effective_at"]}
                for version in self._connection.execute(select(_authoritative_site_versions)
                    .where(_authoritative_site_versions.c.site_id == site_id)
                    .order_by(_authoritative_site_versions.c.version)).mappings()
            ]
        return {"row_id": row_id, "source": source,
                "current": json.loads(row["current_json"]) if site_id is not None else None,
                "version": row["current_version"], "versions": versions,
                "identity_conflict": site_id is None}

    def revise_site(self, batch_id: int, row_id: int, request: Mapping[str, Any]) -> tuple[int, bool]:
        detail = self.site_authority(batch_id, row_id)
        if detail is None:
            raise ValueError("site record not found")
        if detail["identity_conflict"]:
            raise ValueError("site identity is missing or ambiguous")
        site_id = self._connection.execute(select(_authoritative_site_sources.c.site_id).where(
            _authoritative_site_sources.c.ledger_row_id == row_id)).scalar_one()
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        existing = self._connection.execute(select(_authoritative_site_versions).where(
            _authoritative_site_versions.c.site_id == site_id,
            _authoritative_site_versions.c.idempotency_key == request["idempotency_key"])
        ).mappings().one_or_none()
        if existing is not None:
            if existing["request_json"] != request_json:
                raise ValueError("idempotency key was used with different changes")
            return int(existing["version"]), False
        current = detail["current"]
        revised = dict(current)
        revised.update(request["changes"])
        if revised == current:
            return int(detail["version"]), False
        next_version = int(detail["version"]) + 1
        self._connection.execute(insert(_authoritative_site_versions).values(
            site_id=site_id, version=next_version,
            old_json=json.dumps(current, ensure_ascii=False, sort_keys=True),
            new_json=json.dumps(revised, ensure_ascii=False, sort_keys=True),
            evidence=request["evidence"], operator=request["operator"],
            confirmer=request.get("confirmer"),
            error_cause=request["error_cause"], source=request["source"],
            idempotency_key=request["idempotency_key"], request_json=request_json))
        self._connection.execute(update(_authoritative_sites).where(
            _authoritative_sites.c.id == site_id).values(
                current_json=json.dumps(revised, ensure_ascii=False, sort_keys=True),
                current_version=next_version))
        sources = self._connection.execute(select(
            _ledger_rows.c.batch_id, _ledger_rows.c.telecom_site_code,
        ).select_from(_ledger_rows.join(_authoritative_site_sources,
            _ledger_rows.c.id == _authoritative_site_sources.c.ledger_row_id))
            .where(_authoritative_site_sources.c.site_id == site_id)).all()
        for source_batch_id, site_code in sources:
            if site_code:
                self._refresh_related_jurisdiction(int(source_batch_id), str(site_code))
        return next_version, True

    def submit_site_change(self, request: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        existing = self._connection.execute(select(_site_change_requests).where(
            _site_change_requests.c.proposer_user_id == request["proposer_user_id"],
            _site_change_requests.c.idempotency_key == request["idempotency_key"],
        )).mappings().one_or_none()
        if existing is not None:
            if existing["request_json"] != request_json:
                raise ValueError("idempotency key was used with a different request")
            return dict(existing), False
        if self.site_authority(int(request["batch_id"]), int(request["row_id"])) is None:
            raise ValueError("site record not found")
        issue_code = request.get("issue_code")
        if issue_code and self._connection.execute(select(_issues.c.id).select_from(
            _issues.join(_audit_results, _issues.c.audit_result_id == _audit_results.c.id)
        ).where(
            _issues.c.issue_code == issue_code,
            _issues.c.batch_id == request["batch_id"],
            _audit_results.c.ledger_row_id == request["row_id"],
        )).scalar_one_or_none() is None:
            raise ValueError("issue does not belong to the site record")
        status = "recorded" if request["kind"] == "no_change" else "pending"
        result = self._connection.execute(insert(_site_change_requests).values(
            batch_id=request["batch_id"], ledger_row_id=request["row_id"],
            issue_code=issue_code, replaces_request_id=request.get("replaces_request_id"),
            kind=request["kind"], changes_json=json.dumps(request["changes"], ensure_ascii=False),
            evidence=request["evidence"], error_cause=request["error_cause"],
            source=request["source"], note=request["note"],
            proposer_user_id=request["proposer_user_id"],
            proposer_organization_id=request["proposer_organization_id"],
            proposer_username=request["proposer_username"],
            reviewer_organization_id=request.get("reviewer_organization_id"),
            status=status, idempotency_key=request["idempotency_key"], request_json=request_json,
        ))
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return a site change request id")
        record = dict(self._connection.execute(select(_site_change_requests).where(
            _site_change_requests.c.id == primary_key[0]
        )).mappings().one())
        return record, True

    def site_change_request(self, request_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(select(_site_change_requests).where(
            _site_change_requests.c.id == request_id)).mappings().one_or_none()
        return None if row is None else dict(row)

    def decide_site_change(self, request_id: int, *, action: str, note: str,
                           reviewer_user_id: int, reviewer_organization_id: int,
                           reviewer_username: str) -> tuple[dict[str, Any], bool]:
        row = self.site_change_request(request_id)
        if row is None or row["kind"] != "correction":
            raise ValueError("site correction request not found")
        if int(row["reviewer_organization_id"]) != reviewer_organization_id:
            raise PermissionError("only the designated upper-level organization can review")
        if row["status"] != "pending":
            if action == "approve" and row["status"] == "approved":
                return row, False
            raise ValueError("site correction request is no longer pending")
        values: dict[str, Any] = {
            "reviewer_user_id": reviewer_user_id,
            "reviewer_username": reviewer_username,
            "review_note": note,
            "updated_at": func.current_timestamp(),
        }
        if action == "reject":
            values["status"] = "rejected"
        else:
            version, created = self.revise_site(int(row["batch_id"]), int(row["ledger_row_id"]), {
                "changes": json.loads(row["changes_json"]), "evidence": row["evidence"],
                "operator": row["proposer_username"], "confirmer": reviewer_username,
                "error_cause": row["error_cause"], "source": row["source"],
                "idempotency_key": f"online-site-change-{request_id}",
            })
            if not created:
                raise ValueError(
                    "correction does not change the authoritative value; record a no-change conclusion"
                )
            values.update(status="approved", applied_version=version)
        self._connection.execute(update(_site_change_requests).where(
            _site_change_requests.c.id == request_id).values(**values))
        return self.site_change_request(request_id) or row, True

    def submit_tower_rent_change(self, request: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        existing = self._connection.execute(select(_tower_rent_change_requests).where(
            _tower_rent_change_requests.c.proposer_user_id == request["proposer_user_id"],
            _tower_rent_change_requests.c.idempotency_key == request["idempotency_key"]
        )).mappings().one_or_none()
        if existing is not None:
            if existing["request_json"] != request_json:
                raise ValueError("idempotency key was used with a different request")
            return dict(existing), False
        if self.tower_rent_authority(int(request["batch_id"]), int(request["row_id"])) is None:
            raise ValueError("tower rent record not found")
        result = self._connection.execute(insert(_tower_rent_change_requests).values(
            batch_id=request["batch_id"], ledger_row_id=request["row_id"],
            replaces_request_id=request.get("replaces_request_id"),
            changes_json=json.dumps(request["changes"], ensure_ascii=False),
            evidence=request["evidence"], error_cause=request["error_cause"],
            source=request["source"], note=request["note"],
            proposer_user_id=request["proposer_user_id"],
            proposer_organization_id=request["proposer_organization_id"],
            proposer_username=request["proposer_username"],
            reviewer_organization_id=request["reviewer_organization_id"],
            status="pending", idempotency_key=request["idempotency_key"],
            request_json=request_json))
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return a tower rent request id")
        request_id = primary_key[0]
        return self.tower_rent_change_request(int(request_id)) or {}, True

    def tower_rent_change_request(self, request_id: int) -> dict[str, Any] | None:
        row = self._connection.execute(select(_tower_rent_change_requests).where(
            _tower_rent_change_requests.c.id == request_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def decide_tower_rent_change(self, request_id: int, *, action: str, note: str,
                                 reviewer_user_id: int, reviewer_organization_id: int,
                                 reviewer_username: str) -> tuple[dict[str, Any], bool]:
        row = self.tower_rent_change_request(request_id)
        if row is None:
            raise ValueError("tower rent correction request not found")
        if int(row["reviewer_organization_id"]) != reviewer_organization_id:
            raise PermissionError("only the designated upper-level organization can review")
        if row["status"] != "pending":
            if action == "approve" and row["status"] == "approved":
                return row, False
            raise ValueError("tower rent correction request is no longer pending")
        values: dict[str, Any] = {
            "reviewer_user_id": reviewer_user_id, "reviewer_username": reviewer_username,
            "review_note": note, "updated_at": func.current_timestamp(),
        }
        if action == "reject":
            values["status"] = "rejected"
        else:
            version, created = self.revise_tower_rent(int(row["batch_id"]), int(row["ledger_row_id"]), {
                "changes": json.loads(row["changes_json"]), "evidence": row["evidence"],
                "operator": row["proposer_username"], "confirmer": reviewer_username,
                "error_cause": row["error_cause"], "source": row["source"],
                "idempotency_key": f"online-tower-rent-change-{request_id}",
            })
            if not created:
                raise ValueError("correction does not change the authoritative value")
            values.update(status="approved", applied_version=version)
        self._connection.execute(update(_tower_rent_change_requests).where(
            _tower_rent_change_requests.c.id == request_id).values(**values))
        return self.tower_rent_change_request(request_id) or row, True

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
                _ledger_rows.c.row_json.label("ledger_override_json"),
                _authoritative_sites.c.current_json.label("authoritative_json"),
                func.coalesce(_authoritative_tower_rent_sources.c.frozen_json,
                              _authoritative_tower_rents.c.current_json).label("tower_rent_authoritative_json"),
            )
            .select_from(
                _ledger_rows.outerjoin(
                    _raw_rows,
                    _raw_rows.c.id == _ledger_rows.c.raw_row_id,
                ).outerjoin(_authoritative_site_sources,
                    _ledger_rows.c.id == _authoritative_site_sources.c.ledger_row_id,
                ).outerjoin(_authoritative_sites,
                    _authoritative_site_sources.c.site_id == _authoritative_sites.c.id,
                ).outerjoin(_authoritative_tower_rent_sources,
                    _ledger_rows.c.id == _authoritative_tower_rent_sources.c.ledger_row_id,
                ).outerjoin(_authoritative_tower_rents,
                    _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id,
                )
            )
            .where(_ledger_rows.c.batch_id == batch_id)
            .order_by(_ledger_rows.c.id)
        )
        rows: list[LedgerRecord] = []
        for record in self._connection.execute(statement).mappings():
            row = dict(record)
            authoritative = row.pop("authoritative_json")
            tower_authoritative = row.pop("tower_rent_authoritative_json")
            ledger_override = row.pop("ledger_override_json")
            if row["ledger_type"] == "site" and authoritative is not None and ledger_override == "{}":
                current = json.loads(authoritative)
                row["effective_row_json"] = authoritative
                row["city"] = current.get("地市")
                row["district"] = current.get("区县")
                row["telecom_site_name"] = current.get("电信站址名称")
            if row["ledger_type"] == "tower_rent" and tower_authoritative is not None:
                row["effective_row_json"] = tower_authoritative
            rows.append(row)
        return rows


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


class SqliteReviewRepository(ReviewRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_opportunity(self, opportunity_code: str) -> ReviewRecord | None:
        statement = (
            select(
                _analysis_opportunities,
                _issues.c.issue_code,
                _issues.c.ledger_type.label("issue_ledger_type"),
                _issues.c.status.label("issue_status"),
                _issues.c.correction_value,
                _issues.c.correction_note,
                _import_batches.c.status.label("batch_status"),
                _import_batches.c.is_archived,
            )
            .select_from(
                _analysis_opportunities.join(
                    _import_batches,
                    _import_batches.c.id == _analysis_opportunities.c.batch_id,
                ).outerjoin(
                    _issues,
                    _issues.c.issue_code
                    == _analysis_opportunities.c.source_issue_code,
                )
            )
            .where(_analysis_opportunities.c.opportunity_code == opportunity_code)
        )
        row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else dict(row)

    def upsert(
        self,
        opportunity: ReviewRecord,
        verified: float | None,
        realized: float | None,
        note: str,
    ) -> None:
        existing_id = self._connection.execute(
            select(_analysis_opportunity_reviews.c.id).where(
                _analysis_opportunity_reviews.c.opportunity_code
                == opportunity["opportunity_code"]
            )
        ).scalar_one_or_none()
        if existing_id is None:
            self._connection.execute(
                insert(_analysis_opportunity_reviews).values(
                    batch_id=opportunity["batch_id"],
                    domain=opportunity["domain"],
                    opportunity_code=opportunity["opportunity_code"],
                    opportunity_type=opportunity["opportunity_type"],
                    source_issue_code=opportunity["source_issue_code"],
                    estimated_recoverable_amount=opportunity["recoverable_amount"],
                    estimated_saving_amount=opportunity[
                        "saving_opportunity_amount"
                    ],
                    verified_recoverable_amount=verified,
                    realized_saving_amount=realized,
                    review_note=note,
                )
            )
            return
        values: dict[str, Any] = {
            "review_note": note,
            "updated_at": func.current_timestamp(),
        }
        if verified is not None:
            values["verified_recoverable_amount"] = verified
        if realized is not None:
            values["realized_saving_amount"] = realized
        self._connection.execute(
            update(_analysis_opportunity_reviews)
            .where(_analysis_opportunity_reviews.c.id == existing_id)
            .values(**values)
        )

    def sync_note(self, issue_code: str, note: str) -> None:
        self._connection.execute(
            update(_analysis_opportunity_reviews)
            .where(_analysis_opportunity_reviews.c.source_issue_code == issue_code)
            .values(review_note=note, updated_at=func.current_timestamp())
        )

    def load_payload(self, opportunity_code: str) -> ReviewRecord | None:
        statement = (
            select(
                _analysis_opportunities.c.opportunity_code,
                _issues.c.issue_code,
                _issues.c.status.label("issue_status"),
                _issues.c.correction_value,
                _issues.c.correction_note,
                _analysis_opportunity_reviews.c.verified_recoverable_amount,
                _analysis_opportunity_reviews.c.realized_saving_amount,
                _analysis_opportunity_reviews.c.review_note,
                _analysis_opportunity_reviews.c.updated_at.label("reviewed_at"),
            )
            .select_from(
                _analysis_opportunities.outerjoin(
                    _issues,
                    _issues.c.issue_code
                    == _analysis_opportunities.c.source_issue_code,
                ).outerjoin(
                    _analysis_opportunity_reviews,
                    _analysis_opportunity_reviews.c.opportunity_code
                    == _analysis_opportunities.c.opportunity_code,
                )
            )
            .where(_analysis_opportunities.c.opportunity_code == opportunity_code)
        )
        row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else dict(row)

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        try:
            with self._connection.begin_nested():
                yield
        except DBAPIError as exc:
            raise PersistenceError(str(exc.orig)) from exc


class SqliteCorrectionRepository(CorrectionRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def record_return(
        self,
        *,
        source_file: str,
        matched_count: int,
        errors_json: str,
        warnings_json: str,
    ) -> None:
        self._connection.execute(
            insert(_correction_returns).values(
                source_file=source_file,
                matched_count=matched_count,
                error_count=len(json.loads(errors_json)),
                errors_json=errors_json,
                warning_count=len(json.loads(warnings_json)),
                warnings_json=warnings_json,
            )
        )


class SqliteExportRepository(ExportRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def issue_rows(self, batch_id: int) -> list[IssueRecord]:
        effective_row_json = case(
            (_ledger_rows.c.ledger_type == "tower_rent",
             func.coalesce(_authoritative_tower_rent_sources.c.frozen_json,
                           _authoritative_tower_rents.c.current_json,
                           _raw_rows.c.row_json, _ledger_rows.c.row_json)),
            (_ledger_rows.c.row_json != "{}", _ledger_rows.c.row_json),
            else_=func.coalesce(_raw_rows.c.row_json, _ledger_rows.c.row_json),
        ).label("row_json")
        statement = (
            select(
                _issues,
                _audit_results.c.field_name,
                effective_row_json,
                _ledger_rows.c.sheet_name,
                _ledger_rows.c.row_number,
            )
            .select_from(
                _issues.join(
                    _audit_results,
                    _audit_results.c.id == _issues.c.audit_result_id,
                )
                .outerjoin(
                    _ledger_rows,
                    _ledger_rows.c.id == _audit_results.c.ledger_row_id,
                )
                .outerjoin(
                    _raw_rows,
                    _raw_rows.c.id == _ledger_rows.c.raw_row_id,
                ).outerjoin(_authoritative_tower_rent_sources,
                    _ledger_rows.c.id == _authoritative_tower_rent_sources.c.ledger_row_id,
                ).outerjoin(_authoritative_tower_rents,
                    _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id,
                )
            )
            .where(
                _issues.c.batch_id == batch_id,
                _issues.c.status != "resolved_by_reaudit",
            )
            .order_by(_issues.c.city, _issues.c.severity, _issues.c.issue_code)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def mark_exported(
        self,
        issues: list[IssueRecord],
        *,
        note: str,
    ) -> None:
        if not issues:
            return
        ids = [int(issue["id"]) for issue in issues]
        self._connection.execute(
            update(_issues)
            .where(_issues.c.id.in_(ids))
            .values(status="pending_correction", updated_at=func.current_timestamp())
        )
        self._connection.execute(
            insert(_issue_events),
            [
                {
                    "issue_id": issue["id"],
                    "from_status": issue["status"],
                    "to_status": "pending_correction",
                    "source": "export",
                    "note": note,
                }
                for issue in issues
            ],
        )


class SqliteAnalysisRepository(AnalysisRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def source_issues(self, batch_id: int, ledger_type: str) -> list[IssueRecord]:
        effective_row_json = case(
            (_ledger_rows.c.ledger_type == "tower_rent",
             func.coalesce(_authoritative_tower_rent_sources.c.frozen_json,
                           _authoritative_tower_rents.c.current_json,
                           _raw_rows.c.row_json, _ledger_rows.c.row_json)),
            (_ledger_rows.c.row_json != "{}", _ledger_rows.c.row_json),
            else_=func.coalesce(_raw_rows.c.row_json, _ledger_rows.c.row_json),
        ).label("row_json")
        statement = (
            select(
                _issues.c.id,
                _issues.c.issue_code,
                _issues.c.rule_id,
                _issues.c.severity,
                _issues.c.city,
                _issues.c.district,
                _issues.c.telecom_site_code,
                _issues.c.telecom_site_name,
                _issues.c.message,
                _issues.c.suggestion,
                _audit_results.c.ledger_row_id,
                effective_row_json,
            )
            .select_from(
                _issues.join(
                    _audit_results,
                    _audit_results.c.id == _issues.c.audit_result_id,
                )
                .join(
                    _ledger_rows,
                    _ledger_rows.c.id == _audit_results.c.ledger_row_id,
                )
                .outerjoin(
                    _raw_rows,
                    _raw_rows.c.id == _ledger_rows.c.raw_row_id,
                ).outerjoin(_authoritative_tower_rent_sources,
                    _ledger_rows.c.id == _authoritative_tower_rent_sources.c.ledger_row_id,
                ).outerjoin(_authoritative_tower_rents,
                    _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id,
                )
            )
            .where(
                _issues.c.batch_id == batch_id,
                _issues.c.ledger_type == ledger_type,
                _issues.c.status != "resolved_by_reaudit",
            )
            .order_by(_issues.c.id)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def clear_domain(self, batch_id: int, domain: str) -> None:
        self._connection.execute(
            delete(_analysis_opportunities).where(
                _analysis_opportunities.c.batch_id == batch_id,
                _analysis_opportunities.c.domain == domain,
            )
        )

    def add_opportunity(self, record: AnalysisOpportunityRecord) -> None:
        self._connection.execute(insert(_analysis_opportunities).values(**record.__dict__))

    def ledger_overview(self, batch_id: int, ledger_type: str) -> ReviewRecord:
        statement = select(
            func.count().label("row_count"),
            func.count(func.distinct(_ledger_rows.c.telecom_site_code)).label(
                "site_count"
            ),
        ).where(
            _ledger_rows.c.batch_id == batch_id,
            _ledger_rows.c.ledger_type == ledger_type,
        )
        return dict(self._connection.execute(statement).mappings().one())

    def ledger_payloads(self, batch_id: int, ledger_type: str) -> list[ReviewRecord]:
        effective_row_json = case(
            (_ledger_rows.c.ledger_type == "tower_rent",
             func.coalesce(_authoritative_tower_rent_sources.c.frozen_json,
                           _authoritative_tower_rents.c.current_json,
                           _raw_rows.c.row_json, _ledger_rows.c.row_json)),
            (_ledger_rows.c.row_json != "{}", _ledger_rows.c.row_json),
            else_=func.coalesce(_raw_rows.c.row_json, _ledger_rows.c.row_json),
        ).label("row_json")
        statement = (
            select(effective_row_json)
            .select_from(
                _ledger_rows.outerjoin(
                    _raw_rows,
                    _raw_rows.c.id == _ledger_rows.c.raw_row_id,
                ).outerjoin(_authoritative_tower_rent_sources,
                    _ledger_rows.c.id == _authoritative_tower_rent_sources.c.ledger_row_id,
                ).outerjoin(_authoritative_tower_rents,
                    _authoritative_tower_rent_sources.c.rent_id == _authoritative_tower_rents.c.id,
                )
            )
            .where(
                _ledger_rows.c.batch_id == batch_id,
                _ledger_rows.c.ledger_type == ledger_type,
            )
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def opportunity_summary(self, batch_id: int, domain: str) -> ReviewRecord:
        statement = select(
            func.count().label("opportunity_count"),
            func.count(
                func.distinct(_analysis_opportunities.c.telecom_site_code)
            ).label("abnormal_site_count"),
            func.coalesce(func.sum(_analysis_opportunities.c.current_amount), 0).label(
                "current_amount"
            ),
            func.coalesce(
                func.sum(_analysis_opportunities.c.recoverable_amount), 0
            ).label("recoverable_amount"),
            func.coalesce(
                func.sum(_analysis_opportunities.c.saving_opportunity_amount), 0
            ).label("saving_opportunity_amount"),
            func.sum(
                case(
                    (_analysis_opportunities.c.severity == "high", 1),
                    else_=0,
                )
            ).label("high_risk_count"),
        ).where(
            _analysis_opportunities.c.batch_id == batch_id,
            _analysis_opportunities.c.domain == domain,
        )
        return dict(self._connection.execute(statement).mappings().one())

    def was_generated(self, batch_id: int, operation: str) -> bool:
        statement = select(_operation_logs.c.id).where(
            _operation_logs.c.batch_id == batch_id,
            _operation_logs.c.operation == operation,
        ).limit(1)
        return self._connection.execute(statement).first() is not None

    def opportunities(self, query: AnalysisQuery) -> list[ReviewRecord]:
        conditions = [
            _analysis_opportunities.c.batch_id == query.batch_id,
            _analysis_opportunities.c.domain == query.domain,
        ]
        for value, column in (
            (query.city, _analysis_opportunities.c.city),
            (query.opportunity_type, _analysis_opportunities.c.opportunity_type),
            (query.severity, _analysis_opportunities.c.severity),
            (query.confidence, _analysis_opportunities.c.confidence),
            (query.status, _issues.c.status),
        ):
            if value:
                conditions.append(column == value)
        if query.queue == "actionable":
            conditions.append(
                _issues.c.status.in_(
                    (
                        "pending_export",
                        "pending_correction",
                        "returned",
                        "needs_review",
                        "still_invalid",
                    )
                )
            )
        if query.review == "verified":
            conditions.append(
                _analysis_opportunity_reviews.c.verified_recoverable_amount.is_not(
                    None
                )
            )
        elif query.review == "realized":
            conditions.append(
                _analysis_opportunity_reviews.c.realized_saving_amount.is_not(None)
            )
        statement = (
            select(
                _analysis_opportunities,
                _issues.c.issue_code,
                _issues.c.status.label("issue_status"),
                _issues.c.correction_value,
                _issues.c.correction_note,
                _analysis_opportunity_reviews.c.verified_recoverable_amount,
                _analysis_opportunity_reviews.c.realized_saving_amount,
                _analysis_opportunity_reviews.c.review_note,
                _analysis_opportunity_reviews.c.updated_at.label("reviewed_at"),
            )
            .select_from(
                _analysis_opportunities.outerjoin(
                    _issues,
                    _issues.c.issue_code
                    == _analysis_opportunities.c.source_issue_code,
                ).outerjoin(
                    _analysis_opportunity_reviews,
                    _analysis_opportunity_reviews.c.opportunity_code
                    == _analysis_opportunities.c.opportunity_code,
                )
            )
            .where(*conditions)
        )
        if query.queue == "actionable":
            statement = statement.order_by(
                case(
                    (_analysis_opportunities.c.severity == "high", 0),
                    (_analysis_opportunities.c.severity == "medium", 1),
                    else_=2,
                ),
                case(
                    (_issues.c.status == "needs_review", 0),
                    (_issues.c.status == "returned", 1),
                    (_issues.c.status == "still_invalid", 2),
                    (_issues.c.status == "pending_correction", 3),
                    (_issues.c.status == "pending_export", 4),
                    else_=5,
                ),
            )
        statement = statement.order_by(
            _analysis_opportunities.c.recoverable_amount.desc(),
            _analysis_opportunities.c.saving_opportunity_amount.desc(),
            _analysis_opportunities.c.id,
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def breakdown(
        self,
        batch_id: int,
        domain: str,
        field: str,
    ) -> list[ReviewRecord]:
        columns = {
            "city": func.coalesce(_analysis_opportunities.c.city, "未填地市"),
            "opportunity_type": _analysis_opportunities.c.opportunity_type,
        }
        column = columns[field]
        statement = (
            select(
                column.label(field),
                func.count().label("item_count"),
                func.coalesce(
                    func.sum(_analysis_opportunities.c.current_amount), 0
                ).label("current_amount"),
                func.coalesce(
                    func.sum(_analysis_opportunities.c.recoverable_amount), 0
                ).label("recoverable_amount"),
                func.coalesce(
                    func.sum(_analysis_opportunities.c.saving_opportunity_amount), 0
                ).label("saving_opportunity_amount"),
            )
            .where(
                _analysis_opportunities.c.batch_id == batch_id,
                _analysis_opportunities.c.domain == domain,
            )
            .group_by(column)
            .order_by(
                func.sum(_analysis_opportunities.c.recoverable_amount).desc(),
                func.sum(
                    _analysis_opportunities.c.saving_opportunity_amount
                ).desc(),
                func.sum(_analysis_opportunities.c.current_amount).desc(),
            )
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def review_summary(self, batch_id: int, domain: str) -> ReviewRecord:
        statement = (
            select(
                func.sum(
                    case(
                        (
                            _issues.c.status.in_(
                                (
                                    "pending_export",
                                    "pending_correction",
                                    "still_invalid",
                                )
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("pending_count"),
                func.sum(case((_issues.c.status == "returned", 1), else_=0)).label(
                    "returned_count"
                ),
                func.sum(
                    case((_issues.c.status == "needs_review", 1), else_=0)
                ).label("needs_review_count"),
                func.sum(
                    case(
                        (_issues.c.status.in_(("returned", "needs_review")), 1),
                        else_=0,
                    )
                ).label("review_count"),
                func.sum(
                    case(
                        (_issues.c.status.in_(_CLOSED_ISSUE_STATUSES), 1),
                        else_=0,
                    )
                ).label("closed_count"),
                func.coalesce(
                    func.sum(
                        _analysis_opportunity_reviews.c.verified_recoverable_amount
                    ),
                    0,
                ).label("verified_recoverable_amount"),
                func.coalesce(
                    func.sum(_analysis_opportunity_reviews.c.realized_saving_amount),
                    0,
                ).label("realized_saving_amount"),
            )
            .select_from(
                _analysis_opportunities.outerjoin(
                    _issues,
                    _issues.c.issue_code
                    == _analysis_opportunities.c.source_issue_code,
                ).outerjoin(
                    _analysis_opportunity_reviews,
                    _analysis_opportunity_reviews.c.opportunity_code
                    == _analysis_opportunities.c.opportunity_code,
                )
            )
            .where(
                _analysis_opportunities.c.batch_id == batch_id,
                _analysis_opportunities.c.domain == domain,
            )
        )
        return dict(self._connection.execute(statement).mappings().one())


class SqliteArchiveRepository(ArchiveRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def eligibility(self, batch_id: int) -> ReviewRecord:
        batch = self._connection.execute(
            select(_import_batches.c.status, _import_batches.c.is_archived).where(
                _import_batches.c.id == batch_id
            )
        ).mappings().one_or_none()
        if batch is None:
            raise ValueError("batch not found")
        rows = self._connection.execute(
            select(_issues.c.status, func.count().label("count"))
            .where(_issues.c.batch_id == batch_id)
            .group_by(_issues.c.status)
        ).mappings()
        status_counts = {str(row["status"]): int(row["count"]) for row in rows}
        reviewed_closure = self._connection.execute(
            select(_analysis_opportunity_reviews.c.id)
            .select_from(
                _analysis_opportunity_reviews.join(
                    _issues,
                    _issues.c.issue_code
                    == _analysis_opportunity_reviews.c.source_issue_code,
                )
            )
            .where(
                _analysis_opportunity_reviews.c.batch_id == batch_id,
                _issues.c.batch_id == batch_id,
                _issues.c.status.in_(_CLOSED_ISSUE_STATUSES),
            )
            .limit(1)
        ).first()
        high_risk_open = self._connection.execute(
            select(func.count())
            .select_from(_issues)
            .where(
                _issues.c.batch_id == batch_id,
                _issues.c.severity == "high",
                ~_issues.c.status.in_(_CLOSED_ISSUE_STATUSES),
            )
        ).scalar_one()
        return {
            "status": batch["status"],
            "is_archived": batch["is_archived"],
            "status_counts": status_counts,
            "audited_reviewed_closure": bool(reviewed_closure),
            "high_risk_open": int(high_risk_open),
        }

    def severity_counts(self, batch_id: int) -> list[IssueRecord]:
        statement = (
            select(_issues.c.severity, func.count().label("count"))
            .where(_issues.c.batch_id == batch_id)
            .group_by(_issues.c.severity)
            .order_by(func.count().desc(), _issues.c.severity)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def issue_snapshot(
        self,
        batch_id: int,
        *,
        open_only: bool = False,
    ) -> list[IssueRecord]:
        conditions = [_issues.c.batch_id == batch_id]
        if open_only:
            conditions.append(~_issues.c.status.in_(_CLOSED_ISSUE_STATUSES))
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
                _issues.c.correction_note,
            )
            .where(*conditions)
            .order_by(_issues.c.city, _issues.c.issue_code)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def specialist_reviews(self, batch_id: int) -> list[ReviewRecord]:
        statement = (
            select(
                _analysis_opportunity_reviews.c.batch_id,
                _analysis_opportunity_reviews.c.domain,
                _analysis_opportunity_reviews.c.opportunity_code,
                _analysis_opportunity_reviews.c.opportunity_type,
                _analysis_opportunity_reviews.c.source_issue_code,
                _issues.c.status.label("issue_status"),
                func.coalesce(_issues.c.city, "未填地市").label("city"),
                _issues.c.telecom_site_code,
                _issues.c.telecom_site_name,
                _analysis_opportunity_reviews.c.estimated_recoverable_amount,
                _analysis_opportunity_reviews.c.estimated_saving_amount,
                _analysis_opportunity_reviews.c.verified_recoverable_amount,
                _analysis_opportunity_reviews.c.realized_saving_amount,
                _analysis_opportunity_reviews.c.review_note,
                _analysis_opportunity_reviews.c.updated_at,
            )
            .select_from(
                _analysis_opportunity_reviews.join(
                    _issues,
                    _issues.c.issue_code
                    == _analysis_opportunity_reviews.c.source_issue_code,
                )
            )
            .where(_analysis_opportunity_reviews.c.batch_id == batch_id)
            .order_by(
                _analysis_opportunity_reviews.c.domain,
                _analysis_opportunity_reviews.c.opportunity_code,
            )
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def operation_logs(self, batch_id: int) -> list[BatchRecord]:
        statement = (
            select(
                _operation_logs.c.operation,
                _operation_logs.c.message,
                _operation_logs.c.created_at,
            )
            .where(_operation_logs.c.batch_id == batch_id)
            .order_by(_operation_logs.c.id)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def rule_counts(self, batch_id: int) -> list[IssueRecord]:
        statement = (
            select(
                _issues.c.rule_id,
                _issues.c.severity,
                func.count().label("count"),
            )
            .where(_issues.c.batch_id == batch_id)
            .group_by(_issues.c.rule_id, _issues.c.severity)
            .order_by(_issues.c.rule_id)
        )
        return [dict(row) for row in self._connection.execute(statement).mappings()]


class SqliteDashboardRepository(DashboardRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def summary_rows(self, batch_id: int) -> dict[str, Any]:
        issue_filter = _issues.c.batch_id == batch_id
        return {
            "ledger_counts": self._rows(
                select(_ledger_rows.c.ledger_type, func.count().label("count"))
                .where(_ledger_rows.c.batch_id == batch_id)
                .group_by(_ledger_rows.c.ledger_type)
            ),
            "issues_by_city": self._rows(
                select(_issues.c.city, func.count().label("count"))
                .where(issue_filter)
                .group_by(_issues.c.city)
            ),
            "issues_by_rule": self._rows(
                select(_issues.c.rule_id, func.count().label("count"))
                .where(issue_filter)
                .group_by(_issues.c.rule_id)
                .order_by(func.count().desc())
            ),
            "issues_by_severity": self._rows(
                select(_issues.c.severity, func.count().label("count"))
                .where(issue_filter)
                .group_by(_issues.c.severity)
                .order_by(func.count().desc(), _issues.c.severity)
            ),
            "issues_by_ledger_type": self._rows(
                select(_issues.c.ledger_type, func.count().label("count"))
                .where(issue_filter)
                .group_by(_issues.c.ledger_type)
                .order_by(func.count().desc(), _issues.c.ledger_type)
            ),
            "issue_categories": self._rows(
                select(
                    _issues.c.ledger_type,
                    _issues.c.rule_id,
                    _issues.c.severity,
                    func.count().label("count"),
                )
                .where(issue_filter)
                .group_by(
                    _issues.c.ledger_type,
                    _issues.c.rule_id,
                    _issues.c.severity,
                )
                .order_by(
                    func.count().desc(),
                    _issues.c.ledger_type,
                    _issues.c.rule_id,
                )
            ),
            "city_rule_matrix": self._rows(
                select(
                    func.coalesce(_issues.c.city, "未填地市").label("city"),
                    _issues.c.ledger_type,
                    _issues.c.rule_id,
                    func.count().label("count"),
                )
                .where(issue_filter)
                .group_by(
                    func.coalesce(_issues.c.city, "未填地市"),
                    _issues.c.ledger_type,
                    _issues.c.rule_id,
                )
                .order_by(
                    func.coalesce(_issues.c.city, "未填地市"),
                    _issues.c.ledger_type,
                    func.count().desc(),
                    _issues.c.rule_id,
                )
            ),
            "city_ledger_matrix": self._rows(
                select(
                    func.coalesce(_issues.c.city, "未填地市").label("city"),
                    _issues.c.ledger_type,
                    func.count().label("count"),
                )
                .where(issue_filter)
                .group_by(
                    func.coalesce(_issues.c.city, "未填地市"),
                    _issues.c.ledger_type,
                )
                .order_by(
                    func.coalesce(_issues.c.city, "未填地市"),
                    func.count().desc(),
                    _issues.c.ledger_type,
                )
            ),
            "city_severity_matrix": self._rows(
                select(
                    func.coalesce(_issues.c.city, "未填地市").label("city"),
                    _issues.c.severity,
                    func.count().label("count"),
                )
                .where(issue_filter)
                .group_by(
                    func.coalesce(_issues.c.city, "未填地市"),
                    _issues.c.severity,
                )
                .order_by(
                    func.coalesce(_issues.c.city, "未填地市"),
                    func.count().desc(),
                    _issues.c.severity,
                )
            ),
            "status_counts": self._rows(
                select(_issues.c.status, func.count().label("count"))
                .where(issue_filter)
                .group_by(_issues.c.status)
            ),
            "rule_effectiveness": self._rows(
                select(
                    _issues.c.rule_id,
                    _issues.c.severity,
                    func.count().label("total_count"),
                    func.sum(
                        case(
                            (~_issues.c.status.in_(_CLOSED_ISSUE_STATUSES), 1),
                            else_=0,
                        )
                    ).label("open_count"),
                    func.sum(
                        case(
                            (_issues.c.status.in_(_CLOSED_ISSUE_STATUSES), 1),
                            else_=0,
                        )
                    ).label("closed_count"),
                    func.sum(
                        case((_issues.c.status == "not_required", 1), else_=0)
                    ).label("not_required_count"),
                    func.sum(
                        case((_issues.c.status == "still_invalid", 1), else_=0)
                    ).label("still_invalid_count"),
                )
                .where(issue_filter)
                .group_by(_issues.c.rule_id, _issues.c.severity)
                .order_by(
                    case(
                        (_issues.c.severity == "high", 0),
                        (_issues.c.severity == "medium", 1),
                        else_=2,
                    ),
                    func.count().desc(),
                    _issues.c.rule_id,
                )
            ),
        }

    def rule_effectiveness(self, batch_id: int) -> list[IssueRecord]:
        statement = (
            select(
                _issues.c.rule_id,
                func.count().label("total_count"),
                func.sum(
                    case(
                        (~_issues.c.status.in_(_CLOSED_ISSUE_STATUSES), 1),
                        else_=0,
                    )
                ).label("open_count"),
                func.sum(
                    case((_issues.c.status == "not_required", 1), else_=0)
                ).label("not_required_count"),
                func.sum(
                    case((_issues.c.status == "still_invalid", 1), else_=0)
                ).label("still_invalid_count"),
            )
            .where(_issues.c.batch_id == batch_id)
            .group_by(_issues.c.rule_id)
        )
        return [
            dict(row)
            for row in self._connection.execute(statement).mappings()
        ]

    def _rows(self, statement: Any) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(statement).mappings()
        ]


class SqliteRecentFileRepository(RecentFileRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def record(
        self,
        *,
        path: str,
        kind: str,
        ok: bool,
        ledger_counts_json: str,
        error_count: int,
    ) -> None:
        from governance_app.request_context import current_principal

        principal = current_principal()
        timestamp = _current_timestamp(self._connection)
        statement = _dialect_insert(self._connection, _recent_files).values(
            path=path,
            kind=kind,
            ok=1 if ok else 0,
            ledger_counts_json=ledger_counts_json,
            error_count=error_count,
            organization_id=(
                None
                if principal is None
                else principal.organization_id
            ),
            last_used_at=timestamp,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[_recent_files.c.path],
            set_={
                "kind": statement.excluded.kind,
                "ok": statement.excluded.ok,
                "ledger_counts_json": statement.excluded.ledger_counts_json,
                "error_count": statement.excluded.error_count,
                "organization_id": statement.excluded.organization_id,
                "last_used_at": timestamp,
            },
        )
        self._connection.execute(statement)

    def list(self, limit: int = 10) -> list[dict[str, Any]]:
        from governance_app.request_context import current_principal

        statement = (
            select(
                _recent_files.c.path,
                _recent_files.c.kind,
                _recent_files.c.ok,
                _recent_files.c.ledger_counts_json,
                _recent_files.c.error_count,
                _recent_files.c.last_used_at,
            )
            .order_by(_recent_files.c.last_used_at.desc())
            .limit(limit)
        )
        principal = current_principal()
        if principal is not None and principal.data_scope != "all":
            statement = statement.where(
                _recent_files.c.organization_id
                == principal.organization_id
            )
        return [
            dict(row)
            for row in self._connection.execute(statement).mappings()
        ]


class SqliteRuleSettingRepository(RuleSettingRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def upsert(
        self,
        rule_id: str,
        *,
        enabled: bool,
        config_json: str,
    ) -> None:
        statement = _dialect_insert(
            self._connection,
            _audit_rule_settings,
        ).values(
            rule_id=rule_id,
            enabled=1 if enabled else 0,
            config_json=config_json,
            updated_at=func.current_timestamp(),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[_audit_rule_settings.c.rule_id],
            set_={
                "enabled": statement.excluded.enabled,
                "config_json": statement.excluded.config_json,
                "updated_at": func.current_timestamp(),
            },
        )
        self._connection.execute(statement)

    def list(self) -> list[dict[str, Any]]:
        statement = select(
            _audit_rule_settings.c.rule_id,
            _audit_rule_settings.c.enabled,
            _audit_rule_settings.c.config_json,
        )
        return [
            dict(row)
            for row in self._connection.execute(statement).mappings()
        ]


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: Connection) -> None:
        self.batches = SqliteBatchRepository(connection)
        self.issues = SqliteIssueRepository(connection)
        self.ledgers = SqliteLedgerRepository(connection)
        self.audits = SqliteAuditRepository(connection)
        self.reviews = SqliteReviewRepository(connection)
        self.corrections = SqliteCorrectionRepository(connection)
        self.exports = SqliteExportRepository(connection)
        self.analysis = SqliteAnalysisRepository(connection)
        self.archives = SqliteArchiveRepository(connection)
        self.dashboards = SqliteDashboardRepository(connection)
        self.recent_files = SqliteRecentFileRepository(connection)
        self.rule_settings = SqliteRuleSettingRepository(connection)


class SqliteDatabase:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = URL.create("sqlite+pysqlite", database=str(database_path))
        self._engine: Engine = create_engine(url, poolclass=NullPool)
        event.listen(self._engine, "connect", _configure_connection)

    @contextmanager
    def unit_of_work(self) -> Iterator[UnitOfWork]:
        try:
            with self._engine.begin() as connection:
                yield SqliteUnitOfWork(connection)
        except DBAPIError as exc:
            original = exc.orig
            if isinstance(original, BaseException):
                raise original from exc
            raise

    def dispose(self) -> None:
        self._engine.dispose()

    @contextmanager
    def operation_lock(self, key: str) -> Iterator[bool]:
        del key
        yield True


def _configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("pragma foreign_keys = on")
        cursor.execute("pragma busy_timeout = 5000")
    finally:
        cursor.close()


def _dialect_insert(connection: Connection, table: Table) -> Any:
    if connection.dialect.name == "postgresql":
        return postgresql_insert(table)
    return sqlite_insert(table)


def _current_timestamp(connection: Connection) -> Any:
    if connection.dialect.name == "sqlite":
        return func.strftime("%Y-%m-%d %H:%M:%f", "now")
    return func.current_timestamp()


def _issue_conditions(query: IssueQuery) -> list[Any]:
    conditions = [_issues.c.batch_id == query.batch_id]
    conditions.extend(_jurisdiction_conditions(_issues, query.jurisdictions))
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
    conditions.extend(_jurisdiction_conditions(_issues, query.jurisdictions))
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
    if query.row_id is not None:
        conditions.append(_ledger_rows.c.id == query.row_id)
    if query.jurisdictions is not None:
        conditions.extend(_jurisdiction_conditions(_ledger_rows, query.jurisdictions))
    for value, column in (
        (query.ledger_type, _ledger_rows.c.ledger_type),
        (query.city, func.coalesce(_ledger_rows.c.city, "未填地市")),
        (query.district, func.coalesce(_ledger_rows.c.district, "")),
        (query.site_code, func.coalesce(_ledger_rows.c.telecom_site_code, "")),
    ):
        if value:
            conditions.append(column == value)
    return conditions


def _jurisdiction_conditions(table: FromClause, pairs: tuple[tuple[str, str], ...] | None) -> list[Any]:
    if pairs is None:
        return []
    return [or_(*(
        and_(table.c.city == city, table.c.district == district)
        for city, district in pairs
    )) if pairs else table.c.id == -1]
