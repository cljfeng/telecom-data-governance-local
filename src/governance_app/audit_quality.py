import json
from typing import Any


def confidence_for(category: str, severity: str) -> str:
    if category == "problem_audit" and severity == "high":
        return "high"
    if severity == "low":
        return "low"
    return "medium"


def confidence_label(confidence: str) -> str:
    return {
        "high": "确定性问题",
        "medium": "疑似问题",
        "low": "提示性问题",
    }.get(confidence, "疑似问题")


def result_payload(
    *,
    rule_id: str,
    rule_name: str,
    category: str,
    severity: str,
    field_name: str | None,
    message: str,
    ledger_type: str,
    city: str | None,
    district: str | None,
    site_code: str | None,
    site_name: str | None,
    row: dict[str, Any],
) -> dict[str, Any]:
    confidence = confidence_for(category, severity)
    return {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "category": category,
        "field": field_name,
        "message": message,
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "evidence": {
            "field": field_name,
            "value": row.get(field_name) if field_name else None,
            "message": message,
            "ledger_type": ledger_type,
            "city": city,
            "district": district,
            "site_code": site_code,
            "site_name": site_name,
        },
    }


def parse_result_payload(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
