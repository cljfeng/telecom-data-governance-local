import csv
import mimetypes
from io import StringIO
from urllib.parse import ParseResult, quote

from governance_app.config import AppConfig, RuntimeMode
from governance_app.database_runtime import database_for
from governance_app.file_storage_runtime import file_storage_for
from governance_app.identity_store import identity_store_for
from governance_app.request_context import current_principal
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_query,
    json_body,
    json_response,
)
from governance_app.workflow import count_ledger_rows, list_ledger_rows


def handle_site_route(config: AppConfig, method: str, parsed: ParseResult,
                      body: str) -> JsonResponse | None:
    if not parsed.path.startswith("/api/sites/"):
        return None
    if method == "GET" and parsed.path in {"/api/sites/summary", "/api/sites/export"}:
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        total = count_ledger_rows(config, batch_id, {"ledger_type": "site"})
        rows = []
        for offset in range(0, total, 500):
            rows.extend(list_ledger_rows(config, batch_id, {"ledger_type": "site"},
                                         limit=500, offset=offset))
        if parsed.path.endswith("/summary"):
            cities: dict[str, int] = {}
            for row in rows:
                cities[row["city"]] = cities.get(row["city"], 0) + 1
            return json_response({"total": total, "cities": cities})
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(("记录ID", "市州", "区县", "站址编码", "站址名称"))
        for row in rows:
            writer.writerow((row["id"], row["city"], row["district"],
                             row["telecom_site_code"], row["telecom_site_name"]))
        return (200, {"content-type": "text/csv; charset=utf-8",
                      "content-disposition": 'attachment; filename="sites.csv"'},
                ("\ufeff" + output.getvalue()).encode("utf-8"))
    if method == "POST" and parsed.path == "/api/sites/jurisdiction":
        principal = current_principal()
        if config.runtime_mode is not RuntimeMode.ONLINE or principal is None or principal.data_scope != "all":
            return json_response({"error": "resource not found"}, status=404)
        payload, error = json_body(body)
        if error:
            return error
        try:
            batch_id, row_id = int(payload["batch_id"]), int(payload["row_id"])
            city, district, reason = (str(payload[key]).strip() for key in ("city", "district", "reason"))
        except (KeyError, ValueError, TypeError):
            return json_response({"error": "invalid jurisdiction correction"}, status=400)
        if not reason or not identity_store_for(config).is_valid_site_jurisdiction(city, district):
            return json_response({"error": "unrecognized jurisdiction or missing reason"}, status=400)
        try:
            with database_for(config).unit_of_work() as unit:
                batch = unit.batches.get(batch_id)
                if batch is None or batch["is_archived"]:
                    raise ValueError("batch not found or archived")
                changed = unit.ledgers.reassign_site(batch_id, row_id, city, district,
                                                       reason, principal.user_id)
                if changed:
                    unit.batches.add_operation(batch_id, "site_jurisdiction",
                                               f"站址记录 {row_id} 归属更正：{city}/{district}")
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=404)
        return json_response({"updated": changed})
    if method == "POST" and parsed.path == "/api/sites/evidence":
        principal = current_principal()
        if config.runtime_mode is not RuntimeMode.ONLINE or principal is None or principal.data_scope != "all":
            return json_response({"error": "resource not found"}, status=404)
        payload, error = json_body(body)
        if error:
            return error
        try:
            batch_id, row_id = int(payload["batch_id"]), int(payload["row_id"])
            file_id = str(payload["file_id"])
            file_storage_for(config).resolve(file_id)
            with database_for(config).unit_of_work() as unit:
                evidence_id = unit.ledgers.attach_site_evidence(batch_id, row_id,
                                                                  file_id, principal.user_id)
        except (KeyError, TypeError, ValueError, FileNotFoundError) as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"evidence_id": evidence_id})
    if method == "GET":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        parts = parsed.path.removeprefix("/api/sites/").split("/")
        try:
            row_id = int(parts[0])
        except ValueError:
            return json_response({"error": "site record not found"}, status=404)
        rows = list_ledger_rows(config, batch_id,
                                {"ledger_type": "site", "row_id": str(row_id)}, limit=1)
        if not rows:
            return json_response({"error": "site record not found"}, status=404)
        if len(parts) == 1:
            return json_response({"site": rows[0]})
        if len(parts) != 3 or parts[1] != "evidence":
            return json_response({"error": "not found"}, status=404)
        try:
            evidence_id = int(parts[2])
        except ValueError:
            return json_response({"error": "evidence not found"}, status=404)
        with database_for(config).unit_of_work() as unit:
            stored_file_id = unit.ledgers.site_evidence_file(batch_id, row_id, evidence_id)
        if stored_file_id is None:
            return json_response({"error": "evidence not found"}, status=404)
        try:
            file = file_storage_for(config).resolve(stored_file_id)
            content = file.local_path.read_bytes()
        except (FileNotFoundError, ValueError):
            return json_response({"error": "evidence not found"}, status=404)
        return (200, {"content-type": mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                      "content-disposition": f"attachment; filename*=UTF-8''{quote(file.name)}"}, content)
    return None
