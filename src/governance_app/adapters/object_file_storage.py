import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast
from urllib.parse import quote
from uuid import uuid4

from governance_app.ports.file_storage import (
    FileStorage,
    StorageArea,
    StoredFile,
)
from governance_app.request_context import current_principal

_FILE_AREAS: tuple[StorageArea, ...] = (
    "uploads",
    "exports",
    "backups",
)


class ObjectStorageClient(Protocol):
    def put_object(self, **kwargs: Any) -> Any: ...

    def get_object(self, **kwargs: Any) -> Any: ...

    def head_object(self, **kwargs: Any) -> Any: ...

    def list_objects_v2(self, **kwargs: Any) -> Any: ...

    def delete_objects(self, **kwargs: Any) -> Any: ...


class ObjectFileStorage(FileStorage):
    def __init__(
        self,
        *,
        bucket: str,
        staging_dir: Path,
        prefix: str = "governance",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        client: ObjectStorageClient | None = None,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._roots = {
            area: (staging_dir / area).resolve()
            for area in _FILE_AREAS
        }
        self._client = client or _create_client(
            endpoint_url=endpoint_url,
            region_name=region_name,
        )

    def healthcheck(self) -> None:
        self._client.list_objects_v2(
            Bucket=self._bucket,
            Prefix=self._prefix,
            MaxKeys=1,
        )

    def save_upload(self, filename: str, content: bytes) -> StoredFile:
        safe_name = Path(filename or "workbook.xlsx").name
        relative_path = self._scoped_relative(
            f"{uuid4().hex}-{safe_name}"
        )
        path = self._stage("uploads", relative_path, content)
        return self._upload("uploads", relative_path, path)

    def prepare_export(self, relative_path: str) -> Path:
        normalized = self._scoped_relative(relative_path)
        path = (self._roots["exports"] / normalized).resolve()
        if not path.is_relative_to(self._roots["exports"]):
            raise ValueError("导出路径越界")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def publish(self, path: Path) -> StoredFile:
        resolved = path.resolve()
        for area in _FILE_AREAS:
            root = self._roots[area]
            if resolved.is_relative_to(root):
                if not resolved.is_file():
                    raise FileNotFoundError(
                        f"Stored file not found: {path}"
                    )
                relative_path = resolved.relative_to(root).as_posix()
                return self._upload(area, relative_path, resolved)
        raise ValueError("文件不在受管存储目录中")

    def resolve(self, file_id: str) -> StoredFile:
        area, relative_path = _parse_file_id(file_id)
        self._validate_scope(relative_path)
        key = self._key(area, relative_path)
        try:
            metadata = self._client.head_object(
                Bucket=self._bucket,
                Key=key,
            )
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=key,
            )
        except Exception as exc:
            if _is_missing_object(exc):
                raise FileNotFoundError(
                    f"Stored file not found: {file_id}"
                ) from exc
            raise
        body = response["Body"]
        try:
            content = body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        expected_digest = metadata.get("Metadata", {}).get("sha256")
        actual_digest = hashlib.sha256(content).hexdigest()
        if not expected_digest or expected_digest != actual_digest:
            raise ValueError("对象存储文件校验失败")
        path = self._stage(area, relative_path, content)
        return self._stored_file(area, relative_path, path)

    def clear(
        self,
        area: StorageArea,
        *,
        keep_file_ids: set[str] | None = None,
    ) -> int:
        keep_keys = {
            self._key(*_parse_file_id(file_id))
            for file_id in (keep_file_ids or set())
        }
        scope_prefix = f"{self._scope_id()}/"
        object_prefix = self._key(area, scope_prefix)
        continuation_token: str | None = None
        removed = 0
        while True:
            request: dict[str, Any] = {
                "Bucket": self._bucket,
                "Prefix": object_prefix,
            }
            if continuation_token:
                request["ContinuationToken"] = continuation_token
            response = self._client.list_objects_v2(**request)
            keys = [
                item["Key"]
                for item in response.get("Contents", [])
                if item["Key"] not in keep_keys
            ]
            if keys:
                self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in keys]},
                )
                removed += len(keys)
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
        self._clear_staging(area, keep_file_ids or set())
        return removed

    def _upload(
        self,
        area: StorageArea,
        relative_path: str,
        path: Path,
    ) -> StoredFile:
        content = path.read_bytes()
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._key(area, relative_path),
            Body=content,
            Metadata={"sha256": hashlib.sha256(content).hexdigest()},
        )
        return self._stored_file(area, relative_path, path)

    def _stage(
        self,
        area: StorageArea,
        relative_path: str,
        content: bytes,
    ) -> Path:
        normalized = _normalized_relative_path(relative_path)
        path = (self._roots[area] / normalized).resolve()
        if not path.is_relative_to(self._roots[area]):
            raise ValueError("文件引用无效")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _stored_file(
        self,
        area: StorageArea,
        relative_path: str,
        path: Path,
    ) -> StoredFile:
        file_id = f"{area}:{relative_path}"
        return StoredFile(
            file_id=file_id,
            name=Path(relative_path).name,
            local_path=path,
            url=f"/api/files/{quote(file_id, safe='')}",
        )

    def _key(self, area: StorageArea, relative_path: str) -> str:
        parts = [part for part in (self._prefix, area, relative_path) if part]
        key = "/".join(parts)
        return f"{key}/" if not relative_path else key

    def _clear_staging(
        self,
        area: StorageArea,
        keep_file_ids: set[str],
    ) -> None:
        keep_paths = {
            (self._roots[kept_area] / relative_path).resolve()
            for kept_area, relative_path in map(
                _parse_file_id,
                keep_file_ids,
            )
            if kept_area == area
        }
        root = self._roots[area] / str(self._scope_id())
        if not root.exists():
            return
        for path in root.rglob("*"):
            if path.is_file() and path.resolve() not in keep_paths:
                path.unlink()

    def _scoped_relative(self, relative_path: str) -> str:
        normalized = _normalized_relative_path(relative_path)
        return f"{self._scope_id()}/{normalized}"

    def _scope_id(self) -> int:
        principal = current_principal()
        return 0 if principal is None else principal.organization_id

    def _validate_scope(self, relative_path: str) -> None:
        principal = current_principal()
        if principal is None or principal.data_scope == "all":
            return
        raw_scope, separator, _remainder = relative_path.partition("/")
        if (
            not separator
            or not raw_scope.isdigit()
            or int(raw_scope) != principal.organization_id
        ):
            raise FileNotFoundError("Stored file not found")


def _normalized_relative_path(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    if (
        not relative_path
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("文件引用无效")
    return path.as_posix()


def _parse_file_id(file_id: str) -> tuple[StorageArea, str]:
    area, separator, relative_path = file_id.partition(":")
    if not separator or area not in _FILE_AREAS:
        raise ValueError("文件引用无效")
    normalized = _normalized_relative_path(relative_path)
    return area, normalized


def _create_client(
    *,
    endpoint_url: str | None,
    region_name: str | None,
) -> ObjectStorageClient:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "online object storage requires the 'online' dependencies"
        ) from exc
    return cast(
        ObjectStorageClient,
        boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
        ),
    )


def _is_missing_object(error: Exception) -> bool:
    if isinstance(error, KeyError):
        return True
    response = getattr(error, "response", {})
    error_code = response.get("Error", {}).get("Code")
    status_code = response.get("ResponseMetadata", {}).get(
        "HTTPStatusCode"
    )
    return error_code in {"404", "NoSuchKey", "NotFound"} or status_code == 404
