import argparse
from email.parser import BytesParser
from email.policy import default
import json
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from governance_app.analytics import dashboard_summary
from governance_app.archive import archive_batch, archive_precheck, export_notice_report
from governance_app.audit_engine import run_audit
from governance_app.backup import create_backup, restore_backup
from governance_app.config import AppConfig
from governance_app.corrections import import_correction_return
from governance_app.db import initialize_database
from governance_app.exporter import export_issue_packages
from governance_app.import_preview import export_preview_errors, preview_workbook
from governance_app.importer import import_workbook
from governance_app.recent_files import list_recent_files
from governance_app.reset import reset_system
from governance_app.rule_settings import load_rule_settings, upsert_rule_setting
from governance_app.audit_rules import all_batch_rules, all_rules, rule_metadata
from governance_app.settings_service import local_settings, restore_backup_safely
from governance_app.workflow import (
    city_progress,
    create_batch,
    get_batch_workflow,
    list_batches,
    list_issue_rules,
    list_issues,
    list_ledger_rows,
    record_operation,
    set_current_batch,
    update_issue_status,
)


@dataclass(frozen=True)
class LocalApp:
    config: AppConfig

    def handle_test_request(self, method: str, path: str, body: str = "") -> tuple[int, dict[str, str], str]:
        return _route(self.config, method, path, body)

    def handle_test_upload_request(
        self,
        path: str,
        content_type: str,
        body: bytes,
    ) -> tuple[int, dict[str, str], str]:
        fields, files, error = _multipart_body(content_type, body)
        return error or _route_upload(self.config, path, fields, files)


def create_app(config: AppConfig) -> LocalApp:
    return LocalApp(config)


def _route(config: AppConfig, method: str, path: str, body: str = "") -> tuple[int, dict[str, str], str]:
    parsed = urlparse(path)
    if method == "GET" and parsed.path == "/api/health":
        return _json({"status": "ok"})
    if method == "GET" and parsed.path == "/api/dashboard":
        query = parse_qs(parsed.query)
        try:
            batch_id = int(query.get("batch_id", ["1"])[0])
        except ValueError:
            return _json({"error": "invalid batch_id"}, status=400)
        return _json(dashboard_summary(config, batch_id))
    if method == "GET" and parsed.path == "/api/batches":
        return _json({"batches": list_batches(config)})
    if method == "POST" and parsed.path == "/api/batches":
        payload, error = _json_body(body)
        if error:
            return error
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return _json({"error": "name is required"}, status=400)
        try:
            batch_id = create_batch(config, name)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"batch_id": batch_id})
    if method == "POST" and parsed.path == "/api/batches/current":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        try:
            set_current_batch(config, batch_id)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=404)
        return _json({"status": "selected"})
    if method == "GET" and parsed.path == "/api/workflow":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        try:
            return _json(get_batch_workflow(config, batch_id))
        except ValueError as exc:
            return _json({"error": str(exc)}, status=404)
    if method == "GET" and parsed.path == "/api/issues":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        query = parse_qs(parsed.query)
        filters = {key: values[0] for key, values in query.items() if key not in {"batch_id", "limit", "offset"} and values and values[0]}
        limit, offset = _pagination_from_query(query)
        if limit is None:
            return _json({"issues": list_issues(config, batch_id, filters), "rules": list_issue_rules(config, batch_id)})
        page = list_issues(config, batch_id, filters, limit=limit, offset=offset)
        return _json({**page, "rules": list_issue_rules(config, batch_id)})
    if method == "GET" and parsed.path == "/api/ledger-rows":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        query = parse_qs(parsed.query)
        filters = {key: values[0] for key, values in query.items() if key != "batch_id" and values and values[0]}
        return _json({"rows": list_ledger_rows(config, batch_id, filters)})
    if method == "GET" and parsed.path == "/api/city-progress":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        return _json({"cities": city_progress(config, batch_id)})
    if method == "GET" and parsed.path == "/api/rules":
        return _json({"rules": _rule_settings_payload(config)})
    if method == "GET" and parsed.path == "/api/settings":
        return _json(local_settings(config))
    if method == "POST" and parsed.path == "/api/rules/settings":
        payload, error = _json_body(body)
        if error:
            return error
        rule_id = payload.get("rule_id")
        enabled = payload.get("enabled", True)
        config_values = payload.get("config", {})
        if not isinstance(rule_id, str) or not rule_id.strip():
            return _json({"error": "rule_id is required"}, status=400)
        if not isinstance(enabled, bool):
            return _json({"error": "enabled must be boolean"}, status=400)
        if not isinstance(config_values, dict):
            return _json({"error": "config must be object"}, status=400)
        upsert_rule_setting(config, rule_id, enabled=enabled, config_values=config_values)
        return _json({"status": "updated"})
    if method == "POST" and parsed.path == "/api/import":
        payload, error = _json_body(body)
        if error:
            return error
        return _import_from_payload(config, payload)
    if method == "POST" and parsed.path == "/api/import/preview":
        payload, error = _json_body(body)
        if error:
            return error
        return _preview_from_payload(config, payload)
    if method == "GET" and parsed.path == "/api/import/recent":
        return _json({"files": list_recent_files(config)})
    if method == "POST" and parsed.path == "/api/audit":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        result = run_audit(config, batch_id)
        return _json({"audit_run_id": result.audit_run_id, "issue_count": result.issue_count})
    if method == "POST" and parsed.path == "/api/export":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        try:
            mode = payload.get("mode", "city")
            if not isinstance(mode, str):
                return _json({"error": "mode must be string"}, status=400)
            paths = export_issue_packages(config, batch_id, mode=mode)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"paths": [str(path) for path in paths]})
    if method == "POST" and parsed.path == "/api/reports/notice":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        try:
            path = export_notice_report(config, batch_id)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"path": str(path)})
    if method == "POST" and parsed.path == "/api/corrections":
        payload, error = _json_body(body)
        if error:
            return error
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value:
            return _json({"error": "path is required"}, status=400)
        try:
            result = import_correction_return(config, Path(path_value))
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"matched_count": result.matched_count, "errors": result.errors}, status=200 if not result.errors else 400)
    if method == "POST" and parsed.path == "/api/issues/status":
        payload, error = _json_body(body)
        if error:
            return error
        issue_code = payload.get("issue_code")
        status_value = payload.get("status")
        if not isinstance(issue_code, str) or not isinstance(status_value, str):
            return _json({"error": "issue_code and status are required"}, status=400)
        try:
            update_issue_status(config, issue_code, status_value)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"status": "updated"})
    if method == "POST" and parsed.path == "/api/archive":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        try:
            path = archive_batch(config, batch_id)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"path": str(path)})
    if method == "GET" and parsed.path == "/api/archive/precheck":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        try:
            return _json(archive_precheck(config, batch_id))
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
    if method == "POST" and parsed.path == "/api/backup":
        path = create_backup(config)
        return _json({"path": str(path)})
    if method == "POST" and parsed.path == "/api/restore":
        payload, error = _json_body(body)
        if error:
            return error
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value:
            return _json({"error": "path is required"}, status=400)
        safety_backup_path, status_text = restore_backup_safely(config, Path(path_value))
        return _json({"status": status_text, "safety_backup_path": str(safety_backup_path)})
    if method == "POST" and parsed.path == "/api/reset":
        payload, error = _json_body(body)
        if error:
            return error
        confirmation = payload.get("confirmation")
        if not isinstance(confirmation, str):
            return _json({"error": "confirmation is required"}, status=400)
        preserve_exports = payload.get("preserve_exports", True)
        preserve_backups = payload.get("preserve_backups", True)
        if not isinstance(preserve_exports, bool) or not isinstance(preserve_backups, bool):
            return _json({"error": "preserve options must be boolean"}, status=400)
        try:
            result = reset_system(
                config,
                confirmation=confirmation,
                preserve_exports=preserve_exports,
                preserve_backups=preserve_backups,
            )
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json(result)
    return _json({"error": "not found"}, status=404)


def _route_upload(
    config: AppConfig,
    path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
) -> tuple[int, dict[str, str], str]:
    parsed = urlparse(path)
    if parsed.path not in {"/api/import/upload", "/api/import/preview/upload"}:
        return _json({"error": "not found"}, status=404)
    uploaded = files.get("file")
    if uploaded is None:
        return _json({"error": "请选择台账文件"}, status=400)
    filename, content = uploaded
    if not content:
        return _json({"error": "台账文件为空"}, status=400)
    try:
        workbook_path = _save_uploaded_workbook(config, filename, content)
    except ValueError as exc:
        return _json({"error": str(exc)}, status=400)

    payload: dict[str, object] = {"path": str(workbook_path)}
    payload.update(fields)
    if parsed.path == "/api/import/upload":
        return _import_from_payload(config, payload)
    return _preview_from_payload(config, payload)


def _preview_from_payload(config: AppConfig, payload: dict) -> tuple[int, dict[str, str], str]:
    path_value = payload.get("path")
    if not isinstance(path_value, str) or not path_value:
        return _json({"error": "path is required"}, status=400)
    workbook_path = Path(path_value)
    try:
        result = preview_workbook(config, workbook_path)
    except (OSError, InvalidFileException, BadZipFile) as exc:
        return _json({"error": f"无法读取 Excel 文件：{exc}"}, status=400)
    error_export_path = ""
    if result.errors:
        error_export_path = str(export_preview_errors(config, workbook_path, result))
    return _json(
        {
            "error": "" if result.ok else "预检未通过，请按错误明细修正后重试",
            "ok": result.ok,
            "batch_name": result.batch_name,
            "ledger_counts": result.ledger_counts,
            "errors": [error.__dict__ for error in result.errors],
            "error_export_path": error_export_path,
        },
        status=200 if result.ok else 400,
    )


def _import_from_payload(config: AppConfig, payload: dict) -> tuple[int, dict[str, str], str]:
    path_value = payload.get("path")
    if not isinstance(path_value, str) or not path_value:
        return _json({"error": "path is required"}, status=400)
    strategy = payload.get("strategy", "new")
    if not isinstance(strategy, str):
        return _json({"error": "strategy must be string"}, status=400)
    batch_id = payload.get("batch_id")
    try:
        result = import_workbook(
            config,
            Path(path_value),
            strategy=strategy,
            batch_id=int(batch_id) if batch_id not in (None, "") else None,
        )
    except (TypeError, ValueError, OSError, InvalidFileException, BadZipFile) as exc:
        return _json({"error": str(exc)}, status=400)
    return _json(
        {
            "error": "" if result.batch_id is not None else "导入未通过，请按错误明细修正后重试",
            "batch_id": result.batch_id,
            "ledger_counts": result.ledger_counts,
            "errors": [error.__dict__ for error in result.errors],
        },
        status=200 if result.batch_id is not None else 400,
    )


def _save_uploaded_workbook(config: AppConfig, filename: str, content: bytes) -> Path:
    safe_name = Path(filename or "workbook.xlsx").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        raise ValueError("请选择 .xlsx 或 .xlsm 格式的 Excel 台账文件")
    upload_dir = config.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{uuid4().hex}-{safe_name}"
    target.write_bytes(content)
    return target


def _json(payload: dict, status: int = 200) -> tuple[int, dict[str, str], str]:
    return (
        status,
        {"content-type": "application/json; charset=utf-8"},
        json.dumps(payload, ensure_ascii=False),
    )


def _json_body(body: str) -> tuple[dict, tuple[int, dict[str, str], str] | None]:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return {}, _json({"error": "invalid json"}, status=400)
    if not isinstance(payload, dict):
        return {}, _json({"error": "json object required"}, status=400)
    return payload, None


def _multipart_body(
    content_type: str,
    body: bytes,
) -> tuple[dict[str, str], dict[str, tuple[str, bytes]], tuple[int, dict[str, str], str] | None]:
    if "multipart/form-data" not in content_type:
        return {}, {}, _json({"error": "content-type must be multipart/form-data"}, status=400)
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    if not message.is_multipart():
        return {}, {}, _json({"error": "invalid multipart body"}, status=400)

    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        content = part.get_payload(decode=True) or b""
        if filename:
            files[name] = (filename, content)
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = content.decode(charset, errors="replace")
    return fields, files, None


def _batch_id_from_payload(payload: dict) -> tuple[int, tuple[int, dict[str, str], str] | None]:
    try:
        return int(payload.get("batch_id")), None
    except (TypeError, ValueError):
        return 0, _json({"error": "invalid batch_id"}, status=400)


def _batch_id_from_query(query_string: str) -> tuple[int, tuple[int, dict[str, str], str] | None]:
    query = parse_qs(query_string)
    try:
        return int(query.get("batch_id", ["1"])[0]), None
    except ValueError:
        return 0, _json({"error": "invalid batch_id"}, status=400)


def _pagination_from_query(query: dict[str, list[str]]) -> tuple[int | None, int]:
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


def _rule_settings_payload(config: AppConfig) -> list[dict]:
    settings = load_rule_settings(config)
    rule_ids = sorted({rule.rule_id for rule in all_rules()} | {rule.rule_id for rule in all_batch_rules()})
    payload = []
    for rule_id in rule_ids:
        metadata = rule_metadata(rule_id)
        setting = settings.get(rule_id)
        payload.append(
            {
                "rule_id": rule_id,
                "name": metadata.name,
                "ledger_type": metadata.ledger_type,
                "severity": metadata.severity,
                "description": metadata.description,
                "default_suggestion": metadata.default_suggestion,
                "enabled": True if setting is None else setting.enabled,
                "config": {} if setting is None else setting.config,
            }
        )
    return payload


class RequestHandler(SimpleHTTPRequestHandler):
    config: AppConfig

    @staticmethod
    def extra_static_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        }

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            for key, value in self.extra_static_headers().items():
                self.send_header(key, value)
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            status, headers, body = _route(self.config, "GET", self.path)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            length = int(self.headers.get("content-length", "0"))
            content_type = self.headers.get("content-type", "")
            raw_body = self.rfile.read(length)
            if content_type.startswith("multipart/form-data"):
                fields, files, error = _multipart_body(content_type, raw_body)
                status, headers, response_body = error or _route_upload(self.config, self.path, fields, files)
            else:
                body = raw_body.decode("utf-8")
                status, headers, response_body = _route(self.config, "POST", self.path, body)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response_body.encode("utf-8"))
            return
        status, headers, response_body = _json({"error": "not found"}, status=404)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response_body.encode("utf-8"))


def run_server(config: AppConfig, host: str = "127.0.0.1", port: int = 8765) -> None:
    initialize_database(config)
    configured_handler = type("ConfiguredRequestHandler", (RequestHandler,), {"config": config})
    handler = partial(configured_handler, directory=str(config.static_dir))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Local governance app running at http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    run_server(AppConfig.for_workspace(Path(args.workspace)), args.host, args.port)


if __name__ == "__main__":
    main()
