from pathlib import Path
from urllib.parse import ParseResult
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from governance_app.config import AppConfig
from governance_app.import_preview import (
    export_preview_errors,
    preview_error_payload,
    preview_error_summary,
    preview_workbook,
)
from governance_app.importer import import_workbook
from governance_app.operation_guard import OperationConflict, exclusive_operation
from governance_app.recent_files import list_recent_files
from governance_app.routes.common import (
    JsonResponse,
    json_body,
    json_response,
    save_uploaded_workbook,
)

_IMPORT_UPLOAD_PATHS = {"/api/import/upload", "/api/import/preview/upload"}


def handle_import_route(
    config: AppConfig,
    method: str,
    parsed: ParseResult,
    body: str,
) -> JsonResponse | None:
    if method == "POST" and parsed.path == "/api/import":
        payload, error = json_body(body)
        return error or _import_from_payload(config, payload)
    if method == "POST" and parsed.path == "/api/import/preview":
        payload, error = json_body(body)
        return error or _preview_from_payload(config, payload)
    if method == "GET" and parsed.path == "/api/import/recent":
        return json_response({"files": list_recent_files(config)})
    return None


def handle_import_upload(
    config: AppConfig,
    path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
) -> JsonResponse | None:
    if path not in _IMPORT_UPLOAD_PATHS:
        return None
    uploaded = files.get("file")
    if uploaded is None:
        return json_response({"error": "请选择台账文件"}, status=400)
    filename, content = uploaded
    if not content:
        return json_response({"error": "台账文件为空"}, status=400)
    try:
        workbook_path = save_uploaded_workbook(config, filename, content)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)
    payload: dict[str, object] = {"path": str(workbook_path)}
    payload.update(fields)
    if path == "/api/import/upload":
        return _import_from_payload(config, payload)
    return _preview_from_payload(config, payload)


def _preview_from_payload(config: AppConfig, payload: dict) -> JsonResponse:
    path_value = payload.get("path")
    if not isinstance(path_value, str) or not path_value:
        return json_response({"error": "path is required"}, status=400)
    workbook_path = Path(path_value)
    try:
        result = preview_workbook(config, workbook_path)
    except (OSError, InvalidFileException, BadZipFile) as exc:
        return json_response({"error": f"无法读取 Excel 文件：{exc}"}, status=400)
    error_export_path = ""
    if result.errors:
        error_export_path = str(export_preview_errors(config, workbook_path, result))
    return json_response(
        {
            "error": "" if result.ok else "预检未通过，请按错误明细修正后重试",
            "ok": result.ok,
            "batch_name": result.batch_name,
            "ledger_counts": result.ledger_counts,
            "errors": [preview_error_payload(error) for error in result.errors],
            "error_summary": preview_error_summary(result.errors),
            "error_export_path": error_export_path,
        },
        status=200 if result.ok else 400,
    )


def _import_from_payload(config: AppConfig, payload: dict) -> JsonResponse:
    path_value = payload.get("path")
    if not isinstance(path_value, str) or not path_value:
        return json_response({"error": "path is required"}, status=400)
    strategy = payload.get("strategy", "new")
    if not isinstance(strategy, str):
        return json_response({"error": "strategy must be string"}, status=400)
    batch_id = payload.get("batch_id")
    try:
        with exclusive_operation(config, "import"):
            result = import_workbook(
                config,
                Path(path_value),
                strategy=strategy,
                batch_id=int(batch_id) if batch_id not in (None, "") else None,
            )
    except OperationConflict as exc:
        return json_response({"error": str(exc)}, status=409)
    except (TypeError, ValueError, OSError, InvalidFileException, BadZipFile) as exc:
        return json_response({"error": str(exc)}, status=400)
    return json_response(
        {
            "error": "" if result.batch_id is not None else "导入未通过，请按错误明细修正后重试",
            "batch_id": result.batch_id,
            "ledger_counts": result.ledger_counts,
            "errors": [error.__dict__ for error in result.errors],
        },
        status=200 if result.batch_id is not None else 400,
    )
