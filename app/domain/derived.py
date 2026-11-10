"""衍生指标（G7）计算。

规则：衍生指标不落库，按当前有效指标值实时计算，保证改值后立即一致。
所有函数均为纯函数，缺失输入返回 None（不补 0、不估算，PRD 12.5）。
"""

from __future__ import annotations

from statistics import mean, pstdev


def fcf(ocf: float | None, capex: float | None) -> float | None:
    """自由现金流 = 经营净现金流 − 资本开支（口径 A）。"""
    if ocf is None or capex is None:
        return None
    return ocf - capex


def gross_margin(revenue: float | None, cost: float | None) -> float | None:
    if revenue in (None, 0) or cost is None:
        return None
    return (revenue - cost) / revenue


def roe_deducted(np_deducted: float | None, equity: float | None) -> float | None:
    if np_deducted is None or equity in (None, 0):
        return None
    return np_deducted / equity


def net_margin_deducted(np_deducted: float | None, revenue: float | None) -> float | None:
    if np_deducted is None or revenue in (None, 0):
        return None
    return np_deducted / revenue


def ocf_to_profit(ocf: float | None, profit: float | None) -> float | None:
    if ocf is None or profit in (None, 0):
        return None
    return ocf / profit


def turnover_days(balance: float | None, prev_balance: float | None, flow: float | None) -> float | None:
    """周转天数 = 平均余额 ÷ 流量 × 365；无上期时以期末余额代替并仍有意义。"""
    if balance is None or flow in (None, 0):
        return None
    avg = (balance + prev_balance) / 2 if prev_balance is not None else balance
    return avg / flow * 365


def debt_ratio(total_liabilities: float | None, total_assets: float | None) -> float | None:
    if total_liabilities is None or total_assets in (None, 0):
        return None
    return total_liabilities / total_assets


def cagr(first: float | None, last: float | None, years: int) -> float | None:
    """复合增速；首值为负或年限不足时返回 None（不制造无意义增速）。"""
    if first is None or last is None or years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def compute(current: dict[str, float | None], previous: dict[str, float | None] | None = None) -> dict[str, float | None]:
    """计算单个报告期的 G7 衍生指标。"""
    prev = previous or {}
    revenue = current.get("revenue")
    cost = current.get("operating_cost")
    np_ded = current.get("net_profit_deducted")
    profit = current.get("net_profit")
    ocf = current.get("ocf")
    capex = current.get("capex")
    prev_revenue = prev.get("revenue")
    return {
        "revenue_yoy": (revenue / prev_revenue - 1) if revenue is not None and prev_revenue else None,
        "gross_margin": current.get("gross_margin") or gross_margin(revenue, cost),
        "roe_deducted": roe_deducted(np_ded, current.get("total_equity")),
        "net_margin_deducted": net_margin_deducted(np_ded, revenue),
        "ocf_to_net_profit": ocf_to_profit(ocf, profit if profit is not None else np_ded),
        "ar_turnover_days": turnover_days(current.get("receivables"), prev.get("receivables"), revenue),
        "ap_turnover_days": turnover_days(current.get("payables"), prev.get("payables"), cost),
        "debt_ratio": debt_ratio(current.get("total_liabilities"), current.get("total_assets")),
        "fcf": fcf(ocf, capex),
    }


def fcf_stability(fcf_series: list[float]) -> float | None:
    """稳定度 = 标准差 ÷ 均值绝对值；少于 2 个样本返回 None。"""
    if len(fcf_series) < 2:
        return None
    avg = mean(fcf_series)
    denom = abs(avg) if avg != 0 else 1.0
    return pstdev(fcf_series) / denom


def average(values: list[float]) -> float | None:
    return mean(values) if values else None
