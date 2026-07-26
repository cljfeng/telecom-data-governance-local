from pathlib import Path
from urllib.parse import ParseResult

from governance_app.archive import archive_batch, archive_precheck, export_notice_report
from governance_app.config import AppConfig, RuntimeMode
from governance_app.corrections import import_correction_return
from governance_app.exporter import export_issue_packages
from governance_app.operation_guard import OperationConflict, exclusive_operation
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_payload,
    batch_id_from_query,
    file_location,
    file_payload,
    json_body,
    json_response,
    store_uploaded_workbook,
    workbook_path_from_payload,
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
        files = [file_payload(config, path) for path in paths]
        return json_response(
            {
                "paths": [file_location(file) for file in files],
                "files": files,
            }
        )
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
        file = file_payload(config, path)
        return json_response({"path": file_location(file), "file": file})
    if method == "POST" and parsed.path == "/api/corrections":
        payload, error = json_body(body)
        if error:
            return error
        try:
            workbook_path = workbook_path_from_payload(config, payload)
        except (FileNotFoundError, ValueError) as exc:
            return json_response({"error": str(exc)}, status=400)
        return _correction_response(
            config,
            workbook_path,
            source_reference=_online_file_reference(config, payload),
        )
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
        file = file_payload(config, path)
        return json_response({"path": file_location(file), "file": file})
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
        stored_file = store_uploaded_workbook(config, filename, content)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)
    return _correction_response(
        config,
        stored_file.local_path,
        source_reference=(
            stored_file.file_id
            if config.runtime_mode is RuntimeMode.ONLINE
            else None
        ),
    )


def _correction_response(
    config: AppConfig,
    workbook_path: Path,
    *,
    source_reference: str | None = None,
) -> JsonResponse:
    try:
        result = import_correction_return(
            config,
            workbook_path,
            source_reference=source_reference,
        )
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


def _online_file_reference(config: AppConfig, payload: dict) -> str | None:
    file_id = payload.get("file_id")
    if (
        config.runtime_mode is RuntimeMode.ONLINE
        and isinstance(file_id, str)
        and file_id
    ):
        return file_id
    return None
