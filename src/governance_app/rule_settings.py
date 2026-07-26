import json
from dataclasses import dataclass, field
from typing import Any

from governance_app.config import AppConfig
from governance_app.database_runtime import database_for
from governance_app.ports.database import Database


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
    database: Database | None = None,
    **legacy_kwargs,
) -> None:
    values = config_values
    if values is None and "config" in legacy_kwargs:
        values = legacy_kwargs["config"]
    payload = json.dumps(values or {}, ensure_ascii=False)
    selected_database = database or database_for(app_config)
    with selected_database.unit_of_work() as unit_of_work:
        unit_of_work.rule_settings.upsert(
            rule_id,
            enabled=enabled,
            config_json=payload,
        )


def load_rule_settings(
    app_config: AppConfig,
    *,
    database: Database | None = None,
) -> dict[str, RuleSetting]:
    selected_database = database or database_for(app_config)
    with selected_database.unit_of_work() as unit_of_work:
        rows = unit_of_work.rule_settings.list()
    settings: dict[str, RuleSetting] = {}
    for row in rows:
        try:
            values = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError:
            values = {}
        settings[row["rule_id"]] = RuleSetting(row["rule_id"], bool(row["enabled"]), values if isinstance(values, dict) else {})
    return settings
