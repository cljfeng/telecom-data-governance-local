import csv
from io import StringIO
from urllib.parse import ParseResult, parse_qs

from governance_app.analytics import dashboard_summary
from governance_app.config import AppConfig, RuntimeMode
from governance_app.database_runtime import database_for
from governance_app.identity_store import identity_store_for
from governance_app.request_context import current_principal
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_payload,
    batch_id_from_query,
    json_body,
    json_response,
    pagination_from_query,
)
from governance_app.security import (
    claim_batch_for_current_principal,
    filter_batches_for_current_principal,
)
from governance_app.workflow import (
    city_progress,
    count_ledger_rows,
    create_batch,
    get_batch_workflow,
    list_batches,
    list_ledger_rows,
    set_current_batch,
)


def handle_batch_route(
    config: AppConfig,
    method: str,
    parsed: ParseResult,
    body: str,
) -> JsonResponse | None:
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
    if method == "GET" and parsed.path == "/api/dashboard":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        return json_response(dashboard_summary(config, batch_id))
    if method == "GET" and parsed.path == "/api/batches":
        batches = filter_batches_for_current_principal(
            config,
            list_batches(config),
        )
        return json_response({"batches": batches})
    if method == "POST" and parsed.path == "/api/batches":
        payload, error = json_body(body)
        if error:
            return error
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return json_response({"error": "name is required"}, status=400)
        try:
            batch_id = create_batch(config, name)
            claim_batch_for_current_principal(config, batch_id)
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"batch_id": batch_id})
    if method == "POST" and parsed.path == "/api/batches/current":
        payload, error = json_body(body)
        if error:
            return error
        batch_id, error = batch_id_from_payload(payload)
        if error:
            return error
        try:
            set_current_batch(config, batch_id)
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=404)
        return json_response({"status": "selected"})
    if method == "GET" and parsed.path == "/api/workflow":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        try:
            return json_response(get_batch_workflow(config, batch_id))
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=404)
    if method == "GET" and parsed.path == "/api/ledger-rows":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        query = parse_qs(parsed.query)
        filters = {
            key: values[0]
            for key, values in query.items()
            if key not in {"batch_id", "limit", "offset"} and values and values[0]
        }
        limit, offset = pagination_from_query(query)
        page_limit = limit or 50
        return json_response(
            {
                "rows": list_ledger_rows(
                    config, batch_id, filters, limit=page_limit, offset=offset
                ),
                "total": count_ledger_rows(config, batch_id, filters),
                "limit": page_limit,
                "offset": offset,
            }
        )
    if method == "GET" and parsed.path == "/api/city-progress":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        return json_response({"cities": city_progress(config, batch_id)})
    return None
