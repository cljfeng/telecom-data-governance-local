from typing import Any


CITY_ALIASES = {
    "甘南藏族自治州": "甘南",
    "临夏回族自治州": "临夏",
}


def normalize_city(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未填地市"
    text = CITY_ALIASES.get(text, text)
    for suffix in ("藏族自治州", "回族自治州", "蒙古族自治州", "自治州", "地区", "市"):
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text
