import json
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from governance_app.config import AppConfig
from governance_app.db import connect


@dataclass(frozen=True)
class CorrectionImportResult:
    matched_count: int
    errors: list[str] = field(default_factory=list)


def import_correction_return(config: AppConfig, workbook_path: Path) -> CorrectionImportResult:
    wb = load_workbook(workbook_path, data_only=True)
    ws = wb["整改问题清单"]
    headers = [cell.value for cell in ws[1]]
    index = {name: pos for pos, name in enumerate(headers)}
    required = ["问题编号", "整改结果", "整改说明", "整改后值"]
    missing = [name for name in required if name not in index]
    if missing:
        errors = [f"缺少回填列：{name}" for name in missing]
        _record_return(config, workbook_path, 0, errors)
        return CorrectionImportResult(matched_count=0, errors=errors)

    matched_count = 0
    errors: list[str] = []
    with connect(config) as conn:
        for row_number, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            issue_code = values[index["问题编号"]]
            if not issue_code:
                errors.append(f"第{row_number}行缺少问题编号")
                continue
            result = values[index["整改结果"]]
            note = values[index["整改说明"]]
            corrected = values[index["整改后值"]]
            cursor = conn.execute(
                """
                update issues
                   set status = 'needs_review',
                       correction_value = ?,
                       correction_note = ?,
                       updated_at = current_timestamp
                 where issue_code = ?
                """,
                (
                    None if corrected is None else str(corrected),
                    None if note is None else str(note or result or ""),
                    str(issue_code),
                ),
            )
            if cursor.rowcount == 0:
                errors.append(f"第{row_number}行问题编号无法匹配：{issue_code}")
            else:
                matched_count += 1
        conn.execute(
            "insert into correction_returns(source_file, matched_count, error_count, errors_json) values (?, ?, ?, ?)",
            (str(workbook_path), matched_count, len(errors), json.dumps(errors, ensure_ascii=False)),
        )
    return CorrectionImportResult(matched_count=matched_count, errors=errors)


def _record_return(config: AppConfig, workbook_path: Path, matched_count: int, errors: list[str]) -> None:
    with connect(config) as conn:
        conn.execute(
            "insert into correction_returns(source_file, matched_count, error_count, errors_json) values (?, ?, ?, ?)",
            (str(workbook_path), matched_count, len(errors), json.dumps(errors, ensure_ascii=False)),
        )
