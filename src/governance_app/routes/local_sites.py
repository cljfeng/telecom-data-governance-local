from urllib.parse import ParseResult

from governance_app.config import AppConfig, RuntimeMode
from governance_app.database_runtime import database_for
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_query,
    json_body,
    json_response,
)


def handle_local_site_route(config: AppConfig, method: str, parsed: ParseResult,
                            body: str) -> JsonResponse | None:
    if not parsed.path.startswith("/api/local/sites"):
        return None
    if config.runtime_mode is not RuntimeMode.LOCAL:
        return json_response({"error": "local site maintenance is unavailable online"}, status=404)
    if method == "GET" and parsed.path == "/api/local/sites":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        with database_for(config).unit_of_work() as unit:
            return json_response({"sites": unit.ledgers.site_authorities(batch_id)})
    parts = parsed.path.removeprefix("/api/local/sites/").split("/")
    try:
        row_id = int(parts[0])
    except ValueError:
        return json_response({"error": "site record not found"}, status=404)
    if method == "GET" and len(parts) == 1:
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        with database_for(config).unit_of_work() as unit:
            detail = unit.ledgers.site_authority(batch_id, row_id)
        return json_response(detail) if detail is not None else json_response(
            {"error": "site record not found"}, status=404)
    if method == "POST" and parts[1:] == ["corrections"]:
        payload, error = json_body(body)
        if error:
            return error
        try:
            batch_id = int(payload["batch_id"])
        except (KeyError, TypeError, ValueError):
            return json_response({"error": "batch_id is required"}, status=400)
        changes = payload.get("changes")
        if (not isinstance(changes, dict) or not changes
                or any(not isinstance(key, str) or not key.strip() or key == "电信站址编码"
                       for key in changes)
                or any(not isinstance(value, (str, int, float, bool)) and value is not None
                       for value in changes.values())):
            return json_response({"error": "invalid site changes"}, status=400)
        fields = ("evidence", "operator", "error_cause", "source", "idempotency_key")
        if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in fields):
            return json_response({"error": "evidence, operator, cause, source and idempotency key are required"}, status=400)
        request = {"changes": changes, **{key: payload[key].strip() for key in fields}}
        try:
            with database_for(config).unit_of_work() as unit:
                batch = unit.batches.get(batch_id)
                if batch is None or batch["is_archived"]:
                    raise ValueError("batch not found or archived")
                version, created = unit.ledgers.revise_site(batch_id, row_id, request)
                if created:
                    unit.batches.add_operation(batch_id, "revise_authoritative_site",
                                               f"站址记录 {row_id} 生效版本 {version}，操作者：{request['operator']}")
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=409)
        return json_response({"version": version})
    return json_response({"error": "not found"}, status=404)
