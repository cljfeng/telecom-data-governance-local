import re
from pathlib import Path

from openpyxl import Workbook

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
    "风险等级",
    "问题说明",
    "建议整改方向",
    "整改结果",
    "整改说明",
    "整改后值",
    "附件说明",
]


def export_city_issue_packages(config: AppConfig, batch_id: int) -> list[Path]:
    config.export_dir.mkdir(parents=True, exist_ok=True)
    export_root = config.export_dir.resolve()
    paths: list[Path] = []
    with connect(config) as conn:
        cities = conn.execute(
            "select distinct coalesce(city, '未填地市') as city from issues where batch_id = ? order by city",
            (batch_id,),
        ).fetchall()
        for row in cities:
            city = row["city"]
            issues = conn.execute(
                "select * from issues where batch_id = ? and coalesce(city, '未填地市') = ? order by severity, issue_code",
                (batch_id, city),
            ).fetchall()
            if not issues:
                continue
            path = config.export_dir / f"{_safe_filename_part(city)}_整改问题清单_批次{batch_id}.xlsx"
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
    return paths


def _safe_filename_part(value: str | None) -> str:
    text = str(value or "未填地市").strip()
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text)
    while ".." in text:
        text = text.replace("..", "_")
    text = text.strip("._")
    return text or "未填地市"


def _excel_safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value
