from urllib.parse import ParseResult, parse_qs

from governance_app.config import AppConfig, RuntimeMode
from governance_app.identity_store import (
    AuthenticationError,
    identity_store_for,
)
from governance_app.request_context import (
    current_principal,
    current_request,
)
from governance_app.routes.common import (
    JsonResponse,
    json_body,
    json_response,
)


def handle_auth_route(
    config: AppConfig,
    method: str,
    parsed: ParseResult,
    body: str,
) -> JsonResponse | None:
    if not (
        parsed.path.startswith("/api/auth/")
        or parsed.path.startswith("/api/identity/")
        or parsed.path == "/api/audit-logs"
    ):
        return None
    if config.runtime_mode is RuntimeMode.LOCAL:
        return json_response(
            {"error": "identity APIs are available in online mode"},
            status=400,
        )
    store = identity_store_for(config)
    if method == "POST" and parsed.path == "/api/auth/login":
        payload, error = json_body(body)
        if error:
            return error
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return json_response(
                {"error": "username and password are required"},
                status=400,
            )
        request = current_request()
        try:
            grant = store.authenticate(
                username=username,
                password=password,
                source_ip="" if request is None else request.source_ip,
                user_agent="" if request is None else request.user_agent,
                ttl_seconds=config.session_ttl_seconds,
            )
        except AuthenticationError as exc:
            return json_response({"error": str(exc)}, status=401)
        response = json_response(
            {
                "access_token": grant.token,
                "csrf_token": grant.csrf_token,
                "expires_at": grant.expires_at,
                "user": _principal_payload(grant.principal),
            }
        )
        status, headers, response_body = response
        headers["set-cookie"] = (
            f"session={grant.token}; Path=/; HttpOnly; Secure; "
            "SameSite=Lax"
        )
        return status, headers, response_body
    principal = current_principal()
    if principal is None:
        return json_response(
            {"error": "authentication required"},
            status=401,
        )
    if method == "GET" and parsed.path == "/api/auth/me":
        return json_response({"user": _principal_payload(principal)})
    if method == "POST" and parsed.path == "/api/auth/logout":
        if principal.session_id is not None:
            store.logout(principal.session_id)
        response = json_response({"status": "logged_out"})
        status, headers, response_body = response
        headers["set-cookie"] = (
            "session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"
        )
        return status, headers, response_body
    if method == "GET" and parsed.path == "/api/identity/organizations":
        return json_response(
            {"organizations": store.list_organizations(principal)}
        )
    if method == "POST" and parsed.path == "/api/identity/organizations":
        payload, error = json_body(body)
        if error:
            return error
        if principal.data_scope != "all":
            return json_response(
                {"error": "only platform administrators can create organizations"},
                status=403,
            )
        try:
            raw_parent_id = payload.get("parent_id")
            parent_id = (
                None
                if raw_parent_id in (None, "")
                else int(raw_parent_id)
            )
            organization_id = store.create_organization(
                code=str(payload.get("code", "")),
                name=str(payload.get("name", "")),
                parent_id=parent_id,
            )
        except (TypeError, ValueError) as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response(
            {"organization_id": organization_id},
            status=201,
        )
    if method == "GET" and parsed.path == "/api/identity/users":
        return json_response({"users": store.list_users(principal)})
    if method == "POST" and parsed.path == "/api/identity/users":
        payload, error = json_body(body)
        if error:
            return error
        role_codes = payload.get("roles")
        if not isinstance(role_codes, list) or not all(
            isinstance(item, str) for item in role_codes
        ):
            return json_response(
                {"error": "roles must be a list of role codes"},
                status=400,
            )
        try:
            organization_id = int(
                payload.get(
                    "organization_id",
                    principal.organization_id,
                )
            )
            if (
                principal.data_scope != "all"
                and organization_id != principal.organization_id
            ):
                return json_response(
                    {"error": "permission denied"},
                    status=403,
                )
            user_id = store.create_user(
                actor=principal,
                organization_id=organization_id,
                username=str(payload.get("username", "")),
                display_name=str(payload.get("display_name", "")),
                password=str(payload.get("password", "")),
                role_codes=role_codes,
            )
        except (TypeError, ValueError) as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"user_id": user_id}, status=201)
    parts = parsed.path.strip("/").split("/")
    if (
        method == "POST"
        and len(parts) == 5
        and parts[:3] == ["api", "identity", "users"]
    ):
        try:
            user_id = int(parts[3])
        except ValueError:
            return json_response({"error": "invalid user_id"}, status=400)
        payload, error = json_body(body)
        if error:
            return error
        try:
            if parts[4] == "status":
                active = payload.get("active")
                if not isinstance(active, bool):
                    return json_response(
                        {"error": "active must be boolean"},
                        status=400,
                    )
                store.set_user_active(
                    principal,
                    user_id,
                    active=active,
                )
            elif parts[4] == "password":
                password = payload.get("password")
                if not isinstance(password, str):
                    return json_response(
                        {"error": "password is required"},
                        status=400,
                    )
                store.reset_user_password(
                    principal,
                    user_id,
                    password=password,
                )
            else:
                return json_response({"error": "not found"}, status=404)
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"status": "updated"})
    if method == "GET" and parsed.path == "/api/audit-logs":
        query = parse_qs(parsed.query)
        try:
            limit = int(query.get("limit", ["100"])[0])
        except ValueError:
            limit = 100
        return json_response(
            {"logs": store.request_logs(principal, limit=limit)}
        )
    return json_response({"error": "not found"}, status=404)


def _principal_payload(principal) -> dict:
    return {
        "id": principal.user_id,
        "organization_id": principal.organization_id,
        "username": principal.username,
        "permissions": sorted(principal.permissions),
        "data_scope": principal.data_scope,
    }
