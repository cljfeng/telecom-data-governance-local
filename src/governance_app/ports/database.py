from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from governance_app.models import IssueStatus

BatchRecord = Mapping[str, Any]
IssueRecord = Mapping[str, Any]
LedgerRecord = Mapping[str, Any]
ReviewRecord = Mapping[str, Any]


class PersistenceError(RuntimeError):
    """A recoverable repository operation failed."""


@dataclass(frozen=True)
class IssueQuery:
    batch_id: int
    city: str | None = None
    ledger_type: str | None = None
    severity: str | None = None
    status: str | None = None
    rule_id: str | None = None
    closure: str | None = None
    limit: int = 500
    offset: int = 0


@dataclass(frozen=True)
class IssueGroupQuery:
    batch_id: int
    city: str | None = None
    ledger_type: str | None = None
    rule_id: str | None = None
    closure: str | None = None
    limit: int = 200


@dataclass(frozen=True)
class IssueGroupSelector:
    batch_id: int
    city: str
    ledger_type: str
    rule_id: str
    telecom_site_code: str


@dataclass(frozen=True)
class LedgerQuery:
    batch_id: int
    ledger_type: str | None = None
    city: str | None = None
    district: str | None = None
    site_code: str | None = None
    limit: int = 500
    offset: int = 0


@dataclass(frozen=True)
class ImportedLedgerRow:
    ledger_type: str
    sheet_name: str
    row_number: int
    row_json: str
    city: str | None
    district: str | None
    telecom_site_code: str | None
    telecom_site_name: str | None
    tower_site_code: str | None
    tower_site_name: str | None


@dataclass(frozen=True)
class AuditFindingRecord:
    audit_run_id: int
    batch_id: int
    ledger_row_id: int
    ledger_type: str
    city: str | None
    district: str | None
    telecom_site_code: str | None
    telecom_site_name: str | None
    rule_id: str
    severity: str
    message: str
    suggestion: str
    field_name: str | None
    result_json: str
    issue_code: str


@dataclass(frozen=True)
class AnalysisOpportunityRecord:
    batch_id: int
    ledger_row_id: int
    domain: str
    opportunity_code: str
    source_issue_code: str
    opportunity_type: str
    severity: str
    city: str
    district: str | None
    telecom_site_code: str | None
    telecom_site_name: str | None
    period: str | None
    meter_no: str | None
    current_amount: float
    reference_amount: float
    recoverable_amount: float
    saving_opportunity_amount: float
    confidence: str
    source_rule_ids_json: str
    message: str
    suggestion: str


@dataclass(frozen=True)
class AnalysisQuery:
    batch_id: int
    domain: str
    city: str | None = None
    opportunity_type: str | None = None
    severity: str | None = None
    confidence: str | None = None
    status: str | None = None
    queue: str | None = None
    review: str | None = None


class BatchRepository(Protocol):
    def create(self, *, name: str, batch_code: str) -> int: ...

    def create_imported(self, *, source_file: str, name: str, batch_code: str) -> int: ...

    def get(self, batch_id: int) -> BatchRecord | None: ...

    def list_all(self) -> list[BatchRecord]: ...

    def current_id(self) -> int | None: ...

    def set_current(self, batch_id: int) -> None: ...

    def update_status(self, batch_id: int, status: str, *, archive: bool = False) -> None: ...

    def update_source(self, batch_id: int, *, source_file: str, fallback_name: str) -> None: ...

    def add_operation(self, batch_id: int, operation: str, message: str) -> None: ...

    def recent_operations(self, batch_id: int, limit: int = 10) -> list[BatchRecord]: ...


class IssueRepository(Protocol):
    def query(self, query: IssueQuery) -> tuple[list[IssueRecord], int]: ...

    def rule_counts(self, batch_id: int) -> list[IssueRecord]: ...

    def groups(self, query: IssueGroupQuery) -> list[IssueRecord]: ...

    def get_with_batch(self, issue_code: str) -> IssueRecord | None: ...

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
    ) -> None: ...

    def update_group_status(
        self,
        selector: IssueGroupSelector,
        status: IssueStatus,
        *,
        source: str,
        event_note: str,
    ) -> int: ...

    def workflow_summary(self, batch_id: int) -> IssueRecord: ...

    def city_progress(self, batch_id: int) -> list[IssueRecord]: ...

    def top_rules_by_city(self, batch_id: int) -> list[IssueRecord]: ...


class LedgerRepository(Protocol):
    def query(self, query: LedgerQuery) -> list[LedgerRecord]: ...

    def count(self, query: LedgerQuery) -> int: ...

    def add_imported_row(self, batch_id: int, row: ImportedLedgerRow) -> None: ...

    def clear_batch_data(self, batch_id: int) -> None: ...

    def audit_rows(self, batch_id: int) -> list[LedgerRecord]: ...

    def site_authorities(self, batch_id: int) -> list[LedgerRecord]: ...

    def site_authority(self, batch_id: int, row_id: int) -> dict[str, Any] | None: ...

    def revise_site(self, batch_id: int, row_id: int, request: Mapping[str, Any]) -> tuple[int, bool]: ...


class AuditRepository(Protocol):
    def create_run(self, batch_id: int, rule_count: int) -> int: ...

    def save_finding(self, finding: AuditFindingRecord) -> None: ...

    def resolve_missing(
        self,
        batch_id: int,
        audit_run_id: int,
        seen_issue_codes: set[str],
    ) -> int: ...

    def clear_analysis_opportunities(self, batch_id: int) -> None: ...


class ReviewRepository(Protocol):
    def get_opportunity(self, opportunity_code: str) -> ReviewRecord | None: ...

    def upsert(
        self,
        opportunity: ReviewRecord,
        verified: float | None,
        realized: float | None,
        note: str,
    ) -> None: ...

    def sync_note(self, issue_code: str, note: str) -> None: ...

    def load_payload(self, opportunity_code: str) -> ReviewRecord | None: ...

    def savepoint(self) -> AbstractContextManager[None]: ...


class CorrectionRepository(Protocol):
    def record_return(
        self,
        *,
        source_file: str,
        matched_count: int,
        errors_json: str,
        warnings_json: str,
    ) -> None: ...


class ExportRepository(Protocol):
    def issue_rows(self, batch_id: int) -> list[IssueRecord]: ...

    def mark_exported(
        self,
        issues: list[IssueRecord],
        *,
        note: str,
    ) -> None: ...


class AnalysisRepository(Protocol):
    def source_issues(self, batch_id: int, ledger_type: str) -> list[IssueRecord]: ...

    def clear_domain(self, batch_id: int, domain: str) -> None: ...

    def add_opportunity(self, record: AnalysisOpportunityRecord) -> None: ...

    def ledger_overview(self, batch_id: int, ledger_type: str) -> ReviewRecord: ...

    def ledger_payloads(self, batch_id: int, ledger_type: str) -> list[ReviewRecord]: ...

    def opportunity_summary(self, batch_id: int, domain: str) -> ReviewRecord: ...

    def was_generated(self, batch_id: int, operation: str) -> bool: ...

    def opportunities(self, query: AnalysisQuery) -> list[ReviewRecord]: ...

    def breakdown(
        self,
        batch_id: int,
        domain: str,
        field: str,
    ) -> list[ReviewRecord]: ...

    def review_summary(self, batch_id: int, domain: str) -> ReviewRecord: ...


class ArchiveRepository(Protocol):
    def eligibility(self, batch_id: int) -> ReviewRecord: ...

    def severity_counts(self, batch_id: int) -> list[IssueRecord]: ...

    def issue_snapshot(
        self,
        batch_id: int,
        *,
        open_only: bool = False,
    ) -> list[IssueRecord]: ...

    def specialist_reviews(self, batch_id: int) -> list[ReviewRecord]: ...

    def operation_logs(self, batch_id: int) -> list[BatchRecord]: ...

    def rule_counts(self, batch_id: int) -> list[IssueRecord]: ...


class DashboardRepository(Protocol):
    def summary_rows(self, batch_id: int) -> Mapping[str, Any]: ...

    def rule_effectiveness(self, batch_id: int) -> list[IssueRecord]: ...


class RecentFileRepository(Protocol):
    def record(
        self,
        *,
        path: str,
        kind: str,
        ok: bool,
        ledger_counts_json: str,
        error_count: int,
    ) -> None: ...

    def list(self, limit: int = 10) -> list[dict[str, Any]]: ...


class RuleSettingRepository(Protocol):
    def upsert(
        self,
        rule_id: str,
        *,
        enabled: bool,
        config_json: str,
    ) -> None: ...

    def list(self) -> list[dict[str, Any]]: ...


class UnitOfWork(Protocol):
    batches: BatchRepository
    issues: IssueRepository
    ledgers: LedgerRepository
    audits: AuditRepository
    reviews: ReviewRepository
    corrections: CorrectionRepository
    exports: ExportRepository
    analysis: AnalysisRepository
    archives: ArchiveRepository
    dashboards: DashboardRepository
    recent_files: RecentFileRepository
    rule_settings: RuleSettingRepository


class Database(Protocol):
    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]: ...

    def operation_lock(
        self,
        key: str,
    ) -> AbstractContextManager[bool]: ...
