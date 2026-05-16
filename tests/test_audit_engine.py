import json

from governance_app.audit_engine import run_audit
from governance_app.db import connect, initialize_database
from governance_app.importer import import_workbook


def test_run_audit_generates_issue_for_invalid_electricity_price(app_config, sample_workbook):
    initialize_database(app_config)
    result = import_workbook(app_config, sample_workbook)

    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )

    audit = run_audit(app_config, result.batch_id)

    assert audit.issue_count >= 1
    with connect(app_config) as conn:
        issue = conn.execute("select rule_id, status from issues where rule_id = 'electricity_price_range'").fetchone()
        assert issue["status"] == "pending_export"


def test_run_audit_generates_stable_issue_codes(app_config, sample_workbook):
    initialize_database(app_config)
    result = import_workbook(app_config, sample_workbook)

    first = run_audit(app_config, result.batch_id)
    second = run_audit(app_config, result.batch_id)

    assert first.issue_count == second.issue_count
    with connect(app_config) as conn:
        total = conn.execute("select count(*) as c from issues").fetchone()["c"]
        distinct_total = conn.execute("select count(distinct issue_code) as c from issues").fetchone()["c"]
        assert total == distinct_total


def test_run_audit_applies_electricity_governance_rules(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    rows = [
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "电表户号": "M001",
            "报账周期": "2026-03",
            "电费单价": 1.0,
            "供电方式": "直供电",
            "分摊比例(%)": 50,
            "合同约定分摊比例(%)": 50,
            "用电量": 100,
            "合同申报容量": 10,
            "实际用电容量": 10,
        },
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "电表户号": "M001",
            "报账周期": "2026-04",
            "电费单价": 1.2,
            "供电方式": "直供电",
            "分摊比例(%)": 56,
            "合同约定分摊比例(%)": 50,
            "用电量": 140,
            "合同申报容量": 10,
            "实际用电容量": 12,
        },
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "电表户号": "M002",
            "报账周期": "2026-04",
            "电费单价": 1.0,
            "供电方式": "直供电",
            "分摊比例(%)": 50,
            "合同约定分摊比例(%)": 50,
            "用电量": 90,
            "合同申报容量": 10,
            "实际用电容量": 10,
        },
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "电表户号": "M003",
            "报账周期": "2026-04",
            "电费单价": 1.0,
            "供电方式": "直供电",
            "分摊比例(%)": 50,
            "合同约定分摊比例(%)": 50,
            "用电量": 95,
            "合同申报容量": 10,
            "实际用电容量": 10,
        },
    ]
    for row in rows:
        _insert_ledger_row(app_config, batch_id, "electricity", row)

    run_audit(app_config, batch_id)

    assert _rule_ids(app_config) >= {
        "electricity_price_benchmark",
        "electricity_contract_share_variance",
        "electricity_duplicate_payment",
        "electricity_usage_spike_drop",
        "electricity_capacity_mismatch",
    }


def test_run_audit_applies_tower_rent_governance_rules(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    rows = [
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "T001",
            "电信站址名称": "一站",
            "账期": "2026-03",
            "订单号": "O001",
            "业务确认单号": "B001",
            "塔桅类型": "普通地面塔",
            "挂高": 45,
            "塔高": 40,
            "铁塔产品": "普通地面塔A",
            "铁塔共享用户数": 2,
            "机房产品": "自建机房",
            "机房共享用户数": 1,
            "维护费(元/年)": 100,
            "场地费(元/年)": 200,
            "电力引入费(元/年)": 300,
            "产品服务费合计（元/年）（不含税）": 500,
            "铁塔产品单元数": 0,
            "机房产品单元数": 0,
            "配套产品单元数": 0,
            "铁塔共享信息": "共享",
            "维护费共享折扣": 0.9,
            "站址共享信息": "原产权方",
        },
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "T001",
            "电信站址名称": "一站",
            "账期": "2026-03",
            "订单号": "O002",
            "业务确认单号": "B001",
            "塔桅类型": "普通地面塔",
            "挂高": 35,
            "塔高": 35,
            "铁塔产品": "普通地面塔B",
            "铁塔共享用户数": 3,
            "机房产品": "自建机房",
            "机房共享用户数": 2,
            "维护费(元/年)": 50,
            "场地费(元/年)": 100,
            "电力引入费(元/年)": 100,
            "产品服务费合计（元/年）（不含税）": 0,
            "铁塔产品单元数": 1,
            "机房产品单元数": 0,
            "配套产品单元数": 0,
            "铁塔共享信息": "共享",
            "维护费共享折扣": 0.7,
            "站址共享信息": "共享方",
        },
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "T001",
            "电信站址名称": "一站",
            "账期": "2026-04",
            "订单号": "O003",
            "业务确认单号": "B001",
            "塔桅类型": "普通地面塔",
            "挂高": 35,
            "塔高": 35,
            "铁塔产品": "普通地面塔A",
            "铁塔共享用户数": 4,
            "机房产品": "自建机房",
            "机房共享用户数": 2,
            "维护费(元/年)": 0,
            "场地费(元/年)": 0,
            "电力引入费(元/年)": 0,
            "产品服务费合计（元/年）（不含税）": 0,
            "铁塔产品单元数": 1,
            "机房产品单元数": 0,
            "配套产品单元数": 0,
            "铁塔共享信息": "共享",
            "维护费共享折扣": 0.7,
            "站址共享信息": "共享方",
        },
    ]
    for row in rows:
        _insert_ledger_row(app_config, batch_id, "tower_rent", row)

    run_audit(app_config, batch_id)

    assert _rule_ids(app_config) >= {
        "tower_mount_height_exceeds_tower_height",
        "tower_site_height_inconsistent",
        "tower_confirmation_product_changed",
        "tower_product_shared_users_inconsistent",
        "tower_room_shared_users_inconsistent",
        "tower_duplicate_maintenance_fee",
        "tower_duplicate_site_fee",
        "tower_duplicate_power_intro_fee",
        "tower_product_units_zero_fee_nonzero",
        "tower_maintenance_discount_not_lowest",
        "tower_original_owner_power_intro_fee_nonzero",
    }


def _create_batch(app_config) -> int:
    with connect(app_config) as conn:
        return conn.execute(
            "insert into import_batches(source_file, name, status) values (?, ?, ?)",
            ("test.xlsx", "test", "imported"),
        ).lastrowid


def _insert_ledger_row(app_config, batch_id: int, ledger_type: str, row: dict) -> None:
    row_json = json.dumps(row, ensure_ascii=False)
    with connect(app_config) as conn:
        conn.execute(
            """
            insert into ledger_rows(
                batch_id, ledger_type, city, district, telecom_site_code, telecom_site_name,
                tower_site_code, tower_site_name, row_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                ledger_type,
                row.get("地市"),
                row.get("区县"),
                row.get("电信站址编码"),
                row.get("电信站址名称"),
                row.get("铁塔站址编码"),
                row.get("铁塔站址名称"),
                row_json,
            ),
        )


def _rule_ids(app_config) -> set[str]:
    with connect(app_config) as conn:
        return {row["rule_id"] for row in conn.execute("select distinct rule_id from issues")}
