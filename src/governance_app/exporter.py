import re
from pathlib import Path

from openpyxl import Workbook

from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect

ISSUE_HEADERS = [
    "问题编号",
    "地市",
    "区县",
    "电信站址编码",
    "电信站址名称",
    "台账类型",
    "规则编号",
    "规则名称",
    "风险等级",
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
        cities = conn.execute(
            "select distinct coalesce(city, '未填地市') as city from issues where batch_id = ? order by city",
            (batch_id,),
        ).fetchall()
        if not cities:
            conn.execute("update import_batches set status = 'returning' where id = ?", (batch_id,))
            conn.execute(
                "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
                (batch_id, "export", "当前批次无待导出问题，可直接归档"),
            )
            return []
        for row in cities:
            city = row["city"]
            issues = conn.execute(
                "select * from issues where batch_id = ? and coalesce(city, '未填地市') = ? order by severity, issue_code",
                (batch_id, city),
            ).fetchall()
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
                ws.append(
                    [
                        _excel_safe(issue["issue_code"]),
                        _excel_safe(issue["city"]),
                        _excel_safe(issue["district"]),
                        _excel_safe(issue["telecom_site_code"]),
                        _excel_safe(issue["telecom_site_name"]),
                        _excel_safe(issue["ledger_type"]),
                        _excel_safe(issue["rule_id"]),
                        _excel_safe(rule_metadata(issue["rule_id"]).name),
                        _excel_safe(issue["severity"]),
                        _excel_safe(issue["message"]),
                        _excel_safe(issue["suggestion"]),
                        "",
                        "",
                        "",
                        "",
                    ]
                )
            wb.save(path)
            conn.execute(
                """
                update issues
                   set status = 'pending_correction',
                       updated_at = current_timestamp
                 where batch_id = ?
                   and coalesce(city, '未填地市') = ?
                """,
                (batch_id, city),
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
            """
            select id, issue_code, audit_result_id, batch_id, coalesce(city, '未填地市') as city,
                   district, telecom_site_code, telecom_site_name, ledger_type, rule_id, severity,
                   status, message, suggestion, correction_value, correction_note, updated_at
              from issues
             where batch_id = ?
             order by city, severity, issue_code
            """,
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
    ws.append(
        [
            _excel_safe(issue["issue_code"]),
            _excel_safe(issue["city"]),
            _excel_safe(issue["district"]),
            _excel_safe(issue["telecom_site_code"]),
            _excel_safe(issue["telecom_site_name"]),
            _excel_safe(issue["ledger_type"]),
            _excel_safe(issue["rule_id"]),
            _excel_safe(rule_metadata(issue["rule_id"]).name),
            _excel_safe(issue["severity"]),
            _excel_safe(issue["message"]),
            _excel_safe(issue["suggestion"]),
            "",
            "",
            "",
            "",
        ]
    )


def _excel_safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value
