import json
from urllib.parse import ParseResult, parse_qs

from governance_app.config import AppConfig, RuntimeMode
from governance_app.database_runtime import database_for
from governance_app.identity_store import identity_store_for
from governance_app.request_context import current_principal
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_query,
    json_body,
    json_response,
)
from governance_app.routes.local_tower_rents import _valid_changes
from governance_app.workflow import list_ledger_rows


def handle_tower_rent_change_route(config: AppConfig, method: str, parsed: ParseResult,
                                   body: str) -> JsonResponse | None:
    if not parsed.path.startswith(("/api/tower-rents", "/api/tower-rent-corrections")):
        return None
    if config.runtime_mode is not RuntimeMode.ONLINE:
        return json_response({"error": "online rent workflow is unavailable locally"}, status=404)
    principal = current_principal()
    if principal is None:
        return json_response({"error": "authentication required"}, status=401)
    if method == "GET" and parsed.path == "/api/tower-rents":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        params = parse_qs(parsed.query)
        try:
            limit = max(1, min(int(params.get("limit", ["100"])[0]), 500))
            offset = max(0, int(params.get("offset", ["0"])[0]))
        except ValueError:
            return json_response({"error": "invalid pagination"}, status=400)
        visible = list_ledger_rows(config, batch_id, {"ledger_type": "tower_rent"},
                                   limit=limit, offset=offset)
        with database_for(config).unit_of_work() as unit:
            by_id = {row["row_id"]: row for row in unit.ledgers.tower_rent_authorities(batch_id)}
        return json_response({"tower_rents": [by_id[row["id"]] for row in visible]})
    if method == "GET" and parsed.path.startswith("/api/tower-rents/"):
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        try:
            row_id = int(parsed.path.removeprefix("/api/tower-rents/"))
        except ValueError:
            return json_response({"error": "tower rent record not found"}, status=404)
        if not _visible(config, batch_id, row_id):
            return json_response({"error": "tower rent record not found"}, status=404)
        with database_for(config).unit_of_work() as unit:
            detail = unit.ledgers.tower_rent_authority(batch_id, row_id)
        return json_response(detail) if detail is not None else json_response(
            {"error": "tower rent record not found"}, status=404)
    if method == "POST" and parsed.path == "/api/tower-rent-corrections":
        return _submit(config, body)
    parts = parsed.path.removeprefix("/api/tower-rent-corrections/").split("/")
    try:
        request_id = int(parts[0])
    except ValueError:
        return json_response({"error": "tower rent correction request not found"}, status=404)
    if method == "GET" and len(parts) == 1:
        with database_for(config).unit_of_work() as unit:
            record = unit.ledgers.tower_rent_change_request(request_id)
        if record is None or principal.data_scope != "all" and principal.organization_id not in {
            record["proposer_organization_id"], record["reviewer_organization_id"]
        }:
            return json_response({"error": "tower rent correction request not found"}, status=404)
        return json_response({"request": _public(record)})
    if method == "POST" and parts[1:] == ["resubmit"]:
        with database_for(config).unit_of_work() as unit:
            previous = unit.ledgers.tower_rent_change_request(request_id)
        if previous is None:
            return json_response({"error": "tower rent correction request not found"}, status=404)
        if previous["status"] != "rejected":
            return json_response({"error": "only rejected requests can be resubmitted"}, status=409)
        if previous["proposer_user_id"] != principal.user_id:
            return json_response({"error": "only the original proposer can resubmit"}, status=403)
        payload, error = json_body(body)
        if error:
            return error
        payload.update(batch_id=previous["batch_id"], row_id=previous["ledger_row_id"])
        return _submit(config, json.dumps(payload, ensure_ascii=False), request_id)
    if method == "POST" and parts[1:] == ["decision"]:
        payload, error = json_body(body)
        if error:
            return error
        action, note = payload.get("action"), payload.get("note")
        if action not in {"approve", "reject"} or not isinstance(note, str) or not note.strip():
            return json_response({"error": "action and note are required"}, status=400)
        try:
            with database_for(config).unit_of_work() as unit:
                pending = unit.ledgers.tower_rent_change_request(request_id)
                if pending is not None:
                    batch = unit.batches.get(int(pending["batch_id"]))
                    if batch is None or batch["is_archived"]:
                        raise ValueError("batch not found or archived")
                record, changed = unit.ledgers.decide_tower_rent_change(
                    request_id, action=action, note=note.strip(),
                    reviewer_user_id=principal.user_id,
                    reviewer_organization_id=principal.organization_id,
                    reviewer_username=principal.username)
        except PermissionError as exc:
            return json_response({"error": str(exc)}, status=403)
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=409)
        return json_response({"request": _public(record), "changed": changed})
    return json_response({"error": "not found"}, status=404)


def _visible(config: AppConfig, batch_id: int, row_id: int) -> bool:
    return bool(list_ledger_rows(config, batch_id, {
        "ledger_type": "tower_rent", "row_id": str(row_id)
    }, limit=1))


def _submit(config: AppConfig, body: str, replaces_request_id: int | None = None) -> JsonResponse:
    payload, error = json_body(body)
    if error:
        return error
    principal = current_principal()
    assert principal is not None
    try:
        batch_id, row_id = int(payload["batch_id"]), int(payload["row_id"])
        changes = payload["changes"]
        if not _valid_changes(changes):
            raise ValueError("invalid tower rent changes")
        fields = ("evidence", "note", "error_cause", "source", "idempotency_key")
        if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in fields):
            raise ValueError("evidence, note, cause, source and idempotency key are required")
    except (KeyError, TypeError, ValueError) as exc:
        return json_response({"error": str(exc) or "invalid tower rent request"}, status=400)
    if not _visible(config, batch_id, row_id):
        return json_response({"error": "tower rent record not found"}, status=404)
    with database_for(config).unit_of_work() as unit:
        detail = unit.ledgers.tower_rent_authority(batch_id, row_id)
    if detail is None or any(field not in detail["current"] for field in changes):
        return json_response({"error": "tower rent changes must reference existing fields"}, status=400)
    reviewer_id = identity_store_for(config).correction_reviewer_organization_id(
        principal.organization_id)
    if reviewer_id is None:
        return json_response({"error": "organization cannot submit tower rent corrections"}, status=403)
    request = {"batch_id": batch_id, "row_id": row_id, "changes": changes,
               **{key: payload[key].strip() for key in fields},
               "proposer_user_id": principal.user_id,
               "proposer_organization_id": principal.organization_id,
               "proposer_username": principal.username,
               "reviewer_organization_id": reviewer_id,
               "replaces_request_id": replaces_request_id}
    try:
        with database_for(config).unit_of_work() as unit:
            batch = unit.batches.get(batch_id)
            if batch is None or batch["is_archived"]:
                raise ValueError("batch not found or archived")
            record, _ = unit.ledgers.submit_tower_rent_change(request)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=409)
    return json_response({"request": _public(record)}, status=201)


def _public(record: dict) -> dict:
    result = dict(record)
    result["changes"] = json.loads(result.pop("changes_json"))
    result.pop("request_json", None)
    return result
