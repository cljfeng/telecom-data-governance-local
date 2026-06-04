from governance_app.models import LedgerType

EXPECTED_SHEETS: dict[str, LedgerType] = {
    "站址台账": "site",
    "铁塔租费台账": "tower_rent",
    "电费台账": "electricity",
    "发电费台账": "generator",
}

SHEET_ALIASES: dict[LedgerType, tuple[str, ...]] = {
    "site": ("站址台账", "站址清单", "站址基础台账", "站址基础信息"),
    "tower_rent": ("铁塔租费台账", "铁塔租费清单", "租费台账", "铁塔租赁费台账"),
    "electricity": ("电费台账", "电费清单", "电费报账台账", "基站电费台账"),
    "generator": ("发电费台账", "发电费清单", "油机发电费台账", "发电台账"),
}

HEADER_ALIASES: dict[str, str] = {
    "所属地市": "地市",
    "本地网": "地市",
    "地市公司": "地市",
    "所属区县": "区县",
    "县区": "区县",
    "站址编码": "电信站址编码",
    "电信站点编码": "电信站址编码",
    "站点编码": "电信站址编码",
    "站址名称": "电信站址名称",
    "电信站点名称": "电信站址名称",
    "站点名称": "电信站址名称",
    "铁塔编码": "铁塔站址编码",
    "铁塔站点编码": "铁塔站址编码",
    "铁塔名称": "铁塔站址名称",
    "铁塔站点名称": "铁塔站址名称",
    "电表号": "电表户号",
    "电表编号": "电表编码",
    "账期": "报账周期",
    "月份": "账单月份",
    "工单号": "运维系统工单号",
    "分摊比例": "分摊比例(%)",
    "PUE系数": "PUE 系数",
}

REQUIRED_HEADERS: dict[LedgerType, tuple[str, ...]] = {
    "site": ("地市", "区县", "电信站址编码", "电信站址名称"),
    "tower_rent": ("电信站址编码", "电信站址名称", "地市", "区县", "铁塔站址编码", "铁塔站址名称"),
    "electricity": ("地市", "区县", "电信站址编码", "电信站址名称", "电表户号", "报账周期"),
    "generator": ("发电日期", "账单月份", "电信站址编码", "电信站址名称", "运维系统工单号", "发电时长"),
}

HEADER_ROWS: dict[LedgerType, int] = {
    "site": 1,
    "tower_rent": 1,
    "electricity": 1,
    "generator": 2,
}

FIELD_GROUPS: dict[LedgerType, dict[str, tuple[str, ...]]] = {
    "site": {
        "站址基础": ("地市", "区县", "电信站址编码", "电信站址名称", "经度", "纬度"),
        "站址属性": ("站址归属", "站址归属方名称", "设备共站类型", "是否有机房", "是否拉远", "塔桅类型"),
        "专项属性": ("基站等级", "动环监控设备厂家", "动环监控设备型号", "站址发电责任方", "是否普遍服务/宽带边疆/林草"),
    },
    "tower_rent": {
        "站址与需求": ("需求单号", "需求类型", "起租状态", "业务确认单号", "铁塔站址编码", "铁塔站址名称"),
        "产品共享": ("铁塔共享信息", "铁塔产品", "机房共享信息", "机房产品", "配套共享信息", "配套产品"),
        "费用组成": ("铁塔基准价格", "机房基准价格", "配套基准价格", "维护费(元/年)", "场地费(元/年)", "电力引入费(元/年)"),
        "续签退租": ("停租日期", "是否享受续签折扣", "续签折扣", "运营商确认是否续签", "应收赔偿金额"),
    },
    "electricity": {
        "电表报账": ("电表户号", "电表编码", "报账周期", "是否报账", "是否打包报账站址", "是否包干站址"),
        "供电分摊": ("电费单价", "供电方式", "缴费责任方", "共享情况", "分摊比例(%)"),
        "能耗设备": ("PUE系数", "机房类型", "是否有空调", "空调总额定功率", "移动网设备能耗", "非移动网设备能耗"),
    },
    "generator": {
        "发电事件": ("发电日期", "账单月份", "运维系统工单号", "停电原因"),
        "时间时长": ("发电时间 - 停电时间", "发电时间 - 发电开始时间", "发电时间 - 发电结束时间（断电传感器告警消除时间）", "发电时长", "需核减时长", "核减后时长"),
        "金额分摊": ("非5G金额", "5G金额", "分摊金额", "最终分摊金额"),
    },
}


def ledger_type_for_sheet(sheet_name: str) -> LedgerType | None:
    normalized = _normalize_name(sheet_name)
    for ledger_type, aliases in SHEET_ALIASES.items():
        if normalized in {_normalize_name(alias) for alias in aliases}:
            return ledger_type
    return EXPECTED_SHEETS.get(sheet_name)


def required_headers_for(ledger_type: LedgerType) -> tuple[str, ...]:
    return REQUIRED_HEADERS[ledger_type]


def workbook_sheet_for(sheetnames: list[str], ledger_type: LedgerType) -> str | None:
    aliases = {_normalize_name(alias) for alias in SHEET_ALIASES[ledger_type]}
    for sheet_name in sheetnames:
        if _normalize_name(sheet_name) in aliases:
            return sheet_name
    return None


def canonical_header(value: object, ledger_type: LedgerType | None = None, raw_headers: tuple[object, ...] = ()) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    compact = _normalize_name(text)
    if ledger_type == "tower_rent" and _has_explicit_telecom_site_headers(raw_headers):
        if compact == _normalize_name("站址编码"):
            return "铁塔站址编码"
        if compact == _normalize_name("站址名称"):
            return "铁塔站址名称"
    for alias, canonical in HEADER_ALIASES.items():
        if compact == _normalize_name(alias):
            return canonical
    return text


def _has_explicit_telecom_site_headers(raw_headers: tuple[object, ...]) -> bool:
    normalized = {_normalize_name(value) for value in raw_headers if value not in (None, "")}
    return _normalize_name("电信站址编码") in normalized or _normalize_name("电信站址名称") in normalized


def _normalize_name(value: object) -> str:
    return "".join(str(value or "").split()).replace("（", "(").replace("）", ")")
