from typing import Any

from governance_app.config import AppConfig
from governance_app.db import connect


def dashboard_summary(config: AppConfig, batch_id: int) -> dict[str, Any]:
    with connect(config) as conn:
        ledger_counts = {
            row["ledger_type"]: row["count"]
            for row in conn.execute(
                "select ledger_type, count(*) as count from ledger_rows where batch_id = ? group by ledger_type",
                (batch_id,),
            )
        }
        issues_by_city = [
            dict(row)
            for row in conn.execute(
                "select coalesce(city, '未填地市') as city, count(*) as count from issues where batch_id = ? group by coalesce(city, '未填地市') order by count desc",
                (batch_id,),
            )
        ]
        issues_by_rule = [
            dict(row)
            for row in conn.execute(
                "select rule_id, count(*) as count from issues where batch_id = ? group by rule_id order by count desc",
                (batch_id,),
            )
        ]
        return {
            "batch_id": batch_id,
            "ledger_counts": ledger_counts,
            "issues_by_city": issues_by_city,
            "issues_by_rule": issues_by_rule,
        }
