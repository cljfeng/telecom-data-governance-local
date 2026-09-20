import json
from pathlib import Path
from urllib.parse import parse_qs

from governance_app.config import AppConfig, RuntimeMode
from governance_app.file_storage_runtime import file_storage_for
from governance_app.ports.file_storage import FileStorage, StoredFile

JsonResponse = tuple[int, dict[str, str], str | bytes]


def json_response(payload: dict, status: int = 200) -> JsonResponse:
    return (
        status,
        {"content-type": "application/json; charset=utf-8"},
        json.dumps(payload, ensure_ascii=False),
    )


def json_body(body: str) -> tuple[dict, JsonResponse | None]:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return {}, json_response({"error": "invalid json"}, status=400)
    if not isinstance(payload, dict):
        return {}, json_response({"error": "json object required"}, status=400)
    return payload, None


def batch_id_from_payload(payload: dict) -> tuple[int, JsonResponse | None]:
    try:
        raw_batch_id = payload.get("batch_id")
        if raw_batch_id is None:
            raise TypeError
        return int(raw_batch_id), None
    except (TypeError, ValueError):
        return 0, json_response({"error": "invalid batch_id"}, status=400)


def batch_id_from_query(query_string: str) -> tuple[int, JsonResponse | None]:
    query = parse_qs(query_string)
    try:
        return int(query.get("batch_id", ["1"])[0]), None
    except ValueError:
        return 0, json_response({"error": "invalid batch_id"}, status=400)


def pagination_from_query(query: dict[str, list[str]]) -> tuple[int | None, int]:
    raw_limit = query.get("limit", [""])[0]
    raw_offset = query.get("offset", ["0"])[0]
    if not raw_limit:
        return None, 0
    try:
        limit = max(1, min(int(raw_limit), 500))
        offset = max(0, int(raw_offset))
    except ValueError:
        return 50, 0
    return limit, offset


def store_uploaded_workbook(
    config: AppConfig,
    filename: str,
    content: bytes,
    *,
    storage: FileStorage | None = None,
) -> StoredFile:
    safe_name = Path(filename or "workbook.xlsx").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        raise ValueError("请选择 .xlsx 或 .xlsm 格式的 Excel 台账文件")
    return (storage or file_storage_for(config)).save_upload(safe_name, content)


def save_uploaded_workbook(
    config: AppConfig,
    filename: str,
    content: bytes,
    *,
    storage: FileStorage | None = None,
) -> Path:
    return store_uploaded_workbook(
        config,
        filename,
        content,
        storage=storage,
    ).local_path


def file_path_from_payload(
    config: AppConfig,
    payload: dict,
    *,
    storage: FileStorage | None = None,
) -> Path:
    selected_storage = storage or file_storage_for(config)
    file_id = payload.get("file_id")
    if isinstance(file_id, str) and file_id:
        return selected_storage.resolve(file_id).local_path
    path_value = payload.get("path")
    if (
        config.runtime_mode is RuntimeMode.LOCAL
        and isinstance(path_value, str)
        and path_value
    ):
        return Path(path_value)
    raise ValueError("file_id is required")


def workbook_path_from_payload(
    config: AppConfig,
    payload: dict,
    *,
    storage: FileStorage | None = None,
) -> Path:
    return file_path_from_payload(config, payload, storage=storage)


def file_payload(
    config: AppConfig,
    path: Path,
    *,
    storage: FileStorage | None = None,
) -> dict[str, str]:
    stored_file = (storage or file_storage_for(config)).publish(path)
    return stored_file_payload(config, stored_file)


def stored_file_payload(
    config: AppConfig,
    stored_file: StoredFile,
) -> dict[str, str]:
    payload = {
        "file_id": stored_file.file_id,
        "name": stored_file.name,
        "url": stored_file.url or "",
    }
    if config.runtime_mode is RuntimeMode.LOCAL:
        payload["path"] = str(stored_file.local_path)
    return payload


def file_location(payload: dict[str, str]) -> str:
    return payload.get("path") or payload.get("url", "")
