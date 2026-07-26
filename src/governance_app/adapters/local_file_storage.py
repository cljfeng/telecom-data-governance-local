import shutil
from pathlib import Path
from uuid import uuid4

from governance_app.ports.file_storage import (
    FileStorage,
    StorageArea,
    StoredFile,
)

_FILE_AREAS = ("uploads", "exports", "backups")


class LocalFileStorage(FileStorage):
    def __init__(
        self,
        *,
        upload_dir: Path,
        export_dir: Path,
        backup_dir: Path,
    ) -> None:
        self._roots = {
            "uploads": upload_dir.resolve(),
            "exports": export_dir.resolve(),
            "backups": backup_dir.resolve(),
        }

    def save_upload(self, filename: str, content: bytes) -> StoredFile:
        safe_name = Path(filename or "workbook.xlsx").name
        upload_root = self._roots["uploads"]
        upload_root.mkdir(parents=True, exist_ok=True)
        path = upload_root / f"{uuid4().hex}-{safe_name}"
        path.write_bytes(content)
        return self._stored_file("uploads", path)

    def prepare_export(self, relative_path: str) -> Path:
        export_root = self._roots["exports"]
        path = (export_root / relative_path).resolve()
        if not path.is_relative_to(export_root):
            raise ValueError("导出路径越界")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def publish(self, path: Path) -> StoredFile:
        resolved = path.resolve()
        for area in _FILE_AREAS:
            root = self._roots[area]
            if resolved.is_relative_to(root):
                if not resolved.is_file():
                    raise FileNotFoundError(f"Stored file not found: {path}")
                return self._stored_file(area, resolved)
        raise ValueError("文件不在受管存储目录中")

    def resolve(self, file_id: str) -> StoredFile:
        area, separator, relative_value = file_id.partition(":")
        if not separator or area not in _FILE_AREAS or not relative_value:
            raise ValueError("文件引用无效")
        root = self._roots[area]
        path = (root / relative_value).resolve()
        if not path.is_relative_to(root):
            raise ValueError("文件引用无效")
        if not path.is_file():
            raise FileNotFoundError(f"Stored file not found: {file_id}")
        return self._stored_file(area, path)

    def clear(
        self,
        area: StorageArea,
        *,
        keep_file_ids: set[str] | None = None,
    ) -> int:
        root = self._roots[area]
        if not root.exists():
            return 0
        keep_paths = {
            self.resolve(file_id).local_path
            for file_id in (keep_file_ids or set())
        }
        removed = 0
        for item in root.iterdir():
            if item.resolve() in keep_paths:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        return removed

    def _stored_file(self, area: str, path: Path) -> StoredFile:
        root = self._roots[area]
        relative_path = path.resolve().relative_to(root)
        return StoredFile(
            file_id=f"{area}:{relative_path.as_posix()}",
            name=path.name,
            local_path=path.resolve(),
        )
