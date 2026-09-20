from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DatabaseCompaction:
    linked_ledger_rows: int
    deduplicated_ledger_rows: int


class DatabaseAdministration(Protocol):
    def compact(self) -> DatabaseCompaction: ...

    def reset_business_data(self) -> None: ...

    def create_backup(self, destination: Path) -> None: ...

    def restore_backup(self, source: Path) -> None: ...

    def check_integrity(self, path: Path) -> None: ...

    def schema_version(self, path: Path) -> int: ...
