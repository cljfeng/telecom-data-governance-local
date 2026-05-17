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
    if "整改问题清单" not in wb.sheetnames:
        errors = ["缺少 sheet：整改问题清单"]
        _record_return(config, workbook_path, 0, errors)
        return CorrectionImportResult(matched_count=0, errors=errors)
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
    matched_batch_ids: set[int] = set()
    seen_issue_codes: set[str] = set()
    with connect(config) as conn:
        for row_number, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if _is_blank_row(values):
                continue
            issue_code = values[index["问题编号"]]
            if _is_blank(issue_code):
                errors.append(f"第{row_number}行缺少问题编号")
                continue
            result = values[index["整改结果"]]
            note = values[index["整改说明"]]
            corrected = values[index["整改后值"]]
            if _is_blank(result) and _is_blank(note) and _is_blank(corrected):
                continue
            issue_code_text = str(issue_code)
            if issue_code_text in seen_issue_codes:
                errors.append(f"第{row_number}行问题编号重复：{issue_code_text}")
                continue
            seen_issue_codes.add(issue_code_text)
            batch_row = conn.execute(
                """
                select i.batch_id, b.is_archived
                  from issues i
                  join import_batches b on b.id = i.batch_id
                 where i.issue_code = ?
                """,
                (issue_code_text,),
            ).fetchone()
            if batch_row is not None and batch_row["is_archived"]:
                raise ValueError("batch is archived")
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
                    issue_code_text,
                ),
            )
            if cursor.rowcount == 0:
                errors.append(f"第{row_number}行问题编号无法匹配：{issue_code}")
            else:
                if batch_row is not None:
                    matched_batch_ids.add(batch_row["batch_id"])
                matched_count += 1
        conn.execute(
            "insert into correction_returns(source_file, matched_count, error_count, errors_json) values (?, ?, ?, ?)",
            (str(workbook_path), matched_count, len(errors), json.dumps(errors, ensure_ascii=False)),
        )
        if matched_batch_ids:
            for batch_id in matched_batch_ids:
                conn.execute("update import_batches set status = 'returning' where id = ?", (batch_id,))
                conn.execute(
                    "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
                    (batch_id, "correction_return", f"导入整改回传，匹配 {matched_count} 条"),
                )
    return CorrectionImportResult(matched_count=matched_count, errors=errors)


def _is_blank_row(values: tuple[object, ...]) -> bool:
    return all(_is_blank(value) for value in values)


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _record_return(config: AppConfig, workbook_path: Path, matched_count: int, errors: list[str]) -> None:
    with connect(config) as conn:
        conn.execute(
            "insert into correction_returns(source_file, matched_count, error_count, errors_json) values (?, ?, ?, ?)",
            (str(workbook_path), matched_count, len(errors), json.dumps(errors, ensure_ascii=False)),
        )
