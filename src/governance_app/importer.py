import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.models import LedgerType, ValidationErrorDetail
from governance_app.templates import EXPECTED_SHEETS, HEADER_ROWS, required_headers_for


@dataclass(frozen=True)
class ImportResult:
    batch_id: int | None
    errors: list[ValidationErrorDetail] = field(default_factory=list)
    ledger_counts: dict[str, int] = field(default_factory=dict)


def import_workbook(config: AppConfig, workbook_path: Path) -> ImportResult:
    wb = load_workbook(workbook_path, data_only=True)
    errors: list[ValidationErrorDetail] = []
    parsed: dict[LedgerType, tuple[str, list[tuple[int, dict[str, Any]]]]] = {}

    for sheet_name, ledger_type in EXPECTED_SHEETS.items():
        if sheet_name not in wb.sheetnames:
            errors.append(ValidationErrorDetail(0, sheet_name, "缺少必需 sheet"))
            continue
        ws = wb[sheet_name]
        headers = _headers(ws, HEADER_ROWS[ledger_type])
        missing = [name for name in required_headers_for(ledger_type) if name not in headers]
        for name in missing:
            errors.append(ValidationErrorDetail(1, name, "缺少必需字段"))
        if missing:
            continue
        parsed[ledger_type] = (sheet_name, _data_rows(ws, headers, HEADER_ROWS[ledger_type] + 1))

    if errors:
        return ImportResult(batch_id=None, errors=errors)

    with connect(config) as conn:
        batch_id = conn.execute(
            "insert into import_batches(source_file, name, status) values (?, ?, ?)",
            (str(workbook_path), workbook_path.stem, "imported"),
        ).lastrowid
        conn.execute(
            "insert into settings(key, value_json) values ('current_batch_id', ?) "
            "on conflict(key) do update set value_json = excluded.value_json",
            (str(batch_id),),
        )
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, "import", f"导入台账：{workbook_path.name}"),
        )
        ledger_counts: dict[str, int] = {}
        for ledger_type, (sheet_name, rows) in parsed.items():
            ledger_counts[ledger_type] = len(rows)
            for row_number, row in rows:
                row_json = json.dumps(row, ensure_ascii=False, default=str)
                conn.execute(
                    "insert into raw_rows(batch_id, ledger_type, sheet_name, row_number, row_json) values (?, ?, ?, ?, ?)",
                    (batch_id, ledger_type, sheet_name, row_number, row_json),
                )
                conn.execute(
                    """
                    insert into ledger_rows(
                        batch_id, ledger_type, city, district, telecom_site_code, telecom_site_name,
                        tower_site_code, tower_site_name, row_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        ledger_type,
                        _clean(row.get("地市")),
                        _clean(row.get("区县")),
                        _clean(row.get("电信站址编码")),
                        _clean(row.get("电信站址名称")),
                        _clean(row.get("铁塔站址编码")),
                        _clean(row.get("铁塔站址名称")),
                        row_json,
                    ),
                )
        return ImportResult(batch_id=batch_id, ledger_counts=ledger_counts)


def _headers(ws: Worksheet, header_rows: int) -> list[str]:
    if header_rows == 1:
        return [_clean(cell.value) for cell in ws[1] if _clean(cell.value)]
    first = _parent_headers(ws)
    second = [_clean(cell.value) for cell in ws[2]]
    headers: list[str] = []
    for parent, child in zip(first, second, strict=False):
        if parent == "发电时间" and child:
            headers.append(f"{parent} - {child}")
        elif child:
            headers.append(child)
        elif parent:
            headers.append(parent)
    return headers


def _data_rows(ws: Worksheet, headers: list[str], first_data_row: int) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for row_number, values in enumerate(ws.iter_rows(min_row=first_data_row, values_only=True), start=first_data_row):
        row = {header: value for header, value in zip(headers, values, strict=False)}
        if any(value not in (None, "") for value in row.values()):
            rows.append((row_number, row))
    return rows


def _parent_headers(ws: Worksheet) -> list[str | None]:
    headers: list[str | None] = []
    for cell in ws[1]:
        value = _clean(cell.value)
        if value:
            headers.append(value)
            continue
        headers.append(_merged_parent_value(ws, cell.column))
    return headers


def _merged_parent_value(ws: Worksheet, column: int) -> str | None:
    for cell_range in ws.merged_cells.ranges:
        if cell_range.min_row == 1 and cell_range.max_row == 1 and cell_range.min_col <= column <= cell_range.max_col:
            return _clean(ws.cell(row=1, column=cell_range.min_col).value)
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
