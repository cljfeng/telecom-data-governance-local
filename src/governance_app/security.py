import json
from http.cookies import SimpleCookie
from urllib.parse import ParseResult, parse_qs

from governance_app.config import AppConfig, RuntimeMode
from governance_app.identity_store import identity_store_for
from governance_app.request_context import Principal, current_principal
from governance_app.routes.common import JsonResponse, json_response

_PUBLIC_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("GET", "/api/version"),
    ("POST", "/api/auth/login"),
}
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def authorize_request(
    config: AppConfig,
    method: str,
    parsed: ParseResult,
    body: str,
    headers: dict[str, str],
) -> tuple[Principal | None, JsonResponse | None]:
    if config.runtime_mode is RuntimeMode.LOCAL:
        return None, None
    if (method, parsed.path) in _PUBLIC_ROUTES:
        return None, None
    token, auth_method = _session_token(headers)
    if not token:
        return None, json_response(
            {"error": "authentication required"},
            status=401,
        )
    store = identity_store_for(config)
    principal = store.session_principal(
        token,
        auth_method=auth_method,
    )
    if principal is None:
        return None, json_response(
            {"error": "session expired or invalid"},
            status=401,
        )
    if (
        method in _MUTATING_METHODS
        and auth_method == "cookie"
    ):
        csrf_token = headers.get("x-csrf-token", "")
        if (
            principal.session_id is None
            or not store.validate_csrf(
                principal.session_id,
                csrf_token,
            )
        ):
            return principal, json_response(
                {"error": "CSRF validation failed"},
                status=403,
            )
    permission = permission_for(method, parsed.path)
    if permission and not principal.has_permission(permission):
        return principal, json_response(
            {"error": "permission denied"},
            status=403,
        )
    if principal.data_scope != "all" and not _scoped_route(method, parsed.path):
        return principal, json_response({"error": "resource not found"}, status=404)
    batch_id, issue_code = _resource_selector(parsed, body)
    if batch_id is not None and not store.can_access_batch(
        principal,
        batch_id,
    ):
        return principal, json_response(
            {"error": "resource not found"},
            status=404,
        )
    if issue_code and not store.can_access_issue(principal, issue_code):
        return principal, json_response(
            {"error": "resource not found"},
            status=404,
        )
    return principal, None


def _scoped_route(method: str, path: str) -> bool:
    if path.startswith("/api/auth/") or path.startswith("/api/identity/"):
        return True
    if method == "GET":
        return path in {"/api/batches", "/api/ledger-rows", "/api/issues", "/api/issue-groups",
                        "/api/sites/summary", "/api/sites/export"} or path.startswith(
                            ("/api/sites/", "/api/related-ledgers/",
                             "/api/site-corrections/")
                        )
    return method == "POST" and (
        path == "/api/issues/status"
        or path.startswith("/api/site-corrections")
        or path == "/api/site-conclusions"
    )


def permission_for(method: str, path: str) -> str | None:
    if path.startswith("/api/auth/"):
        return None
    if path.startswith("/api/identity/"):
        return "identity.manage"
    if path.startswith("/api/tasks"):
        return "task.manage"
    if path == "/api/audit-logs":
        return "identity.manage"
    if method == "GET":
        if path in {"/api/settings"}:
            return "system.admin"
        return "dashboard.read"
    if path.startswith("/api/import"):
        return "import.run"
    if path in {"/api/audit", "/api/rules/settings"}:
        return "audit.run"
    if path.startswith("/api/issues") or path.startswith(
        "/api/corrections"
    ):
        return "issue.manage"
    if path.startswith(("/api/site-corrections", "/api/site-conclusions")):
        return "issue.manage"
    if path in {"/api/sites/jurisdiction", "/api/sites/evidence",
                "/api/related-ledgers/evidence"}:
        return "issue.manage"
    if path.startswith("/api/batches/") and "analysis" in path:
        return (
            "report.export"
            if path.endswith("/export")
            else "analysis.run"
        )
    if path.startswith("/api/export") or path.startswith(
        "/api/reports"
    ) or path.startswith("/api/archive"):
        return "report.export"
    if path.startswith("/api/batches"):
        return "batch.manage"
    return "system.admin"


def security_headers(
    response: JsonResponse,
    *,
    online: bool,
) -> JsonResponse:
    status, headers, body = response
    protected = {
        **headers,
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "no-referrer",
        "permissions-policy": "camera=(), microphone=(), geolocation=()",
        "content-security-policy": (
            "default-src 'self'; script-src 'self'; style-src 'self' "
            "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'"
        ),
    }
    if online:
        protected["strict-transport-security"] = (
            "max-age=31536000; includeSubDomains"
        )
        protected["cache-control"] = "no-store"
    return status, protected, body


def claim_batch_for_current_principal(
    config: AppConfig,
    batch_id: int,
) -> None:
    if config.runtime_mode is not RuntimeMode.ONLINE:
        return
    principal = current_principal()
    if principal is None:
        raise ValueError("authentication required")
    identity_store_for(config).claim_batch(batch_id, principal)


def filter_batches_for_current_principal(
    config: AppConfig,
    records: list[dict],
) -> list[dict]:
    if config.runtime_mode is not RuntimeMode.ONLINE:
        return records
    principal = current_principal()
    if principal is None:
        return []
    allowed = identity_store_for(config).allowed_batch_ids(principal)
    if allowed is None:
        return records
    return [
        record
        for record in records
        if int(record["id"]) in allowed
    ]


def ensure_batch_access(
    config: AppConfig,
    batch_id: int,
) -> None:
    if config.runtime_mode is not RuntimeMode.ONLINE:
        return
    principal = current_principal()
    if (
        principal is None
        or not identity_store_for(config).can_access_batch(
            principal,
            batch_id,
        )
    ):
        raise ValueError("resource not found")


def _session_token(
    headers: dict[str, str],
) -> tuple[str | None, str]:
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip(), "bearer"
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("cookie", ""))
    except Exception:
        return None, "cookie"
    session = cookie.get("session")
    return (
        None if session is None else session.value,
        "cookie",
    )


def _resource_selector(
    parsed: ParseResult,
    body: str,
) -> tuple[int | None, str | None]:
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 3 and parts[:2] == ["api", "batches"]:
        try:
            return int(parts[2]), None
        except ValueError:
            return None, None
    query = parse_qs(parsed.query)
    raw_batch_id = query.get("batch_id", [None])[0]
    if raw_batch_id is not None:
        try:
            return int(raw_batch_id), None
        except (TypeError, ValueError):
            return None, None
    if not body:
        return None, None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    raw_batch_id = payload.get("batch_id")
    if raw_batch_id not in (None, ""):
        try:
            return int(raw_batch_id), None
        except (TypeError, ValueError):
            pass
    issue_code = payload.get("issue_code")
    return (
        None,
        issue_code if isinstance(issue_code, str) else None,
    )
