from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from governance_app.config import AppConfig
from governance_app.importer import _data_rows, _headers
from governance_app.models import LedgerType, ValidationErrorDetail
from governance_app.templates import EXPECTED_SHEETS, HEADER_ROWS, required_headers_for


@dataclass(frozen=True)
class ImportPreviewResult:
    ok: bool
    batch_name: str
    ledger_counts: dict[str, int] = field(default_factory=dict)
    errors: list[ValidationErrorDetail] = field(default_factory=list)


def preview_workbook(config: AppConfig, workbook_path: Path) -> ImportPreviewResult:
    del config
    wb = load_workbook(workbook_path, data_only=True)
    errors: list[ValidationErrorDetail] = []
    ledger_counts: dict[str, int] = {}

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
            ledger_counts[ledger_type] = 0
            continue
        ledger_counts[ledger_type] = len(_data_rows(ws, headers, HEADER_ROWS[ledger_type] + 1))

    return ImportPreviewResult(
        ok=not errors,
        batch_name=workbook_path.stem,
        ledger_counts=_with_all_ledgers(ledger_counts),
        errors=errors,
    )


def _with_all_ledgers(counts: dict[LedgerType, int]) -> dict[str, int]:
    return {ledger_type: counts.get(ledger_type, 0) for ledger_type in ("site", "tower_rent", "electricity", "generator")}
