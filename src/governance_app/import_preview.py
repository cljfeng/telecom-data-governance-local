import re
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import Workbook
from openpyxl import load_workbook

from governance_app.config import AppConfig
from governance_app.importer import _data_rows, _headers
from governance_app.models import LedgerType, ValidationErrorDetail
from governance_app.recent_files import list_recent_files, record_recent_file
from governance_app.templates import EXPECTED_SHEETS, HEADER_ROWS, required_headers_for


@dataclass(frozen=True)
class ImportPreviewResult:
    ok: bool
    batch_name: str
    ledger_counts: dict[str, int] = field(default_factory=dict)
    errors: list[ValidationErrorDetail] = field(default_factory=list)


def preview_workbook(config: AppConfig, workbook_path: Path) -> ImportPreviewResult:
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

    result = ImportPreviewResult(
        ok=not errors,
        batch_name=workbook_path.stem,
        ledger_counts=_with_all_ledgers(ledger_counts),
        errors=errors,
    )
    record_recent_file(config, workbook_path, "preview", result.ok, result.ledger_counts, len(result.errors))
    return result


def _with_all_ledgers(counts: dict[LedgerType, int]) -> dict[str, int]:
    return {ledger_type: counts.get(ledger_type, 0) for ledger_type in ("site", "tower_rent", "electricity", "generator")}


def export_preview_errors(config: AppConfig, workbook_path: Path, result: ImportPreviewResult) -> Path:
    error_dir = config.export_dir / "import_errors"
    error_dir.mkdir(parents=True, exist_ok=True)
    path = error_dir / f"{_safe_filename_part(workbook_path.stem)}_导入预检错误.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "导入错误明细"
    ws.append(["来源文件", "行号", "字段名", "错误类型", "处理建议"])
    for error in result.errors:
        ws.append(
            [
                str(workbook_path),
                error.row_number,
                error.field_name,
                error.message,
                _suggestion_for(error),
            ]
        )
    wb.save(path)
    return path


def _safe_filename_part(value: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", value.strip())
    while ".." in text:
        text = text.replace("..", "_")
    return text.strip("._") or "导入预检"


def _suggestion_for(error: ValidationErrorDetail) -> str:
    if "sheet" in error.message:
        return "补充模板中缺失的工作表后重新预检"
    if "字段" in error.message:
        return "按省公司模板补齐字段列名后重新预检"
    return "核对模板内容后重新预检"
