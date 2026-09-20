import json

from governance_app.db import connect, initialize_database
from governance_app.migrations import apply_migrations
from governance_app.server import create_app


def test_local_site_source_authority_versions_and_reaudit(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    imported = app.handle_test_request("POST", "/api/import",
                                       json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(imported[2])["batch_id"]
    assert imported[0] == 200
    site = app.handle_test_request("GET", f"/api/local/sites?batch_id={batch_id}")
    row_id = json.loads(site[2])["sites"][0]["row_id"]
    detail_path = f"/api/local/sites/{row_id}?batch_id={batch_id}"
    before = json.loads(app.handle_test_request("GET", detail_path)[2])
    assert before["source"]["地市"] == "杭州"
    assert before["current"]["地市"] == "杭州"
    assert before["version"] == 0
    payload = {"batch_id": batch_id, "changes": {"地市": ""}, "evidence": "市州签认材料 A",
               "operator": "省公司张工", "error_cause": "原台账错填", "source": "市州汇总表",
               "idempotency_key": "correction-1"}
    changed = app.handle_test_request("POST", f"/api/local/sites/{row_id}/corrections",
                                      json.dumps(payload, ensure_ascii=False))
    assert changed[0] == 200
    assert json.loads(changed[2])["version"] == 1
    repeated = app.handle_test_request("POST", f"/api/local/sites/{row_id}/corrections",
                                       json.dumps(payload, ensure_ascii=False))
    assert json.loads(repeated[2])["version"] == 1
    conflicting_replay = app.handle_test_request("POST", f"/api/local/sites/{row_id}/corrections",
        json.dumps({**payload, "changes": {"地市": "宁波"}}, ensure_ascii=False))
    assert conflicting_replay[0] == 409
    changed_payload = json.loads(app.handle_test_request("GET", detail_path)[2])
    assert changed_payload["source"]["地市"] == "杭州"
    assert changed_payload["current"]["地市"] == ""
    assert changed_payload["versions"][0]["old_value"]["地市"] == "杭州"
    assert changed_payload["versions"][0]["evidence"] == "市州签认材料 A"
    assert changed_payload["versions"][0]["operator"] == "省公司张工"
    with connect(app_config) as db:
        assert json.loads(db.execute("select row_json from raw_rows where ledger_type = 'site'").fetchone()[0])["地市"] == "杭州"
    audit = app.handle_test_request("POST", "/api/audit", json.dumps({"batch_id": batch_id}))
    assert audit[0] == 200
    issues = json.loads(app.handle_test_request("GET", f"/api/issues?batch_id={batch_id}")[2])["issues"]
    assert any(issue["rule_id"] == "required_city" and issue["telecom_site_code"] == "HZ001" for issue in issues)
    second = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    second_id = json.loads(second[2])["batch_id"]
    assert second_id != batch_id
    second_row = json.loads(app.handle_test_request("GET", f"/api/local/sites?batch_id={second_id}")[2])["sites"][0]
    second_detail = json.loads(app.handle_test_request("GET", f"/api/local/sites/{second_row['row_id']}?batch_id={second_id}")[2])
    assert second_detail["source"]["地市"] == "杭州"
    assert second_detail["current"]["地市"] == ""
    assert second_detail["version"] == 1
    assert json.loads(app.handle_test_request("GET", detail_path)[2])["source"]["地市"] == "杭州"


def test_existing_site_sources_are_backfilled_without_rewriting_history(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    batch_id = json.loads(app.handle_test_request("POST", "/api/import",
        json.dumps({"path": str(sample_workbook)}))[2])["batch_id"]
    with connect(app_config) as db:
        db.execute("drop table authoritative_site_versions")
        db.execute("drop table authoritative_site_sources")
        db.execute("drop table authoritative_sites")
        db.execute("delete from schema_migrations where version = 5")
    with connect(app_config) as db:
        apply_migrations(db)
    rows = json.loads(app.handle_test_request("GET", f"/api/local/sites?batch_id={batch_id}")[2])["sites"]
    detail = json.loads(app.handle_test_request("GET", f"/api/local/sites/{rows[0]['row_id']}?batch_id={batch_id}")[2])
    assert detail["source"] == detail["current"]
    assert detail["version"] == 0
    assert detail["versions"] == []
