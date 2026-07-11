from typing import Any, cast
from urllib.parse import ParseResult, parse_qs

from governance_app.audit_engine import run_audit
from governance_app.audit_rules import all_batch_rules, all_rules, rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.models import IssueStatus
from governance_app.operation_guard import OperationConflict, exclusive_operation
from governance_app.routes.common import (
    JsonResponse,
    batch_id_from_payload,
    batch_id_from_query,
    json_body,
    json_response,
    pagination_from_query,
)
from governance_app.rule_settings import load_rule_settings, upsert_rule_setting
from governance_app.workflow import (
    list_issue_groups,
    list_issue_rules,
    list_issues,
    update_issue_group_status,
    update_issue_status,
)


def handle_audit_route(config: AppConfig, method: str, parsed: ParseResult, body: str) -> JsonResponse | None:
    if method == "GET" and parsed.path == "/api/issues":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        query = parse_qs(parsed.query)
        filters = {
            key: values[0]
            for key, values in query.items()
            if key not in {"batch_id", "limit", "offset"} and values and values[0]
        }
        limit, offset = pagination_from_query(query)
        rules = list_issue_rules(config, batch_id)
        if limit is None:
            return json_response({"issues": list_issues(config, batch_id, filters), "rules": rules})
        page = cast(dict[str, Any], list_issues(config, batch_id, filters, limit=limit, offset=offset))
        return json_response({**page, "rules": rules})
    if method == "GET" and parsed.path == "/api/issue-groups":
        batch_id, error = batch_id_from_query(parsed.query)
        if error:
            return error
        query = parse_qs(parsed.query)
        filters = {
            key: values[0]
            for key, values in query.items()
            if key != "batch_id" and values and values[0]
        }
        return json_response({"groups": list_issue_groups(config, batch_id, filters)})
    if method == "GET" and parsed.path == "/api/rules":
        query = parse_qs(parsed.query)
        raw_batch_id = query.get("batch_id", [""])[0]
        selected_batch_id: int | None = None
        if raw_batch_id:
            try:
                selected_batch_id = int(raw_batch_id)
            except ValueError:
                return json_response({"error": "invalid batch_id"}, status=400)
        return json_response({"rules": _rule_settings_payload(config, selected_batch_id)})
    if method == "POST" and parsed.path == "/api/rules/settings":
        payload, error = json_body(body)
        if error:
            return error
        rule_id = payload.get("rule_id")
        enabled = payload.get("enabled", True)
        config_values = payload.get("config", {})
        if not isinstance(rule_id, str) or not rule_id.strip():
            return json_response({"error": "rule_id is required"}, status=400)
        if not isinstance(enabled, bool):
            return json_response({"error": "enabled must be boolean"}, status=400)
        if not isinstance(config_values, dict):
            return json_response({"error": "config must be object"}, status=400)
        upsert_rule_setting(config, rule_id, enabled=enabled, config_values=config_values)
        return json_response({"status": "updated"})
    if method == "POST" and parsed.path == "/api/audit":
        payload, error = json_body(body)
        if error:
            return error
        batch_id, error = batch_id_from_payload(payload)
        if error:
            return error
        try:
            with exclusive_operation(config, "audit"):
                result = run_audit(config, batch_id)
        except OperationConflict as exc:
            return json_response({"error": str(exc)}, status=409)
        return json_response({"audit_run_id": result.audit_run_id, "issue_count": result.issue_count})
    if method == "POST" and parsed.path == "/api/issues/status":
        payload, error = json_body(body)
        if error:
            return error
        issue_code = payload.get("issue_code")
        status_value = payload.get("status")
        if not isinstance(issue_code, str) or not isinstance(status_value, str):
            return json_response({"error": "issue_code and status are required"}, status=400)
        try:
            update_issue_status(config, issue_code, cast(IssueStatus, status_value))
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"status": "updated"})
    if method == "POST" and parsed.path == "/api/issues/group-status":
        payload, error = json_body(body)
        if error:
            return error
        batch_id, error = batch_id_from_payload(payload)
        if error:
            return error
        status_value = payload.get("status")
        group = payload.get("group")
        if not isinstance(status_value, str) or not isinstance(group, dict):
            return json_response({"error": "group and status are required"}, status=400)
        try:
            updated = update_issue_group_status(
                config,
                batch_id,
                group,
                cast(IssueStatus, status_value),
            )
        except ValueError as exc:
            return json_response({"error": str(exc)}, status=400)
        return json_response({"status": "updated", "updated_count": updated})
    return None


def _rule_settings_payload(config: AppConfig, batch_id: int | None = None) -> list[dict]:
    settings = load_rule_settings(config)
    effectiveness = _rule_effectiveness_by_rule(config, batch_id) if batch_id is not None else {}
    rule_ids = sorted({rule.rule_id for rule in all_rules()} | {rule.rule_id for rule in all_batch_rules()})
    payload = []
    for rule_id in rule_ids:
        metadata = rule_metadata(rule_id)
        setting = settings.get(rule_id)
        rule_effectiveness = effectiveness.get(rule_id, _empty_rule_effectiveness())
        payload.append(
            {
                "rule_id": rule_id,
                "name": metadata.name,
                "ledger_type": metadata.ledger_type,
                "severity": metadata.severity,
                "description": metadata.description,
                "default_suggestion": metadata.default_suggestion,
                "enabled": True if setting is None else setting.enabled,
                "config": {} if setting is None else setting.config,
                "category": metadata.category,
                "parameters": _rule_parameters(rule_id),
                "effectiveness": rule_effectiveness,
                "tuning_recommendation": _rule_tuning_recommendation(rule_effectiveness, metadata.severity),
            }
        )
    return payload


def _rule_effectiveness_by_rule(config: AppConfig, batch_id: int | None) -> dict[str, dict]:
    if batch_id is None:
        return {}
    with connect(config) as conn:
        rows = conn.execute(
            """
            select rule_id, count(*) as total_count,
                   sum(case when status not in ('closed', 'not_required', 'resolved_by_reaudit') then 1 else 0 end) as open_count,
                   sum(case when status = 'not_required' then 1 else 0 end) as not_required_count,
                   sum(case when status = 'still_invalid' then 1 else 0 end) as still_invalid_count
              from issues where batch_id = ? group by rule_id
            """,
            (batch_id,),
        ).fetchall()
    result = {}
    for row in rows:
        total = int(row["total_count"] or 0)
        not_required = int(row["not_required_count"] or 0)
        open_count = int(row["open_count"] or 0)
        result[row["rule_id"]] = {
            "total_count": total,
            "open_count": open_count,
            "not_required_count": not_required,
            "still_invalid_count": int(row["still_invalid_count"] or 0),
            "not_required_rate": round((not_required / total) * 100, 1) if total else 0.0,
            "open_rate": round((open_count / total) * 100, 1) if total else 0.0,
        }
    return result


def _empty_rule_effectiveness() -> dict:
    return {
        "total_count": 0, "open_count": 0, "not_required_count": 0,
        "still_invalid_count": 0, "not_required_rate": 0.0, "open_rate": 0.0,
    }


def _rule_tuning_recommendation(effectiveness: dict, severity: str) -> dict[str, str]:
    total = int(effectiveness.get("total_count") or 0)
    if not total:
        return {"level": "neutral", "message": "当前批次未命中，可保持默认口径"}
    not_required_rate = float(effectiveness.get("not_required_rate") or 0)
    open_count = int(effectiveness.get("open_count") or 0)
    still_invalid_count = int(effectiveness.get("still_invalid_count") or 0)
    if not_required_rate >= 50:
        return {"level": "warning", "message": "无需整改率较高，建议复核规则口径或适当调整阈值"}
    if severity == "high" and open_count:
        return {"level": "danger", "message": "高风险规则仍有未闭环问题，建议优先推动整改"}
    if still_invalid_count:
        return {"level": "warning", "message": "回传后仍异常较多，建议检查整改说明和佐证材料"}
    return {"level": "success", "message": "规则效果稳定，可继续沿用当前口径"}


_RULE_PARAMETERS = {
    "electricity_price_range": [{"key": "max", "label": "电费单价上限", "unit": "元/度", "default": 0.9, "step": 0.01}],
    "electricity_share_percent": [
        {"key": "min", "label": "分摊比例下限", "unit": "%", "default": 0, "step": 1},
        {"key": "max", "label": "分摊比例上限", "unit": "%", "default": 100, "step": 1},
    ],
    "generator_duration_over_24h": [{"key": "max_hours", "label": "单次发电时长上限", "unit": "小时", "default": 24, "step": 0.5}],
    "electricity_contract_share_variance": [{"key": "max_points", "label": "合同分摊允许偏差", "unit": "百分点", "default": 3, "step": 0.5}],
    "electricity_usage_spike_drop": [{"key": "change_ratio", "label": "用电量环比波动阈值", "unit": "倍", "default": 0.3, "step": 0.05}],
    "electricity_reading_usage_mismatch": [
        {"key": "variance_ratio", "label": "读数电量比例偏差", "unit": "倍", "default": 0.1, "step": 0.05},
        {"key": "variance_min", "label": "读数电量最小偏差", "unit": "度", "default": 10, "step": 1},
    ],
    "electricity_amount_calculation_mismatch": [
        {"key": "variance_ratio", "label": "金额计算比例偏差", "unit": "倍", "default": 0.1, "step": 0.05},
        {"key": "variance_min", "label": "金额计算最小偏差", "unit": "元", "default": 100, "step": 10},
    ],
    "electricity_price_commercial_range": [
        {"key": "min", "label": "商业电价下限", "unit": "元/度", "default": 0.3, "step": 0.01},
        {"key": "max", "label": "商业电价上限", "unit": "元/度", "default": 1.5, "step": 0.01},
    ],
    "fee_amount_period_spike": [{"key": "change_ratio", "label": "费用环比突变阈值", "unit": "倍", "default": 1, "step": 0.1}],
    "electricity_price_city_supply_outlier": [{"key": "deviation_ratio", "label": "同区县电价偏离阈值", "unit": "倍", "default": 0.2, "step": 0.05}],
    "generator_duration_mismatch": [{"key": "allowed_hours", "label": "发电时长允许偏差", "unit": "小时", "default": 0.25, "step": 0.05}],
    "generator_cost_per_hour_outlier": [
        {"key": "multiplier", "label": "小时单价中位数倍数", "unit": "倍", "default": 1.5, "step": 0.1},
        {"key": "min_rate", "label": "小时单价绝对下限", "unit": "元/小时", "default": 300, "step": 10},
    ],
}


def _rule_parameters(rule_id: str) -> list[dict]:
    return _RULE_PARAMETERS.get(rule_id, [])
