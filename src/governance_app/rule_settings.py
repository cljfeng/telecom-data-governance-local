import json
from dataclasses import dataclass, field
from typing import Any

from governance_app.config import AppConfig
from governance_app.db import connect


@dataclass(frozen=True)
class RuleSetting:
    rule_id: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


def upsert_rule_setting(
    app_config: AppConfig,
    rule_id: str,
    *,
    enabled: bool = True,
    config_values: dict[str, Any] | None = None,
    **legacy_kwargs,
) -> None:
    values = config_values
    if values is None and "config" in legacy_kwargs:
        values = legacy_kwargs["config"]
    payload = json.dumps(values or {}, ensure_ascii=False)
    with connect(app_config) as conn:
        conn.execute(
            """
            insert into audit_rule_settings(rule_id, enabled, config_json)
            values (?, ?, ?)
            on conflict(rule_id) do update set
                enabled = excluded.enabled,
                config_json = excluded.config_json,
                updated_at = current_timestamp
            """,
            (rule_id, 1 if enabled else 0, payload),
        )


def load_rule_settings(app_config: AppConfig) -> dict[str, RuleSetting]:
    with connect(app_config) as conn:
        rows = conn.execute("select rule_id, enabled, config_json from audit_rule_settings").fetchall()
    settings: dict[str, RuleSetting] = {}
    for row in rows:
        try:
            values = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError:
            values = {}
        settings[row["rule_id"]] = RuleSetting(row["rule_id"], bool(row["enabled"]), values if isinstance(values, dict) else {})
    return settings
