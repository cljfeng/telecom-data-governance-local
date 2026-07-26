import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from governance_app.config import AppConfig
from governance_app.database_runtime import database_for
from governance_app.models import LedgerType, ValidationErrorDetail
from governance_app.ports.database import Database, ImportedLedgerRow
from governance_app.recent_files import record_recent_file
from governance_app.templates import (
    EXPECTED_SHEETS,
    HEADER_ROWS,
    canonical_header,
    required_headers_for,
    workbook_sheet_for,
)
from governance_app.workflow import _new_batch_code, transition_batch_in_unit_of_work


@dataclass(frozen=True)
class ImportResult:
    batch_id: int | None
    errors: list[ValidationErrorDetail] = field(default_factory=list)
    ledger_counts: dict[str, int] = field(default_factory=dict)


def import_workbook(
    config: AppConfig,
    workbook_path: Path,
    strategy: str = "new",
    batch_id: int | None = None,
    *,
    database: Database | None = None,
) -> ImportResult:
    started_at = perf_counter()
    wb = load_workbook(workbook_path, data_only=True)
    errors: list[ValidationErrorDetail] = []
    parsed: dict[LedgerType, tuple[str, list[tuple[int, dict[str, Any]]]]] = {}

    for canonical_sheet_name, ledger_type in EXPECTED_SHEETS.items():
        sheet_name = workbook_sheet_for(wb.sheetnames, ledger_type)
        if sheet_name is None:
            errors.append(ValidationErrorDetail(0, canonical_sheet_name, "缺少必需 sheet"))
            continue
        ws = wb[sheet_name]
        headers = _headers(ws, HEADER_ROWS[ledger_type], ledger_type)
        missing = [name for name in required_headers_for(ledger_type) if name not in headers]
        for name in missing:
            errors.append(ValidationErrorDetail(1, name, "缺少必需字段"))
        if missing:
            continue
        parsed[ledger_type] = (sheet_name, _data_rows(ws, headers, HEADER_ROWS[ledger_type] + 1))

    if errors:
        return ImportResult(batch_id=None, errors=errors)

    if strategy not in {"new", "append", "replace"}:
        raise ValueError("invalid import strategy")
    if strategy in {"append", "replace"} and batch_id is None:
        raise ValueError("batch_id is required")

    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        if strategy == "new":
            batch_name = _clean_batch_name(workbook_path.stem)
            batch_id = unit_of_work.batches.create_imported(
                source_file=str(workbook_path),
                name=batch_name,
                batch_code=_new_batch_code(),
            )
            operation = "import"
            message = f"导入台账：{workbook_path.name}"
        else:
            assert batch_id is not None
            batch = unit_of_work.batches.get(batch_id)
            if batch is None:
                raise ValueError("batch not found")
            if batch["is_archived"]:
                raise ValueError("batch is archived")
            if strategy == "replace":
                unit_of_work.ledgers.clear_batch_data(batch_id)
                operation = "import_replace"
                message = f"覆盖导入台账：{workbook_path.name}"
            else:
                operation = "import_append"
                message = f"追加导入台账：{workbook_path.name}"
            unit_of_work.batches.update_source(
                batch_id,
                source_file=str(workbook_path),
                fallback_name=_clean_batch_name(workbook_path.stem),
            )
            transition_batch_in_unit_of_work(unit_of_work, batch_id, "import")
        unit_of_work.batches.set_current(batch_id)
        ledger_counts: dict[str, int] = {}
        for ledger_type, (sheet_name, rows) in parsed.items():
            ledger_counts[ledger_type] = len(rows)
            for row_number, row in rows:
                row_json = json.dumps(row, ensure_ascii=False, default=str)
                unit_of_work.ledgers.add_imported_row(
                    batch_id,
                    ImportedLedgerRow(
                        ledger_type=ledger_type,
                        sheet_name=sheet_name,
                        row_number=row_number,
                        row_json=row_json,
                        city=_clean(row.get("地市")),
                        district=_clean(row.get("区县")),
                        telecom_site_code=_clean(row.get("电信站址编码")),
                        telecom_site_name=_clean(row.get("电信站址名称")),
                        tower_site_code=_clean(row.get("铁塔站址编码")),
                        tower_site_name=_clean(row.get("铁塔站址名称")),
                    ),
                )
        total_records = sum(ledger_counts.values())
        elapsed = perf_counter() - started_at
        unit_of_work.batches.add_operation(
            batch_id,
            operation,
            f"{message}；记录 {total_records} 条；耗时 {elapsed:.2f} 秒",
        )
    record_recent_file(config, workbook_path, "import", True, ledger_counts, 0)
    return ImportResult(batch_id=batch_id, ledger_counts=ledger_counts)


def _headers(ws: Worksheet, header_rows: int, ledger_type: LedgerType) -> list[str]:
    if header_rows == 1:
        raw_headers = tuple(cell.value for cell in ws[1])
        return [header for cell in ws[1] if (header := canonical_header(cell.value, ledger_type, raw_headers))]
    first = _parent_headers(ws, ledger_type)
    raw_second_headers = tuple(cell.value for cell in ws[2])
    second = [canonical_header(cell.value, ledger_type, raw_second_headers) for cell in ws[2]]
    headers: list[str] = []
    for parent, child in zip(first, second, strict=False):
        if parent == "发电时间" and child and child != "发电时长":
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


def _parent_headers(ws: Worksheet, ledger_type: LedgerType) -> list[str | None]:
    headers: list[str | None] = []
    raw_headers = tuple(cell.value for cell in ws[1])
    for cell in ws[1]:
        value = canonical_header(cell.value, ledger_type, raw_headers)
        if value:
            headers.append(value)
            continue
        headers.append(_merged_parent_value(ws, cell.column))
    return headers


def _merged_parent_value(ws: Worksheet, column: int) -> str | None:
    for cell_range in ws.merged_cells.ranges:
        if cell_range.min_row == 1 and cell_range.max_row == 1 and cell_range.min_col <= column <= cell_range.max_col:
            return canonical_header(ws.cell(row=1, column=cell_range.min_col).value)
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_batch_name(value: str) -> str:
    return re.sub(r"^[0-9a-fA-F]{32}-", "", value).strip() or value
