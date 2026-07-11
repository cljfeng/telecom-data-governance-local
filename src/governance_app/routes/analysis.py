from urllib.parse import ParseResult, parse_qs

from governance_app.config import AppConfig
from governance_app.electricity_analysis import (
    export_electricity_opportunities,
    get_electricity_opportunities,
    get_electricity_summary,
    run_electricity_analysis,
)
from governance_app.routes.common import JsonResponse, json_response
from governance_app.tower_rent_analysis import (
    export_tower_rent_clues,
    get_tower_rent_clues,
    get_tower_rent_summary,
    run_tower_rent_analysis,
)

_ACTIONS = {"run", "summary", "opportunities", "export"}
_DOMAINS = {"electricity-analysis", "tower-rent-analysis"}


def analysis_path(path: str) -> tuple[str, int, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) < 4 or parts[:2] != ["api", "batches"] or parts[3] not in _DOMAINS:
        return None
    if len(parts) != 5 or parts[4] not in _ACTIONS:
        return None
    try:
        batch_id = int(parts[2])
    except ValueError as exc:
        raise ValueError("invalid batch_id") from exc
    return parts[3], batch_id, parts[4]


def handle_analysis_route(
    config: AppConfig,
    method: str,
    parsed: ParseResult,
    body: str,
) -> JsonResponse | None:
    del body
    parts = parsed.path.strip("/").split("/")
    owns_path = len(parts) >= 4 and parts[:2] == ["api", "batches"] and parts[3] in _DOMAINS
    try:
        matched = analysis_path(parsed.path)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)
    if matched is None:
        return json_response({"error": "not found"}, status=404) if owns_path else None
    domain, batch_id, action = matched
    try:
        if domain == "electricity-analysis":
            return _electricity_response(config, method, parsed, batch_id, action)
        return _tower_rent_response(config, method, parsed, batch_id, action)
    except ValueError as exc:
        return json_response({"error": str(exc)}, status=400)


def _electricity_response(config, method, parsed, batch_id, action) -> JsonResponse:
    if method == "POST" and action == "run":
        return json_response(run_electricity_analysis(config, batch_id))
    if method == "GET" and action == "summary":
        return json_response(get_electricity_summary(config, batch_id))
    if method == "GET" and action == "opportunities":
        filters = _filters(parsed)
        return json_response({"opportunities": get_electricity_opportunities(config, batch_id, filters=filters)})
    if method == "POST" and action == "export":
        return json_response({"path": str(export_electricity_opportunities(config, batch_id))})
    return json_response({"error": "not found"}, status=404)


def _tower_rent_response(config, method, parsed, batch_id, action) -> JsonResponse:
    if method == "POST" and action == "run":
        return json_response(run_tower_rent_analysis(config, batch_id))
    if method == "GET" and action == "summary":
        return json_response(get_tower_rent_summary(config, batch_id))
    if method == "GET" and action == "opportunities":
        filters = _filters(parsed)
        return json_response({"opportunities": get_tower_rent_clues(config, batch_id, filters=filters)})
    if method == "POST" and action == "export":
        return json_response({"path": str(export_tower_rent_clues(config, batch_id))})
    return json_response({"error": "not found"}, status=404)


def _filters(parsed: ParseResult) -> dict[str, str]:
    query = parse_qs(parsed.query)
    return {key: values[0] for key, values in query.items() if values and values[0]}
