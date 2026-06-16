from dataclasses import dataclass

from governance_app.models import LedgerType, Severity


@dataclass(frozen=True)
class RuleMetadata:
    rule_id: str
    name: str
    ledger_type: LedgerType | str
    severity: Severity | str
    description: str
    default_suggestion: str
    category: str = "problem_audit"



RULE_CATALOG: dict[str, RuleMetadata] = {
    "required_site_code": RuleMetadata("required_site_code", "站址编码必填", "site", "high", "检查站址台账电信站址编码是否为空。", "补充电信站址编码", "data_quality"),
    "required_city": RuleMetadata("required_city", "地市信息必填", "site", "medium", "检查站址台账地市字段是否为空。", "补充地市", "data_quality"),
    "electricity_price_range": RuleMetadata("electricity_price_range", "电费高单价", "electricity", "high", "检查电费单价是否超过 0.9 元。", "核实电费单价、电价依据或转供电合同"),
    "electricity_share_percent": RuleMetadata("electricity_share_percent", "电费分摊比例范围", "electricity", "medium", "检查电费分摊比例是否在 0-100% 范围内。", "核实共享情况和分摊比例", "data_quality"),
    "generator_duration_over_24h": RuleMetadata("generator_duration_over_24h", "发电时长超过24小时", "generator", "high", "检查单次发电时长是否超过 24 小时。", "核实发电开始时间、结束时间和工单时长"),
    "amount_negative": RuleMetadata("amount_negative", "金额为负数", "all", "high", "检查费用台账金额字段是否为负数。", "核实费用金额、冲销记录和报账口径"),
    "electricity_contract_share_variance": RuleMetadata("electricity_contract_share_variance", "合同分摊比例偏差", "electricity", "medium", "比较实际分摊比例与合同约定分摊比例。", "核对合同约定和实际分摊比例"),
    "electricity_duplicate_payment": RuleMetadata("electricity_duplicate_payment", "电费重复报账", "electricity", "high", "识别同站址、同电表、同账期的重复电费记录。", "核实同账期是否重复报账"),
    "electricity_usage_spike_drop": RuleMetadata("electricity_usage_spike_drop", "用电量异常波动", "electricity", "high", "识别同站址电表用电量较历史记录的异常上升或下降。", "核实抄表数据、设备变化和报账周期"),
    "electricity_capacity_mismatch": RuleMetadata("electricity_capacity_mismatch", "合同容量与实际容量不一致", "electricity", "medium", "比较合同申报容量与实际用电容量。", "核对合同容量和现场实际容量", "data_quality"),
    "electricity_meter_reading_reverse": RuleMetadata("electricity_meter_reading_reverse", "电表读数倒退", "electricity", "medium", "检查本次抄表数是否小于上次抄表数。", "核实换表记录、倍率和抄表录入", "data_quality"),
    "electricity_reading_usage_mismatch": RuleMetadata("electricity_reading_usage_mismatch", "电量与读数不匹配", "electricity", "medium", "比较用电量与本次、上次抄表读数差值。", "核实抄表读数、倍率和用电量计算口径", "data_quality"),
    "electricity_zero_usage_positive_fee": RuleMetadata("electricity_zero_usage_positive_fee", "零电量有电费", "electricity", "high", "检查用电量为0但仍发生电费支出。", "核实固定费用、录入错误或异常报账"),
    "electricity_amount_calculation_mismatch": RuleMetadata("electricity_amount_calculation_mismatch", "电费金额异常", "electricity", "high", "比较电费金额与用电量、电价、分摊比例计算值。", "核实电量、电价、分摊比例和支付金额"),
    "electricity_period_overlap": RuleMetadata("electricity_period_overlap", "时段重叠疑似重复", "electricity", "high", "识别同站址同电表抄表区间交叉重叠。", "核实同一时段是否重复计费"),
    "electricity_price_commercial_range": RuleMetadata("electricity_price_commercial_range", "电价异常", "electricity", "medium", "检查电价是否偏离常见商业电价范围。", "核实电价依据、供电方式和转供电加价"),
    "tower_mount_height_exceeds_tower_height": RuleMetadata("tower_mount_height_exceeds_tower_height", "挂高超过塔高", "tower_rent", "high", "检查设备挂高是否超过铁塔塔高。", "核对设备挂高和塔高基础属性"),
    "tower_site_height_inconsistent": RuleMetadata("tower_site_height_inconsistent", "同站址塔高不一致", "tower_rent", "medium", "识别同一电信站址多订单塔高不一致。", "核对铁塔站址基础属性和订单塔高"),
    "tower_confirmation_product_changed": RuleMetadata("tower_confirmation_product_changed", "业务确认单产品不一致", "tower_rent", "medium", "识别同一业务确认单前后账期铁塔产品不一致。", "核对业务确认单产品变更依据"),
    "tower_product_shared_users_inconsistent": RuleMetadata("tower_product_shared_users_inconsistent", "铁塔共享用户数不一致", "tower_rent", "medium", "识别同站址同铁塔产品共享用户数不一致。", "核对同站址多订单的铁塔共享用户数"),
    "tower_room_shared_users_inconsistent": RuleMetadata("tower_room_shared_users_inconsistent", "机房共享用户数不一致", "tower_rent", "medium", "识别同站址同机房产品共享用户数不一致。", "核对同站址多订单的机房共享用户数"),
    "tower_duplicate_product_service_fee": RuleMetadata("tower_duplicate_product_service_fee", "产品服务费重复计费", "tower_rent", "high", "识别同账期同站址产品服务费多次计费。", "核实产品服务费是否重复计费"),
    "tower_duplicate_maintenance_fee": RuleMetadata("tower_duplicate_maintenance_fee", "维护费重复计费", "tower_rent", "high", "识别同账期同站址维护费多次计费。", "核实维护费是否重复计费"),
    "tower_duplicate_site_fee": RuleMetadata("tower_duplicate_site_fee", "场地费重复计费", "tower_rent", "high", "识别同账期同站址场地费多次计费。", "核实场地费是否重复计费"),
    "tower_duplicate_power_intro_fee": RuleMetadata("tower_duplicate_power_intro_fee", "电力引入费重复计费", "tower_rent", "high", "识别同账期同站址电力引入费多次计费。", "核实电力引入费是否重复计费"),
    "tower_product_units_zero_fee_nonzero": RuleMetadata("tower_product_units_zero_fee_nonzero", "产品单元为零但费用非零", "tower_rent", "high", "检查产品单元数为零时是否仍产生产品服务费。", "核对产品配置和费用生成口径"),
    "tower_maintenance_discount_not_lowest": RuleMetadata("tower_maintenance_discount_not_lowest", "维护费共享折扣非最优惠", "tower_rent", "medium", "检查共享场景下维护费共享折扣是否异常。", "核对共享折扣政策和适用用户数"),
    "tower_original_owner_power_intro_fee_nonzero": RuleMetadata("tower_original_owner_power_intro_fee_nonzero", "原产权方电力引入费非零", "tower_rent", "high", "检查原产权方站址是否仍收取电力引入费。", "核实站址产权属性和电力引入费依据"),
    "site_code_missing_in_master": RuleMetadata("site_code_missing_in_master", "站址编码跨表不存在", "all", "high", "检查费用台账中的电信站址编码是否存在于站址台账。", "补充站址主数据或核对费用台账站址编码", "data_quality"),
    "site_name_mismatch_across_ledgers": RuleMetadata("site_name_mismatch_across_ledgers", "站址名称跨表不一致", "all", "medium", "检查同一站址编码在费用台账与站址台账中的名称是否一致。", "统一站址名称或核对站址编码", "data_quality"),
    "tower_stopped_site_still_charged": RuleMetadata("tower_stopped_site_still_charged", "停租日期已填仍有费用", "tower_rent", "medium", "检查停租日期已填写但仍产生租费的基础逻辑异常。", "核对停租状态、账期和费用生成口径", "data_quality"),
    "electricity_lump_sum_still_reimbursed": RuleMetadata("electricity_lump_sum_still_reimbursed", "包干站址仍重复报账", "electricity", "high", "检查包干站址是否仍标记为报账。", "核对包干电费和报账口径，避免重复报账"),
    "electricity_transfer_without_contract": RuleMetadata("electricity_transfer_without_contract", "转供电无合同", "electricity", "high", "检查转供电站址是否缺少转供电合同。", "补充转供电合同或核实供电方式", "data_quality"),
    "generator_missing_responsible_party": RuleMetadata("generator_missing_responsible_party", "发电责任方缺失", "all", "medium", "检查存在发电费但站址发电责任方缺失的记录。", "补充站址发电责任方并核对发电费用口径", "data_quality"),
    "generator_missing_date_with_cost": RuleMetadata("generator_missing_date_with_cost", "发电日期缺失但有费用", "generator", "high", "检查发电费台账有金额或时长时是否缺少发电日期。", "补充发电日期并核对工单", "data_quality"),
    "generator_duplicate_work_order": RuleMetadata("generator_duplicate_work_order", "发电工单重复", "generator", "high", "识别同一运维系统工单号重复出现在发电费台账。", "核实同一工单是否重复报账"),
    "generator_duration_mismatch": RuleMetadata("generator_duration_mismatch", "发电时长与起止时间不一致", "generator", "medium", "比较发电开始/结束时间计算时长与填报发电时长。", "核对发电开始时间、结束时间和填报时长"),
    "fee_amount_period_spike": RuleMetadata("fee_amount_period_spike", "费用金额环比突变", "all", "high", "识别同站址同费用字段相邻账期金额突增或突降。", "核对账期费用、调账冲销和录入口径"),
    "fee_paid_without_master_site": RuleMetadata("fee_paid_without_master_site", "无站址仍支付费用", "all", "high", "识别站址台账不存在但租费、电费或发电费仍有正向费用支出的记录。", "暂停支付并核实站址主数据、费用依据和报账归属"),
    "missing_site_code_duplicate_name": RuleMetadata("missing_site_code_duplicate_name", "站址编码缺失且名称重复", "site", "high", "识别站址编码为空但同名站址重复出现。", "补充站址编码并核对是否重复建档", "data_quality"),
    "tower_charged_after_stop_period": RuleMetadata("tower_charged_after_stop_period", "停租后跨账期持续计费", "tower_rent", "high", "检查账期晚于停租月份但仍产生费用。", "核对停租日期、账期和计费终止口径"),
    "electricity_price_city_supply_outlier": RuleMetadata("electricity_price_city_supply_outlier", "同区县供电方式电价偏离", "electricity", "medium", "比较同地市区县同供电方式电费单价与中位数偏差。", "核实电价依据、供电方式和转供电合同"),
    "generator_cost_per_hour_outlier": RuleMetadata("generator_cost_per_hour_outlier", "发电小时单价异常", "generator", "high", "检查发电金额折算小时单价是否明显偏高。", "核实发电时长、金额和结算标准"),
}
