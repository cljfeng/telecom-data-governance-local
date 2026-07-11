from pathlib import Path
from urllib.parse import ParseResult

from governance_app.archive import archive_batch, archive_precheck, export_notice_report
from governance_app.config import AppConfig
from governance_app.corrections import import_correction_return
from governance_app.exporter import export_issue_packages
from governance_app.operation_guard import OperationConflict, exclusive_operation
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_payload,
    batch_id_from_query,
    json_body,
    json_response,
    save_uploaded_workbook,
)


def handle_report_route(config: AppConfig, method: str, parsed: ParseResult, body: str) -> JsonResponse | None:
    if method == "POST" and parsed.path == "/api/export":
        payload, error = json_body(body)
        if error:
            return error
        batch_id, error = batch_id_from_payload(payload)
        if error:
            return error
        try:
            mode = payload.get("mode", "city")
            if not isinstance(mode, str):
                return json_response({"error": "mode must be string"}, status=400)
            paths = export_issue_packages(config, batch_id, mode=mode)
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"paths": [str(path) for path in paths]})
    if method == "POST" and parsed.path == "/api/reports/notice":
        payload, error = json_body(body)
        if error:
            return error
        batch_id, error = batch_id_from_payload(payload)
        if error:
            return error
        try:
            path = export_notice_report(config, batch_id)
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"path": str(path)})
    if method == "POST" and parsed.path == "/api/corrections":
        payload, error = json_body(body)
        if error:
            return error
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value:
            return json_response({"error": "path is required"}, status=400)
        return _correction_response(config, Path(path_value))
    if method == "POST" and parsed.path == "/api/archive":
        payload, error = json_body(body)
        if error:
            return error
        batch_id, error = batch_id_from_payload(payload)
        if error:
            return error
        try:
            with exclusive_operation(config, "archive"):
                path = archive_batch(config, batch_id)
        except OperationConflict as exc:
            return json_response({"error": str(exc)}, status=409)
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"path": str(path)})
    if method == "GET" and parsed.path == "/api/archive/precheck":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        try:
            return json_response(archive_precheck(config, batch_id))
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
    return None


def handle_report_upload(
    config: AppConfig,
    path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
) -> JsonResponse | None:
    del fields
    if path != "/api/corrections/upload":
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
    return _correction_response(config, workbook_path)


def _correction_response(config: AppConfig, workbook_path: Path) -> JsonResponse:
    try:
        result = import_correction_return(config, workbook_path)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)
    return json_response(
        {
            "matched_count": result.matched_count,
            "errors": result.errors,
            "review_warnings": result.review_warnings,
            "auto_review": result.auto_review,
        },
        status=200 if not result.errors else 400,
    )
