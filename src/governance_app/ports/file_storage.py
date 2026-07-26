from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredFile:
    file_id: str
    name: str
    local_path: Path
    url: str | None = None


class FileStorage(Protocol):
    def save_upload(self, filename: str, content: bytes) -> StoredFile: ...

    def publish(self, path: Path) -> StoredFile: ...

    def resolve(self, file_id: str) -> StoredFile: ...
