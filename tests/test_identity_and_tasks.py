import json
import sqlite3
import sys
from contextlib import nullcontext
from dataclasses import replace
from email.message import Message
from io import BytesIO
from urllib.parse import urlparse

from openpyxl import load_workbook

from governance_app import desktop, online_admin, server
from governance_app.config import RuntimeMode
from governance_app.db import initialize_database
from governance_app.identity_store import (
    AuthenticationError,
    IdentityStore,
    TaskRecord,
)
from governance_app.importer import import_workbook
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
        "governance_app.routes.site_changes",
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


def test_province_batch_site_records_are_scoped_by_verified_organization_pairs(app_config, monkeypatch):
    store = _prepared_store(app_config)
    _patch_identity_store(monkeypatch, store)
    monkeypatch.setattr("governance_app.workflow.identity_store_for", lambda _config: store)
    from governance_app.database_runtime import database_for
    monkeypatch.setattr("governance_app.workflow.database_for", lambda _config: database_for(app_config))
    monkeypatch.setattr("governance_app.routes.sites.database_for", lambda _config: database_for(app_config))
    monkeypatch.setattr("governance_app.routes.sites.identity_store_for", lambda _config: store)
    from governance_app.file_storage_runtime import file_storage_for
    storage = file_storage_for(app_config)
    monkeypatch.setattr("governance_app.routes.sites.file_storage_for", lambda _config: storage)
    province = store.authenticate(username="admin", password="administrator-password",
                                  source_ip="", user_agent="", ttl_seconds=3600)
    hz = store.create_organization(code="hz", name="杭州")
    xihu = store.create_organization(code="xihu", name="西湖", parent_id=hz)
    binjiang = store.create_organization(code="binjiang", name="滨江", parent_id=hz)
    nb = store.create_organization(code="nb", name="宁波")
    store.create_organization(code="yinzhou", name="鄞州", parent_id=nb)
    grants = {}
    for name, org in (("hz", hz), ("xihu", xihu), ("binjiang", binjiang), ("nb", nb)):
        store.create_user(actor=province.principal, organization_id=org,
                          username=name, display_name=name, password="operator-password",
                          role_codes=["operator"])
        grants[name] = store.authenticate(username=name, password="operator-password",
                                          source_ip="", user_agent="", ttl_seconds=3600)
    batch = create_batch(app_config, "全省站址")
    store.claim_batch(batch, province.principal)
    rows = (("杭州", "西湖", "A"), ("杭州", "滨江", "B"),
            ("宁波", "鄞州", "C"), ("杭州", None, "D"),
            ("杭州", "鄞州", "E"))
    with sqlite3.connect(app_config.database_path) as db:
        db.executemany("insert into ledger_rows(batch_id, ledger_type, city, district, telecom_site_code, row_json) values (?, 'site', ?, ?, ?, '{}')",
                       ((batch, city, district, code) for city, district, code in rows))
        run_id = db.execute("insert into audit_runs(batch_id, rule_count) values (?, 1)", (batch,)).lastrowid
        for row_id, city, district, code in db.execute(
                "select id, city, district, telecom_site_code from ledger_rows where batch_id = ?", (batch,)).fetchall():
            result_id = db.execute("insert into audit_results(audit_run_id, ledger_row_id, rule_id, severity, message, result_json) values (?, ?, 'site_code_required', 'high', 'test', '{}')",
                                   (run_id, row_id)).lastrowid
            db.execute("insert into issues(issue_code, audit_result_id, batch_id, city, district, telecom_site_code, ledger_type, rule_id, severity, message, suggestion) values (?, ?, ?, ?, ?, ?, 'site', 'site_code_required', 'high', 'test', 'test')",
                       (f"I-{code}", result_id, batch, city, district, code))
    app = create_app(_online_config(app_config))
    def request(grant, path):
        response = app.handle_test_request("GET", path,
                                           headers={"Authorization": f"Bearer {grant.token}"})
        return response[0], json.loads(response[2])
    assert request(province, f"/api/ledger-rows?batch_id={batch}")[1]["total"] == 5
    for name, expected in (("hz", {"A", "B"}), ("xihu", {"A"}),
                           ("binjiang", {"B"}), ("nb", {"C"})):
        status, payload = request(grants[name], f"/api/ledger-rows?batch_id={batch}")
        assert status == 200
        assert {row["telecom_site_code"] for row in payload["rows"]} == expected
        assert payload["total"] == len(expected)
        assert batch in {entry["id"] for entry in request(grants[name], "/api/batches")[1]["batches"]}
        status, issue_page = request(grants[name], f"/api/issues?batch_id={batch}&limit=20")
        assert status == 200
        assert {issue["telecom_site_code"] for issue in issue_page["issues"]} == expected
        assert issue_page["total"] == len(expected)
        assert {group["telecom_site_code"] for group in request(
            grants[name], f"/api/issue-groups?batch_id={batch}")[1]["groups"]} == expected
    assert request(grants["xihu"], f"/api/ledger-rows?batch_id={batch}&city=宁波")[1]["total"] == 0
    for path in (f"/api/dashboard?batch_id={batch}", "/api/files/private", f"/api/city-progress?batch_id={batch}"):
        assert request(grants["xihu"], path)[0] == 404
    assert request(grants["xihu"], f"/api/sites/1?batch_id={batch}")[1]["site"]["telecom_site_code"] == "A"
    assert request(grants["xihu"], f"/api/sites/2?batch_id={batch}")[0] == 404
    assert request(grants["xihu"], "/api/tasks")[0] == 404
    evidence_file = storage.save_upload("proof.txt", b"verified-site-A")
    registered = app.handle_test_request("POST", "/api/sites/evidence",
        json.dumps({"batch_id": batch, "row_id": 1, "file_id": evidence_file.file_id}),
        headers={"Authorization": f"Bearer {province.token}"})
    evidence_id = json.loads(registered[2])["evidence_id"]
    assert registered[0] == 200
    evidence_path = f"/api/sites/1/evidence/{evidence_id}?batch_id={batch}"
    assert app.handle_test_request("GET", evidence_path,
        headers={"Authorization": f"Bearer {grants['xihu'].token}"})[2] == b"verified-site-A"
    assert request(grants["binjiang"], evidence_path)[0] == 404
    def update(grant, code):
        return app.handle_test_request("POST", "/api/issues/status",
                                       json.dumps({"issue_code": f"I-{code}", "status": "closed"}),
                                       headers={"Authorization": f"Bearer {grant.token}"})[0]
    assert update(grants["xihu"], "B") == 404
    assert update(grants["xihu"], "D") == 404
    assert update(grants["xihu"], "A") == 200
    assert app.handle_test_request("POST", "/api/sites/jurisdiction",
        json.dumps({"batch_id": batch, "row_id": 4, "city": "杭州", "district": "西湖", "reason": "现场确认"}),
        headers={"Authorization": f"Bearer {grants['xihu'].token}"})[0] == 404
    def assign(city, district):
        return app.handle_test_request("POST", "/api/sites/jurisdiction",
            json.dumps({"batch_id": batch, "row_id": 4, "city": city, "district": district, "reason": "现场确认"}),
            headers={"Authorization": f"Bearer {province.token}"})
    assert assign("杭州", "鄞州")[0] == 400
    assert json.loads(assign("杭州", "西湖")[2])["updated"] is True
    assert json.loads(assign("杭州", "西湖")[2])["updated"] is False
    assert request(grants["xihu"], f"/api/ledger-rows?batch_id={batch}")[1]["total"] == 2
    assert request(grants["xihu"], f"/api/issues?batch_id={batch}&limit=20")[1]["total"] == 2
    assert request(grants["xihu"], f"/api/sites/summary?batch_id={batch}")[1] == {
        "total": 2, "cities": {"杭州": 2}}
    exported = app.handle_test_request("GET", f"/api/sites/export?batch_id={batch}",
        headers={"Authorization": f"Bearer {grants['xihu'].token}"})
    assert exported[0] == 200
    assert "I-" not in exported[2].decode("utf-8")
    assert "A" in exported[2].decode("utf-8") and "D" in exported[2].decode("utf-8")
    assert "B" not in exported[2].decode("utf-8") and "C" not in exported[2].decode("utf-8")
    with sqlite3.connect(app_config.database_path) as db:
        assert db.execute("select count(*) from site_jurisdiction_events").fetchone()[0] == 1


def test_related_ledgers_follow_the_unique_site_jurisdiction(
    app_config, sample_workbook, monkeypatch
):
    store = _prepared_store(app_config)
    _patch_identity_store(monkeypatch, store)
    monkeypatch.setattr(
        "governance_app.workflow.identity_store_for", lambda _config: store
    )
    from governance_app.database_runtime import database_for

    monkeypatch.setattr(
        "governance_app.workflow.database_for", lambda _config: database_for(app_config)
    )
    monkeypatch.setattr(
        "governance_app.routes.sites.database_for",
        lambda _config: database_for(app_config),
    )
    monkeypatch.setattr(
        "governance_app.routes.sites.identity_store_for", lambda _config: store
    )
    from governance_app.file_storage_runtime import file_storage_for

    storage = file_storage_for(app_config)
    monkeypatch.setattr(
        "governance_app.routes.related_ledgers.database_for",
        lambda _config: database_for(app_config),
    )
    monkeypatch.setattr(
        "governance_app.routes.related_ledgers.file_storage_for",
        lambda _config: storage,
    )
    province = store.authenticate(
        username="admin",
        password="administrator-password",
        source_ip="",
        user_agent="",
        ttl_seconds=3600,
    )
    hz = store.create_organization(code="hz", name="杭州")
    xihu = store.create_organization(code="xihu", name="西湖", parent_id=hz)
    nb = store.create_organization(code="nb", name="宁波")
    yinzhou = store.create_organization(code="yinzhou", name="鄞州", parent_id=nb)
    grants = {}
    for name, org in (("xihu", xihu), ("yinzhou", yinzhou)):
        store.create_user(
            actor=province.principal,
            organization_id=org,
            username=name,
            display_name=name,
            password="operator-password",
            role_codes=["operator"],
        )
        grants[name] = store.authenticate(
            username=name,
            password="operator-password",
            source_ip="",
            user_agent="",
            ttl_seconds=3600,
        )

    workbook = load_workbook(sample_workbook)
    workbook["铁塔租费台账"]["D2"] = "宁波"
    workbook["铁塔租费台账"]["E2"] = "鄞州"
    workbook["电费台账"]["B2"] = "宁波"
    workbook["电费台账"]["C2"] = "鄞州"
    workbook["发电费台账"].append(
        [2, "2026-04-11", "2026-04", "UNKNOWN", "未知站址", "", "", "WO002", 2]
    )
    path = sample_workbook.with_name("related-scope.xlsx")
    workbook.save(path)
    batch_id = import_workbook(app_config, path).batch_id
    store.claim_batch(batch_id, province.principal)
    with sqlite3.connect(app_config.database_path) as db:
        run_id = db.execute(
            "insert into audit_runs(batch_id, rule_count) values (?, 1)", (batch_id,)
        ).lastrowid
        scoped_rows = db.execute(
            "select id, ledger_type, city, district, telecom_site_code from ledger_rows "
            "where batch_id = ? and ledger_type != 'site'",
            (batch_id,),
        ).fetchall()
        for row_id, ledger_type, city, district, code in scoped_rows:
            result_id = db.execute(
                "insert into audit_results(audit_run_id, ledger_row_id, rule_id, severity, message, result_json) "
                "values (?, ?, 'scope-test', 'high', 'test', '{}')",
                (run_id, row_id),
            ).lastrowid
            db.execute(
                "insert into issues(issue_code, audit_result_id, batch_id, city, district, "
                "telecom_site_code, ledger_type, rule_id, severity, message, suggestion) "
                "values (?, ?, ?, ?, ?, ?, ?, 'scope-test', 'high', 'test', 'test')",
                (
                    f"I-{ledger_type}-{row_id}",
                    result_id,
                    batch_id,
                    city,
                    district,
                    code,
                    ledger_type,
                ),
            )
    app = create_app(_online_config(app_config))

    def rows(grant, ledger_type):
        response = app.handle_test_request(
            "GET",
            f"/api/ledger-rows?batch_id={batch_id}&ledger_type={ledger_type}",
            headers={"Authorization": f"Bearer {grant.token}"},
        )
        assert response[0] == 200
        return json.loads(response[2])

    for ledger_type in ("tower_rent", "electricity", "generator"):
        payload = rows(grants["xihu"], ledger_type)
        assert payload["total"] == 1
        assert {(row["city"], row["district"]) for row in payload["rows"]} == {
            ("杭州", "西湖")
        }
        assert rows(grants["yinzhou"], ledger_type)["total"] == 0
    assert rows(province, "generator")["total"] == 2
    summary = app.handle_test_request(
        "GET",
        f"/api/related-ledgers/summary?batch_id={batch_id}",
        headers={"Authorization": f"Bearer {grants['xihu'].token}"},
    )
    assert json.loads(summary[2]) == {
        "total": 3,
        "ledger_types": {"electricity": 1, "generator": 1, "tower_rent": 1},
    }
    exported = app.handle_test_request(
        "GET",
        f"/api/related-ledgers/export?batch_id={batch_id}",
        headers={"Authorization": f"Bearer {grants['xihu'].token}"},
    )
    assert exported[0] == 200
    assert "HZ001" in exported[2].decode("utf-8")
    assert "UNKNOWN" not in exported[2].decode("utf-8")
    with sqlite3.connect(app_config.database_path) as db:
        related_row_id = db.execute(
            "select id from ledger_rows where batch_id = ? and ledger_type = 'electricity'",
            (batch_id,),
        ).fetchone()[0]
    detail = app.handle_test_request(
        "GET",
        f"/api/related-ledgers/{related_row_id}?batch_id={batch_id}",
        headers={"Authorization": f"Bearer {grants['xihu'].token}"},
    )
    assert json.loads(detail[2])["record"]["ledger_type"] == "electricity"
    assert (
        app.handle_test_request(
            "GET",
            f"/api/related-ledgers/{related_row_id}?batch_id={batch_id}",
            headers={"Authorization": f"Bearer {grants['yinzhou'].token}"},
        )[0]
        == 404
    )
    evidence_file = storage.save_upload("electricity-proof.txt", b"meter-proof")
    registered = app.handle_test_request(
        "POST",
        "/api/related-ledgers/evidence",
        json.dumps(
            {
                "batch_id": batch_id,
                "row_id": related_row_id,
                "file_id": evidence_file.file_id,
            }
        ),
        headers={"Authorization": f"Bearer {province.token}"},
    )
    assert registered[0] == 200
    evidence_id = json.loads(registered[2])["evidence_id"]
    evidence_path = f"/api/related-ledgers/{related_row_id}/evidence/{evidence_id}?batch_id={batch_id}"
    assert (
        app.handle_test_request(
            "GET",
            evidence_path,
            headers={"Authorization": f"Bearer {grants['xihu'].token}"},
        )[2]
        == b"meter-proof"
    )
    assert (
        app.handle_test_request(
            "GET",
            evidence_path,
            headers={"Authorization": f"Bearer {grants['yinzhou'].token}"},
        )[0]
        == 404
    )
    xihu_issues = app.handle_test_request(
        "GET",
        f"/api/issues?batch_id={batch_id}&limit=20",
        headers={"Authorization": f"Bearer {grants['xihu'].token}"},
    )
    assert json.loads(xihu_issues[2])["total"] == 3

    with sqlite3.connect(app_config.database_path) as db:
        site_row_id = db.execute(
            "select id from ledger_rows where batch_id = ? and ledger_type = 'site'",
            (batch_id,),
        ).fetchone()[0]
    moved = app.handle_test_request(
        "POST",
        "/api/sites/jurisdiction",
        json.dumps(
            {
                "batch_id": batch_id,
                "row_id": site_row_id,
                "city": "宁波",
                "district": "鄞州",
                "reason": "省级核实归属",
            }
        ),
        headers={"Authorization": f"Bearer {province.token}"},
    )
    assert moved[0] == 200
    for ledger_type in ("tower_rent", "electricity", "generator"):
        assert rows(grants["xihu"], ledger_type)["total"] == 0
        assert rows(grants["yinzhou"], ledger_type)["total"] == 1
    yinzhou_issues = app.handle_test_request(
        "GET",
        f"/api/issues?batch_id={batch_id}&limit=20",
        headers={"Authorization": f"Bearer {grants['yinzhou'].token}"},
    )
    assert json.loads(yinzhou_issues[2])["total"] == 3

    import_workbook(app_config, path, strategy="append", batch_id=batch_id)
    for ledger_type in ("tower_rent", "electricity", "generator"):
        assert rows(grants["xihu"], ledger_type)["total"] == 0
        assert rows(grants["yinzhou"], ledger_type)["total"] == 0



def test_online_site_corrections_follow_organization_review_chain(
    app_config, sample_workbook, monkeypatch
):
    store = _prepared_store(app_config)
    _patch_identity_store(monkeypatch, store)
    monkeypatch.setattr(
        "governance_app.workflow.identity_store_for", lambda _config: store
    )
    from governance_app.database_runtime import database_for

    monkeypatch.setattr(
        "governance_app.workflow.database_for", lambda _config: database_for(app_config)
    )
    monkeypatch.setattr(
        "governance_app.routes.site_changes.database_for",
        lambda _config: database_for(app_config),
    )
    province = store.authenticate(
        username="admin", password="administrator-password",
        source_ip="", user_agent="", ttl_seconds=3600,
    )
    city_id = store.create_organization(code="hz-review", name="杭州")
    district_id = store.create_organization(
        code="xihu-review", name="西湖", parent_id=city_id
    )
    grants = {"province": province}
    for username, organization_id in (("city-review", city_id), ("district-review", district_id)):
        store.create_user(
            actor=province.principal, organization_id=organization_id,
            username=username, display_name=username, password="operator-password",
            role_codes=["operator"],
        )
        grants[username.split("-")[0]] = store.authenticate(
            username=username, password="operator-password",
            source_ip="", user_agent="", ttl_seconds=3600,
        )
    batch_id = import_workbook(app_config, sample_workbook).batch_id
    store.claim_batch(batch_id, province.principal)
    app = create_app(_online_config(app_config))

    def post(grant, path, payload):
        response = app.handle_test_request(
            "POST", path, json.dumps(payload, ensure_ascii=False),
            headers={"Authorization": f"Bearer {grant.token}"},
        )
        return response[0], json.loads(response[2])

    def get(grant, path):
        response = app.handle_test_request(
            "GET", path, headers={"Authorization": f"Bearer {grant.token}"}
        )
        return response[0], json.loads(response[2])

    def submit(grant, value, key):
        return post(grant, "/api/site-corrections", {
            "batch_id": batch_id, "row_id": 1,
            "changes": {"电信站址名称": value}, "evidence": f"核实材料-{key}",
            "note": "现场核实", "error_cause": "来源填报错误",
            "source": "现场核实", "idempotency_key": key,
        })

    status, submitted = submit(grants["district"], "区县更正", "district-1")
    request_id = submitted["request"]["id"]
    assert status == 201 and submitted["request"]["status"] == "pending"
    assert submit(grants["district"], "区县更正", "district-1")[1]["request"]["id"] == request_id
    assert submit(grants["district"], "冲突重试", "district-1")[0] == 409
    assert get(grants["district"], f"/api/site-corrections/{request_id}")[1]["request"]["id"] == request_id
    assert get(grants["district"], "/api/site-corrections/not-a-number")[0] == 404
    assert get(grants["district"], "/api/site-corrections/999999")[0] == 404
    assert post(grants["district"], "/api/site-corrections/not-a-number/resubmit",
                {"idempotency_key": "bad-id"})[0] == 404
    assert post(grants["district"], "/api/site-corrections/999999/resubmit",
                {"idempotency_key": "missing-id"})[0] == 404
    with sqlite3.connect(app_config.database_path) as db:
        before = db.execute(
            "select current_json, current_version from authoritative_sites"
        ).fetchone()
    assert json.loads(before[0])["电信站址名称"] != "区县更正"
    assert post(grants["district"], f"/api/site-corrections/{request_id}/decision",
                {"action": "approve", "note": "越权"})[0] == 403
    assert post(grants["province"], f"/api/site-corrections/{request_id}/decision",
                {"action": "approve", "note": "越级"})[0] == 403
    approved = post(grants["city"], f"/api/site-corrections/{request_id}/decision",
                    {"action": "approve", "note": "市州确认"})
    assert approved[0] == 200 and approved[1]["request"]["applied_version"] == 1
    assert post(grants["district"], f"/api/site-corrections/{request_id}/resubmit",
                {"idempotency_key": "not-rejected"})[0] == 409
    repeated = post(grants["city"], f"/api/site-corrections/{request_id}/decision",
                    {"action": "approve", "note": "重复确认"})
    assert repeated[1]["changed"] is False

    _, city_request = submit(grants["city"], "市州更正", "city-1")
    city_request_id = city_request["request"]["id"]
    rejected = post(grants["province"], f"/api/site-corrections/{city_request_id}/decision",
                    {"action": "reject", "note": "证据不足"})
    assert rejected[1]["request"]["status"] == "rejected"
    assert post(grants["district"], f"/api/site-corrections/{city_request_id}/resubmit",
                {"idempotency_key": "wrong-proposer"})[0] == 403
    assert post(grants["province"], f"/api/site-corrections/{city_request_id}/decision",
                {"action": "approve", "note": "不能确认已退回请求"})[0] == 409
    assert app.handle_test_request(
        "POST", f"/api/site-corrections/{city_request_id}/resubmit", "{",
        headers={"Authorization": f"Bearer {grants['city'].token}"},
    )[0] == 400
    resubmitted = post(grants["city"],
        f"/api/site-corrections/{city_request_id}/resubmit", {
            "changes": {"电信站址名称": "市州修订后更正"},
            "evidence": "补充核实材料", "note": "按退回意见补充",
            "error_cause": "来源填报错误", "source": "现场核实",
            "idempotency_key": "city-2",
        })[1]
    assert resubmitted["request"]["replaces_request_id"] == city_request_id
    assert post(grants["province"],
                f"/api/site-corrections/{resubmitted['request']['id']}/decision",
                {"action": "approve", "note": "省级确认"})[1]["request"]["status"] == "approved"

    _, province_request = submit(grants["province"], "省级更正", "province-1")
    assert post(grants["province"],
                f"/api/site-corrections/{province_request['request']['id']}/decision",
                {"action": "approve", "note": "省级自行确认"})[1]["request"]["status"] == "approved"
    _, no_op = submit(grants["province"], "省级更正", "province-no-op")
    assert post(grants["province"],
                f"/api/site-corrections/{no_op['request']['id']}/decision",
                {"action": "approve", "note": "无变化"})[0] == 409
    with sqlite3.connect(app_config.database_path) as db:
        run_id = db.execute(
            "insert into audit_runs(batch_id, rule_count) values (?, 1)", (batch_id,)
        ).lastrowid
        result_id = db.execute(
            "insert into audit_results(audit_run_id, ledger_row_id, rule_id, severity, message, result_json) "
            "values (?, 1, 'site-name', 'medium', '名称待核实', '{}')", (run_id,),
        ).lastrowid
        db.execute(
            "insert into issues(issue_code, audit_result_id, batch_id, city, district, "
            "ledger_type, rule_id, severity, message, suggestion) "
            "values ('I-SITE-NC', ?, ?, '杭州', '西湖', 'site', 'site-name', "
            "'medium', '名称待核实', '核实名称')", (result_id, batch_id),
        )
    conclusion = post(grants["district"], "/api/site-conclusions", {
        "batch_id": batch_id, "row_id": 1, "evidence": "现场照片",
        "note": "无需整改，现值正确", "idempotency_key": "no-change-1",
        "issue_code": "I-SITE-NC",
    })
    assert conclusion[0] == 201 and conclusion[1]["request"]["status"] == "recorded"
    assert post(grants["city"],
                f"/api/site-corrections/{conclusion[1]['request']['id']}/decision",
                {"action": "approve", "note": "不改值无需确认"})[0] == 409
    assert post(grants["city"], f"/api/site-corrections/{request_id}/decision",
                {"action": "invalid", "note": "无效动作"})[0] == 409
    with sqlite3.connect(app_config.database_path) as db:
        current, version = db.execute(
            "select current_json, current_version from authoritative_sites"
        ).fetchone()
        history = db.execute(
            "select operator, confirmer from authoritative_site_versions order by version"
        ).fetchall()
        issue_state = db.execute(
            "select status, correction_note from issues where issue_code = 'I-SITE-NC'"
        ).fetchone()
    assert json.loads(current)["电信站址名称"] == "省级更正"
    assert version == 3
    assert issue_state == ("not_required", "现场照片")
    assert history == [
        ("district-review", "city-review"),
        ("city-review", "admin"),
        ("admin", "admin"),
    ]


def test_online_tower_rent_correction_requires_upper_level_confirmation(
    app_config, sample_workbook, monkeypatch
):
    store = _prepared_store(app_config)
    _patch_identity_store(monkeypatch, store)
    monkeypatch.setattr("governance_app.workflow.identity_store_for", lambda _config: store)
    from governance_app.database_runtime import database_for
    monkeypatch.setattr("governance_app.workflow.database_for", lambda _config: database_for(app_config))
    monkeypatch.setattr("governance_app.routes.tower_rent_changes.database_for",
                        lambda _config: database_for(app_config))
    monkeypatch.setattr("governance_app.routes.tower_rent_changes.identity_store_for",
                        lambda _config: store)
    province = store.authenticate(username="admin", password="administrator-password",
                                  source_ip="", user_agent="", ttl_seconds=3600)
    city_id = store.create_organization(code="hz-rent", name="杭州")
    district_id = store.create_organization(code="xihu-rent", name="西湖", parent_id=city_id)
    grants = {"province": province}
    for username, organization_id in (("city-rent", city_id), ("district-rent", district_id)):
        store.create_user(actor=province.principal, organization_id=organization_id,
                          username=username, display_name=username, password="operator-password",
                          role_codes=["operator"])
        grants[username.split("-")[0]] = store.authenticate(
            username=username, password="operator-password", source_ip="", user_agent="",
            ttl_seconds=3600)
    batch_id = import_workbook(app_config, sample_workbook).batch_id
    store.claim_batch(batch_id, province.principal)
    app = create_app(_online_config(app_config))

    def request(grant, method, path, payload=None):
        response = app.handle_test_request(
            method, path, json.dumps(payload, ensure_ascii=False) if payload is not None else "",
            headers={"Authorization": f"Bearer {grant.token}"})
        return response[0], json.loads(response[2])

    _, rows = request(grants["district"], "GET", f"/api/ledger-rows?batch_id={batch_id}&ledger_type=tower_rent")
    row_id = rows["rows"][0]["id"]
    detail_path = f"/api/tower-rents/{row_id}?batch_id={batch_id}"
    assert request(grants["district"], "GET", detail_path)[1]["version"] == 0
    payload = {"batch_id": batch_id, "row_id": row_id,
               "changes": {"产品服务费合计（元/年）（不含税）": 9000},
               "evidence": "合同核实", "note": "申请更正", "error_cause": "原值错误",
               "source": "现场核实", "idempotency_key": "rent-online-1"}
    status, submitted = request(grants["district"], "POST", "/api/tower-rent-corrections", payload)
    assert status == 201 and submitted["request"]["status"] == "pending"
    request_id = submitted["request"]["id"]
    assert request(grants["district"], "POST", "/api/tower-rent-corrections", payload)[1]["request"]["id"] == request_id
    assert request(grants["district"], "GET", detail_path)[1]["current"]["产品服务费合计（元/年）（不含税）"] == 10000
    assert request(grants["province"], "POST", f"/api/tower-rent-corrections/{request_id}/decision",
                   {"action": "approve", "note": "越级"})[0] == 403
    assert request(grants["city"], "POST", f"/api/tower-rent-corrections/{request_id}/decision",
                   {"action": "approve", "note": "市州确认"})[1]["request"]["applied_version"] == 1
    detail = request(grants["district"], "GET", detail_path)[1]
    assert detail["source"]["产品服务费合计（元/年）（不含税）"] == 10000
    assert detail["current"]["产品服务费合计（元/年）（不含税）"] == 9000
    assert detail["versions"][0]["confirmer"] == "city-rent"
    assert request(grants["city"], "POST", f"/api/tower-rent-corrections/{request_id}/decision",
                   {"action": "approve", "note": "重复"})[1]["changed"] is False
    assert request(grants["district"], "GET", f"/api/tower-rent-corrections/{request_id}")[1]["request"]["status"] == "approved"
    assert request(grants["district"], "POST", f"/api/tower-rent-corrections/{request_id}/resubmit",
                   {"idempotency_key": "invalid"})[0] == 409
    assert request(grants["district"], "POST", "/api/tower-rent-corrections",
                   {**payload, "changes": {"铁塔站址编码": "other"},
                    "idempotency_key": "invalid-identity"})[0] == 400

    city_request = request(grants["city"], "POST", "/api/tower-rent-corrections",
                           {**payload, "changes": {"产品服务费合计（元/年）（不含税）": 8000},
                            "idempotency_key": "rent-online-2"})[1]["request"]
    assert request(grants["province"], "POST",
                   f"/api/tower-rent-corrections/{city_request['id']}/decision",
                   {"action": "reject", "note": "补充合同"})[1]["request"]["status"] == "rejected"
    assert request(grants["district"], "POST",
                   f"/api/tower-rent-corrections/{city_request['id']}/resubmit",
                   {"idempotency_key": "wrong-user"})[0] == 403
    resubmitted = request(grants["city"], "POST",
                   f"/api/tower-rent-corrections/{city_request['id']}/resubmit",
                   {**payload, "changes": {"产品服务费合计（元/年）（不含税）": 8000},
                    "idempotency_key": "rent-online-3"})[1]["request"]
    assert resubmitted["replaces_request_id"] == city_request["id"]
    assert request(grants["province"], "POST",
                   f"/api/tower-rent-corrections/{resubmitted['id']}/decision",
                   {"action": "approve", "note": "省级确认"})[1]["request"]["applied_version"] == 2
    assert request(grants["province"], "GET", f"/api/tower-rents?batch_id={batch_id}")[1]["tower_rents"][0]["current_version"] == 2
    assert request(grants["province"], "GET", f"/api/tower-rents?batch_id={batch_id}&offset=1")[1]["tower_rents"] == []
    assert request(grants["province"], "GET", f"/api/tower-rents?batch_id={batch_id}&offset=bad")[0] == 400
    no_change = request(grants["province"], "POST", "/api/tower-rent-corrections",
                        {**payload, "changes": {"产品服务费合计（元/年）（不含税）": 8000},
                         "idempotency_key": "rent-online-no-change"})[1]["request"]
    assert request(grants["province"], "POST",
                   f"/api/tower-rent-corrections/{no_change['id']}/decision",
                   {"action": "approve", "note": "未变化"})[0] == 409
    province_change = request(grants["province"], "POST", "/api/tower-rent-corrections",
                              {**payload, "changes": {"产品服务费合计（元/年）（不含税）": 7000},
                               "idempotency_key": "rent-online-province"})[1]["request"]
    assert request(grants["province"], "POST",
                   f"/api/tower-rent-corrections/{province_change['id']}/decision",
                   {"action": "approve", "note": "省级自提确认"})[1]["request"]["applied_version"] == 3
    assert request(grants["province"], "POST", "/api/tower-rent-corrections",
                   {**payload, "changes": {"不存在的字段": 1},
                    "idempotency_key": "rent-online-unknown"})[0] == 400
    other_city_id = store.create_organization(code="nb-rent", name="宁波")
    store.create_user(actor=province.principal, organization_id=other_city_id,
                      username="nb-rent-user", display_name="宁波人员",
                      password="operator-password", role_codes=["operator"])
    other = store.authenticate(username="nb-rent-user", password="operator-password",
                               source_ip="", user_agent="", ttl_seconds=3600)
    assert request(other, "GET", detail_path)[0] == 404
    assert request(other, "POST", "/api/tower-rent-corrections", {
        **payload, "idempotency_key": "nb-unauthorized"})[0] == 404
    assert request(other, "GET", f"/api/tower-rents?batch_id={batch_id}")[1]["tower_rents"] == []
    assert request(grants["district"], "GET", f"/api/tower-rents/99999?batch_id={batch_id}")[0] == 404
    assert request(grants["district"], "GET", f"/api/tower-rents/not-a-number?batch_id={batch_id}")[0] == 404
    assert request(grants["district"], "GET", "/api/tower-rent-corrections/not-a-number")[0] == 404
    assert request(grants["district"], "GET", "/api/tower-rent-corrections/99999")[0] == 404
    assert request(grants["district"], "POST", "/api/tower-rent-corrections/99999/decision",
                   {"action": "approve", "note": "找不到"})[0] == 409
    assert request(grants["district"], "POST", f"/api/tower-rent-corrections/{request_id}/decision",
                   {"action": "invalid", "note": "无效"})[0] == 400
    assert request(grants["district"], "POST", "/api/tower-rent-corrections/not-a-number/resubmit",
                   {"idempotency_key": "invalid"})[0] == 404
    assert request(grants["district"], "POST", "/api/tower-rent-corrections/99999/resubmit",
                   {"idempotency_key": "invalid"})[0] == 404
    malformed = app.handle_test_request(
        "POST", f"/api/tower-rent-corrections/{city_request['id']}/resubmit", "{",
        headers={"Authorization": f"Bearer {grants['city'].token}"})
    assert malformed[0] == 400
    assert request(grants["district"], "POST", "/api/tower-rent-corrections",
                   {**payload, "idempotency_key": "rent-online-1",
                    "changes": {"产品服务费合计（元/年）（不含税）": 7000}})[0] == 409
    assert request(grants["district"], "POST", "/api/tower-rent-corrections",
                   {**payload, "idempotency_key": "invalid-evidence", "evidence": ""})[0] == 400
    assert request(grants["district"], "GET", f"/api/tower-rents?batch_id={batch_id}&limit=1")[1]["tower_rents"][0]["row_id"] == row_id


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
        urlparse("/api/issues/status"),
        json.dumps({"issue_code": "ISSUE-10"}),
        {"cookie": "session=valid"},
    )[1][0] == 403
    allowed, error = authorize_request(
        config,
        "POST",
        urlparse("/api/issues/status"),
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
        urlparse("/api/issues/status"),
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
