import json
import sys
from contextlib import nullcontext
from dataclasses import replace
from email.message import Message
from io import BytesIO
from urllib.parse import urlparse

from governance_app import desktop, online_admin, server
from governance_app.config import RuntimeMode
from governance_app.db import initialize_database
from governance_app.identity_store import (
    AuthenticationError,
    IdentityStore,
    TaskRecord,
)
from governance_app.online_migration import migrate_sqlite_to_postgres
from governance_app.request_context import (
    Principal,
    RequestMetadata,
    principal_context,
    request_context,
)
from governance_app.routes.analysis import handle_analysis_route
from governance_app.routes.audits import handle_audit_route
from governance_app.routes.auth import handle_auth_route
from governance_app.routes.reports import handle_report_route
from governance_app.routes.tasks import handle_task_route
from governance_app.security import (
    authorize_request,
    claim_batch_for_current_principal,
    ensure_batch_access,
    filter_batches_for_current_principal,
    permission_for,
    security_headers,
)
from governance_app.server import RequestHandler, create_app, run_server
from governance_app.task_runtime import (
    TaskManager,
    _execute_task,
    enqueue_online_task,
)
from governance_app.workflow import create_batch


def _online_config(app_config):
    return replace(
        app_config,
        runtime_mode=RuntimeMode.ONLINE,
        database_url="postgresql://unused/governance",
        object_store_bucket="unused",
        bootstrap_admin_username="admin",
        bootstrap_admin_password="administrator-password",
    )


def _prepared_store(app_config):
    initialize_database(app_config)
    store = IdentityStore(app_config)
    store.initialize()
    store.bootstrap(
        username="admin",
        password="administrator-password",
    )
    return store


def _patch_identity_store(monkeypatch, store):
    for module in (
        "governance_app.server",
        "governance_app.security",
        "governance_app.routes.auth",
        "governance_app.routes.tasks",
    ):
        monkeypatch.setattr(
            f"{module}.identity_store_for",
            lambda _config, selected=store: selected,
        )


def test_online_login_bearer_cookie_csrf_and_security_headers(
    app_config,
    monkeypatch,
):
    store = _prepared_store(app_config)
    _patch_identity_store(monkeypatch, store)
    app = create_app(_online_config(app_config))

    login = app.handle_test_request(
        "POST",
        "/api/auth/login",
        json.dumps(
            {
                "username": "admin",
                "password": "administrator-password",
            }
        ),
        headers={"User-Agent": "pytest"},
    )
    payload = json.loads(login[2])
    cookie = login[1]["set-cookie"].split(";", 1)[0]
    token = payload["access_token"]

    bearer_me = app.handle_test_request(
        "GET",
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    rejected_csrf = app.handle_test_request(
        "POST",
        "/api/identity/organizations",
        json.dumps({"code": "hz", "name": "杭州"}),
        headers={"Cookie": cookie},
    )
    created = app.handle_test_request(
        "POST",
        "/api/identity/organizations",
        json.dumps({"code": "hz", "name": "杭州"}),
        headers={
            "Cookie": cookie,
            "X-CSRF-Token": payload["csrf_token"],
        },
    )

    assert login[0] == 200
    assert login[1]["strict-transport-security"].startswith("max-age=")
    assert bearer_me[0] == 200
    assert rejected_csrf[0] == 403
    assert created[0] == 201
    assert store.request_logs(
        store.session_principal(token, auth_method="bearer")
    )


def test_roles_enforce_permissions_and_organization_batch_scope(
    app_config,
):
    store = _prepared_store(app_config)
    admin_grant = store.authenticate(
        username="admin",
        password="administrator-password",
        source_ip="127.0.0.1",
        user_agent="pytest",
        ttl_seconds=3600,
    )
    hangzhou_id = store.create_organization(code="hz", name="杭州")
    ningbo_id = store.create_organization(code="nb", name="宁波")
    operator_id = store.create_user(
        actor=admin_grant.principal,
        organization_id=hangzhou_id,
        username="hz-operator",
        display_name="杭州整改员",
        password="operator-password",
        role_codes=["operator"],
    )
    operator = store.authenticate(
        username="hz-operator",
        password="operator-password",
        source_ip="127.0.0.1",
        user_agent="pytest",
        ttl_seconds=3600,
    ).principal
    ningbo_user_id = store.create_user(
        actor=admin_grant.principal,
        organization_id=ningbo_id,
        username="nb-operator",
        display_name="宁波整改员",
        password="operator-password",
        role_codes=["operator"],
    )
    ningbo = store.authenticate(
        username="nb-operator",
        password="operator-password",
        source_ip="127.0.0.1",
        user_agent="pytest",
        ttl_seconds=3600,
    ).principal

    hangzhou_batch = create_batch(app_config, "杭州批次")
    ningbo_batch = create_batch(app_config, "宁波批次")
    store.claim_batch(hangzhou_batch, operator)
    store.claim_batch(ningbo_batch, ningbo)

    assert operator.user_id == operator_id
    assert ningbo.user_id == ningbo_user_id
    assert operator.has_permission("issue.manage")
    assert not operator.has_permission("identity.manage")
    assert store.can_access_batch(operator, hangzhou_batch)
    assert not store.can_access_batch(operator, ningbo_batch)
    assert store.allowed_batch_ids(operator) == {hangzhou_batch}
    assert store.allowed_batch_ids(admin_grant.principal) is None


def test_failed_logins_lock_account_and_valid_session_can_logout(
    app_config,
):
    store = _prepared_store(app_config)

    for _ in range(5):
        try:
            store.authenticate(
                username="admin",
                password="wrong-password",
                source_ip="127.0.0.1",
                user_agent="pytest",
                ttl_seconds=3600,
            )
        except AuthenticationError:
            pass

    try:
        store.authenticate(
            username="admin",
            password="administrator-password",
            source_ip="127.0.0.1",
            user_agent="pytest",
            ttl_seconds=3600,
        )
    except AuthenticationError as exc:
        assert "锁定" in str(exc)
    else:
        raise AssertionError("locked account should reject login")


def test_persistent_tasks_deduplicate_progress_retry_and_complete(
    app_config,
):
    store = _prepared_store(app_config)
    principal = store.authenticate(
        username="admin",
        password="administrator-password",
        source_ip="127.0.0.1",
        user_agent="pytest",
        ttl_seconds=3600,
    ).principal

    first, created = store.enqueue_task(
        principal,
        kind="audit",
        payload={"batch_id": 1},
        idempotency_key="audit-1",
        max_attempts=1,
    )
    duplicate, duplicate_created = store.enqueue_task(
        principal,
        kind="audit",
        payload={"batch_id": 1},
        idempotency_key="audit-1",
    )
    running = store.claim_task(first.id)
    store.update_task_progress(first.id, 50)
    retry = store.fail_task(first.id, "failed")
    failed = store.get_task(principal, first.id)
    reset = store.retry_task(principal, first.id)
    claimed_again = store.claim_task(first.id)
    store.complete_task(first.id, {"audit_run_id": 9})
    completed = store.get_task(principal, first.id)

    assert created
    assert not duplicate_created
    assert duplicate.id == first.id
    assert running.status == "running"
    assert not retry
    assert failed.status == "failed"
    assert reset.status == "retry"
    assert claimed_again.status == "running"
    assert completed.status == "completed"
    assert completed.result == {"audit_run_id": 9}


def test_sqlite_migration_dry_run_counts_without_postgres_driver(
    app_config,
):
    _prepared_store(app_config)
    create_batch(app_config, "迁移盘点")

    report = migrate_sqlite_to_postgres(
        app_config.database_path,
        "postgresql://not-used/governance",
        dry_run=True,
    )

    assert report.dry_run
    assert report.source_counts["import_batches"] == 1
    assert report.source_counts["organizations"] == 1
    assert report.target_counts == {}


def test_identity_routes_manage_scoped_users_and_sessions(
    app_config,
    monkeypatch,
):
    store = _prepared_store(app_config)
    config = _online_config(app_config)
    _patch_identity_store(monkeypatch, store)
    admin_grant = store.authenticate(
        username="admin",
        password="administrator-password",
        source_ip="127.0.0.1",
        user_agent="pytest",
        ttl_seconds=3600,
    )
    metadata = RequestMetadata(
        request_id="identity-routes",
        method="POST",
        path="/api/identity/users",
        source_ip="127.0.0.1",
        user_agent="pytest",
    )
    with request_context(admin_grant.principal, metadata):
        organization = handle_auth_route(
            config,
            "POST",
            urlparse("/api/identity/organizations"),
            json.dumps({"code": "js", "name": "江苏"}),
        )
        organization_id = json.loads(organization[2])["organization_id"]
        created = handle_auth_route(
            config,
            "POST",
            urlparse("/api/identity/users"),
            json.dumps(
                {
                    "organization_id": organization_id,
                    "username": "js-admin",
                    "display_name": "江苏管理员",
                    "password": "organization-password",
                    "roles": ["city_admin"],
                }
            ),
        )
        user_id = json.loads(created[2])["user_id"]
        users = handle_auth_route(
            config,
            "GET",
            urlparse("/api/identity/users"),
            "",
        )
        organizations = handle_auth_route(
            config,
            "GET",
            urlparse("/api/identity/organizations"),
            "",
        )
        changed = handle_auth_route(
            config,
            "POST",
            urlparse(f"/api/identity/users/{user_id}/password"),
            json.dumps({"password": "replacement-password"}),
        )
        disabled = handle_auth_route(
            config,
            "POST",
            urlparse(f"/api/identity/users/{user_id}/status"),
            json.dumps({"active": False}),
        )
        logs = handle_auth_route(
            config,
            "GET",
            urlparse("/api/audit-logs?limit=bad"),
            "",
        )
        logout = handle_auth_route(
            config,
            "POST",
            urlparse("/api/auth/logout"),
            "",
        )

    assert organization[0] == 201
    assert created[0] == 201
    assert any(item["id"] == user_id for item in json.loads(users[2])["users"])
    assert len(json.loads(organizations[2])["organizations"]) == 2
    assert changed[0] == disabled[0] == 200
    assert logs[0] == 200
    assert logout[0] == 200
    assert "Max-Age=0" in logout[1]["set-cookie"]
    assert store.session_principal(
        admin_grant.token,
        auth_method="bearer",
    ) is None


def test_identity_route_validation_and_organization_scope(
    app_config,
    monkeypatch,
):
    store = _prepared_store(app_config)
    config = _online_config(app_config)
    _patch_identity_store(monkeypatch, store)
    platform = store.authenticate(
        username="admin",
        password="administrator-password",
        source_ip="",
        user_agent="",
        ttl_seconds=3600,
    ).principal
    organization_id = store.create_organization(code="ah", name="安徽")
    user_id = store.create_user(
        actor=platform,
        organization_id=organization_id,
        username="ah-admin",
        display_name="安徽管理员",
        password="organization-password",
        role_codes=["city_admin"],
    )
    scoped = store.authenticate(
        username="ah-admin",
        password="organization-password",
        source_ip="",
        user_agent="",
        ttl_seconds=3600,
    ).principal

    with principal_context(scoped):
        forbidden_org = handle_auth_route(
            config,
            "POST",
            urlparse("/api/identity/organizations"),
            json.dumps({"code": "x", "name": "越权"}),
        )
        invalid_roles = handle_auth_route(
            config,
            "POST",
            urlparse("/api/identity/users"),
            json.dumps({"roles": "operator"}),
        )
        bad_status = handle_auth_route(
            config,
            "POST",
            urlparse(f"/api/identity/users/{user_id}/status"),
            json.dumps({"active": "yes"}),
        )
        own_orgs = handle_auth_route(
            config,
            "GET",
            urlparse("/api/identity/organizations"),
            "",
        )

    assert forbidden_org[0] == 403
    assert invalid_roles[0] == bad_status[0] == 400
    assert len(json.loads(own_orgs[2])["organizations"]) == 1
    assert handle_auth_route(
        config,
        "GET",
        urlparse("/api/auth/me"),
        "",
    )[0] == 401
    assert handle_auth_route(
        app_config,
        "GET",
        urlparse("/api/auth/me"),
        "",
    )[0] == 400
    assert handle_auth_route(
        config,
        "GET",
        urlparse("/api/unrelated"),
        "",
    ) is None


def test_online_organization_roles_block_cross_city_requests_and_escalation(
    app_config, monkeypatch,
):
    store = _prepared_store(app_config)
    store.provision_platform_admin(
        username="platform-operator", password="platform-secret-password",
    )
    _patch_identity_store(monkeypatch, store)
    app = create_app(_online_config(app_config))

    def login(username, password):
        response = app.handle_test_request(
            "POST", "/api/auth/login",
            json.dumps({"username": username, "password": password}),
        )
        assert response[0] == 200
        return {"Authorization": f"Bearer {json.loads(response[2])['access_token']}"}

    def request(headers, method, path, payload=None):
        return app.handle_test_request(
            method, path, json.dumps(payload or {}), headers=headers,
        )

    province = login("admin", "administrator-password")
    platform = login("platform-operator", "platform-secret-password")
    assert request(province, "GET", "/api/settings")[0] == 403
    assert request(platform, "GET", "/api/batches")[0] == 403
    assert request(platform, "POST", "/api/identity/organizations", {
        "code": "rogue", "name": "越权组织",
    })[0] == 403

    city_ids = {}
    for code in ("city_a", "cityXa"):
        created = request(province, "POST", "/api/identity/organizations", {
            "code": code, "name": code,
        })
        assert created[0] == 201
        city_ids[code] = json.loads(created[2])["organization_id"]
    root_id = next(item["id"] for item in json.loads(
        request(province, "GET", "/api/identity/organizations")[2]
    )["organizations"] if item["code"] == "province")
    assert request(province, "PATCH", f"/api/identity/organizations/{city_ids['city_a']}", {
        "name": "市州 A", "parent_id": root_id,
    })[0] == 200
    all_organizations = json.loads(request(
        province, "GET", "/api/identity/organizations"
    )[2])["organizations"]
    assert next(item for item in all_organizations if item["code"] == "cityXa")["domain_path"] == "/province/cityXa/"
    assert request(province, "POST", "/api/identity/organizations", {
        "code": "city_a", "name": "重复",
    })[0] == 400
    assert request(province, "POST", "/api/identity/organizations", {
        "code": "bad/code", "name": "非法",
    })[0] == 400
    county = request(province, "POST", "/api/identity/organizations", {
        "code": "county-a", "name": "区县 A", "parent_id": city_ids["city_a"],
    })
    assert county[0] == 201
    county_id = json.loads(county[2])["organization_id"]
    renamed = request(province, "PATCH", f"/api/identity/organizations/{county_id}", {
        "name": "更正后的区县", "parent_id": city_ids["city_a"],
    })
    assert renamed[0] == 200
    body = json.dumps({"name": "HTTP 修改", "parent_id": city_ids["city_a"]})
    handler = object.__new__(RequestHandler)
    handler.config = _online_config(app_config)
    handler.path = f"/api/identity/organizations/{county_id}"
    handler.client_address = ("127.0.0.1", 0)
    handler.rfile = BytesIO(body.encode())
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(body.encode()))
    handler.headers["Authorization"] = province["Authorization"]
    captured = []
    handler._write_response = captured.append
    handler.do_PATCH()
    assert captured[0][0] == 200

    user_ids = {}
    for code in ("city_a", "cityXa"):
        created = request(province, "POST", "/api/identity/users", {
            "organization_id": city_ids[code], "username": code,
            "display_name": code, "password": "city_admin-password",
            "roles": ["city_admin"],
        })
        assert created[0] == 201
        user_ids[code] = json.loads(created[2])["user_id"]
    city_a = login("city_a", "city_admin-password")
    own_organizations = json.loads(request(
        city_a, "GET", "/api/identity/organizations"
    )[2])["organizations"]
    assert {item["code"] for item in own_organizations} == {"city_a", "county-a"}
    visible = json.loads(request(city_a, "GET", "/api/identity/users")[2])["users"]
    assert {row["username"] for row in visible} == {"city_a"}
    assert request(city_a, "POST", f"/api/identity/users/{user_ids['cityXa']}/status", {
        "active": False,
    })[0] == 403
    assert request(city_a, "POST", "/api/identity/users", {
        "organization_id": city_ids["cityXa"], "username": "intruder",
        "password": "city_admin-password", "roles": ["operator"],
    })[0] == 403
    assert request(city_a, "POST", "/api/identity/users", {
        "organization_id": county_id, "username": "elevated",
        "password": "city_admin-password", "roles": ["province_admin"],
    })[0] in (400, 403)
    assert request(city_a, "POST", f"/api/identity/users/{user_ids['city_a']}/status", {
        "active": False,
    })[0] == 403
    county_user = request(province, "POST", "/api/identity/users", {
        "organization_id": county_id, "username": "county-a",
        "password": "county-user-password", "roles": ["operator"],
    })
    assert county_user[0] == 201
    visible = json.loads(request(city_a, "GET", "/api/identity/users")[2])["users"]
    assert {row["username"] for row in visible} == {"city_a", "county-a"}
    assert request(city_a, "POST", "/api/identity/users", {
        "organization_id": county_id, "username": "county-colleague",
        "password": "county-user-password", "roles": ["operator"],
    })[0] == 201
    county_headers = login("county-a", "county-user-password")
    assert request(county_headers, "POST", "/api/identity/users", {
        "organization_id": county_id, "username": "self-promoted",
        "password": "county-user-password", "roles": ["city_admin"],
    })[0] == 403


def test_task_routes_list_get_retry_and_scope(
    app_config,
    monkeypatch,
):
    store = _prepared_store(app_config)
    config = _online_config(app_config)
    _patch_identity_store(monkeypatch, store)
    principal = store.authenticate(
        username="admin",
        password="administrator-password",
        source_ip="",
        user_agent="",
        ttl_seconds=3600,
    ).principal
    task, _ = store.enqueue_task(
        principal,
        kind="audit",
        payload={"batch_id": 1},
        idempotency_key="route-task",
        max_attempts=1,
    )
    store.claim_task(task.id)
    store.fail_task(task.id, "expected")

    class Submitted:
        task_id = None

        def submit_existing(self, task_id):
            self.task_id = task_id

    submitted = Submitted()
    monkeypatch.setattr(
        "governance_app.routes.tasks.task_manager_for",
        lambda _config: submitted,
    )
    with principal_context(principal):
        listed = handle_task_route(
            config,
            "GET",
            urlparse("/api/tasks?limit=bad"),
            "",
        )
        fetched = handle_task_route(
            config,
            "GET",
            urlparse(f"/api/tasks/{task.id}"),
            "",
        )
        retried = handle_task_route(
            config,
            "POST",
            urlparse(f"/api/tasks/{task.id}/retry"),
            "",
        )
        missing = handle_task_route(
            config,
            "GET",
            urlparse("/api/tasks/99999"),
            "",
        )
        invalid = handle_task_route(
            config,
            "GET",
            urlparse("/api/tasks/not-a-number"),
            "",
        )

    assert listed[0] == fetched[0] == 200
    assert retried[0] == 202
    assert submitted.task_id == task.id
    assert missing[0] == 404
    assert invalid[0] == 400
    assert handle_task_route(
        config,
        "GET",
        urlparse("/api/tasks"),
        "",
    )[0] == 401
    assert handle_task_route(
        app_config,
        "GET",
        urlparse("/api/tasks"),
        "",
    )[0] == 400


def _task(kind, payload):
    return TaskRecord(
        id=1,
        organization_id=1,
        user_id=1,
        kind=kind,
        status="running",
        payload=payload,
        result=None,
        error=None,
        progress=1,
        attempts=1,
        max_attempts=2,
        idempotency_key=f"{kind}-1",
    )


def test_task_executor_covers_analysis_and_export_kinds(
    app_config,
    monkeypatch,
    tmp_path,
):
    exported = tmp_path / "result.xlsx"
    exported.write_bytes(b"result")
    monkeypatch.setattr(
        "governance_app.task_runtime.run_audit",
        lambda _config, batch_id: type(
            "AuditResult",
            (),
            {"__dict__": {"batch_id": batch_id, "issue_count": 2}},
        )(),
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.run_electricity_analysis",
        lambda _config, batch_id: {"batch_id": batch_id},
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.run_tower_rent_analysis",
        lambda _config, batch_id: {"batch_id": batch_id},
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.export_issue_packages",
        lambda *_args, **_kwargs: [exported],
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.export_notice_report",
        lambda *_args: exported,
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.archive_batch",
        lambda *_args: exported,
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.export_electricity_opportunities",
        lambda *_args: exported,
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.export_tower_rent_clues",
        lambda *_args: exported,
    )
    monkeypatch.setattr(
        "governance_app.task_runtime._published",
        lambda _config, path: {"file_id": path.name},
    )

    assert _execute_task(app_config, _task("audit", {"batch_id": 7}))[
        "issue_count"
    ] == 2
    assert _execute_task(
        app_config,
        _task("electricity_analysis", {"batch_id": 7}),
    )["batch_id"] == 7
    assert _execute_task(
        app_config,
        _task("tower_rent_analysis", {"batch_id": 7}),
    )["batch_id"] == 7
    for kind in (
        "export_issues",
        "notice_report",
        "archive",
        "electricity_export",
        "tower_rent_export",
    ):
        assert _execute_task(
            app_config,
            _task(kind, {"batch_id": 7}),
        )


def test_task_manager_run_retries_and_enqueue_helpers(
    app_config,
    monkeypatch,
):
    store = _prepared_store(app_config)
    config = _online_config(app_config)
    principal = store.authenticate(
        username="admin",
        password="administrator-password",
        source_ip="",
        user_agent="",
        ttl_seconds=3600,
    ).principal
    monkeypatch.setattr(
        "governance_app.task_runtime.identity_store_for",
        lambda _config: store,
    )

    class ImmediateExecutor:
        submitted = []

        def submit(self, function, task_id):
            self.submitted.append((function, task_id))

    manager = TaskManager(config)
    manager._executor = ImmediateExecutor()
    with principal_context(principal):
        task, created = manager.enqueue(
            kind="audit",
            payload={"batch_id": 3},
            idempotency_key="manager-run",
        )
        duplicate, duplicate_created = manager.enqueue(
            kind="audit",
            payload={"batch_id": 3},
            idempotency_key="manager-run",
        )
    assert created and not duplicate_created
    assert duplicate.id == task.id
    assert manager._executor.submitted

    monkeypatch.setattr(
        "governance_app.task_runtime._execute_task",
        lambda _config, _task: {"ok": True},
    )
    manager._run(task.id)
    assert store.get_task(principal, task.id).status == "completed"

    response = enqueue_online_task(
        app_config,
        kind="audit",
        payload={"batch_id": 1},
    )
    assert response is None


def test_security_authorization_permissions_and_resource_boundaries(
    app_config,
    monkeypatch,
):
    config = _online_config(app_config)
    principal = Principal(
        user_id=2,
        organization_id=9,
        username="operator",
        permissions=frozenset(
            {"dashboard.read", "issue.manage", "task.manage"}
        ),
        data_scope="organization",
        session_id=4,
        auth_method="cookie",
    )

    class SecurityStore:
        claimed = None

        def session_principal(self, token, *, auth_method):
            if token != "valid":
                return None
            return replace(principal, auth_method=auth_method)

        def validate_csrf(self, session_id, token):
            return session_id == 4 and token == "csrf"

        def can_access_batch(self, _principal, batch_id):
            return batch_id == 10

        def can_access_issue(self, _principal, issue_code):
            return issue_code == "ISSUE-10"

        def allowed_batch_ids(self, _principal):
            return {10}

        def claim_batch(self, batch_id, _principal):
            self.claimed = batch_id

    store = SecurityStore()
    monkeypatch.setattr(
        "governance_app.security.identity_store_for",
        lambda _config: store,
    )
    assert authorize_request(
        app_config,
        "POST",
        urlparse("/api/batches"),
        "",
        {},
    ) == (None, None)
    assert authorize_request(
        config,
        "GET",
        urlparse("/api/health"),
        "",
        {},
    ) == (None, None)
    assert authorize_request(
        config,
        "GET",
        urlparse("/api/tasks"),
        "",
        {},
    )[1][0] == 401
    assert authorize_request(
        config,
        "GET",
        urlparse("/api/tasks"),
        "",
        {"authorization": "Bearer invalid"},
    )[1][0] == 401
    assert authorize_request(
        config,
        "POST",
        urlparse("/api/issues"),
        json.dumps({"issue_code": "ISSUE-10"}),
        {"cookie": "session=valid"},
    )[1][0] == 403
    allowed, error = authorize_request(
        config,
        "POST",
        urlparse("/api/issues"),
        json.dumps({"issue_code": "ISSUE-10"}),
        {"cookie": "session=valid", "x-csrf-token": "csrf"},
    )
    assert allowed.username == "operator"
    assert error is None
    assert authorize_request(
        config,
        "POST",
        urlparse("/api/audit"),
        json.dumps({"batch_id": 10}),
        {"authorization": "Bearer valid"},
    )[1][0] == 403
    assert authorize_request(
        config,
        "GET",
        urlparse("/api/batches/11"),
        "",
        {"authorization": "Bearer valid"},
    )[1][0] == 404
    assert authorize_request(
        config,
        "POST",
        urlparse("/api/issues"),
        json.dumps({"issue_code": "ISSUE-11"}),
        {"authorization": "Bearer valid"},
    )[1][0] == 404

    with principal_context(principal):
        claim_batch_for_current_principal(config, 10)
        assert filter_batches_for_current_principal(
            config,
            [{"id": 10}, {"id": 11}],
        ) == [{"id": 10}]
        ensure_batch_access(config, 10)
        try:
            ensure_batch_access(config, 11)
        except ValueError as exc:
            assert "not found" in str(exc)
        else:
            raise AssertionError("out-of-scope batch should be rejected")
    assert store.claimed == 10
    assert filter_batches_for_current_principal(
        config,
        [{"id": 10}],
    ) == []


def test_security_permission_map_headers_and_malformed_resources(
    app_config,
):
    expected = {
        ("GET", "/api/settings"): "system.admin",
        ("GET", "/api/dashboard"): "dashboard.read",
        ("POST", "/api/import"): "import.run",
        ("POST", "/api/audit"): "audit.run",
        ("POST", "/api/issues/x"): "issue.manage",
        ("POST", "/api/batches/2/analysis/electricity"): "analysis.run",
        ("POST", "/api/batches/2/analysis/electricity/export"): "report.export",
        ("POST", "/api/reports/export"): "report.export",
        ("POST", "/api/batches"): "batch.manage",
        ("POST", "/api/unknown"): "system.admin",
    }
    for (method, path), permission in expected.items():
        assert permission_for(method, path) == permission
    assert permission_for("GET", "/api/auth/me") is None
    assert permission_for("GET", "/api/tasks") == "task.manage"
    protected = security_headers(
        (200, {"content-type": "application/json"}, "{}"),
        online=True,
    )
    assert protected[1]["x-frame-options"] == "DENY"
    assert "strict-transport-security" in protected[1]
    assert "strict-transport-security" not in security_headers(
        (200, {}, ""),
        online=False,
    )[1]


def test_full_sqlite_to_target_migration_with_transactional_verification(
    app_config,
    tmp_path,
    monkeypatch,
):
    source_store = _prepared_store(app_config)
    source_store.create_organization(code="migration", name="迁移组织")
    create_batch(app_config, "迁移批次")

    target_config = type(app_config).for_workspace(tmp_path / "target")
    initialize_database(target_config)
    IdentityStore(target_config).initialize()
    from sqlalchemy import create_engine as sqlalchemy_create_engine

    target_engine = sqlalchemy_create_engine(
        f"sqlite+pysqlite:///{target_config.database_path.resolve()}"
    )
    source_create_engine = sqlalchemy_create_engine

    def fake_create_engine(url, **kwargs):
        if url == "postgres-target":
            return target_engine
        return source_create_engine(url, **kwargs)

    class FakePostgresDatabase:
        def __init__(self, _url):
            pass

        def initialize(self):
            pass

        def dispose(self):
            pass

    monkeypatch.setattr(
        "governance_app.online_migration.PostgresDatabase",
        FakePostgresDatabase,
    )
    monkeypatch.setattr(
        "governance_app.online_migration._psycopg_url",
        lambda _url: "postgres-target",
    )
    monkeypatch.setattr(
        "governance_app.online_migration.create_engine",
        fake_create_engine,
    )
    monkeypatch.setattr(
        "governance_app.online_migration._reset_postgres_sequence",
        lambda *_args: None,
    )

    report = migrate_sqlite_to_postgres(
        app_config.database_path,
        "postgresql://target/governance",
    )

    assert not report.dry_run
    assert report.migrated_rows > 0
    assert report.source_counts == report.target_counts


def test_online_long_running_routes_enqueue_persistent_tasks(
    app_config,
    monkeypatch,
):
    config = _online_config(app_config)
    queued = (202, {"content-type": "application/json"}, '{"queued":true}')
    calls = []

    def enqueue(_config, *, kind, payload):
        calls.append((kind, payload))
        return queued

    for module in (
        "governance_app.routes.analysis",
        "governance_app.routes.audits",
        "governance_app.routes.reports",
    ):
        monkeypatch.setattr(f"{module}.enqueue_online_task", enqueue)

    responses = [
        handle_audit_route(
            config,
            "POST",
            urlparse("/api/audit"),
            json.dumps({"batch_id": 8}),
        ),
        handle_analysis_route(
            config,
            "POST",
            urlparse("/api/batches/8/electricity-analysis/run"),
            "",
        ),
        handle_analysis_route(
            config,
            "POST",
            urlparse("/api/batches/8/electricity-analysis/export"),
            "",
        ),
        handle_analysis_route(
            config,
            "POST",
            urlparse("/api/batches/8/tower-rent-analysis/run"),
            "",
        ),
        handle_analysis_route(
            config,
            "POST",
            urlparse("/api/batches/8/tower-rent-analysis/export"),
            "",
        ),
        handle_report_route(
            config,
            "POST",
            urlparse("/api/export"),
            json.dumps({"batch_id": 8, "mode": "city"}),
        ),
        handle_report_route(
            config,
            "POST",
            urlparse("/api/reports/notice"),
            json.dumps({"batch_id": 8}),
        ),
        handle_report_route(
            config,
            "POST",
            urlparse("/api/archive"),
            json.dumps({"batch_id": 8}),
        ),
    ]

    assert all(response == queued for response in responses)
    assert {kind for kind, _payload in calls} == {
        "audit",
        "electricity_analysis",
        "electricity_export",
        "tower_rent_analysis",
        "tower_rent_export",
        "export_issues",
        "notice_report",
        "archive",
    }


def test_task_manager_recovery_failure_and_required_payload(
    app_config,
    monkeypatch,
):
    store = _prepared_store(app_config)
    config = _online_config(app_config)
    principal = store.authenticate(
        username="admin",
        password="administrator-password",
        source_ip="",
        user_agent="",
        ttl_seconds=3600,
    ).principal
    monkeypatch.setattr(
        "governance_app.task_runtime.identity_store_for",
        lambda _config: store,
    )

    class ImmediateExecutor:
        submitted = []

        def submit(self, function, task_id):
            self.submitted.append((function, task_id))

    manager = TaskManager(config)
    manager._executor = ImmediateExecutor()
    with principal_context(principal):
        task, _ = store.enqueue_task(
            principal,
            kind="unsupported",
            payload={"batch_id": 1},
            idempotency_key="failure",
            max_attempts=2,
        )
    monkeypatch.setattr(
        "governance_app.task_runtime._execute_task",
        lambda *_args: (_ for _ in ()).throw(ValueError("failed task")),
    )
    manager._run(task.id)
    failed = store.get_task(principal, task.id)
    assert failed.status == "retry"
    assert manager._executor.submitted[-1][1] == task.id

    manager.recover()
    assert manager._executor.submitted
    try:
        _execute_task(app_config, _task("unknown", {"batch_id": 1}))
    except ValueError as exc:
        assert "unsupported task kind" in str(exc)
    else:
        raise AssertionError("unknown task kind should be rejected")


def test_import_task_resolves_object_and_claims_created_batch(
    app_config,
    monkeypatch,
    tmp_path,
):
    workbook = tmp_path / "queued.xlsx"
    workbook.write_bytes(b"queued")
    claimed = []

    class Storage:
        def resolve(self, file_id):
            assert file_id == "uploads:9/queued.xlsx"
            return type("Resolved", (), {"local_path": workbook})()

    result = type(
        "ImportResult",
        (),
        {
            "batch_id": 22,
            "ledger_counts": {"site": 3},
            "errors": [],
        },
    )()
    monkeypatch.setattr(
        "governance_app.task_runtime.file_storage_for",
        lambda _config: Storage(),
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.exclusive_operation",
        lambda *_args: nullcontext(),
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.import_workbook",
        lambda *_args, **_kwargs: result,
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.claim_batch_for_current_principal",
        lambda _config, batch_id: claimed.append(batch_id),
    )

    payload = _execute_task(
        _online_config(app_config),
        _task(
            "import",
            {
                "file_id": "uploads:9/queued.xlsx",
                "strategy": "replace",
                "batch_id": "4",
            },
        ),
    )
    assert payload["batch_id"] == 22
    assert payload["ledger_counts"] == {"site": 3}
    assert claimed == [22]
    try:
        _execute_task(
            _online_config(app_config),
            _task("import", {"file_id": ""}),
        )
    except ValueError as exc:
        assert "file_id is required" in str(exc)
    else:
        raise AssertionError("empty queued file identifier should fail")


def test_online_server_startup_bootstraps_identity_and_recovers_tasks(
    app_config,
    monkeypatch,
):
    events = []

    class Store:
        def initialize(self):
            events.append("identity.initialize")

        def bootstrap(self, *, username, password):
            events.append((username, password))

    class Manager:
        def recover(self):
            events.append("tasks.recover")

    class Server:
        def __init__(self, address, handler):
            events.append(address)
            assert handler

        def serve_forever(self):
            events.append("serve")

    monkeypatch.setattr(
        "governance_app.server.initialize_database",
        lambda _config: events.append("database.initialize"),
    )
    monkeypatch.setattr(
        "governance_app.server.identity_store_for",
        lambda _config: Store(),
    )
    monkeypatch.setattr(
        "governance_app.task_runtime.task_manager_for",
        lambda _config: Manager(),
    )
    monkeypatch.setattr(
        "governance_app.server.ThreadingHTTPServer",
        Server,
    )
    run_server(_online_config(app_config), host="127.0.0.2", port=9876)

    assert events == [
        "database.initialize",
        "identity.initialize",
        ("admin", "administrator-password"),
        "tasks.recover",
        ("127.0.0.2", 9876),
        "serve",
    ]


def test_online_server_refuses_missing_bootstrap_secret(
    app_config,
    monkeypatch,
):
    config = replace(
        _online_config(app_config),
        bootstrap_admin_password="",
    )
    monkeypatch.setattr(
        "governance_app.server.initialize_database",
        lambda _config: None,
    )
    monkeypatch.setattr(
        "governance_app.server.identity_store_for",
        lambda _config: type("Store", (), {"initialize": lambda self: None})(),
    )
    try:
        run_server(config)
    except RuntimeError as exc:
        assert "bootstrap administrator" in str(exc)
    else:
        raise AssertionError("missing bootstrap password should stop startup")


def test_online_enqueue_response_and_deduplication(
    app_config,
    monkeypatch,
):
    config = _online_config(app_config)
    task = _task("audit", {"batch_id": 1})

    class Manager:
        created = True

        def enqueue(self, **_kwargs):
            return task, self.created

    manager = Manager()
    monkeypatch.setattr(
        "governance_app.task_runtime.task_manager_for",
        lambda _config: manager,
    )
    created = enqueue_online_task(
        config,
        kind="audit",
        payload={"batch_id": 1, "idempotency_key": " audit-1 "},
    )
    manager.created = False
    duplicate = enqueue_online_task(
        config,
        kind="audit",
        payload={"batch_id": 1},
    )
    assert created[0] == 202
    assert duplicate[0] == 200
    assert json.loads(duplicate[2])["deduplicated"]


def test_command_entrypoints_delegate_parsed_configuration(
    app_config,
    monkeypatch,
    capsys,
):
    calls = []
    monkeypatch.setattr(
        desktop.AppConfig,
        "for_workspace",
        lambda path: ("desktop-config", path),
    )
    monkeypatch.setattr(
        desktop,
        "run_server",
        lambda config, host, port: calls.append((config, host, port)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "governance-desktop",
            "--workspace",
            str(app_config.workspace_dir),
            "--host",
            "127.0.0.3",
            "--port",
            "9001",
            "--no-browser",
        ],
    )
    desktop.main()
    assert calls[-1][1:] == ("127.0.0.3", 9001)

    opened = []
    monkeypatch.setattr(desktop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        desktop.webbrowser,
        "open",
        lambda url: opened.append(url),
    )
    desktop._open_browser_later("http://localhost:9001")
    assert opened == ["http://localhost:9001"]

    report = type(
        "Report",
        (),
        {
            "source_counts": {"users": 1},
            "target_counts": {},
            "migrated_rows": 0,
            "dry_run": True,
        },
    )()
    monkeypatch.setattr(
        online_admin,
        "migrate_sqlite_to_postgres",
        lambda *_args, **_kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "governance-online-admin",
            "migrate",
            "--sqlite",
            str(app_config.database_path),
            "--postgres-url",
            "postgresql://example/governance",
            "--dry-run",
        ],
    )
    online_admin.main()
    assert json.loads(capsys.readouterr().out)["dry_run"]

    monkeypatch.setattr(
        server.AppConfig,
        "from_environment",
        lambda path: ("server-config", path),
    )
    monkeypatch.setattr(
        server,
        "run_server",
        lambda config, host, port: calls.append((config, host, port)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "governance-server",
            "--workspace",
            str(app_config.workspace_dir),
            "--host",
            "0.0.0.0",
            "--port",
            "9002",
        ],
    )
    server.main()
    assert calls[-1][1:] == ("0.0.0.0", 9002)
