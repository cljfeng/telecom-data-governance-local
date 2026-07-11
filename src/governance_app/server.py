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

from governance_app.archive import archive_batch, archive_precheck, export_notice_report
from governance_app.audit_engine import run_audit
from governance_app.config import AppConfig
from governance_app.corrections import import_correction_return
from governance_app.db import connect, initialize_database
from governance_app.exporter import export_issue_packages
from governance_app.import_preview import export_preview_errors, preview_error_payload, preview_error_summary, preview_workbook
from governance_app.importer import import_workbook
from governance_app.operation_guard import OperationConflict, exclusive_operation
from governance_app.recent_files import list_recent_files
from governance_app.routes.analysis import handle_analysis_route
from governance_app.routes.audits import handle_audit_route
from governance_app.routes.batches import handle_batch_route
from governance_app.routes.imports import handle_import_route, handle_import_upload
from governance_app.routes.system import handle_system_route
from governance_app.rule_settings import load_rule_settings, upsert_rule_setting
from governance_app.audit_rules import all_batch_rules, all_rules, rule_metadata
from governance_app.workflow import (
    city_progress,
    create_batch,
    get_batch_workflow,
    list_batches,
    list_issue_groups,
    list_issue_rules,
    list_issues,
    list_ledger_rows,
    record_operation,
    set_current_batch,
    update_issue_group_status,
    update_issue_status,
)

MAX_REQUEST_BODY_BYTES = 100 * 1024 * 1024


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
    system_response = handle_system_route(config, method, parsed, body)
    if system_response is not None:
        return system_response
    batch_response = handle_batch_route(config, method, parsed, body)
    if batch_response is not None:
        return batch_response
    import_response = handle_import_route(config, method, parsed, body)
    if import_response is not None:
        return import_response
    audit_response = handle_audit_route(config, method, parsed, body)
    if audit_response is not None:
        return audit_response
    analysis_response = handle_analysis_route(config, method, parsed, body)
    if analysis_response is not None:
        return analysis_response
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
    if method == "GET" and parsed.path == "/api/issue-groups":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        query = parse_qs(parsed.query)
        filters = {key: values[0] for key, values in query.items() if key != "batch_id" and values and values[0]}
        return _json({"groups": list_issue_groups(config, batch_id, filters)})
    if method == "GET" and parsed.path == "/api/rules":
        query = parse_qs(parsed.query)
        raw_batch_id = query.get("batch_id", [""])[0]
        batch_id = None
        if raw_batch_id:
            try:
                batch_id = int(raw_batch_id)
            except ValueError:
                return _json({"error": "invalid batch_id"}, status=400)
        return _json({"rules": _rule_settings_payload(config, batch_id=batch_id)})
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
    if method == "POST" and parsed.path == "/api/audit":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        try:
            with exclusive_operation(config, "audit"):
                result = run_audit(config, batch_id)
        except OperationConflict as exc:
            return _json({"error": str(exc)}, status=409)
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
        return _json(
            {
                "matched_count": result.matched_count,
                "errors": result.errors,
                "review_warnings": result.review_warnings,
                "auto_review": result.auto_review,
            },
            status=200 if not result.errors else 400,
        )
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
    if method == "POST" and parsed.path == "/api/issues/group-status":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        status_value = payload.get("status")
        group = payload.get("group")
        if not isinstance(status_value, str) or not isinstance(group, dict):
            return _json({"error": "group and status are required"}, status=400)
        try:
            updated = update_issue_group_status(config, batch_id, group, status_value)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"status": "updated", "updated_count": updated})
    if method == "POST" and parsed.path == "/api/archive":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        try:
            with exclusive_operation(config, "archive"):
                path = archive_batch(config, batch_id)
        except OperationConflict as exc:
            return _json({"error": str(exc)}, status=409)
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
    return _json({"error": "not found"}, status=404)


def _route_upload(
    config: AppConfig,
    path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
) -> tuple[int, dict[str, str], str]:
    import_response = handle_import_upload(config, path, fields, files)
    if import_response is not None:
        return import_response
    parsed = urlparse(path)
    if parsed.path != "/api/corrections/upload":
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
    if parsed.path == "/api/corrections/upload":
        try:
            result = import_correction_return(config, workbook_path)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json(
            {
                "matched_count": result.matched_count,
                "errors": result.errors,
                "review_warnings": result.review_warnings,
                "auto_review": result.auto_review,
            },
            status=200 if not result.errors else 400,
        )
    return _json({"error": "not found"}, status=404)


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
            "errors": [preview_error_payload(error) for error in result.errors],
            "error_summary": preview_error_summary(result.errors),
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
        with exclusive_operation(config, "import"):
            result = import_workbook(
                config,
                Path(path_value),
                strategy=strategy,
                batch_id=int(batch_id) if batch_id not in (None, "") else None,
            )
    except OperationConflict as exc:
        return _json({"error": str(exc)}, status=409)
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


def _content_length(value: str | None) -> tuple[int | None, tuple[int, dict[str, str], str] | None]:
    try:
        length = int(value) if value is not None else -1
    except ValueError:
        length = -1
    if length < 0:
        return None, _json({"error": "Content-Length 缺失或无效"}, status=400)
    if length > MAX_REQUEST_BODY_BYTES:
        return None, _json({"error": "上传内容不能超过 100 MiB"}, status=413)
    return length, None


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


def _rule_settings_payload(config: AppConfig, batch_id: int | None = None) -> list[dict]:
    settings = load_rule_settings(config)
    effectiveness = _rule_effectiveness_by_rule(config, batch_id) if batch_id is not None else {}
    rule_ids = sorted({rule.rule_id for rule in all_rules()} | {rule.rule_id for rule in all_batch_rules()})
    payload = []
    for rule_id in rule_ids:
        metadata = rule_metadata(rule_id)
        setting = settings.get(rule_id)
        rule_effectiveness = effectiveness.get(rule_id, _empty_rule_effectiveness())
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
                "category": metadata.category,
                "parameters": _rule_parameters(rule_id),
                "effectiveness": rule_effectiveness,
                "tuning_recommendation": _rule_tuning_recommendation(rule_effectiveness, metadata.severity),
            }
        )
    return payload


def _rule_effectiveness_by_rule(config: AppConfig, batch_id: int | None) -> dict[str, dict]:
    if batch_id is None:
        return {}
    with connect(config) as conn:
        rows = conn.execute(
            """
            select rule_id,
                   count(*) as total_count,
                   sum(case when status not in ('closed', 'not_required', 'resolved_by_reaudit') then 1 else 0 end) as open_count,
                   sum(case when status = 'not_required' then 1 else 0 end) as not_required_count,
                   sum(case when status = 'still_invalid' then 1 else 0 end) as still_invalid_count
              from issues
             where batch_id = ?
             group by rule_id
            """,
            (batch_id,),
        ).fetchall()
    result: dict[str, dict] = {}
    for row in rows:
        total = int(row["total_count"] or 0)
        not_required = int(row["not_required_count"] or 0)
        open_count = int(row["open_count"] or 0)
        result[row["rule_id"]] = {
            "total_count": total,
            "open_count": open_count,
            "not_required_count": not_required,
            "still_invalid_count": int(row["still_invalid_count"] or 0),
            "not_required_rate": round((not_required / total) * 100, 1) if total else 0.0,
            "open_rate": round((open_count / total) * 100, 1) if total else 0.0,
        }
    return result


def _empty_rule_effectiveness() -> dict:
    return {
        "total_count": 0,
        "open_count": 0,
        "not_required_count": 0,
        "still_invalid_count": 0,
        "not_required_rate": 0.0,
        "open_rate": 0.0,
    }


def _rule_tuning_recommendation(effectiveness: dict, severity: str) -> dict[str, str]:
    total = int(effectiveness.get("total_count") or 0)
    if not total:
        return {"level": "neutral", "message": "当前批次未命中，可保持默认口径"}
    not_required_rate = float(effectiveness.get("not_required_rate") or 0)
    open_count = int(effectiveness.get("open_count") or 0)
    still_invalid_count = int(effectiveness.get("still_invalid_count") or 0)
    if not_required_rate >= 50:
        return {"level": "warning", "message": "无需整改率较高，建议复核规则口径或适当调整阈值"}
    if severity == "high" and open_count:
        return {"level": "danger", "message": "高风险规则仍有未闭环问题，建议优先推动整改"}
    if still_invalid_count:
        return {"level": "warning", "message": "回传后仍异常较多，建议检查整改说明和佐证材料"}
    return {"level": "success", "message": "规则效果稳定，可继续沿用当前口径"}


_RULE_PARAMETERS = {
    "electricity_price_range": [
        {"key": "max", "label": "电费单价上限", "unit": "元/度", "default": 0.9, "step": 0.01},
    ],
    "electricity_share_percent": [
        {"key": "min", "label": "分摊比例下限", "unit": "%", "default": 0, "step": 1},
        {"key": "max", "label": "分摊比例上限", "unit": "%", "default": 100, "step": 1},
    ],
    "generator_duration_over_24h": [
        {"key": "max_hours", "label": "单次发电时长上限", "unit": "小时", "default": 24, "step": 0.5},
    ],
    "electricity_contract_share_variance": [
        {"key": "max_points", "label": "合同分摊允许偏差", "unit": "百分点", "default": 3, "step": 0.5},
    ],
    "electricity_usage_spike_drop": [
        {"key": "change_ratio", "label": "用电量环比波动阈值", "unit": "倍", "default": 0.3, "step": 0.05},
    ],
    "electricity_reading_usage_mismatch": [
        {"key": "variance_ratio", "label": "读数电量比例偏差", "unit": "倍", "default": 0.1, "step": 0.05},
        {"key": "variance_min", "label": "读数电量最小偏差", "unit": "度", "default": 10, "step": 1},
    ],
    "electricity_amount_calculation_mismatch": [
        {"key": "variance_ratio", "label": "金额计算比例偏差", "unit": "倍", "default": 0.1, "step": 0.05},
        {"key": "variance_min", "label": "金额计算最小偏差", "unit": "元", "default": 100, "step": 10},
    ],
    "electricity_price_commercial_range": [
        {"key": "min", "label": "商业电价下限", "unit": "元/度", "default": 0.3, "step": 0.01},
        {"key": "max", "label": "商业电价上限", "unit": "元/度", "default": 1.5, "step": 0.01},
    ],
    "fee_amount_period_spike": [
        {"key": "change_ratio", "label": "费用环比突变阈值", "unit": "倍", "default": 1, "step": 0.1},
    ],
    "electricity_price_city_supply_outlier": [
        {"key": "deviation_ratio", "label": "同区县电价偏离阈值", "unit": "倍", "default": 0.2, "step": 0.05},
    ],
    "generator_duration_mismatch": [
        {"key": "allowed_hours", "label": "发电时长允许偏差", "unit": "小时", "default": 0.25, "step": 0.05},
    ],
    "generator_cost_per_hour_outlier": [
        {"key": "multiplier", "label": "小时单价中位数倍数", "unit": "倍", "default": 1.5, "step": 0.1},
        {"key": "min_rate", "label": "小时单价绝对下限", "unit": "元/小时", "default": 300, "step": 10},
    ],
}


def _rule_parameters(rule_id: str) -> list[dict]:
    return _RULE_PARAMETERS.get(rule_id, [])


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
            length, length_error = _content_length(self.headers.get("content-length"))
            if length_error is not None:
                status, headers, response_body = length_error
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(response_body.encode("utf-8"))
                return
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
