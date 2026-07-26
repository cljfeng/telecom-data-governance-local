from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from governance_app.models import IssueStatus

BatchRecord = Mapping[str, Any]
IssueRecord = Mapping[str, Any]


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


class BatchRepository(Protocol):
    def create(self, *, name: str, batch_code: str) -> int: ...

    def get(self, batch_id: int) -> BatchRecord | None: ...

    def list_all(self) -> list[BatchRecord]: ...

    def current_id(self) -> int | None: ...

    def set_current(self, batch_id: int) -> None: ...

    def update_status(self, batch_id: int, status: str, *, archive: bool = False) -> None: ...

    def add_operation(self, batch_id: int, operation: str, message: str) -> None: ...


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
    ) -> None: ...

    def update_group_status(
        self,
        selector: IssueGroupSelector,
        status: IssueStatus,
        *,
        source: str,
        event_note: str,
    ) -> int: ...


class UnitOfWork(Protocol):
    batches: BatchRepository
    issues: IssueRepository


class Database(Protocol):
    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]: ...
