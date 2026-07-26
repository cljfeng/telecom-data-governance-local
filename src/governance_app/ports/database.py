from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Mapping, Protocol

BatchRecord = Mapping[str, Any]


class BatchRepository(Protocol):
    def create(self, *, name: str, batch_code: str) -> int: ...

    def get(self, batch_id: int) -> BatchRecord | None: ...

    def list_all(self) -> list[BatchRecord]: ...

    def current_id(self) -> int | None: ...

    def set_current(self, batch_id: int) -> None: ...

    def update_status(self, batch_id: int, status: str, *, archive: bool = False) -> None: ...

    def add_operation(self, batch_id: int, operation: str, message: str) -> None: ...


class UnitOfWork(Protocol):
    batches: BatchRepository


class Database(Protocol):
    def unit_of_work(self) -> AbstractContextManager[UnitOfWork]: ...
