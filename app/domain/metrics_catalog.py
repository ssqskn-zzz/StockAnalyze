"""指标字典（G1–G7）。

指标是受控条目：抽取、报告、估值只能引用这里定义的 code。
aliases 用于识别年报/导入表中的原始行名，匹配前会做归一化（去空白、去"其中："等前缀）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricDef:
    code: str
    name: str
    group_code: str
    unit_type: str                 # amount / ratio / days / volume
    definition: str
    dimension_type: str = "none"   # none / product / region / channel
    is_valuation_required: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)


GROUP_TITLES = {
    "G1": "G1 主要经营指标",
    "G2": "G2 成本结构",
    "G3": "G3 资产负债简表",
    "G4": "G4 现金流量",
    "G5": "G5 分部数据",
    "G6": "G6 实物量",
    "G7": "G7 衍生指标",
}

METRICS: tuple[MetricDef, ...] = (
    # G1 主要经营指标
    MetricDef("revenue", "营业收入", "G1", "amount", "合并利润表营业总收入（不含营业外收入）", aliases=("营业总收入", "营业收入", "主营业务收入", "revenue", "totalrevenue")),
    MetricDef("net_profit_deducted", "扣非净利润", "G1", "amount", "扣除非经常性损益后归属于母公司股东的净利润", aliases=("扣除非经常性损益后的归属于母公司股东的净利润", "扣除非经常性损益后归属于母公司股东的净利润", "扣非净利润", "扣除非经常性损益后的净利润", "归属于上市公司股东的扣除非经常性损益的净利润", "归属于上市公司股东的扣除非经常性损益后的净利润")),
    MetricDef("net_profit_parent", "归母净利润", "G1", "amount", "归属于母公司股东的净利润", aliases=("归属于母公司股东的净利润", "归属于母公司所有者的净利润", "归属于上市公司股东的净利润", "归母净利润")),
    MetricDef("net_profit", "净利润", "G1", "amount", "含少数股东损益的净利润", aliases=("净利润", "netincome")),
    MetricDef("ocf", "经营活动现金流量净额", "G1", "amount", "经营活动产生的现金流量净额", is_valuation_required=True, aliases=("经营活动产生的现金流量净额", "经营活动现金流量净额", "经营现金流净额", "netcashfromoperatingactivities")),

    # G2 成本结构
    MetricDef("operating_cost", "营业成本", "G2", "amount", "合并利润表营业成本", aliases=("营业成本",)),
    MetricDef("selling_expense", "销售费用", "G2", "amount", "销售费用", aliases=("销售费用",)),
    MetricDef("admin_expense", "管理费用", "G2", "amount", "管理费用", aliases=("管理费用",)),
    MetricDef("rnd_expense", "研发费用", "G2", "amount", "研发费用", aliases=("研发费用",)),
    MetricDef("finance_expense", "财务费用", "G2", "amount", "财务费用", aliases=("财务费用",)),
    MetricDef("gross_margin", "毛利率", "G2", "ratio", "（营业收入 − 营业成本）÷ 营业收入；存储小数", aliases=("毛利率", "grossmargin")),

    # G3 资产负债简表
    MetricDef("cash", "货币资金", "G3", "amount", "合并资产负债表货币资金", is_valuation_required=True, aliases=("货币资金", "cashandcashequivalents")),
    MetricDef("trading_fin_assets", "交易性金融资产", "G3", "amount", "交易性金融资产（理财等）", aliases=("交易性金融资产",)),
    MetricDef("receivables", "应收款", "G3", "amount", "应收账款 + 应收票据", aliases=("应收账款", "应收票据", "应收款项")),
    MetricDef("prepayments", "预付款项", "G3", "amount", "预付款项", aliases=("预付款项", "预付款")),
    MetricDef("inventory", "存货", "G3", "amount", "存货", aliases=("存货",)),
    MetricDef("current_assets_total", "流动资产合计", "G3", "amount", "流动资产合计", aliases=("流动资产合计",)),
    MetricDef("fixed_assets", "固定资产", "G3", "amount", "固定资产", aliases=("固定资产",)),
    MetricDef("construction_in_progress", "在建工程", "G3", "amount", "在建工程", aliases=("在建工程",)),
    MetricDef("intangible_assets", "无形资产", "G3", "amount", "无形资产", aliases=("无形资产",)),
    MetricDef("development_expenditure", "开发支出", "G3", "amount", "开发支出", aliases=("开发支出",)),
    MetricDef("goodwill", "商誉", "G3", "amount", "商誉", aliases=("商誉",)),
    MetricDef("long_term_equity_invest", "长期股权投资", "G3", "amount", "长期股权投资", aliases=("长期股权投资",)),
    MetricDef("non_current_assets_total", "非流动资产合计", "G3", "amount", "非流动资产合计", aliases=("非流动资产合计",)),
    MetricDef("short_term_borrowings", "短期借款", "G3", "amount", "短期借款", is_valuation_required=True, aliases=("短期借款",)),
    MetricDef("payables", "应付款", "G3", "amount", "应付账款 + 应付票据", aliases=("应付账款", "应付票据", "应付款项")),
    MetricDef("contract_liabilities", "合同负债", "G3", "amount", "合同负债 / 预收款项", aliases=("合同负债", "预收款项")),
    MetricDef("payroll_payable", "应付职工薪酬", "G3", "amount", "应付职工薪酬", aliases=("应付职工薪酬",)),
    MetricDef("taxes_payable", "应交税费", "G3", "amount", "应交税费", aliases=("应交税费", "应交税金")),
    MetricDef("current_portion_lt_debt", "一年内到期的非流动负债", "G3", "amount", "一年内到期的非流动负债", is_valuation_required=True, aliases=("一年内到期的非流动负债",)),
    MetricDef("current_liabilities_total", "流动负债合计", "G3", "amount", "流动负债合计", aliases=("流动负债合计",)),
    MetricDef("long_term_borrowings", "长期借款", "G3", "amount", "长期借款", is_valuation_required=True, aliases=("长期借款",)),
    MetricDef("bonds_payable", "应付债券", "G3", "amount", "应付债券", is_valuation_required=True, aliases=("应付债券",)),
    MetricDef("non_current_liabilities_total", "非流动负债合计", "G3", "amount", "非流动负债合计", aliases=("非流动负债合计",)),
    MetricDef("total_equity", "所有者权益合计", "G3", "amount", "所有者权益（股东权益）合计", aliases=("所有者权益合计", "股东权益合计", "归属于上市公司股东的所有者权益")),
    MetricDef("share_capital", "股本", "G3", "amount", "股本（总股本面值）", is_valuation_required=True, aliases=("股本", "实收资本")),
    MetricDef("total_assets", "资产总计", "G3", "amount", "资产总计", aliases=("资产总计", "资产合计")),
    MetricDef("total_liabilities", "负债合计", "G3", "amount", "负债合计", aliases=("负债合计",)),

    # G4 现金流量
    MetricDef("capex", "资本开支", "G4", "amount", "购建固定资产、无形资产和其他长期资产支付的现金", is_valuation_required=True, aliases=("购建固定资产、无形资产和其他长期资产支付的现金", "购建固定资产无形资产和其他长期资产支付的现金", "资本开支", "资本性支出", "capitalexpenditure")),
    MetricDef("investing_cf", "投资活动现金流量净额", "G4", "amount", "投资活动产生的现金流量净额", aliases=("投资活动产生的现金流量净额", "投资活动现金流量净额")),
    MetricDef("financing_cf", "筹资活动现金流量净额", "G4", "amount", "筹资活动产生的现金流量净额", aliases=("筹资活动产生的现金流量净额", "筹资活动现金流量净额")),
    MetricDef("net_cash_increase", "现金及现金等价物净增加额", "G4", "amount", "现金及现金等价物净增加额", aliases=("现金及现金等价物净增加额", "现金及等价物净增加额")),
    MetricDef("fx_effect", "汇率变动对现金的影响", "G4", "amount", "汇率变动对现金及现金等价物的影响", aliases=("汇率变动对现金及现金等价物的影响", "汇率变动对现金的影响")),

    # G5 分部数据（必须带维度）
    MetricDef("segment_revenue", "分部收入", "G5", "amount", "按产品/区域拆分的营业收入", dimension_type="product", aliases=("分产品收入", "分区域收入", "营业收入")),
    MetricDef("segment_cost", "分部成本", "G5", "amount", "按产品/区域拆分的营业成本", dimension_type="product", aliases=("分产品成本", "分区域成本", "营业成本")),
    MetricDef("segment_gross_margin", "分部毛利率", "G5", "ratio", "分部（收入 − 成本）÷ 收入", dimension_type="product", aliases=("毛利率", "分部毛利率")),

    # G6 实物量
    MetricDef("sales_volume", "销量", "G6", "volume", "年报披露的实物销量（单位随行业）", dimension_type="product", aliases=("销量", "销售量", "产量")),
)

METRIC_BY_CODE = {m.code: m for m in METRICS}
METRIC_BY_NAME = {m.name: m for m in METRICS}

VALUATION_REQUIRED = tuple(m.code for m in METRICS if m.is_valuation_required)


def normalize_label(text: str) -> str:
    """归一化原始行名：去空白、括号、常见前缀与单位后缀。"""
    if text is None:
        return ""
    s = str(text)
    for token in (" ", "\u3000", "\n", "\r", "\t", "：", ":", "（", "）", "(", ")", "其中", "加", "减", "—", "-", "或股东权益", "或股本"):
        s = s.replace(token, "")
    for suffix in ("元", "万元", "亿元", "千元", "百万元"):
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            s = s[: -len(suffix)]
            break
    return s.lower()


_ALIAS_INDEX: dict[str, str] = {}
for _m in METRICS:
    _ALIAS_INDEX[normalize_label(_m.name)] = _m.code
    for _a in _m.aliases:
        _ALIAS_INDEX.setdefault(normalize_label(_a), _m.code)


def match_metric(raw_label: str) -> str | None:
    """把原始行名映射到指标 code；未入字典返回 None（进入候选指标）。"""
    key = normalize_label(raw_label)
    if not key:
        return None
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    # 允许"其中：应收账款"这类前缀被归一化后仍带附加词
    for alias, code in _ALIAS_INDEX.items():
        if alias and key.startswith(alias) and len(key) - len(alias) <= 2:
            return code
    return None
