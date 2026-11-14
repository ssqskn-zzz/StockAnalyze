"""报告正文生成（7 部分骨架，PRD 6.1）。

所有结论性数字均来自传入的已确认数据；外部行业数据缺失时写"待补资料"，
不编造、不留空话。生成结果是与前端无关的结构化 block 列表。
"""

from __future__ import annotations

from typing import Any

from app.domain import derived as dv
from app.utils import fmt_pct


def _yi(value: float | None) -> str:
    if value is None:
        return "未披露"
    return f"{value / 1e8:,.2f} 亿元"


def _pct(value: float | None) -> str:
    return "未披露" if value is None else fmt_pct(value)


def _find(series: list[dict[str, Any]], code: str) -> list[tuple[str, float | None]]:
    out = []
    for item in series:
        out.append((item["period"]["label"], item["values"].get(code)))
    return out


def _latest(series: list[dict[str, Any]]) -> dict[str, Any]:
    return series[-1] if series else {"period": {"label": "—", "year": 0}, "values": {}, "derived": {}}


def _latest_value(series: list[dict[str, Any]], code: str) -> float | None:
    if not series:
        return None
    return series[-1]["values"].get(code)


def _avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _growth_phrase(series: list[dict[str, Any]], code: str, label: str = "营业收入") -> str:
    if len(series) < 2:
        return f"{label}历史数据不足，暂不计算复合增速"
    first, last = series[0]["values"].get(code), series[-1]["values"].get(code)
    years = series[-1]["period"]["year"] - series[0]["period"]["year"]
    rate = dv.cagr(first, last, years)
    if rate is None:
        return f"{label}由 {_yi(first)} 变为 {_yi(last)}，因首末年存在非正值，不计算复合增速"
    return f"{label}由 {series[0]['period']['label']} 的 {_yi(first)} 增至 {series[-1]['period']['label']} 的 {_yi(last)}，复合增速 {_pct(rate)}"


def _table(title: str, columns: list[str], rows: list[list[str]], note: str = "") -> dict[str, Any]:
    return {"type": "table", "title": title, "columns": columns, "rows": rows, "note": note}


def _paragraph(text: str, cite: list[str] | None = None) -> dict[str, Any]:
    return {"type": "paragraph", "text": text, "cite": cite or []}


def _bullets(items: list[str], cite: list[str] | None = None) -> dict[str, Any]:
    return {"type": "bullets", "items": items, "cite": cite or []}


def _note(text: str) -> dict[str, Any]:
    return {"type": "note", "text": text}


def build_content(
    *,
    company: Any,
    annual: list[dict[str, Any]],
    quarterly: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    peers: list[dict[str, Any]],
    valuation: Any | None,
    snapshot_code: str,
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    latest = _latest(annual)
    lv = latest["values"]
    ld = latest["derived"]
    name = company["name"]
    recent = annual[-5:]
    # 展示用倒序：近期在前、远期在后（数据逻辑仍用 chronological 的 recent）
    recent_desc = list(reversed(recent))

    # ---------------- Part 1 企业印象 ----------------
    roe = ld.get("roe_deducted")
    gm = ld.get("gross_margin")
    cash_conv = ld.get("ocf_to_net_profit")
    judgement = "现金流与回报水平一般，需结合行业与竞争格局判断"
    if roe is not None and roe >= 0.15 and cash_conv is not None and cash_conv >= 1:
        judgement = "属于高资本回报、现金创造能力较强的生意，但高回报是否可持续需看 Part 3 竞争格局"
    elif roe is not None and roe < 0.08:
        judgement = "资本回报偏低，需警惕增长依赖资本投入而非内生回报"

    part1 = [
        _paragraph(
            f"{name}（{company['market']}·{company['code'] or '无代码'}，{company['industry'] or '行业未标注'}）"
            f"截至 {latest['period']['label']}，营业收入 {_yi(lv.get('revenue'))}，"
            f"扣非净利润 {_yi(lv.get('net_profit_deducted'))}，扣非 ROE {_pct(roe)}，毛利率 {_pct(gm)}。",
            ["revenue", "net_profit_deducted"],
        ),
        _paragraph(_growth_phrase(annual, "revenue") + "；" + _growth_phrase(annual, "net_profit_deducted", "扣非净利润") + "。",
                   ["revenue", "net_profit_deducted"]),
        _paragraph(f"观点：{judgement}。", ["roe_deducted", "gross_margin"]),
        _note("本部分为观点性判断，需在版本评审时逐条确认；估值结论见页面估值卡片，不在正文复制数字。"),
    ]
    parts.append({"code": "part1", "title": "Part 1 企业印象", "blocks": part1,
                  "cited": ["revenue", "net_profit_deducted", "roe_deducted", "gross_margin"]})

    # ---------------- Part 2 基本经营情况 ----------------
    cols = ["指标"] + [item["period"]["label"] for item in recent_desc]
    rows = []
    for label, code in (("营业收入", "revenue"), ("扣非净利润", "net_profit_deducted"),
                        ("归母净利润", "net_profit_parent"), ("经营净现金流", "ocf")):
        rows.append([label] + [_yi(item["values"].get(code)) for item in recent_desc])
    rows.append(["营业收入同比"] + [_pct(item["derived"].get("revenue_yoy")) for item in recent_desc])
    part2_blocks = [
        _paragraph("结论先行：先看规模与增长，再看成本结构与分部结构；所有数字均来自已确认指标值。", []),
        {"type": "heading", "text": "2.1 基础经营指标"},
        _table("近 5 年主要经营指标", cols, rows, "金额单位：亿元；同比由相邻年度计算"),
    ]

    cost_rows = []
    for label, code in (("营业成本", "operating_cost"), ("销售费用", "selling_expense"),
                        ("管理费用", "admin_expense"), ("研发费用", "rnd_expense"),
                        ("财务费用", "finance_expense")):
        cost_rows.append([label] + [_yi(item["values"].get(code)) for item in recent_desc])
    cost_rows.append(["毛利率"] + [_pct(item["derived"].get("gross_margin")) for item in recent_desc])
    part2_blocks += [
        {"type": "heading", "text": "2.2 成本结构"},
        _table("成本结构与毛利率", cols, cost_rows),
    ]

    ratio_rows = []
    for label, code in (("扣非 ROE", "roe_deducted"), ("扣非净利率", "net_margin_deducted"),
                        ("经营净现金流 ÷ 净利润", "ocf_to_net_profit")):
        ratio_rows.append([label] + [_pct(item["derived"].get(code)) for item in recent_desc])
    part2_blocks += [
        {"type": "heading", "text": "2.3 毛利率与扣非 ROE 走势"},
        _table("回报与利润率", cols, ratio_rows),
        _paragraph(
            f"最新一期毛利率 {_pct(gm)}，扣非净利率 {_pct(ld.get('net_margin_deducted'))}，"
            f"扣非 ROE {_pct(roe)}。若与近三年均值差异明显，需在结论中解释拐点原因。",
            ["gross_margin", "net_margin_deducted", "roe_deducted"],
        ),
    ]

    q_recent = quarterly[-4:]
    q_recent_desc = list(reversed(q_recent))
    q_cols = ["指标"] + [item["period"]["label"] for item in q_recent_desc]
    q_rows = [
        ["营业收入（累计）"] + [_yi(item["values"].get("revenue")) for item in q_recent_desc],
        ["经营净现金流（累计）"] + [_yi(item["values"].get("ocf")) for item in q_recent_desc],
        ["扣非净利润（累计）"] + [_yi(item["values"].get("net_profit_deducted")) for item in q_recent_desc],
    ]
    part2_blocks += [
        {"type": "heading", "text": "2.4 季度节奏"},
        _table("最近四期累计经营数据", q_cols, q_rows,
               "季报/半年报披露的是累计值；单季值需由差值计算，本版不自动计算。") if q_recent
        else _note("库内暂无季度材料，季度节奏待补充半年报/季报后生成。"),
    ]

    seg_latest = [s for s in segments if s["year"] == latest["period"]["year"]]
    product_seg = [s for s in seg_latest if s["dimension_type"] == "product" and s["metric_code"] == "segment_revenue"]
    region_seg = [s for s in seg_latest if s["dimension_type"] == "region" and s["metric_code"] == "segment_revenue"]
    if product_seg:
        total = lv.get("revenue") or sum(s["value"] for s in product_seg)
        prod_rows = []
        for s in sorted(product_seg, key=lambda x: -x["value"]):
            margin = next((x["value"] for x in seg_latest
                           if x["metric_code"] == "segment_gross_margin" and x["name"] == s["name"]), None)
            prod_rows.append([s["name"], _yi(s["value"]), _pct(s["value"] / total if total else None), _pct(margin)])
        part2_blocks += [
            {"type": "heading", "text": "2.5 主要产品销售结构"},
            _table(f"{latest['period']['label']} 分产品收入", ["产品", "收入", "占比", "毛利率"], prod_rows),
        ]
    else:
        part2_blocks += [{"type": "heading", "text": "2.5 主要产品销售结构"},
                         _note("库内暂无分产品数据，需在导入材料中补充「分部数据」工作表。")]

    if region_seg:
        total = lv.get("revenue") or sum(s["value"] for s in region_seg)
        region_rows = []
        for s in sorted(region_seg, key=lambda x: -x["value"]):
            margin = next((x["value"] for x in seg_latest
                           if x["metric_code"] == "segment_gross_margin" and x["name"] == s["name"]), None)
            region_rows.append([s["name"], _yi(s["value"]), _pct(s["value"] / total if total else None), _pct(margin)])
        part2_blocks += [
            {"type": "heading", "text": "2.6 营收区域结构"},
            _table(f"{latest['period']['label']} 分区域收入", ["区域", "收入", "占比", "毛利率"], region_rows),
        ]
    else:
        part2_blocks += [{"type": "heading", "text": "2.6 营收区域结构"},
                         _note("库内暂无分区域数据，需在导入材料中补充「分部数据」工作表。")]

    parts.append({"code": "part2", "title": "Part 2 基本经营情况", "blocks": part2_blocks,
                  "cited": ["revenue", "net_profit_deducted", "net_profit_parent", "ocf",
                            "operating_cost", "selling_expense", "admin_expense", "rnd_expense",
                            "finance_expense", "gross_margin", "roe_deducted", "net_margin_deducted",
                            "ocf_to_net_profit"]})

    # ---------------- Part 3 行业概况 ----------------
    peer_rows = []
    for peer in peers:
        peer_rows.append([
            f"{peer['name']}（{peer['market']}）", peer["period"], _yi(peer["revenue"]),
            _pct(peer["gross_margin"]), _pct(peer["net_margin"]), _pct(peer["roe"]),
            f"{peer['cash_conv']:.2f}" if peer["cash_conv"] is not None else "—",
            _pct(peer["debt_ratio"]), f"{peer['ar_days']:.0f}" if peer["ar_days"] is not None else "—",
        ])
    part3_blocks = [
        _paragraph(
            f"结论先行：行业规模、政策与份额数据需外部资料，本版尚未接入外部行业数据源；"
            f"当前仅能用库内同业企业做横向对比。",
            [],
        )
    ]
    if peer_rows:
        part3_blocks.append(_table(
            "同业财务对比", ["公司", "报告期", "营业收入", "毛利率", "扣非净利率", "扣非ROE",
                           "经营现金流÷净利润", "资产负债率", "应收周转天数"], peer_rows,
            "同业范围：库内与本企业行业标签相同的其他企业；不含外部数据库。",
        ))
    else:
        part3_blocks.append(_note("待补资料：行业现状、行业增长、政策、份额与直接对手清单。"))
    parts.append({"code": "part3", "title": "Part 3 行业概况", "blocks": part3_blocks,
                  "cited": ["revenue", "gross_margin", "net_margin_deducted", "roe_deducted",
                            "ocf_to_net_profit", "debt_ratio", "ar_turnover_days"]})

    # ---------------- Part 4 企业经营 ----------------
    part4_blocks = [
        _paragraph(
            f"{name}的经营结构可从分部数据观察：最新一期共有 {len(product_seg)} 个产品口径、"
            f"{len(region_seg)} 个区域口径披露收入。",
            ["segment_revenue"],
        )
    ]
    if product_seg:
        top = max(product_seg, key=lambda x: x["value"])
        part4_blocks.append(_paragraph(
            f"收入占比最高的分部为「{top['name']}」，收入 {_yi(top['value'])}，"
            f"占营业收入 {_pct(top['value'] / lv['revenue']) if lv.get('revenue') else '未披露'}。"
            f"观点：分部集中度决定了对单一业务周期的暴露程度。",
            ["segment_revenue", "revenue"],
        ))
    rnd_ratio = None
    if lv.get("rnd_expense") and lv.get("revenue"):
        rnd_ratio = lv["rnd_expense"] / lv["revenue"]
    part4_blocks.append(_paragraph(
        f"研发费用率 {_pct(rnd_ratio)}。上下游关系、战略路径与第二曲线属于定性信息，"
        f"需结合年报「管理层讨论与分析」补充，本版未自动生成。",
        ["rnd_expense", "revenue"],
    ))
    part4_blocks.append(_note("待补资料：主要客户/供应商集中度、渠道结构、战略举措原文摘录。"))
    parts.append({"code": "part4", "title": "Part 4 企业经营", "blocks": part4_blocks,
                  "cited": ["segment_revenue", "rnd_expense", "revenue"]})

    # ---------------- Part 5 财务状况 ----------------
    bs_items = [
        ("货币资金", "cash"), ("交易性金融资产", "trading_fin_assets"), ("应收款", "receivables"),
        ("存货", "inventory"), ("流动资产合计", "current_assets_total"),
        ("固定资产", "fixed_assets"), ("在建工程", "construction_in_progress"),
        ("无形资产", "intangible_assets"), ("商誉", "goodwill"),
        ("非流动资产合计", "non_current_assets_total"), ("资产总计", "total_assets"),
        ("短期借款", "short_term_borrowings"), ("应付款", "payables"),
        ("合同负债", "contract_liabilities"), ("流动负债合计", "current_liabilities_total"),
        ("长期借款", "long_term_borrowings"), ("应付债券", "bonds_payable"),
        ("非流动负债合计", "non_current_liabilities_total"), ("负债合计", "total_liabilities"),
        ("所有者权益合计", "total_equity"),
    ]
    bs_rows = [[label] + [_yi(item["values"].get(code)) for item in recent_desc] for label, code in bs_items]
    interest_debt = None
    if lv.get("short_term_borrowings") is not None or lv.get("long_term_borrowings") is not None:
        interest_debt = sum(lv.get(c) or 0 for c in (
            "short_term_borrowings", "current_portion_lt_debt", "long_term_borrowings", "bonds_payable"))
    part5_blocks = [
        _paragraph(
            f"结论先行：资产负债结构以 {_pct(ld.get('debt_ratio'))} 的资产负债率呈现，"
            f"有息负债合计 {_yi(interest_debt)}，货币资金 {_yi(lv.get('cash'))}。",
            ["debt_ratio", "cash"],
        ),
        {"type": "heading", "text": "5.1 资产负债简表"},
        _table("资产负债简表（近 5 年）", cols, bs_rows, "金额单位：亿元"),
    ]
    turnover_rows = []
    for label, code in (("应收账款周转天数", "ar_turnover_days"), ("应付账款周转天数", "ap_turnover_days")):
        turnover_rows.append([label] + [
            f"{item['derived'].get(code):.0f}" if item["derived"].get(code) is not None else "—"
            for item in recent_desc
        ])
    part5_blocks += [
        {"type": "heading", "text": "5.2 周转与有息负债"},
        _table("周转天数", cols, turnover_rows,
               "周转天数 = 平均余额 ÷ 流量 × 365；缺上期余额时以期末余额代替。"),
        _note("应收账龄结构与债券利率需从年报附注补充，本版未采集。"),
    ]
    parts.append({"code": "part5", "title": "Part 5 财务状况", "blocks": part5_blocks,
                  "cited": ["debt_ratio", "cash", "short_term_borrowings", "long_term_borrowings",
                            "bonds_payable", "receivables", "payables", "ar_turnover_days",
                            "ap_turnover_days"]})

    # ---------------- Part 6 企业优势 ----------------
    advantages: list[str] = []
    if roe is not None and roe >= 0.15:
        advantages.append(f"资本回报高：最新扣非 ROE {_pct(roe)}，近三年均值 {_pct(_avg([i['derived'].get('roe_deducted') for i in annual[-3:]]))}。")
    if cash_conv is not None and cash_conv >= 1:
        advantages.append(f"现金转化好：经营净现金流 ÷ 净利润 = {cash_conv:.2f}，利润有现金支撑。")
    if gm is not None and gm >= 0.35:
        advantages.append(f"毛利率 {_pct(gm)}，具备一定定价能力（需与同业对比确认）。")
    if ld.get("debt_ratio") is not None and ld["debt_ratio"] < 0.4:
        advantages.append(f"财务稳健：资产负债率 {_pct(ld['debt_ratio'])}，有息负债压力较小。")
    rev_cagr = _cagr_value(annual, "revenue")
    if rev_cagr is not None and rev_cagr >= 0.10:
        advantages.append(f"成长性：营业收入复合增速 {_pct(rev_cagr)}（区间见 2.1）。")
    if not advantages:
        advantages.append("暂未识别出可用数据验证的显著优势，建议补充行业对比与商业模式资料后重写。")
    part6_blocks = [
        _paragraph("结论先行：以下优势均可由库内数据验证；未验证的判断不得写入本节。", []),
        _bullets(advantages, ["roe_deducted", "ocf_to_net_profit", "gross_margin", "debt_ratio", "revenue"]),
        _note("定性章节：必须逐条复核（仍成立 / 需改写 / 已失效）后才允许发布。"),
    ]
    parts.append({"code": "part6", "title": "Part 6 企业优势", "blocks": part6_blocks,
                  "cited": ["roe_deducted", "ocf_to_net_profit", "gross_margin", "debt_ratio", "revenue"]})

    # ---------------- Part 7 风险点 ----------------
    risks: list[str] = []
    if len(annual) >= 2:
        last_rev = annual[-1]["values"].get("revenue")
        prev_rev = annual[-2]["values"].get("revenue")
        if last_rev is not None and prev_rev and last_rev < prev_rev:
            risks.append(f"收入下滑：最新一期营业收入同比 {_pct(last_rev / prev_rev - 1)}；观察指标为下一期营业收入同比，阈值 0%。")
        last_ar = annual[-1]["values"].get("receivables")
        prev_ar = annual[-2]["values"].get("receivables")
        if last_ar and prev_ar and last_rev and prev_rev and (last_ar / prev_ar) > (last_rev / prev_rev) * 1.1:
            risks.append("应收增速快于收入：观察应收账款周转天数，阈值较近三年均值上升 20%。")
    if cash_conv is not None and cash_conv < 0.8:
        risks.append(f"现金转化偏弱：经营净现金流 ÷ 净利润 = {cash_conv:.2f}；观察指标为该比值，阈值 0.8。")
    if ld.get("debt_ratio") is not None and ld["debt_ratio"] > 0.6:
        risks.append(f"杠杆偏高：资产负债率 {_pct(ld['debt_ratio'])}；观察指标为有息负债/EBITDA，需补数据。")
    fcf_values = [i["derived"].get("fcf") for i in annual[-5:]]
    stability = dv.fcf_stability([v for v in fcf_values if v is not None])
    if stability is not None and stability > 0.5:
        risks.append(f"自由现金流波动大：近 5 年波动度 {stability:.2f}；观察指标为年度 FCF，阈值波动度 0.5。")
    if stability is None or not risks:
        risks.append("未识别出显著财务风险信号；需补充行业、政策与客户集中度信息后再复核。")
    part7_blocks = [
        _paragraph("结论先行：以下风险均给出观察指标与触发阈值，便于后续版本跟踪。", []),
        _bullets(risks, ["revenue", "receivables", "ocf_to_net_profit", "debt_ratio", "fcf"]),
        _note("定性章节：必须逐条复核（仍成立 / 需改写 / 已失效）后才允许发布。"),
    ]
    parts.append({"code": "part7", "title": "Part 7 风险点", "blocks": part7_blocks,
                  "cited": ["revenue", "receivables", "ocf_to_net_profit", "debt_ratio", "fcf"]})

    # 估值引用卡片（不复制数字逻辑，只引用唯一一份估值结果）
    if valuation is not None:
        part1.append({
            "type": "valuation_card",
            "company_id": company["id"],
            "equity_value": valuation["equity_value"],
            "per_share_value": valuation["per_share_value"],
            "updated_at": valuation["updated_at"],
            "snapshot_code": snapshot_code,
        })
    else:
        part1.append(_note("尚无估值结果，结论卡片仅含经营判断。"))

    return parts


def _cagr_value(series: list[dict[str, Any]], code: str) -> float | None:
    if len(series) < 2:
        return None
    years = series[-1]["period"]["year"] - series[0]["period"]["year"]
    return dv.cagr(series[0]["values"].get(code), series[-1]["values"].get(code), years)
