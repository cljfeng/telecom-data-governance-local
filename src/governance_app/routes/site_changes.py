import json
from typing import Any
from urllib.parse import ParseResult

from governance_app.config import AppConfig, RuntimeMode
from governance_app.database_runtime import database_for
from governance_app.identity_store import identity_store_for
from governance_app.request_context import current_principal
from governance_app.routes.common import JsonResponse, json_body, json_response
from governance_app.workflow import list_ledger_rows


def handle_site_change_route(
    config: AppConfig, method: str, parsed: ParseResult, body: str
) -> JsonResponse | None:
    if not parsed.path.startswith(("/api/site-corrections", "/api/site-conclusions")):
        return None
    if config.runtime_mode is not RuntimeMode.ONLINE:
        return json_response({"error": "online site workflow is unavailable locally"}, status=404)
    principal = current_principal()
    if principal is None:
        return json_response({"error": "authentication required"}, status=401)
    if method == "POST" and parsed.path in {"/api/site-corrections", "/api/site-conclusions"}:
        return _submit(config, body, parsed.path.endswith("conclusions"))
    parts = parsed.path.removeprefix("/api/site-corrections/").split("/")
    if method == "POST" and len(parts) == 2 and parts[1] == "resubmit":
        return _resubmit(config, parts[0], body)
    if method == "POST" and len(parts) == 2 and parts[1] == "decision":
        return _decide(config, parts[0], body)
    if method == "GET" and len(parts) == 1:
        return _detail(config, parts[0])
    return json_response({"error": "not found"}, status=404)


def _submit(
    config: AppConfig, body: str, no_change: bool, replaces_request_id: int | None = None
) -> JsonResponse:
    payload, error = json_body(body)
    if error:
        return error
    principal = current_principal()
    assert principal is not None
    try:
        batch_id, row_id = int(payload["batch_id"]), int(payload["row_id"])
        evidence = _required_text(payload, "evidence")
        note = _required_text(payload, "note")
        idempotency_key = _required_text(payload, "idempotency_key")
        changes = {} if no_change else _changes(payload)
        error_cause = "" if no_change else _required_text(payload, "error_cause")
        source = "" if no_change else _required_text(payload, "source")
        issue_code = payload.get("issue_code") or None
        if issue_code is not None and not isinstance(issue_code, str):
            raise ValueError("issue_code must be text")
    except (KeyError, TypeError, ValueError) as exc:
        return json_response({"error": str(exc) or "invalid site request"}, status=400)
    if not list_ledger_rows(config, batch_id, {"ledger_type": "site", "row_id": str(row_id)}, limit=1):
        return json_response({"error": "site record not found"}, status=404)
    store = identity_store_for(config)
    organization = store.organization(principal.organization_id)
    if organization is None:
        return json_response({"error": "organization not found"}, status=404)
    reviewer_id = None if no_change else store.correction_reviewer_organization_id(
        principal.organization_id
    )
    if not no_change and reviewer_id is None:
        return json_response({"error": "organization cannot submit site corrections"}, status=403)
    request = {
        "batch_id": batch_id, "row_id": row_id,
        "kind": "no_change" if no_change else "correction", "changes": changes,
        "evidence": evidence, "error_cause": error_cause, "source": source, "note": note,
        "proposer_user_id": principal.user_id,
        "proposer_organization_id": principal.organization_id,
        "proposer_username": principal.username,
        "reviewer_organization_id": reviewer_id,
        "idempotency_key": idempotency_key,
        "issue_code": issue_code,
        "replaces_request_id": replaces_request_id,
    }
    try:
        with database_for(config).unit_of_work() as unit:
            batch = unit.batches.get(batch_id)
            if batch is None or batch["is_archived"]:
                raise ValueError("batch not found or archived")
            record, created = unit.ledgers.submit_site_change(request)
            if no_change and issue_code and created:
                issue = unit.issues.get_with_batch(issue_code)
                if issue is None:
                    raise ValueError("issue not found")
                unit.issues.update_status(
                    issue, "not_required", source="site_no_change",
                    event_note=note, correction_note=evidence,
                    update_correction_note=True,
                )
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=409)
    return json_response({"request": _public(record)}, status=201)


def _resubmit(config: AppConfig, raw_id: str, body: str) -> JsonResponse:
    principal = current_principal()
    assert principal is not None
    try:
        request_id = int(raw_id)
    except ValueError:
        return json_response({"error": "site correction request not found"}, status=404)
    with database_for(config).unit_of_work() as unit:
        previous = unit.ledgers.site_change_request(request_id)
    if previous is None:
        return json_response({"error": "site correction request not found"}, status=404)
    if previous["status"] != "rejected":
        return json_response({"error": "only rejected requests can be resubmitted"}, status=409)
    if int(previous["proposer_user_id"]) != principal.user_id:
        return json_response({"error": "only the original proposer can resubmit"}, status=403)
    payload, error = json_body(body)
    if error:
        return error
    payload.update({
        "batch_id": previous["batch_id"],
        "row_id": previous["ledger_row_id"],
        "issue_code": previous["issue_code"],
    })
    return _submit(config, json.dumps(payload, ensure_ascii=False), False, request_id)


def _decide(config: AppConfig, raw_id: str, body: str) -> JsonResponse:
    payload, error = json_body(body)
    if error:
        return error
    principal = current_principal()
    assert principal is not None
    try:
        request_id = int(raw_id)
        action = _required_text(payload, "action")
        note = _required_text(payload, "note")
        if action not in {"approve", "reject"}:
            raise ValueError("action must be approve or reject")
        with database_for(config).unit_of_work() as unit:
            pending = unit.ledgers.site_change_request(request_id)
            if pending is not None:
                batch = unit.batches.get(int(pending["batch_id"]))
                if batch is None or batch["is_archived"]:
                    raise ValueError("batch not found or archived")
            record, changed = unit.ledgers.decide_site_change(
                request_id, action=action, note=note,
                reviewer_user_id=principal.user_id,
                reviewer_organization_id=principal.organization_id,
                reviewer_username=principal.username,
            )
    except PermissionError as exc:
        return json_response({"error": str(exc)}, status=403)
    except (TypeError, ValueError) as exc:
        return json_response({"error": str(exc)}, status=409)
    return json_response({"request": _public(record), "changed": changed})


def _detail(config: AppConfig, raw_id: str) -> JsonResponse:
    principal = current_principal()
    assert principal is not None
    try:
        request_id = int(raw_id)
    except ValueError:
        return json_response({"error": "site correction request not found"}, status=404)
    with database_for(config).unit_of_work() as unit:
        record = unit.ledgers.site_change_request(request_id)
    if record is None or principal.data_scope != "all" and principal.organization_id not in {
        record["proposer_organization_id"], record["reviewer_organization_id"]
    }:
        return json_response({"error": "site correction request not found"}, status=404)
    return json_response({"request": _public(record)})


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _changes(payload: dict[str, Any]) -> dict[str, Any]:
    changes = payload["changes"]
    if (not isinstance(changes, dict) or not changes
            or any(not isinstance(key, str) or not key.strip() or key == "电信站址编码" for key in changes)
            or any(not isinstance(value, (str, int, float, bool)) and value is not None
                   for value in changes.values())):
        raise ValueError("invalid site changes")
    return changes


def _public(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result["changes"] = json.loads(str(result.pop("changes_json")))
    result.pop("request_json", None)
    return result
