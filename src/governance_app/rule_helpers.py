from datetime import datetime
from typing import Any

from governance_app.rule_fields import AMOUNT_FIELD_KEYWORDS as _AMOUNT_FIELD_KEYWORDS


def _first_value(row: dict[str, Any], field_names: tuple[str, ...]) -> Any:
    for field_name in field_names:
        value = row.get(field_name)
        if value not in (None, ""):
            return value
    return None


def _positive_fee_field(row: dict[str, Any]) -> tuple[str | None, float]:
    for field_name, value in row.items():
        if not any(keyword in field_name for keyword in _AMOUNT_FIELD_KEYWORDS):
            continue
        amount = _number(value)
        if amount is not None and amount > 0:
            return field_name, amount
    return None, 0


def _positive_field(row: dict[str, Any], field_names: tuple[str, ...]) -> tuple[str | None, float]:
    for field_name in field_names:
        amount = _number(row.get(field_name))
        if amount is not None and amount > 0:
            return field_name, amount
    return None, 0


def _positive_or_zero_field(row: dict[str, Any], field_names: tuple[str, ...]) -> tuple[str | None, float]:
    for field_name in field_names:
        amount = _number(row.get(field_name))
        if amount is not None and amount >= 0:
            return field_name, amount
    return None, 0


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_placeholder(value: str) -> bool:
    return value.strip().upper() in {"#N/A", "N/A", "NA", "NULL", "NONE", "-", "--", "无", "缺失"}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if text.endswith("%"):
        text = text[:-1]
    for unit in ("元/年", "元", "kWh", "KWH", "度", "米", "m", "M"):
        text = text.replace(unit, "")
    try:
        return float(text)
    except ValueError:
        return None


def _datetime_value(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _month_key(value: Any) -> str:
    dt = _datetime_value(value)
    if dt is not None:
        return dt.strftime("%Y-%m")
    text = _period_key(value)
    return text[:7] if len(text) >= 7 else text


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _period_key(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return text.replace("年", "-").replace("月", "").replace("/", "-").strip()
