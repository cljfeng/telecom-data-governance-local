import json

from governance_app.audit_engine import run_audit
from governance_app.audit_rules import DEFAULT_THRESHOLDS, all_batch_rules, all_rules, rule_metadata
from governance_app.db import connect, initialize_database
from governance_app.importer import import_workbook
from governance_app.rule_settings import upsert_rule_setting


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


def test_rule_catalog_covers_all_audit_rules():
    rule_ids = {rule.rule_id for rule in all_rules()} | {rule.rule_id for rule in all_batch_rules()}

    metadata = {rule_id: rule_metadata(rule_id) for rule_id in rule_ids}

    assert metadata["electricity_price_range"].name == "电费高单价"
    assert "electricity_price_benchmark" not in rule_ids
    assert all(item.name for item in metadata.values())
    assert all(item.description for item in metadata.values())
    assert all(item.default_suggestion for item in metadata.values())


def test_unknown_rule_metadata_falls_back_to_rule_id():
    metadata = rule_metadata("future_rule_not_registered")

    assert metadata.rule_id == "future_rule_not_registered"
    assert metadata.name == "future_rule_not_registered"


def test_default_rule_thresholds_document_key_ranges():
    assert DEFAULT_THRESHOLDS.electricity_price_min == 0
    assert DEFAULT_THRESHOLDS.electricity_price_max == 0.9
    assert DEFAULT_THRESHOLDS.share_percent_min == 0
    assert DEFAULT_THRESHOLDS.share_percent_max == 100


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
        "electricity_price_range",
        "electricity_contract_share_variance",
        "electricity_duplicate_payment",
        "electricity_usage_spike_drop",
        "electricity_capacity_mismatch",
    }


def test_electricity_high_price_flags_only_prices_above_nine_tenths(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    rows = [
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "电表户号": "M001",
            "报账周期": "2026-04",
            "电费单价": 0.89,
            "供电方式": "直供电",
            "分摊比例(%)": 100,
        },
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E002",
            "电信站址名称": "二站",
            "电表户号": "M002",
            "报账周期": "2026-04",
            "电费单价": 0.91,
            "供电方式": "直供电",
            "分摊比例(%)": 100,
        },
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E003",
            "电信站址名称": "三站",
            "电表户号": "M003",
            "报账周期": "2026-04",
            "电费单价": -1,
            "供电方式": "直供电",
            "分摊比例(%)": 100,
        },
    ]
    for row in rows:
        _insert_ledger_row(app_config, batch_id, "electricity", row)

    run_audit(app_config, batch_id)

    with connect(app_config) as conn:
        issues = conn.execute(
            """
            select message, telecom_site_code, rule_id
            from issues
            where rule_id = 'electricity_price_range'
            order by telecom_site_code
            """
        ).fetchall()
    assert [issue["telecom_site_code"] for issue in issues] == ["E002"]
    assert issues[0]["rule_id"] == "electricity_price_range"
    assert "超过 0.9 元" in issues[0]["message"]


def test_electricity_share_percent_accepts_percent_text(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    _insert_ledger_row(
        app_config,
        batch_id,
        "electricity",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "电表户号": "M001",
            "报账周期": "2026-04",
            "电费单价": 0.8,
            "分摊比例(%)": "80%",
        },
    )

    run_audit(app_config, batch_id)

    assert "electricity_share_percent" not in _rule_ids(app_config)


def test_generator_duration_flags_only_values_above_24_hours(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    _insert_ledger_row(
        app_config,
        batch_id,
        "generator",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "G001",
            "电信站址名称": "一站",
            "运维系统工单号": "WO001",
            "发电时长": 0,
        },
    )
    _insert_ledger_row(
        app_config,
        batch_id,
        "generator",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "G002",
            "电信站址名称": "二站",
            "运维系统工单号": "WO002",
            "发电时长": 25,
        },
    )

    run_audit(app_config, batch_id)

    with connect(app_config) as conn:
        issues = conn.execute(
            """
            select telecom_site_code, message
              from issues
             where rule_id = 'generator_duration_over_24h'
             order by telecom_site_code
            """
        ).fetchall()
    assert [issue["telecom_site_code"] for issue in issues] == ["G002"]
    assert "超过 24 小时" in issues[0]["message"]


def test_run_audit_applies_additional_finance_and_generator_rules(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    _insert_ledger_row(
        app_config,
        batch_id,
        "electricity",
        {
            "地市": "兰州市",
            "区县": "城关",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "报账周期": "2026-04",
            "电费单价": 0.8,
            "电费金额": -10,
            "分摊比例(%)": 100,
        },
    )
    _insert_ledger_row(
        app_config,
        batch_id,
        "generator",
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "G001",
            "电信站址名称": "发电站一",
            "运维系统工单号": "WO001",
            "发电时长": 3,
            "最终分摊金额": 50,
        },
    )
    _insert_ledger_row(
        app_config,
        batch_id,
        "generator",
        {
            "地市": "兰州市",
            "区县": "城关",
            "电信站址编码": "G001",
            "电信站址名称": "发电站一",
            "运维系统工单号": "WO001",
            "发电日期": "2026-04-01",
            "发电时间 - 发电开始时间": "2026-04-01 08:00",
            "发电时间 - 发电结束时间（断电传感器告警消除时间）": "2026-04-01 10:00",
            "发电时长": 5,
            "最终分摊金额": 20,
        },
    )
    _insert_ledger_row(
        app_config,
        batch_id,
        "tower_rent",
        {
            "地市": "兰州市",
            "区县": "城关",
            "电信站址编码": "T001",
            "电信站址名称": "铁塔站一",
            "账期": "2026-04",
            "停租日期": "2026-03-31",
            "产品服务费合计（元/年）（不含税）": 100,
        },
    )

    run_audit(app_config, batch_id)

    assert _rule_ids(app_config) >= {
        "amount_negative",
        "generator_missing_date_with_cost",
        "generator_duplicate_work_order",
        "generator_duration_mismatch",
        "tower_stopped_site_still_charged",
    }


def test_run_audit_applies_advanced_recommendation_rules(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    for row in [
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "E001",
            "电信站址名称": "电费一站",
            "报账周期": "2026-01",
            "供电方式": "直供电",
            "电费单价": 0.8,
            "电费金额": 100,
            "分摊比例(%)": 100,
        },
        {
            "地市": "兰州市",
            "区县": "城关",
            "电信站址编码": "E001",
            "电信站址名称": "电费一站",
            "报账周期": "2026-02",
            "供电方式": "直供电",
            "电费单价": 0.8,
            "电费金额": 400,
            "分摊比例(%)": 100,
        },
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "E002",
            "电信站址名称": "电费二站",
            "报账周期": "2026-02",
            "供电方式": "直供电",
            "电费单价": 0.8,
            "分摊比例(%)": 100,
        },
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "E003",
            "电信站址名称": "电费三站",
            "报账周期": "2026-02",
            "供电方式": "直供电",
            "电费单价": 0.8,
            "分摊比例(%)": 100,
        },
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "E004",
            "电信站址名称": "电费四站",
            "报账周期": "2026-02",
            "供电方式": "直供电",
            "电费单价": 1.05,
            "分摊比例(%)": 100,
        },
    ]:
        _insert_ledger_row(app_config, batch_id, "electricity", row)
    for row in [
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "",
            "电信站址名称": "重复无编码站",
        },
        {
            "地市": "兰州市",
            "区县": "七里河",
            "电信站址编码": "",
            "电信站址名称": "重复无编码站",
        },
    ]:
        _insert_ledger_row(app_config, batch_id, "site", row)
    _insert_ledger_row(
        app_config,
        batch_id,
        "tower_rent",
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "T001",
            "电信站址名称": "停租站",
            "账期": "2026-05",
            "停租日期": "2026-03-31",
            "产品服务费合计（元/年）（不含税）": 100,
        },
    )
    for row in [
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "G001",
            "电信站址名称": "发电一站",
            "运维系统工单号": "WO100",
            "发电日期": "2026-04-01",
            "发电时长": 2,
            "最终分摊金额": 100,
        },
        {
            "地市": "兰州",
            "区县": "城关",
            "电信站址编码": "G002",
            "电信站址名称": "发电二站",
            "运维系统工单号": "WO101",
            "发电日期": "2026-04-01",
            "发电时长": 2,
            "最终分摊金额": 1200,
        },
    ]:
        _insert_ledger_row(app_config, batch_id, "generator", row)

    run_audit(app_config, batch_id)

    assert _rule_ids(app_config) >= {
        "fee_amount_period_spike",
        "missing_site_code_duplicate_name",
        "tower_charged_after_stop_period",
        "electricity_price_city_supply_outlier",
        "generator_cost_per_hour_outlier",
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


def test_run_audit_respects_disabled_rule_setting(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    _insert_ledger_row(
        app_config,
        batch_id,
        "electricity",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "电表户号": "M001",
            "报账周期": "2026-04",
            "电费单价": 9.9,
            "分摊比例(%)": 100,
        },
    )
    upsert_rule_setting(app_config, "electricity_price_range", enabled=False)

    run_audit(app_config, batch_id)

    assert "electricity_price_range" not in _rule_ids(app_config)


def test_run_audit_uses_configured_electricity_price_threshold(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    _insert_ledger_row(
        app_config,
        batch_id,
        "electricity",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "E001",
            "电信站址名称": "一站",
            "电表户号": "M001",
            "报账周期": "2026-04",
            "电费单价": 2.5,
            "分摊比例(%)": 100,
        },
    )
    upsert_rule_setting(app_config, "electricity_price_range", enabled=True, config={"max": 3})

    run_audit(app_config, batch_id)

    assert "electricity_price_range" not in _rule_ids(app_config)


def test_run_audit_applies_cross_ledger_governance_rules(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    _insert_ledger_row(
        app_config,
        batch_id,
        "site",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "S001",
            "电信站址名称": "站址主数据名称",
            "站址发电责任方": "",
        },
    )
    _insert_ledger_row(
        app_config,
        batch_id,
        "electricity",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "S001",
            "电信站址名称": "电费台账名称",
            "电表户号": "M001",
            "报账周期": "2026-04",
            "电费单价": 0.8,
            "分摊比例(%)": 100,
            "供电方式": "转供电",
            "转供电合同情况": "",
            "是否包干站址": "是",
            "是否报账": "是",
        },
    )
    _insert_ledger_row(
        app_config,
        batch_id,
        "tower_rent",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "S002",
            "电信站址名称": "缺失站址",
            "铁塔站址编码": "TT002",
            "铁塔站址名称": "铁塔缺失站址",
            "账期": "2026-04",
            "停租日期": "2026-03-31",
            "产品服务费合计（元/年）（不含税）": 100,
        },
    )
    _insert_ledger_row(
        app_config,
        batch_id,
        "generator",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "S001",
            "电信站址名称": "站址主数据名称",
            "运维系统工单号": "WO001",
            "发电时长": 3,
            "最终分摊金额": 50,
        },
    )

    run_audit(app_config, batch_id)

    assert _rule_ids(app_config) >= {
        "site_code_missing_in_master",
        "site_name_mismatch_across_ledgers",
        "tower_stopped_site_still_charged",
        "electricity_lump_sum_still_reimbursed",
        "electricity_transfer_without_contract",
        "generator_missing_responsible_party",
    }


def test_site_code_master_rule_distinguishes_placeholder_code(app_config):
    initialize_database(app_config)
    batch_id = _create_batch(app_config)
    _insert_ledger_row(
        app_config,
        batch_id,
        "site",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "S001",
            "电信站址名称": "一站",
        },
    )
    _insert_ledger_row(
        app_config,
        batch_id,
        "tower_rent",
        {
            "地市": "杭州",
            "区县": "西湖",
            "电信站址编码": "#N/A",
            "电信站址名称": "占位站",
            "铁塔站址编码": "TT001",
            "铁塔站址名称": "铁塔占位站",
            "产品服务费合计（元/年）（不含税）": 100,
        },
    )

    run_audit(app_config, batch_id)

    with connect(app_config) as conn:
        issue = conn.execute("select message from issues where rule_id = 'site_code_missing_in_master'").fetchone()
    assert "为空或为占位值" in issue["message"]


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
