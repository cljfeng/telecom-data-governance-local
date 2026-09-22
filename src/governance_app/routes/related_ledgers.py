import csv
import mimetypes
from io import StringIO
from typing import Any, Mapping
from urllib.parse import ParseResult, parse_qs, quote

from governance_app.config import AppConfig, RuntimeMode
from governance_app.database_runtime import database_for
from governance_app.file_storage_runtime import file_storage_for
from governance_app.models import LedgerType
from governance_app.request_context import current_principal
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_query,
    json_body,
    json_response,
)
from governance_app.workflow import count_ledger_rows, list_ledger_rows

_RELATED_LEDGER_TYPES: tuple[LedgerType, ...] = (
    "tower_rent",
    "electricity",
    "generator",
)


def handle_related_ledger_route(
    config: AppConfig, method: str, parsed: ParseResult, body: str
) -> JsonResponse | None:
    if not parsed.path.startswith("/api/related-ledgers/"):
        return None
    if method == "POST" and parsed.path == "/api/related-ledgers/evidence":
        return _register_evidence(config, body)
    if method != "GET":
        return None
    batch_id, error = batch_id_from_query(parsed.query)
    if error:
        return error
    if parsed.path in {"/api/related-ledgers/summary", "/api/related-ledgers/export"}:
        return _summary_or_export(config, parsed, batch_id)
    return _record_or_evidence(config, parsed.path, batch_id)


def _register_evidence(config: AppConfig, body: str) -> JsonResponse:
    principal = current_principal()
    if (
        config.runtime_mode is not RuntimeMode.ONLINE
        or principal is None
        or principal.data_scope != "all"
    ):
        return json_response({"error": "resource not found"}, status=404)
    payload, error = json_body(body)
    if error:
        return error
    try:
        batch_id, row_id = int(payload["batch_id"]), int(payload["row_id"])
        file_id = str(payload["file_id"])
        file_storage_for(config).resolve(file_id)
        with database_for(config).unit_of_work() as unit:
            evidence_id = unit.ledgers.attach_record_evidence(
                batch_id, row_id, file_id, principal.user_id, _RELATED_LEDGER_TYPES
            )
    except (KeyError, TypeError, ValueError, FileNotFoundError) as exc:
        return json_response({"error": str(exc)}, status=400)
    return json_response({"evidence_id": evidence_id})


def _summary_or_export(
    config: AppConfig, parsed: ParseResult, batch_id: int
) -> JsonResponse:
    requested = parse_qs(parsed.query).get("ledger_type", [""])[0]
    if requested and requested not in _RELATED_LEDGER_TYPES:
        return json_response({"error": "invalid related ledger type"}, status=400)
    ledger_types: tuple[str, ...] = (
        (requested,) if requested else _RELATED_LEDGER_TYPES
    )
    rows: list[Mapping[str, Any]] = []
    counts: dict[str, int] = {}
    for ledger_type in ledger_types:
        filters: dict[str, str] = {"ledger_type": ledger_type}
        total = count_ledger_rows(config, batch_id, filters)
        counts[ledger_type] = total
        for offset in range(0, total, 500):
            rows.extend(
                list_ledger_rows(config, batch_id, filters, limit=500, offset=offset)
            )
    if parsed.path.endswith("/summary"):
        return json_response({"total": sum(counts.values()), "ledger_types": counts})
    return _csv_export(rows)


def _csv_export(rows: list[Mapping[str, Any]]) -> JsonResponse:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(("记录ID", "台账类型", "市州", "区县", "站址编码", "站址名称"))
    for row in rows:
        writer.writerow(
            (
                row["id"],
                row["ledger_type"],
                row["city"],
                row["district"],
                row["telecom_site_code"],
                row["telecom_site_name"],
            )
        )
    return (
        200,
        {
            "content-type": "text/csv; charset=utf-8",
            "content-disposition": 'attachment; filename="related-ledgers.csv"',
        },
        ("\ufeff" + output.getvalue()).encode("utf-8"),
    )


def _record_or_evidence(config: AppConfig, path: str, batch_id: int) -> JsonResponse:
    parts = path.removeprefix("/api/related-ledgers/").split("/")
    try:
        row_id = int(parts[0])
    except ValueError:
        return json_response({"error": "related ledger record not found"}, status=404)
    rows = list_ledger_rows(config, batch_id, {"row_id": str(row_id)}, limit=1)
    if not rows or rows[0]["ledger_type"] not in _RELATED_LEDGER_TYPES:
        return json_response({"error": "related ledger record not found"}, status=404)
    if len(parts) == 1:
        return json_response({"record": rows[0]})
    if len(parts) != 3 or parts[1] != "evidence":
        return json_response({"error": "not found"}, status=404)
    return _evidence_download(config, batch_id, row_id, parts[2])


def _evidence_download(
    config: AppConfig, batch_id: int, row_id: int, evidence_value: str
) -> JsonResponse:
    try:
        evidence_id = int(evidence_value)
    except ValueError:
        return json_response({"error": "evidence not found"}, status=404)
    with database_for(config).unit_of_work() as unit:
        stored_file_id = unit.ledgers.record_evidence_file(
            batch_id, row_id, evidence_id, _RELATED_LEDGER_TYPES
        )
    if stored_file_id is None:
        return json_response({"error": "evidence not found"}, status=404)
    try:
        file = file_storage_for(config).resolve(stored_file_id)
        content = file.local_path.read_bytes()
    except (FileNotFoundError, ValueError):
        return json_response({"error": "evidence not found"}, status=404)
    return (
        200,
        {
            "content-type": mimetypes.guess_type(file.name)[0]
            or "application/octet-stream",
            "content-disposition": f"attachment; filename*=UTF-8''{quote(file.name)}",
        },
        content,
    )
