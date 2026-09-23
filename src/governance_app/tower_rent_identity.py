import json


def business_key(row: dict[str, object]) -> str | None:
    """Only link batches when an agreement number identifies a rent record."""
    fields = ("电信站址编码", "铁塔站址编码", "需求单号", "业务确认单号",
              "报账周期", "账期", "账单月份", "计费账期")
    values = [str(row.get(field) or "").strip() for field in fields]
    if not values[0] or not values[1] or not (values[2] or values[3]):
        return None
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
