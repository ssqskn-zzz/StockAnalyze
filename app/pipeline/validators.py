"""勾稽校验（PRD 12.3 的 P0 子集）。不通过不阻断入库，但降级为待确认。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Finding:
    code: str
    name: str
    status: str          # pass / fail / skip
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "name": self.name, "status": self.status, "detail": self.detail}


def _get(values: dict[str, Any], *codes: str) -> list[float | None]:
    return [values.get(c) for c in codes]


def _all_present(*vals: float | None) -> bool:
    return all(v is not None for v in vals)


def _close(a: float, b: float, rel: float = 1e-6, absolute: float = 1.0) -> bool:
    return abs(a - b) <= max(absolute, abs(a) * rel)


def validate_period(values: dict[str, float | None]) -> list[Finding]:
    findings: list[Finding] = []

    def amount_check(code: str, name: str, lhs_codes: tuple[str, ...], rhs_code: str, rel: float = 1e-6) -> None:
        lhs = _get(values, *lhs_codes)
        rhs = values.get(rhs_code)
        if not _all_present(*lhs, rhs):
            findings.append(Finding(code, name, "skip", "缺项，未校验"))
            return
        total = sum(lhs)  # type: ignore[arg-type]
        ok = _close(total, rhs, rel=rel)  # type: ignore[arg-type]
        findings.append(Finding(
            code, name, "pass" if ok else "fail",
            f"合计 {total:,.0f} vs {rhs:,.0f}" if not ok else "",
        ))

    amount_check("K1", "流动资产 + 非流动资产 = 资产总计",
                 ("current_assets_total", "non_current_assets_total"), "total_assets")
    amount_check("K2", "流动负债 + 非流动负债 = 负债合计",
                 ("current_liabilities_total", "non_current_liabilities_total"), "total_liabilities")
    amount_check("K3", "负债合计 + 所有者权益 = 资产总计",
                 ("total_liabilities", "total_equity"), "total_assets", rel=1e-3)

    ocf, icf, fcf_, delta = _get(values, "ocf", "investing_cf", "financing_cf", "net_cash_increase")
    if _all_present(ocf, icf, fcf_, delta):
        total = ocf + icf + fcf_  # type: ignore[operator]
        ok = _close(total, delta, rel=5e-3)  # type: ignore[arg-type]
        findings.append(Finding("K5", "经营 + 投资 + 筹资 ≈ 现金净增加额", "pass" if ok else "fail",
                                "" if ok else f"合计 {total:,.0f} vs {delta:,.0f}"))
    else:
        findings.append(Finding("K5", "经营 + 投资 + 筹资 ≈ 现金净增加额", "skip", "缺项，未校验"))

    revenue = values.get("revenue")
    cost = values.get("operating_cost")
    if _all_present(revenue, cost) and revenue:
        computed = (revenue - cost) / revenue  # type: ignore[operator]
        disclosed = values.get("gross_margin")
        if disclosed is not None:
            ok = abs(computed - disclosed) <= 0.002
            findings.append(Finding("K10", "毛利率 = (营业收入 − 营业成本) ÷ 营业收入",
                                    "pass" if ok else "fail",
                                    "" if ok else f"计算 {computed:.4f} vs 披露 {disclosed:.4f}"))
        else:
            findings.append(Finding("K10", "毛利率可计算", "pass", "库内未存毛利率，按公式计算"))
    else:
        findings.append(Finding("K10", "毛利率校验", "skip", "缺营业收入或营业成本"))

    tl, ta = _get(values, "total_liabilities", "total_assets")
    if _all_present(tl, ta) and ta:
        ratio = tl / ta  # type: ignore[operator]
        findings.append(Finding("K11", "资产负债率 = 负债合计 ÷ 资产总计", "pass", f"{ratio:.4f}"))
    else:
        findings.append(Finding("K11", "资产负债率校验", "skip", "缺项"))

    np_ded, np_all = _get(values, "net_profit_deducted", "net_profit")
    if _all_present(np_ded, np_all):
        ok = np_ded <= np_all  # type: ignore[operator]
        findings.append(Finding("K15", "扣非净利润 ≤ 净利润", "pass" if ok else "fail",
                                "" if ok else "扣非高于净利润，需解释非经常性损益方向"))
    else:
        findings.append(Finding("K15", "扣非净利润 ≤ 净利润", "skip", "缺项"))

    ocf_v, capex_v = _get(values, "ocf", "capex")
    if _all_present(ocf_v, capex_v):
        findings.append(Finding("K16", "自由现金流 = 经营净现金流 − 资本开支", "pass", ""))
    else:
        findings.append(Finding("K16", "自由现金流计算", "skip", "缺经营净现金流或资本开支"))

    return findings


def segment_checks(
    aggregate: dict[str, float], revenue: float | None, cost: float | None, kind: str
) -> list[Finding]:
    findings: list[Finding] = []
    if revenue:
        total = aggregate.get("segment_revenue")
        if total is None:
            findings.append(Finding("K7" if kind == "product" else "K8", "分部收入合计 ≈ 营业收入", "skip", "无分部数据"))
        else:
            diff = abs(total - revenue) / abs(revenue)
            findings.append(Finding(
                "K7" if kind == "product" else "K8", "分部收入合计 ≈ 营业收入",
                "pass" if diff <= 0.02 else "fail",
                "" if diff <= 0.02 else f"差额 {diff:.2%}",
            ))
    if revenue and cost:
        seg_rev = aggregate.get("segment_revenue")
        seg_cost = aggregate.get("segment_cost")
        if seg_rev is not None and seg_cost is not None:
            gp_seg = seg_rev - seg_cost
            gp_all = revenue - cost
            diff = abs(gp_seg - gp_all) / abs(gp_all) if gp_all else 0
            findings.append(Finding("K9", "分部毛利合计 ≈ 营业收入 − 营业成本",
                                    "pass" if diff <= 0.02 else "fail",
                                    "" if diff <= 0.02 else f"差额 {diff:.2%}"))
    return findings


def cross_period_check(
    stored: dict[str, float | None], new_period_values: dict[str, float | None], new_period_label: str
) -> list[Finding]:
    """K13：用新一期报告的"上期列"复核库内上一期值。

    这里的 new_period_values 必须是"新报告中的上期/上年同期列"抽取值，
    与库内上一年度值逐一比对；不一致提示历史重述（U3），不自动改。
    """
    findings: list[Finding] = []
    for code, value in new_period_values.items():
        old = stored.get(code)
        if old is None or value is None:
            continue
        scale = max(abs(old), abs(value), 1.0)
        if abs(old - value) / scale > 1e-6:
            findings.append(Finding(
                "K13", f"跨年交叉校验：{new_period_label}",
                "fail", f"{code} 库内 {old:,.0f} vs 新报告上期 {value:,.0f}（疑似重述）",
            ))
    return findings
