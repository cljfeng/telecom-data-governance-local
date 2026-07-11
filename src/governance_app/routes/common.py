import json
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

from governance_app.config import AppConfig

JsonResponse = tuple[int, dict[str, str], str]


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


def save_uploaded_workbook(config: AppConfig, filename: str, content: bytes) -> Path:
    safe_name = Path(filename or "workbook.xlsx").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        raise ValueError("请选择 .xlsx 或 .xlsm 格式的 Excel 台账文件")
    upload_dir = config.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{uuid4().hex}-{safe_name}"
    target.write_bytes(content)
    return target
