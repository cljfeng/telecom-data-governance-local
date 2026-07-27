from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

StorageArea = Literal["uploads", "exports", "backups"]


@dataclass(frozen=True)
class StoredFile:
    file_id: str
    name: str
    local_path: Path
    url: str | None = None


class FileStorage(Protocol):
    def healthcheck(self) -> None: ...

    def save_upload(self, filename: str, content: bytes) -> StoredFile: ...

    def prepare_export(self, relative_path: str) -> Path: ...

    def publish(self, path: Path) -> StoredFile: ...

    def resolve(self, file_id: str) -> StoredFile: ...

    def clear(
        self,
        area: StorageArea,
        *,
        keep_file_ids: set[str] | None = None,
    ) -> int: ...
