from governance_app.rule_helpers import _is_placeholder, _text
from governance_app.rule_types import (
    AuditLedgerRow,
    AuditRule,
    BatchAuditRule,
    BatchRuleFinding,
    RuleThresholds,
)
from governance_app.rules.factories import required


def site_rules(thresholds: RuleThresholds) -> list[AuditRule]:
    del thresholds
    return [
        AuditRule("required_site_code", "site", "high", required("电信站址编码", "站址编码为空", "补充电信站址编码")),
        AuditRule("required_city", "site", "medium", required("地市", "地市为空", "补充地市")),
    ]


def site_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]:
    del thresholds
    return [
        BatchAuditRule("missing_site_code_duplicate_name", "site", "high", _missing_site_code_duplicate_name),
        BatchAuditRule("site_code_missing_in_master", "all", "high", _site_code_missing_in_master),
    ]


def _missing_site_code_duplicate_name(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    grouped: dict[str, list[AuditLedgerRow]] = {}
    for ledger_row in rows:
        if ledger_row.ledger_type != "site":
            continue
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        site_name = _text(ledger_row.telecom_site_name or ledger_row.row.get("电信站址名称"))
        if not site_code and site_name:
            grouped.setdefault(site_name, []).append(ledger_row)
    return [
        BatchRuleFinding(
            ledger_row.ledger_row_id,
            "电信站址编码",
            f"站址编码为空，且站址名称“{site_name}”重复出现",
            "补充电信站址编码，核对是否重复建档",
        )
        for site_name, group in grouped.items()
        if len(group) > 1
        for ledger_row in group
    ]


def _site_code_missing_in_master(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    site_codes = {
        _text(row.telecom_site_code or row.row.get("电信站址编码"))
        for row in rows
        if row.ledger_type == "site"
    }
    site_codes = {site_code for site_code in site_codes if site_code and not _is_placeholder(site_code)}
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type == "site":
            continue
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        if not site_code or _is_placeholder(site_code):
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "电信站址编码",
                    "费用台账电信站址编码为空或为占位值，无法与站址台账进行匹配",
                    "补充正确的电信站址编码后重新核对跨表一致性",
                )
            )
        elif site_code not in site_codes:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "电信站址编码",
                    f"费用台账电信站址编码“{site_code}”未在站址台账中找到",
                    "补充站址主数据或核对费用台账站址编码",
                )
            )
    return findings
