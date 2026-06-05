import re
import json
from pathlib import Path

from openpyxl import Workbook

from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.geo import normalize_city

ISSUE_HEADERS = [
    "问题编号",
    "地市",
    "区县",
    "电信站址编码",
    "电信站址名称",
    "台账类型",
    "规则分类",
    "规则编号",
    "规则名称",
    "风险等级",
    "原台账sheet",
    "原始行号",
    "命中字段",
    "原始字段值",
    "问题说明",
    "建议整改方向",
    "整改结果",
    "整改说明",
    "整改后值",
    "附件说明",
]


def export_issue_packages(config: AppConfig, batch_id: int, mode: str = "city") -> list[Path]:
    if mode not in {"city", "province"}:
        raise ValueError("invalid export mode")
    if mode == "province":
        return _export_province_issue_package(config, batch_id)
    return export_city_issue_packages(config, batch_id)


def export_city_issue_packages(config: AppConfig, batch_id: int) -> list[Path]:
    config.export_dir.mkdir(parents=True, exist_ok=True)
    export_root = config.export_dir.resolve()
    paths: list[Path] = []
    with connect(config) as conn:
        batch = conn.execute("select status, is_archived, batch_code from import_batches where id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError("batch not found")
        if batch["is_archived"]:
            raise ValueError("batch is archived")
        if batch["status"] != "audited":
            raise ValueError("batch must be audited before export")
        issue_rows = conn.execute(
            _ISSUE_EXPORT_SQL + " order by i.severity, i.issue_code",
            (batch_id,),
        ).fetchall()
        if not issue_rows:
            conn.execute("update import_batches set status = 'returning' where id = ?", (batch_id,))
            conn.execute(
                "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
                (batch_id, "export", "当前批次无待导出问题，可直接归档"),
            )
            return []
        grouped: dict[str, list] = {}
        for issue in issue_rows:
            grouped.setdefault(normalize_city(issue["city"]), []).append(issue)
        for city in sorted(grouped):
            issues = grouped[city]
            if not issues:
                continue
            batch_code = batch["batch_code"] or f"批次{batch_id}"
            path = config.export_dir / f"{_safe_filename_part(city)}_整改问题清单_{_safe_filename_part(batch_code)}.xlsx"
            if not path.resolve().is_relative_to(export_root):
                raise ValueError(f"导出路径越界：{path}")
            wb = Workbook()
            ws = wb.active
            ws.title = "整改问题清单"
            ws.append(ISSUE_HEADERS)
            for issue in issues:
                _append_issue_row(ws, issue)
            wb.save(path)
            conn.executemany(
                """
                update issues
                   set status = 'pending_correction',
                       updated_at = current_timestamp
                 where id = ?
                """,
                [(issue["id"],) for issue in issues],
            )
            paths.append(path)
        if paths:
            conn.execute("update import_batches set status = 'distributed' where id = ?", (batch_id,))
            conn.execute(
                "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
                (batch_id, "export", f"导出地市整改包 {len(paths)} 个"),
            )
    return paths


def _export_province_issue_package(config: AppConfig, batch_id: int) -> list[Path]:
    config.export_dir.mkdir(parents=True, exist_ok=True)
    export_root = config.export_dir.resolve()
    with connect(config) as conn:
        batch = conn.execute("select status, is_archived, batch_code from import_batches where id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError("batch not found")
        if batch["is_archived"]:
            raise ValueError("batch is archived")
        if batch["status"] != "audited":
            raise ValueError("batch must be audited before export")
        issues = conn.execute(
            _ISSUE_EXPORT_SQL + " order by city, i.severity, i.issue_code",
            (batch_id,),
        ).fetchall()
        if not issues:
            conn.execute("update import_batches set status = 'returning' where id = ?", (batch_id,))
            conn.execute(
                "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
                (batch_id, "export", "当前批次无待导出问题，可直接归档"),
            )
            return []
        batch_code = batch["batch_code"] or f"批次{batch_id}"
        path = config.export_dir / f"全省_整改问题清单_{_safe_filename_part(batch_code)}.xlsx"
        if not path.resolve().is_relative_to(export_root):
            raise ValueError(f"导出路径越界：{path}")
        wb = Workbook()
        ws = wb.active
        ws.title = "整改问题清单"
        ws.append(ISSUE_HEADERS)
        for issue in issues:
            _append_issue_row(ws, issue)
        wb.save(path)
        conn.execute(
            """
            update issues
               set status = 'pending_correction',
                   updated_at = current_timestamp
             where batch_id = ?
            """,
            (batch_id,),
        )
        conn.execute("update import_batches set status = 'distributed' where id = ?", (batch_id,))
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, "export", "导出全省汇总整改包 1 个"),
        )
    return [path]


def _safe_filename_part(value: str | None) -> str:
    text = str(value or "未填地市").strip()
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text)
    while ".." in text:
        text = text.replace("..", "_")
    text = text.strip("._")
    return text or "未填地市"


def _append_issue_row(ws, issue) -> None:
    metadata = rule_metadata(issue["rule_id"])
    ws.append(
        [
            _excel_safe(issue["issue_code"]),
            _excel_safe(normalize_city(issue["city"])),
            _excel_safe(issue["district"]),
            _excel_safe(issue["telecom_site_code"]),
            _excel_safe(issue["telecom_site_name"]),
            _excel_safe(_ledger_label(issue["ledger_type"])),
            _excel_safe(_rule_category_label(metadata.category)),
            _excel_safe(issue["rule_id"]),
            _excel_safe(metadata.name),
            _excel_safe(_severity_label(issue["severity"])),
            _excel_safe(issue["sheet_name"]),
            issue["row_number"] or "",
            _excel_safe(issue["field_name"]),
            _excel_safe(_matched_field_value(issue)),
            _excel_safe(_detailed_issue_message(issue)),
            _excel_safe(issue["suggestion"]),
            "",
            "",
            "",
            "",
        ]
    )


_ISSUE_EXPORT_SQL = """
            select i.id, i.issue_code, i.audit_result_id, i.batch_id, coalesce(i.city, '未填地市') as city,
                   i.district, i.telecom_site_code, i.telecom_site_name, i.ledger_type, i.rule_id, i.severity,
                   i.status, i.message, i.suggestion, i.correction_value, i.correction_note, i.updated_at,
                   ar.field_name,
                   case
                       when lr.row_json is not null and lr.row_json <> '{}' then lr.row_json
                       else coalesce(rr.row_json, lr.row_json)
                   end as row_json,
                   lr.sheet_name, lr.row_number
              from issues i
              join audit_results ar on ar.id = i.audit_result_id
              left join ledger_rows lr on lr.id = ar.ledger_row_id
              left join raw_rows rr on rr.id = lr.raw_row_id
             where i.batch_id = ?
"""


def _detailed_issue_message(issue) -> str:
    raw = _row_json(issue["row_json"])
    parts = [f"系统在{_ledger_label(issue['ledger_type'])}台账中发现该问题"]
    location = _row_location(issue)
    if location:
        parts.append(location)
    site = _site_sentence(issue)
    if site:
        parts.append(site)
    field_name = issue["field_name"]
    if field_name:
        current_value = raw.get(field_name)
        if current_value not in (None, ""):
            parts.append(f"本次命中的字段是“{field_name}”，原始填报值为“{_format_value(current_value)}”")
        else:
            parts.append(f"本次命中的字段是“{field_name}”，原始填报值为空")
    reason = _format_message(issue["message"])
    if reason:
        parts.append(f"系统判定依据为{reason}")
    parts.append("请在原台账对应行核实后填写整改结果、整改说明和必要附件")
    return "。".join(part.rstrip("。") for part in parts if part) + "。"


def _row_location(issue) -> str:
    sheet_name = issue["sheet_name"]
    row_number = issue["row_number"]
    if sheet_name and row_number:
        return f"原始位置为“{sheet_name}”sheet 第 {row_number} 行"
    if sheet_name:
        return f"原始位置在“{sheet_name}”sheet"
    if row_number:
        return f"原始位置为第 {row_number} 行"
    return ""


def _site_sentence(issue) -> str:
    fragments = []
    if issue["city"]:
        fragments.append(normalize_city(issue["city"]))
    if issue["district"]:
        fragments.append(str(issue["district"]))
    site_name = issue["telecom_site_name"]
    site_code = issue["telecom_site_code"]
    if site_name and site_code:
        fragments.append(f"站址“{site_name}”（编码 {site_code}）")
    elif site_name:
        fragments.append(f"站址“{site_name}”")
    elif site_code:
        fragments.append(f"站址编码 {site_code}")
    return "、".join(fragments) if fragments else ""


def _matched_field_value(issue) -> str:
    field_name = issue["field_name"]
    if not field_name:
        return ""
    raw = _row_json(issue["row_json"])
    value = raw.get(field_name)
    return "" if value in (None, "") else _format_value(value)


def _format_message(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"(?<![\d.])(-?\d+\.\d{3,})(?![\d.])", lambda match: _format_value(match.group(1)), text)


def _format_value(value: object) -> str:
    if isinstance(value, float | int):
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    text = str(value).strip()
    try:
        number = float(text.replace(",", "").replace("，", "").rstrip("%"))
    except ValueError:
        return text
    suffix = "%" if text.endswith("%") else ""
    return f"{number:.2f}".rstrip("0").rstrip(".") + suffix


def _row_json(value: str | None) -> dict:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _ledger_label(value: str | None) -> str:
    labels = {
        "site": "站址",
        "tower_rent": "铁塔租费",
        "electricity": "电费",
        "generator": "发电费",
        "all": "跨台账",
    }
    return labels.get(str(value or ""), str(value or "未知"))


def _severity_label(value: str | None) -> str:
    labels = {"high": "高", "medium": "中", "low": "低"}
    return labels.get(str(value or ""), str(value or "未知"))


def _rule_category_label(value: str | None) -> str:
    labels = {"data_quality": "基础数据质量", "problem_audit": "问题稽核"}
    return labels.get(str(value or ""), str(value or "未知"))


def _excel_safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value
