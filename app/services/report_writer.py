"""报告正文生成（7 部分骨架，PRD 6.1）。

所有结论性数字均来自传入的已确认数据；外部行业数据缺失时写"待补资料"，
不编造、不留空话。生成结果是与前端无关的结构化 block 列表。
"""

from __future__ import annotations

from statistics import pstdev
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


def _pp(delta: float | None) -> str:
    """比率差转换为百分点表述。"""
    if delta is None:
        return "—"
    return f"{delta * 100:+.2f} 个百分点"


def _avg_metric(series: list[dict[str, Any]], code: str, n: int = 5, derived: bool = False) -> float | None:
    items = series[-n:]
    key = "derived" if derived else "values"
    return _avg([item[key].get(code) for item in items])


def _endpoints(series: list[dict[str, Any]], code: str, n: int = 5, derived: bool = False) -> tuple[float | None, float | None]:
    """返回观察窗口内首末两期数值（升序序列）。"""
    items = series[-n:]
    if not items:
        return None, None
    key = "derived" if derived else "values"
    return items[0][key].get(code), items[-1][key].get(code)


def _expense_ratio(item: dict[str, Any], code: str) -> float | None:
    revenue = item["values"].get("revenue")
    value = item["values"].get(code)
    if value is None or not revenue:
        return None
    return value / revenue


def _dupont(item: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """ROE 三因子拆解：净利率 × 总资产周转率 × 权益乘数。"""
    revenue = item["values"].get("revenue")
    assets = item["values"].get("total_assets")
    equity = item["values"].get("total_equity")
    profit = item["values"].get("net_profit_deducted")
    net_margin = profit / revenue if profit is not None and revenue else None
    turnover = revenue / assets if revenue and assets else None
    multiplier = assets / equity if assets and equity else None
    return net_margin, turnover, multiplier


def _rel_change(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start == 0:
        return None
    return end / start - 1




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

    # 增长质量：利润增速与收入增速的对比，判断规模扩张是否带来盈利改善
    rev_cagr = _cagr_value(annual, "revenue")
    np_cagr = _cagr_value(annual, "net_profit_deducted")
    growth_quality = "收入与利润的复合增速因数据窗口不足暂不评价。"
    if rev_cagr is not None and np_cagr is not None:
        if np_cagr > rev_cagr + 0.01:
            growth_quality = (
                f"扣非净利润复合增速高出收入约 {(np_cagr - rev_cagr) * 100:.2f} 个百分点，"
                f"说明规模扩张的同时盈利能力在改善（产品结构升级、费用摊薄或成本传导更有效）。"
            )
        elif np_cagr < rev_cagr - 0.01:
            growth_quality = (
                f"扣非净利润复合增速落后收入约 {(rev_cagr - np_cagr) * 100:.2f} 个百分点，"
                f"增收未能同步增利，需要从成本、费用和竞争价格三方面找原因。"
            )
        else:
            growth_quality = "扣非净利润与收入基本同步增长，增长质量平稳，利润没有明显被侵蚀。"

    roe_3y = _avg_metric(annual, "roe_deducted", 3, derived=True)
    conv_5y = _avg_metric(annual, "ocf_to_net_profit", 5, derived=True)
    cash_quality = "现金转化数据不足，暂不评价收益质量。"
    if cash_conv is not None and conv_5y is not None:
        if cash_conv >= 1:
            cash_quality = (
                f"最新现金转化率 {cash_conv:.2f}（近五年均值 {conv_5y:.2f}），"
                f"利润基本被经营现金流覆盖，说明收入确认较为扎实、应收与存货未过度占用资金。"
            )
        else:
            cash_quality = (
                f"最新现金转化率仅 {cash_conv:.2f}（近五年均值 {conv_5y:.2f}），"
                f"利润的现金含量偏低，需要关注应收账款回收与存货积压。"
            )

    part1 = [
        _paragraph(
            f"{name}（{company['market']}·{company['code'] or '无代码'}，{company['industry'] or '行业未标注'}）"
            f"截至 {latest['period']['label']}，营业收入 {_yi(lv.get('revenue'))}，"
            f"扣非净利润 {_yi(lv.get('net_profit_deducted'))}，扣非 ROE {_pct(roe)}，毛利率 {_pct(gm)}。",
            ["revenue", "net_profit_deducted"],
        ),
        _paragraph(_growth_phrase(annual, "revenue") + "；" + _growth_phrase(annual, "net_profit_deducted", "扣非净利润") + "。",
                   ["revenue", "net_profit_deducted"]),
        _paragraph(f"增长质量：{growth_quality}", ["revenue", "net_profit_deducted"]),
        _paragraph(
            f"回报与现金：扣非 ROE {_pct(roe)}（近三年均值 {_pct(roe_3y)}）。{cash_quality}",
            ["roe_deducted", "ocf_to_net_profit"],
        ),
        _paragraph(f"观点：{judgement}。", ["roe_deducted", "gross_margin"]),
    ]

    # 估值对照：把 DCF 每股价值与市值隐含股价并列，给出折溢价解释
    if valuation is not None and valuation["market_cap"] and valuation["shares"] and valuation["per_share_value"]:
        implied_price = valuation["market_cap"] / valuation["shares"]
        gap = valuation["per_share_value"] / implied_price - 1
        direction = "高于" if gap > 0 else "低于"
        part1.append(_paragraph(
            f"估值对照：按总市值 {valuation['market_cap'] / 1e8:,.2f} 亿元与总股本 "
            f"{valuation['shares'] / 1e8:,.2f} 亿股折算，市场隐含股价约 {implied_price:,.2f} 元；"
            f"DCF 每股价值 {valuation['per_share_value']:,.2f} 元，{direction}市场价 {abs(gap) * 100:.2f}%。"
            f"这一差异反映的是模型对长期自由现金流增速（前五年 10%、后五年 5%）与折现率 10% 的假设，"
            f"并不代表短期股价判断；若实际增速低于假设，安全边际会被明显压缩。",
            ["ocf", "capex"],
        ))
    part1.append(_note("本部分为观点性判断，需在版本评审时逐条确认；估值结论见页面估值卡片，不在正文复制数字。"))
    parts.append({"code": "part1", "title": "Part 1 企业印象", "blocks": part1,
                  "cited": ["revenue", "net_profit_deducted", "roe_deducted", "gross_margin"]})

    # ---------------- Part 2 基本经营情况 ----------------
    cols = ["指标"] + [item["period"]["label"] for item in recent_desc]
    rows = []
    for label, code in (("营业收入", "revenue"), ("扣非净利润", "net_profit_deducted"),
                        ("归母净利润", "net_profit_parent"), ("经营净现金流", "ocf")):
        rows.append([label] + [_yi(item["values"].get(code)) for item in recent_desc])
    rows.append(["营业收入同比"] + [_pct(item["derived"].get("revenue_yoy")) for item in recent_desc])
    yoy_values = [i["derived"].get("revenue_yoy") for i in recent if i["derived"].get("revenue_yoy") is not None]
    yoy_vol = pstdev(yoy_values) if len(yoy_values) >= 2 else None
    recent_cagr = _cagr_value(recent, "revenue")
    latest_yoy = latest["derived"].get("revenue_yoy")
    if yoy_vol is None:
        rhythm = "历史年度样本不足，增长波动暂不评价。"
    else:
        level = "低" if yoy_vol < 0.05 else ("中等" if yoy_vol < 0.15 else "高")
        rhythm = (
            f"近五个年度收入复合增速 {_pct(recent_cagr)}，最新一年同比 {_pct(latest_yoy)}，"
            f"年度同比标准差 {yoy_vol * 100:.2f} 个百分点，增长波动{level}。"
        )
        if recent_cagr is not None and latest_yoy is not None and latest_yoy > recent_cagr:
            rhythm += "最新增速高于五年均值，说明增长在加速而非失速。"
        elif recent_cagr is not None and latest_yoy is not None and latest_yoy < recent_cagr:
            rhythm += "最新增速低于五年均值，需确认是基数效应还是需求走弱。"
    part2_blocks = [
        _paragraph("结论先行：先看规模与增长，再看成本结构与分部结构；所有数字均来自已确认指标值。", []),
        {"type": "heading", "text": "2.1 基础经营指标"},
        _table("近 5 年主要经营指标", cols, rows, "金额单位：亿元；同比由相邻年度计算"),
        _paragraph(f"增长节奏：{rhythm}", ["revenue"]),
    ]

    cost_rows = []
    for label, code in (("营业成本", "operating_cost"), ("销售费用", "selling_expense"),
                        ("管理费用", "admin_expense"), ("研发费用", "rnd_expense"),
                        ("财务费用", "finance_expense")):
        cost_rows.append([label] + [_yi(item["values"].get(code)) for item in recent_desc])
    cost_rows.append(["毛利率"] + [_pct(item["derived"].get("gross_margin")) for item in recent_desc])
    base_item = recent[0]
    gm_start, gm_end = base_item["derived"].get("gross_margin"), ld.get("gross_margin")
    sell_start, sell_end = _expense_ratio(base_item, "selling_expense"), _expense_ratio(latest, "selling_expense")
    admin_start, admin_end = _expense_ratio(base_item, "admin_expense"), _expense_ratio(latest, "admin_expense")
    rnd_start, rnd_end = _expense_ratio(base_item, "rnd_expense"), _expense_ratio(latest, "rnd_expense")
    cost_parts = [
        f"毛利率由 {_pct(gm_start)} 变为 {_pct(gm_end)}（{_pp((gm_end - gm_start) if gm_start is not None and gm_end is not None else None)}）",
        f"销售费用率 {_pct(sell_end)}（{_pp((sell_end - sell_start) if sell_start is not None and sell_end is not None else None)}）",
        f"管理费用率 {_pct(admin_end)}（{_pp((admin_end - admin_start) if admin_start is not None and admin_end is not None else None)}）",
        f"研发费用率 {_pct(rnd_end)}（{_pp((rnd_end - rnd_start) if rnd_start is not None and rnd_end is not None else None)}）",
    ]
    cost_note = "；".join(cost_parts) + "。"
    expense_delta = None
    if None not in (sell_start, sell_end, admin_start, admin_end, rnd_start, rnd_end):
        expense_delta = (sell_end - sell_start) + (admin_end - admin_start) + (rnd_end - rnd_start)
    if gm_start is not None and gm_end is not None and gm_end > gm_start:
        if expense_delta is not None and expense_delta < 0:
            cost_note += "毛利率抬升且三项费用率合计下降，利润空间被双重打开，成本与效率改善都成立。"
        elif expense_delta is not None and gm_end - gm_start > expense_delta:
            cost_note += "毛利率提升幅度大于三项费用率合计升幅，成本改善仍有净贡献，但费用扩张开始侵蚀部分成果。"
        else:
            cost_note += "毛利率虽提升，但费用率上升更快，净利率改善有限，需要警惕费用投放效率。"
    elif gm_start is not None and gm_end is not None and gm_end < gm_start:
        cost_note += "毛利率下滑，若同时费用率上升，则利润空间被两头挤压，需要在 Part 7 跟踪。"
    if lv.get("finance_expense") is not None and lv["finance_expense"] < 0:
        cost_note += f"财务费用为 {_yi(lv['finance_expense'])}，利息收入覆盖利息支出，账上现金较为充裕。"
    part2_blocks += [
        {"type": "heading", "text": "2.2 成本结构"},
        _table("成本结构与毛利率", cols, cost_rows),
        _paragraph(f"成本与费用：{cost_note}", ["operating_cost", "selling_expense", "admin_expense",
                                                "rnd_expense", "finance_expense", "gross_margin"]),
    ]

    ratio_rows = []
    for label, code in (("扣非 ROE", "roe_deducted"), ("扣非净利率", "net_margin_deducted"),
                        ("经营净现金流 ÷ 净利润", "ocf_to_net_profit")):
        ratio_rows.append([label] + [_pct(item["derived"].get(code)) for item in recent_desc])
    nm0, at0, em0 = _dupont(base_item)
    nm1, at1, em1 = _dupont(latest)
    roe_start = base_item["derived"].get("roe_deducted")
    roe_change = _rel_change(roe_start, roe)
    dupont_text = (
        f"杜邦拆解：扣非净利率 {_pct(nm0)} → {_pct(nm1)}，总资产周转率 "
        f"{at0:.2f} → {at1:.2f} 次，权益乘数 {em0:.2f} → {em1:.2f} 倍。"
        if None not in (nm0, at0, em0, nm1, at1, em1)
        else "杜邦三因子数据不完整，暂不做拆解。"
    )
    if None not in (nm0, at0, em0, nm1, at1, em1):
        changes = {
            "净利率": _rel_change(nm0, nm1),
            "总资产周转率": _rel_change(at0, at1),
            "权益乘数": _rel_change(em0, em1),
        }
        usable = {k: v for k, v in changes.items() if v is not None}
        if usable:
            driver = max(usable, key=lambda k: abs(usable[k]))
            dupont_text += f"其中变化幅度最大的是{driver}（{usable[driver] * 100:+.2f}%），"
            if roe_change is not None and usable[driver] * roe_change > 0:
                dupont_text += "方向与 ROE 一致，是本期回报变化的主要推手。"
            else:
                dupont_text += "但其影响被其余因子的反向变化抵消，ROE 最终未随其改善。"
    if roe_change is not None:
        direction = "提升" if roe_change > 0 else "下降"
        dupont_text += f"扣非 ROE 由 {_pct(roe_start)} {direction}至 {_pct(roe)}（{_pp(roe - roe_start if roe is not None and roe_start is not None else None)}）。"
        if roe_change < -0.1:
            dupont_text += "回报下滑超过一成，需要在 Part 6/7 判断是周期扰动还是竞争力削弱。"
    part2_blocks += [
        {"type": "heading", "text": "2.3 毛利率与扣非 ROE 走势"},
        _table("回报与利润率", cols, ratio_rows),
        _paragraph(
            f"最新一期毛利率 {_pct(gm)}，扣非净利率 {_pct(ld.get('net_margin_deducted'))}，"
            f"扣非 ROE {_pct(roe)}，经营净现金流÷净利润 {cash_conv:.2f}。" if cash_conv is not None else
            f"最新一期毛利率 {_pct(gm)}，扣非净利率 {_pct(ld.get('net_margin_deducted'))}，扣非 ROE {_pct(roe)}。",
            ["gross_margin", "net_margin_deducted", "roe_deducted"],
        ),
        _paragraph(dupont_text, ["roe_deducted", "net_margin_deducted", "revenue", "total_assets", "total_equity"]),
    ]

    q_recent = quarterly[-4:]
    q_recent_desc = list(reversed(q_recent))
    q_cols = ["指标"] + [item["period"]["label"] for item in q_recent_desc]
    q_rows = [
        ["营业收入（累计）"] + [_yi(item["values"].get("revenue")) for item in q_recent_desc],
        ["经营净现金流（累计）"] + [_yi(item["values"].get("ocf")) for item in q_recent_desc],
        ["扣非净利润（累计）"] + [_yi(item["values"].get("net_profit_deducted")) for item in q_recent_desc],
    ]
    q_by_key = {(item["period"]["type"], item["period"]["year"]): item for item in quarterly}
    quarterly_note = ""
    if q_recent:
        q_latest = quarterly[-1]
        q_prev = q_by_key.get((q_latest["period"]["type"], q_latest["period"]["year"] - 1))
        if q_prev:
            def _q_yoy(code: str) -> str:
                cur = q_latest["values"].get(code)
                old = q_prev["values"].get(code)
                if cur is None or not old:
                    return "—"
                return _pct(cur / old - 1)

            rev_q, np_q, ocf_q = _q_yoy("revenue"), _q_yoy("net_profit_deducted"), _q_yoy("ocf")
            quarterly_note = (
                f"季度节奏：{q_latest['period']['label']} 累计营业收入同比 {rev_q}，"
                f"扣非净利润同比 {np_q}，经营净现金流同比 {ocf_q}（对比 {q_prev['period']['label']}）。"
            )
            cur_np, old_np = q_latest["values"].get("net_profit_deducted"), q_prev["values"].get("net_profit_deducted")
            cur_rev, old_rev = q_latest["values"].get("revenue"), q_prev["values"].get("revenue")
            if cur_np and old_np and cur_rev and old_rev and (cur_np / old_np) > (cur_rev / old_rev):
                quarterly_note += "利润累计同比快于收入，年内盈利能力在改善。"
            elif cur_np and old_np and cur_rev and old_rev:
                quarterly_note += "利润累计同比慢于收入，年内盈利承压，需在下一期确认是否延续。"
    part2_blocks += [
        {"type": "heading", "text": "2.4 季度节奏"},
        _table("最近四期累计经营数据", q_cols, q_rows,
               "季报/半年报披露的是累计值；单季值需由差值计算，本版不自动计算。") if q_recent
        else _note("库内暂无季度材料，季度节奏待补充半年报/季报后生成。"),
    ]
    if quarterly_note:
        part2_blocks.append(_paragraph(quarterly_note, ["revenue", "net_profit_deducted", "ocf"]))

    seg_latest = [s for s in segments if s["year"] == latest["period"]["year"]]
    product_seg = [s for s in seg_latest if s["dimension_type"] == "product" and s["metric_code"] == "segment_revenue"]
    region_seg = [s for s in seg_latest if s["dimension_type"] == "region" and s["metric_code"] == "segment_revenue"]
    if product_seg:
        total = lv.get("revenue") or sum(s["value"] for s in product_seg)
        prod_rows = []
        ordered_products = sorted(product_seg, key=lambda x: -x["value"])
        for s in ordered_products:
            margin = next((x["value"] for x in seg_latest
                           if x["metric_code"] == "segment_gross_margin" and x["name"] == s["name"]), None)
            prod_rows.append([s["name"], _yi(s["value"]), _pct(s["value"] / total if total else None), _pct(margin)])
        part2_blocks += [
            {"type": "heading", "text": "2.5 主要产品销售结构"},
            _table(f"{latest['period']['label']} 分产品收入", ["产品", "收入", "占比", "毛利率"], prod_rows),
        ]

        # 分部结构解读：集中度、毛利率差异与两年增速
        def _seg_rev(year: int, seg_name: str) -> float | None:
            return next((x["value"] for x in segments
                         if x["year"] == year and x["dimension_type"] == "product"
                         and x["name"] == seg_name and x["metric_code"] == "segment_revenue"), None)

        top = ordered_products[:2]
        if len(top) >= 2 and total:
            share2 = (top[0]["value"] + top[1]["value"]) / total
            m1 = next((x["value"] for x in seg_latest if x["metric_code"] == "segment_gross_margin"
                       and x["name"] == top[0]["name"]), None)
            m2 = next((x["value"] for x in seg_latest if x["metric_code"] == "segment_gross_margin"
                       and x["name"] == top[1]["name"]), None)
            seg_text = (
                f"结构解读：{top[0]['name']} 与 {top[1]['name']} 合计占营业收入 {_pct(share2)}，"
                f"毛利率分别为 {_pct(m1)} 与 {_pct(m2)}。"
            )
            if m1 is not None and m2 is not None and abs(m1 - m2) > 0.03:
                high, low = (top[0], top[1]) if m1 > m2 else (top[1], top[0])
                seg_text += (
                    f"「{high['name']}」毛利率更高，是当前利润的压舱石；"
                    f"「{low['name']}」以更低的毛利率换取规模，属于第二曲线，"
                    f"其毛利率能否随规模提升是观察重点。"
                )
            prev_year = latest["period"]["year"] - 1
            g1, g2 = _seg_rev(prev_year, top[0]["name"]), _seg_rev(prev_year, top[1]["name"])
            if g1 and g2:
                cagr1 = top[0]["value"] / g1 - 1
                cagr2 = top[1]["value"] / g2 - 1
                seg_text += (
                    f"最新一年同比增速：{top[0]['name']} {_pct(cagr1)}，{top[1]['name']} {_pct(cagr2)}；"
                    + ("第二曲线扩张更快，收入结构正在从单一业务向多元业务迁移，"
                       "这对估值的影响是降低了单一品类的周期暴露，但也拉低了整体毛利率。"
                       if cagr2 > cagr1 else
                       "核心业务仍是增长主引擎，第二曲线尚未形成足够的增量贡献。")
                )
            part2_blocks.append(_paragraph(seg_text, ["segment_revenue", "segment_gross_margin", "revenue"]))
    else:
        part2_blocks += [{"type": "heading", "text": "2.5 主要产品销售结构"},
                         _note("库内暂无分产品数据，需在导入材料中补充「分部数据」工作表。")]

    if region_seg:
        total = lv.get("revenue") or sum(s["value"] for s in region_seg)
        region_rows = []
        ordered_regions = sorted(region_seg, key=lambda x: -x["value"])
        for s in ordered_regions:
            margin = next((x["value"] for x in seg_latest
                           if x["metric_code"] == "segment_gross_margin" and x["name"] == s["name"]), None)
            region_rows.append([s["name"], _yi(s["value"]), _pct(s["value"] / total if total else None), _pct(margin)])
        part2_blocks += [
            {"type": "heading", "text": "2.6 营收区域结构"},
            _table(f"{latest['period']['label']} 分区域收入", ["区域", "收入", "占比", "毛利率"], region_rows),
        ]
        if len(ordered_regions) >= 2:
            r1, r2 = ordered_regions[0], ordered_regions[1]
            m1 = next((x["value"] for x in seg_latest if x["metric_code"] == "segment_gross_margin"
                       and x["name"] == r1["name"]), None)
            m2 = next((x["value"] for x in seg_latest if x["metric_code"] == "segment_gross_margin"
                       and x["name"] == r2["name"]), None)
            overseas = next((r for r in ordered_regions if any(k in r["name"] for k in ("海外", "境外", "国外"))), None)
            region_text = (
                f"区域解读：{r1['name']} 收入 {_yi(r1['value'])}（占比 {_pct(r1['value'] / total if total else None)}，"
                f"毛利率 {_pct(m1)}），{r2['name']} 收入 {_yi(r2['value'])}"
                f"（占比 {_pct(r2['value'] / total if total else None)}，毛利率 {_pct(m2)}）。"
            )
            if m1 is not None and m2 is not None:
                if abs(m1 - m2) < 0.01:
                    region_text += (
                        "两区域毛利率接近，说明海外业务并非依靠低价换份额，"
                        "关税、海运与汇率是比产品定价更主要的边际变量。"
                    )
                elif m2 > m1:
                    region_text += "次区域毛利率更高，外销产品结构与议价能力好于预期，海外扩张对利润有正贡献。"
                else:
                    region_text += "外销毛利率偏低，更多依赖性价比与规模，汇率和关税波动会放大利润波动。"
            part2_blocks.append(_paragraph(region_text, ["segment_revenue", "segment_gross_margin", "revenue"]))
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
    # 外部行业数据缺失时，用财务结构反推经营模式，避免本节只有一句待补资料
    rev_l = lv.get("revenue")
    if rev_l:
        inv_ratio = (lv.get("inventory") or 0) / rev_l
        rec_ratio = (lv.get("receivables") or 0) / rev_l
        contract_ratio = (lv.get("contract_liabilities") or 0) / rev_l
        fixed_ratio = (lv.get("fixed_assets") or 0) / (lv.get("total_assets") or 1)
        goodwill_ratio = (lv.get("goodwill") or 0) / (lv.get("total_equity") or 1)
        part3_blocks.append(_paragraph(
            f"从财务结构反推经营模式：存货占营业收入 {_pct(inv_ratio)}，应收款占营业收入 {_pct(rec_ratio)}，"
            f"合同负债占营业收入 {_pct(contract_ratio)}，固定资产占总资产 {_pct(fixed_ratio)}，"
            f"商誉占净资产 {_pct(goodwill_ratio)}。"
            + (
                "存货与应收合计占收入比重较高，说明规模扩张需要占用较多营运资本，"
                "渠道与库存管理直接决定现金流质量。"
                if inv_ratio + rec_ratio > 0.25 else
                "存货与应收占收入比重不高，营运资本占用相对轻，增长对现金的消耗有限。"
            )
            + (
                "合同负债占收入比重较高，反映下游先款后货或经销商预付款能力强，"
                "是产业链话语权的直接体现。"
                if contract_ratio > 0.1 else
                "合同负债占比不高，对下游的占款能力一般。"
            )
            + (
                "商誉占净资产比重较高，并购形成的资产较大，需在 Part 5 关注减值风险。"
                if goodwill_ratio > 0.15 else
                "商誉占净资产比重可控，并购减值对净资产的直接冲击有限。"
            ),
            ["inventory", "receivables", "contract_liabilities", "fixed_assets", "goodwill", "total_equity"],
        ))
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
        ordered = sorted(product_seg, key=lambda x: -x["value"])
        top = ordered[0]
        top_share = top["value"] / lv["revenue"] if lv.get("revenue") else None
        part4_blocks.append(_paragraph(
            f"收入占比最高的分部为「{top['name']}」，收入 {_yi(top['value'])}，"
            f"占营业收入 {_pct(top_share)}。"
            + (
                "收入高度依赖单一业务，该业务的周期与竞争变化会直接决定整体表现，"
                "第二曲线的意义在于对冲这一集中度风险。"
                if top_share is not None and top_share > 0.6 else
                "收入结构相对分散，单一业务波动对整体的冲击有限，但也意味着没有一条业务能独占利润。"
            ),
            ["segment_revenue", "revenue"],
        ))
    rnd_ratio = None
    if lv.get("rnd_expense") and lv.get("revenue"):
        rnd_ratio = lv["rnd_expense"] / lv["revenue"]
    part4_blocks.append(_paragraph(
        f"研发费用率 {_pct(rnd_ratio)}，较 {base_item['period']['label']} 的 {_pct(rnd_start)} "
        f"变化 {_pp((rnd_end - rnd_start) if rnd_start is not None and rnd_end is not None else None)}。"
        + (
            "研发强度持续抬升，说明公司以产品升级和效率工具维持竞争力，而非单纯依赖价格战；"
            "这部分投入的回报需要在后续毛利率与新品收入中验证。"
            if rnd_start is not None and rnd_end is not None and rnd_end > rnd_start else
            "研发强度基本稳定，投入力度未见明显加码，竞争优势更多依赖规模与渠道。"
        ),
        ["rnd_expense", "revenue"],
    ))

    # 经营效率：产能、并购与营运资本占用
    fixed_start, fixed_end = base_item["values"].get("fixed_assets"), lv.get("fixed_assets")
    goodwill_start, goodwill_end = base_item["values"].get("goodwill"), lv.get("goodwill")
    fixed_growth = _rel_change(fixed_start, fixed_end)
    goodwill_growth = _rel_change(goodwill_start, goodwill_end)
    capex_ratios = [
        i["values"]["capex"] / i["values"]["revenue"]
        for i in recent
        if i["values"].get("capex") is not None and i["values"].get("revenue")
    ]
    capex_avg = _avg(capex_ratios)
    capex_now = (lv["capex"] / lv["revenue"]) if lv.get("capex") is not None and lv.get("revenue") else None
    inventory_days = (
        lv["inventory"] / lv["operating_cost"] * 365
        if lv.get("inventory") is not None and lv.get("operating_cost") else None
    )
    inv_days_start = (
        base_item["values"]["inventory"] / base_item["values"]["operating_cost"] * 365
        if base_item["values"].get("inventory") is not None and base_item["values"].get("operating_cost") else None
    )
    ar_now, ap_now = ld.get("ar_turnover_days"), ld.get("ap_turnover_days")
    ar_start, ap_start = base_item["derived"].get("ar_turnover_days"), base_item["derived"].get("ap_turnover_days")
    cash_cycle_now = (ar_now + inventory_days - ap_now) if None not in (ar_now, inventory_days, ap_now) else None
    cash_cycle_start = (ar_start + inv_days_start - ap_start) if None not in (ar_start, inv_days_start, ap_start) else None
    efficiency = (
        f"经营效率：固定资产由 {_yi(fixed_start)} 变为 {_yi(fixed_end)}（{_pct(fixed_growth)}），"
        f"商誉由 {_yi(goodwill_start)} 变为 {_yi(goodwill_end)}（{_pct(goodwill_growth)}）；"
        f"资本开支占营业收入 {_pct(capex_now)}（近五年均值 {_pct(capex_avg)}）。"
    )
    if inventory_days is not None:
        efficiency += f"存货周转天数 {inventory_days:.0f} 天（五年前 {inv_days_start:.0f} 天）。" if inv_days_start is not None else f"存货周转天数 {inventory_days:.0f} 天。"
    if cash_cycle_now is not None and cash_cycle_start is not None:
        delta = cash_cycle_now - cash_cycle_start
        if cash_cycle_now < 0:
            efficiency += (
                f"现金转换周期（应收 + 存货 − 应付）为 {cash_cycle_now:.0f} 天（五年前 {cash_cycle_start:.0f} 天），"
                "为负值意味着公司依靠占用上下游资金即可覆盖营运资金需求，自身垫资压力很小。"
            )
        elif abs(delta) < 3:
            efficiency += (
                f"现金转换周期 {cash_cycle_now:.0f} 天，与五年前基本持平，营运资金效率稳定。"
            )
        else:
            efficiency += (
                f"现金转换周期由 {cash_cycle_start:.0f} 天变为 {cash_cycle_now:.0f} 天，"
                + ("占用天数缩短，营运资金效率改善。" if delta < 0 else "占用天数拉长，扩张对营运资金的消耗在增加。")
            )
    part4_blocks.append(_paragraph(efficiency, ["fixed_assets", "goodwill", "capex", "inventory",
                                                "receivables", "payables", "ar_turnover_days", "ap_turnover_days"]))
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
    cash_like = (lv.get("cash") or 0) + (lv.get("trading_fin_assets") or 0)
    net_cash = cash_like - (interest_debt or 0)
    current_ratio = (
        lv["current_assets_total"] / lv["current_liabilities_total"]
        if lv.get("current_assets_total") and lv.get("current_liabilities_total") else None
    )
    quick_ratio = (
        (lv["current_assets_total"] - (lv.get("inventory") or 0)) / lv["current_liabilities_total"]
        if lv.get("current_assets_total") and lv.get("current_liabilities_total") else None
    )
    debt_start = base_item["derived"].get("debt_ratio")
    goodwill_ratio_now = (lv.get("goodwill") or 0) / (lv.get("total_equity") or 1)
    solvency = (
        f"偿债能力：货币资金与交易性金融资产合计 {_yi(cash_like)}，扣除有息负债后为"
        f"{'净现金' if net_cash >= 0 else '净负债'} {_yi(abs(net_cash))}；"
        f"流动比率 {current_ratio:.2f}、速动比率 {quick_ratio:.2f}；"
        f"资产负债率由 {base_item['period']['label']} 的 {_pct(debt_start)} 变为 {_pct(ld.get('debt_ratio'))}。"
        if current_ratio is not None and quick_ratio is not None else
        f"偿债能力：货币资金与交易性金融资产合计 {_yi(cash_like)}，有息负债合计 {_yi(interest_debt)}。"
    )
    if net_cash >= 0:
        solvency += "净现金状态意味着公司不依赖外部融资即可维持运营，利率上行周期抗风险能力较强。"
    else:
        solvency += "净负债状态下，利率与再融资条件变化会直接影响利润，需要关注债务期限结构。"
    if ld.get("debt_ratio") is not None and ld["debt_ratio"] > 0.6:
        solvency += "资产负债率高于 60%，杠杆偏高，需结合有息负债占比判断真实压力。"
    solvency += f"商誉占净资产 {_pct(goodwill_ratio_now)}，若发生减值将直接侵蚀净资产与 ROE。"
    part5_blocks.append(_paragraph(solvency, ["cash", "trading_fin_assets", "short_term_borrowings",
                                              "long_term_borrowings", "bonds_payable", "total_equity",
                                              "current_assets_total", "current_liabilities_total", "debt_ratio"]))
    turnover_rows = []
    for label, code in (("应收账款周转天数", "ar_turnover_days"), ("应付账款周转天数", "ap_turnover_days")):
        turnover_rows.append([label] + [
            f"{item['derived'].get(code):.0f}" if item["derived"].get(code) is not None else "—"
            for item in recent_desc
        ])
    turnover_text = ""
    if ar_now is not None and ar_start is not None:
        ar_delta = ar_now - ar_start
        turnover_text = f"周转效率：应收周转天数由 {ar_start:.0f} 天变为 {ar_now:.0f} 天，" + (
            f"回款周期缩短 {abs(ar_delta):.0f} 天，收入向现金的转化在改善。"
            if ar_delta < 0 else
            f"回款周期拉长 {ar_delta:.0f} 天，若收入同时放缓，需警惕以放宽信用换取增长。"
        )
        if ap_now is not None and ap_start is not None:
            ap_delta = ap_now - ap_start
            turnover_text += f"应付周转天数由 {ap_start:.0f} 天变为 {ap_now:.0f} 天，" + (
                "对上游的占款能力增强，有利于缓解自身资金压力。"
                if ap_delta > 0 else
                "对上游占款能力减弱，付款节奏加快会消耗更多现金。"
            )
    short_debt = lv.get("short_term_borrowings") or 0
    long_debt = (lv.get("long_term_borrowings") or 0) + (lv.get("bonds_payable") or 0)
    if short_debt or long_debt:
        short_share = short_debt / (short_debt + long_debt) if (short_debt + long_debt) else None
        turnover_text += (
            f"有息负债结构：短期借款 {_yi(short_debt)}，长期借款与应付债券合计 {_yi(long_debt)}，"
            f"短期占比 {_pct(short_share)}。"
            + ("短期负债占比偏高，再融资滚动压力更依赖银行授信。" if short_share is not None and short_share > 0.6
               else "长短期结构相对均衡，期限错配风险可控。")
        )
    part5_blocks += [
        {"type": "heading", "text": "5.2 周转与有息负债"},
        _table("周转天数", cols, turnover_rows,
               "周转天数 = 平均余额 ÷ 流量 × 365；缺上期余额时以期末余额代替。"),
    ]
    if turnover_text:
        part5_blocks.append(_paragraph(turnover_text, ["receivables", "payables", "ar_turnover_days",
                                                       "ap_turnover_days", "short_term_borrowings",
                                                       "long_term_borrowings", "bonds_payable"]))
    part5_blocks.append(_note("应收账龄结构与债券利率需从年报附注补充，本版未采集。"))
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
    advantage_comment = "综合判断："
    if roe is not None and roe >= 0.15 and cash_conv is not None and cash_conv >= 1:
        advantage_comment += (
            f"最新扣非 ROE {_pct(roe)} 且现金转化率 {cash_conv:.2f}，说明高回报不是靠会计口径堆出来的，"
            "而是有真实现金流支撑，这是最值得重视的一条优势。"
        )
    elif advantages:
        advantage_comment += (
            "上述优势主要由规模和财务结构支撑，尚未看到明显的定价权或技术壁垒证据，"
            "需要在行业对比中进一步确认可持续性。"
        )
    if ld.get("debt_ratio") is not None and ld["debt_ratio"] < 0.4:
        advantage_comment += "低杠杆意味着在需求下行时有加杠杆或逆势投入的空间。"
    advantage_comment += "优势是否成立最终取决于竞争格局，本版缺少外部份额数据，需在评审时补充。"
    part6_blocks = [
        _paragraph("结论先行：以下优势均可由库内数据验证；未验证的判断不得写入本节。", []),
        _bullets(advantages, ["roe_deducted", "ocf_to_net_profit", "gross_margin", "debt_ratio", "revenue"]),
        _paragraph(advantage_comment, ["roe_deducted", "ocf_to_net_profit", "debt_ratio"]),
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
    # 最近一期季报的利润/现金流拐点，比年度数据更及时
    q_profit_yoy = None
    if q_recent:
        q_latest = q_recent[-1]
        q_prev = q_by_key.get((q_latest["period"]["type"], q_latest["period"]["year"] - 1))
        if q_prev and q_prev["values"].get("net_profit_deducted") and q_latest["values"].get("net_profit_deducted") is not None:
            q_profit_yoy = q_latest["values"]["net_profit_deducted"] / q_prev["values"]["net_profit_deducted"] - 1
            if q_profit_yoy < -0.10:
                risks.append(
                    f"季度利润明显下滑：{q_latest['period']['label']} 累计扣非净利润同比 {_pct(q_profit_yoy)}；"
                    f"观察指标为下一期累计同比，阈值 -10%，若持续则年度逻辑需重估。"
                )
    if stability is None or not risks:
        risks.append("未识别出显著财务风险信号；需补充行业、政策与客户集中度信息后再复核。")
    risk_priority = "风险排序："
    if q_profit_yoy is not None and q_profit_yoy < -0.10:
        risk_priority += (
            "当前最紧急的是最近一期季报的利润下滑，它可能意味着年度盈利拐点，"
            "需要先判断是一次性因素（如并购整合、汇兑、减值）还是需求端走弱。"
        )
    elif cash_conv is not None and cash_conv < 0.8:
        risk_priority += "当前最需要跟踪的是现金转化率偏低，它直接决定利润的含金量。"
    elif ld.get("debt_ratio") is not None and ld["debt_ratio"] > 0.6:
        risk_priority += "当前最需要跟踪的是杠杆水平，利率与融资环境变化会放大盈利波动。"
    elif len(annual) >= 2 and annual[-1]["values"].get("revenue") and annual[-2]["values"].get("revenue") \
            and annual[-1]["values"]["revenue"] < annual[-2]["values"]["revenue"]:
        risk_priority += "当前最需要跟踪的是收入下滑是否延续，下一期同比是核心观察指标。"
    elif stability is not None and stability > 0.5:
        risk_priority += "当前最需要跟踪的是自由现金流波动，DCF 基准值对异常年份较为敏感。"
    else:
        risk_priority += (
            "财务层面暂未出现明显预警，最大的不确定性来自外部行业需求、政策与竞争格局，"
            "本版尚未接入这些外部数据，需在评审时补充。"
        )
    risk_priority += "上述风险均给出观察指标与触发阈值，下一版应按最新季度数据逐项复核。"
    part7_blocks = [
        _paragraph("结论先行：以下风险均给出观察指标与触发阈值，便于后续版本跟踪。", []),
        _bullets(risks, ["revenue", "receivables", "ocf_to_net_profit", "debt_ratio", "fcf"]),
        _paragraph(risk_priority, ["revenue", "ocf_to_net_profit", "debt_ratio", "fcf"]),
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
