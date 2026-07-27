from urllib.parse import ParseResult, parse_qs

from governance_app.config import AppConfig, RuntimeMode
from governance_app.identity_store import (
    identity_store_for,
    task_payload,
)
from governance_app.request_context import current_principal
from governance_app.routes.common import JsonResponse, json_response
from governance_app.task_runtime import task_manager_for


def handle_task_route(
    config: AppConfig,
    method: str,
    parsed: ParseResult,
    body: str,
) -> JsonResponse | None:
    del body
    if not parsed.path.startswith("/api/tasks"):
        return None
    if config.runtime_mode is RuntimeMode.LOCAL:
        return json_response(
            {"error": "background tasks are available in online mode"},
            status=400,
        )
    principal = current_principal()
    if principal is None:
        return json_response(
            {"error": "authentication required"},
            status=401,
        )
    store = identity_store_for(config)
    if method == "GET" and parsed.path == "/api/tasks":
        query = parse_qs(parsed.query)
        try:
            limit = int(query.get("limit", ["100"])[0])
        except ValueError:
            limit = 100
        return json_response(
            {
                "tasks": [
                    task_payload(task)
                    for task in store.list_tasks(principal, limit=limit)
                ]
            }
        )
    parts = parsed.path.strip("/").split("/")
    if len(parts) not in {3, 4} or parts[:2] != ["api", "tasks"]:
        return json_response({"error": "not found"}, status=404)
    try:
        task_id = int(parts[2])
    except ValueError:
        return json_response({"error": "invalid task_id"}, status=400)
    if method == "GET" and len(parts) == 3:
        task = store.get_task(principal, task_id)
        if task is None:
            return json_response({"error": "task not found"}, status=404)
        return json_response({"task": task_payload(task)})
    if method == "POST" and parts[3:] == ["retry"]:
        task = store.retry_task(principal, task_id)
        if task is None:
            return json_response(
                {"error": "failed task not found"},
                status=404,
            )
        task_manager_for(config).submit_existing(task.id)
        return json_response({"task": task_payload(task)}, status=202)
    return json_response({"error": "not found"}, status=404)
