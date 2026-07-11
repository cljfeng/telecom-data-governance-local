from urllib.parse import ParseResult, parse_qs

from governance_app.analytics import dashboard_summary
from governance_app.config import AppConfig
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_payload,
    batch_id_from_query,
    json_body,
    json_response,
)
from governance_app.workflow import (
    city_progress,
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
    if method == "GET" and parsed.path == "/api/dashboard":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        return json_response(dashboard_summary(config, batch_id))
    if method == "GET" and parsed.path == "/api/batches":
        return json_response({"batches": list_batches(config)})
    if method == "POST" and parsed.path == "/api/batches":
        payload, error = json_body(body)
        if error:
            return error
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return json_response({"error": "name is required"}, status=400)
        try:
            batch_id = create_batch(config, name)
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
            if key != "batch_id" and values and values[0]
        }
        return json_response({"rows": list_ledger_rows(config, batch_id, filters)})
    if method == "GET" and parsed.path == "/api/city-progress":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        return json_response({"cities": city_progress(config, batch_id)})
    return None
