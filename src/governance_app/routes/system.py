from pathlib import Path
from urllib.parse import ParseResult

from governance_app.backup import create_backup
from governance_app.config import AppConfig
from governance_app.maintenance import compact_database
from governance_app.operation_guard import OperationConflict, exclusive_operation
from governance_app.reset import reset_system
from governance_app.settings_service import local_settings, restore_backup_safely
from governance_app.version import version_payload

JsonResponse = tuple[int, dict[str, str], str]


def handle_system_route(config: AppConfig, method: str, parsed: ParseResult, body: str, json_response, json_body) -> JsonResponse | None:
    if method == "GET" and parsed.path == "/api/health":
        return json_response({"status": "ok"})
    if method == "GET" and parsed.path == "/api/version":
        return json_response(version_payload())
    if method == "GET" and parsed.path == "/api/settings":
        return json_response(local_settings(config))
    if method == "POST" and parsed.path == "/api/backup":
        path = create_backup(config)
        return json_response({"path": str(path)})
    if method == "POST" and parsed.path == "/api/restore":
        payload, error = json_body(body)
        if error:
            return error
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value:
            return json_response({"error": "path is required"}, status=400)
        try:
            with exclusive_operation(config, "restore"):
                safety_backup_path, status_text = restore_backup_safely(config, Path(path_value))
        except OperationConflict as exc:
            return json_response({"error": str(exc)}, status=409)
        except (FileNotFoundError, ValueError) as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"status": status_text, "safety_backup_path": str(safety_backup_path)})
    if method == "POST" and parsed.path == "/api/reset":
        payload, error = json_body(body)
        if error:
            return error
        confirmation = payload.get("confirmation")
        if not isinstance(confirmation, str):
            return json_response({"error": "confirmation is required"}, status=400)
        preserve_exports = payload.get("preserve_exports", True)
        preserve_backups = payload.get("preserve_backups", True)
        if not isinstance(preserve_exports, bool) or not isinstance(preserve_backups, bool):
            return json_response({"error": "preserve options must be boolean"}, status=400)
        try:
            with exclusive_operation(config, "reset"):
                result = reset_system(
                    config,
                    confirmation=confirmation,
                    preserve_exports=preserve_exports,
                    preserve_backups=preserve_backups,
                )
        except OperationConflict as exc:
            return json_response({"error": str(exc)}, status=409)
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response(result)
    if method == "POST" and parsed.path == "/api/maintenance/compact":
        payload, error = json_body(body)
        if error:
            return error
        clear_uploads = payload.get("clear_uploads", False)
        if not isinstance(clear_uploads, bool):
            return json_response({"error": "clear_uploads must be boolean"}, status=400)
        try:
            with exclusive_operation(config, "maintenance"):
                result = compact_database(config, clear_uploads=clear_uploads)
        except OperationConflict as exc:
            return json_response({"error": str(exc)}, status=409)
        return json_response(result)
    return None
