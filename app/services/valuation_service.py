"""DCF 估值（口径 A：股东现金流）。

规则来自 PRD 7：
- FCF = 经营净现金流 − 资本开支；
- 显式期 10 年，g1（1–5 年）、g2（6–10 年）、永续 g；
- 折现率 r 为配置项，来源层级写入版本；
- 金融企业禁用；净现金项缺失禁止出股权价值；基准 FCF 为负或波动过大降级为"仅供参考"。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app import config
from app.db.repositories import audit
from app.db.repositories import metrics as metric_repo
from app.db.repositories import snapshots as snapshot_repo
from app.db.repositories import valuations as valuation_repo
from app.domain import derived as dv
from app.services import metric_service
from app.utils import now_iso


class ValuationError(ValueError):
    pass


BASE_METHOD_YEARS = {"1y": 1, "avg3": 3, "avg5": 5, "avg7": 7}
BASE_METHOD_LABELS = {
    "1y": "最近一年", "avg3": "近 3 年平均", "avg5": "近 5 年平均", "avg7": "近 7 年（或全部可得）平均",
}
R_LEVEL_LABELS = {
    "override": "本版手填", "company": "企业覆盖", "industry": "行业档位",
    "global": "全局默认", "builtin_default": "内置默认",
}
FINANCIAL_TAGS = ("银行", "保险", "证券", "信托", "金融")


def is_financial(company) -> bool:
    if company["is_financial"]:
        return True
    tags = " ".join([
        str(company["industry"] or ""),
        str(company["industry_tags"] or ""),
    ])
    return any(tag in tags for tag in FINANCIAL_TAGS)


def _base_fcf(annual: list[dict[str, Any]], method: str) -> tuple[float | None, list[dict[str, Any]], str]:
    years = BASE_METHOD_YEARS.get(method)
    if not years:
        raise ValuationError(f"未知基准口径：{method}")
    usable = []
    for item in annual:
        ocf = item["values"].get("ocf")
        capex = item["values"].get("capex")
        if ocf is None or capex is None:
            continue
        usable.append({
            "year": item["period"]["label"],
            "ocf": ocf,
            "capex": capex,
            "fcf": ocf - capex,
        })
    if not usable:
        return None, [], "缺少经营净现金流或资本开支，无法计算基准 FCF"
    used = usable[-years:] if years > 1 else usable[-1:]
    note = ""
    if years > 1 and len(used) < years:
        note = f"应有 {years} 年数据，实际可用 {len(used)} 年，按可得年度平均"
    base = sum(u["fcf"] for u in used) / len(used)
    return base, used, note


def _project(base: float, *, r: float, g1: float, g2: float, g: float, n: int) -> tuple[list[dict[str, Any]], float, float]:
    rows = []
    pv_sum = 0.0
    fcf = base
    for year in range(1, n + 1):
        growth = g1 if year <= 5 else g2
        fcf = fcf * (1 + growth)
        factor = 1 / (1 + r) ** year
        pv = fcf * factor
        pv_sum += pv
        rows.append({"year": year, "growth": growth, "fcf": fcf, "factor": factor, "pv": pv})
    terminal_fcf = fcf * (1 + g)
    terminal_value = terminal_fcf / (r - g)
    pv_terminal = terminal_value / (1 + r) ** n
    enterprise_value = pv_sum + pv_terminal
    return rows, enterprise_value, pv_terminal


def _net_cash(values: dict[str, float | None]) -> dict[str, Any]:
    """净现金项：货币资金 + 交易性金融资产 − 有息负债。"""
    cash = values.get("cash")
    trading = values.get("trading_fin_assets")
    debt_codes = {
        "short_term_borrowings": values.get("short_term_borrowings"),
        "current_portion_lt_debt": values.get("current_portion_lt_debt"),
        "long_term_borrowings": values.get("long_term_borrowings"),
        "bonds_payable": values.get("bonds_payable"),
    }
    notes = []
    if trading is None:
        notes.append("交易性金融资产缺失，按 0 处理")
    for code, value in debt_codes.items():
        if value is None:
            notes.append(f"{code} 缺失，按 0 处理")
    core_missing = (
        cash is None
        or debt_codes["short_term_borrowings"] is None
        or debt_codes["long_term_borrowings"] is None
    )
    total_debt = sum(v or 0 for v in debt_codes.values())
    return {
        "cash": cash,
        "trading_fin_assets": trading or 0.0,
        "trading_missing": trading is None,
        "debt_items": debt_codes,
        "interest_bearing_debt": total_debt,
        "notes": notes,
        "equity_allowed": not core_missing,
        "block_reason": "货币资金或核心有息负债缺失，禁止生成股权价值" if core_missing else "",
    }


def _market_cap_stale(market_cap_date: str | None) -> bool:
    if not market_cap_date:
        return False
    try:
        d = datetime.strptime(market_cap_date, "%Y-%m-%d").date()
    except ValueError:
        return True
    return date.today() - d > timedelta(days=config.MARKET_CAP_VALID_DAYS)


def _sensitivity(
    base: float, *, r: float, g1: float, g2: float, g: float, n: int, net_cash_value: float | None, equity_allowed: bool
) -> dict[str, Any]:
    r_values = sorted({round(min(max(r + delta, config.R_MIN), config.R_MAX), 4) for delta in (-0.04, -0.02, 0, 0.02, 0.04)})
    g_values = [0.0, 0.01, 0.02, 0.03, 0.04]
    matrix = []
    for rr in r_values:
        row = []
        for gg in g_values:
            if gg >= rr:
                row.append(None)
                continue
            _, ev, _ = _project(base, r=rr, g1=g1, g2=g2, g=gg, n=n)
            if equity_allowed and net_cash_value is not None:
                row.append(ev + net_cash_value)
            else:
                row.append(ev)
        matrix.append({"r": rr, "values": row})
    return {
        "r_axis": r_values,
        "g_axis": g_values,
        "matrix": matrix,
        "basis": "equity_value" if equity_allowed else "enterprise_value",
    }


def _base_method_comparison(
    annual: list[dict[str, Any]], params: dict[str, Any], net_cash_value: float | None, equity_allowed: bool
) -> list[dict[str, Any]]:
    out = []
    for method in BASE_METHOD_YEARS:
        base, used, _ = _base_fcf(annual, method)
        if base is None:
            continue
        _, ev, _ = _project(base, r=params["r"], g1=params["g1"], g2=params["g2"], g=params["g"], n=params["n"])
        out.append({
            "method": method,
            "label": BASE_METHOD_LABELS[method],
            "base_fcf": base,
            "enterprise_value": ev,
            "equity_value": (ev + net_cash_value) if equity_allowed and net_cash_value is not None else None,
            "years_used": len(used),
        })
    return out


def run_valuation(
    conn,
    company_id: int,
    *,
    scheme: str = "中性",
    base_method: str | None = None,
    g1: float | None = None,
    g2: float | None = None,
    g: float | None = None,
    r_override: float | None = None,
    explicit_years: int | None = None,
    market_cap: float | None = None,
    market_cap_date: str | None = None,
    shares: float | None = None,
    snapshot_id: int | None = None,
    actor: str = "assistant",
) -> dict[str, Any]:
    from app.db.repositories import companies as company_repo

    company = company_repo.get_company(conn, company_id)
    if not company:
        raise ValueError("企业不存在")
    if is_financial(company):
        raise ValuationError("金融企业（银行/保险/证券/信托）不适用自由现金流估值，口径 A 已禁用")

    snapshot = (snapshot_repo.get_snapshot(conn, snapshot_id) if snapshot_id
                else snapshot_repo.latest_snapshot(conn, company_id))
    if not snapshot:
        raise ValueError("尚无数据快照，请先确认指标并生成快照")

    params = {
        "base_method": base_method or config.DCF_DEFAULTS["base_method"],
        "n": int(explicit_years or config.DCF_DEFAULTS["explicit_years"]),
        "g1": config.DCF_DEFAULTS["g1"] if g1 is None else g1,
        "g2": config.DCF_DEFAULTS["g2"] if g2 is None else g2,
        "g": config.DCF_DEFAULTS["g"] if g is None else g,
    }
    if params["base_method"] not in BASE_METHOD_YEARS:
        raise ValuationError(f"未知基准口径：{params['base_method']}")
    if not (5 <= params["n"] <= 15):
        raise ValuationError("显式预测期需在 5–15 年")
    if not (config.G1_MIN <= params["g1"] <= config.G1_MAX):
        raise ValuationError("g1 超出 −20%–40%")
    if params["g2"] > params["g1"]:
        raise ValuationError("g2 不得高于 g1")
    if not (config.G_RANGE[0] <= params["g"] <= config.G_RANGE[1]):
        raise ValuationError("永续增长率需在 0%–5%")
    if params["g2"] < params["g"]:
        raise ValuationError("g2 不得低于永续增长率 g")

    rate_info = valuation_repo.resolve_rate(conn, company)
    if r_override is not None:
        if not (config.R_MIN <= r_override <= config.R_MAX):
            raise ValuationError(f"折现率需在 {config.R_MIN:.0%}–{config.R_MAX:.0%}")
        rate_info = {
            "rate": r_override, "level": "override",
            "config_version": rate_info.get("config_version", 0),
            "config_id": rate_info.get("config_id"), "industry_tag": rate_info.get("industry_tag"),
        }
    r = float(rate_info["rate"])
    if r <= params["g"]:
        raise ValuationError("折现率必须大于永续增长率")
    params["r"] = r
    params["r_source"] = rate_info["level"]
    params["r_config_version"] = rate_info.get("config_version", 0)

    annual = metric_service.snapshot_series(conn, snapshot, "annual")
    if not annual:
        raise ValueError("快照中无年度数据")
    base, used_years, base_note = _base_fcf(annual, params["base_method"])
    if base is None:
        raise ValuationError(base_note)
    params["base_method_note"] = base_note

    projection, enterprise_value, pv_terminal = _project(
        base, r=r, g1=params["g1"], g2=params["g2"], g=params["g"], n=params["n"]
    )

    latest = annual[-1]
    net_cash = _net_cash(latest["values"])
    net_cash_value = None
    equity_value = None
    per_share = None
    if net_cash["equity_allowed"]:
        net_cash_value = (net_cash["cash"] or 0) + net_cash["trading_fin_assets"] - net_cash["interest_bearing_debt"]
        equity_value = enterprise_value + net_cash_value

    share_count = shares
    share_note = ""
    if share_count is None:
        share_cap = latest["values"].get("share_capital")
        if share_cap:
            share_count = share_cap
            share_note = "总股本未单独录入，按股本（面值）近似为股数，需人工核对"
    if equity_value is not None and share_count:
        per_share = equity_value / share_count

    stale = _market_cap_stale(market_cap_date)
    premium = None
    if equity_value is not None and market_cap and not stale:
        premium = (equity_value - market_cap) / market_cap

    fcf_series = [item["values"].get("ocf") - item["values"].get("capex")
                  for item in annual[-5:]
                  if item["values"].get("ocf") is not None and item["values"].get("capex") is not None]
    stability = dv.fcf_stability(fcf_series)

    applicability = "normal"
    notes = list(net_cash["notes"])
    if base <= 0:
        applicability = "reference_only"
        notes.append("基准自由现金流为负，结果仅供参考")
    elif stability is not None and stability > 1.0:
        applicability = "reference_only"
        notes.append(f"自由现金流波动度 {stability:.2f} > 1.0，结果仅供参考")
    if not net_cash["equity_allowed"]:
        notes.append(net_cash["block_reason"])
    if company["is_cyclical"]:
        notes.append("周期股：当前 FCF 可能处于周期高位/低位，建议基准切换为近 7 年平均")
    if stale:
        notes.append("市值时点超过 90 天，已隐藏折溢价")
    if share_note:
        notes.append(share_note)
    if base_note:
        notes.insert(0, base_note)

    sensitivity = _sensitivity(
        base, r=r, g1=params["g1"], g2=params["g2"], g=params["g"], n=params["n"],
        net_cash_value=net_cash_value, equity_allowed=net_cash["equity_allowed"],
    )
    comparison = _base_method_comparison(annual, params, net_cash_value, net_cash["equity_allowed"])

    model_id = valuation_repo.get_or_create_model(conn, company_id, "A", scheme)
    version_no = valuation_repo.next_version_no(conn, model_id)

    # 记录本次输入指标值（用于溯源与复算）
    input_rows = metric_repo.values_by_ids(conn, snapshot_repo.snapshot_value_ids(snapshot))
    used_codes = {"ocf", "capex", "cash", "trading_fin_assets", "short_term_borrowings",
                  "current_portion_lt_debt", "long_term_borrowings", "bonds_payable", "share_capital"}
    input_values = [{
        "code": row["metric_code"],
        "period": row["fiscal_year"],
        "value_id": row["id"],
        "value": row["value"],
        "version_no": row["version_no"],
        "status": row["status"],
    } for row in input_rows if row["metric_code"] in used_codes]

    version_id = valuation_repo.create_version(
        conn,
        model_id=model_id,
        company_id=company_id,
        version_no=version_no,
        scheme=scheme,
        method="A",
        based_on_snapshot_id=snapshot["id"],
        base_period_id=latest["period"]["id"],
        base_fcf_method=params["base_method"],
        base_fcf_value=base,
        fcf_detail=used_years,
        params=params,
        projection={"rows": projection, "pv_terminal": pv_terminal},
        net_cash=net_cash,
        shares=share_count,
        market_cap=market_cap,
        market_cap_date=market_cap_date,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        per_share_value=per_share,
        premium_discount=premium,
        stability=stability,
        applicability=applicability,
        applicability_note="；".join(note for note in notes if note),
        sensitivity={"matrix": sensitivity, "base_methods": comparison},
        input_values=input_values,
        status="pending_confirm",
        status_reason="待用户确认",
    )
    audit.add(conn, object_type="valuation_version", object_id=version_id, action="create",
              reason=f"基于快照 {snapshot['code']}，r={r:.2%}（{R_LEVEL_LABELS.get(rate_info['level'], rate_info['level'])}）",
              actor=actor)
    return {
        "version_id": version_id,
        "version_no": version_no,
        "snapshot": snapshot["code"],
        "params": params,
        "base_fcf": base,
        "base_years": used_years,
        "projection": projection,
        "enterprise_value": enterprise_value,
        "net_cash": net_cash,
        "equity_value": equity_value,
        "per_share_value": per_share,
        "premium_discount": premium,
        "stability": stability,
        "applicability": applicability,
        "applicability_note": "；".join(note for note in notes if note),
        "sensitivity": sensitivity,
        "base_method_comparison": comparison,
        "status": "pending_confirm",
    }


def confirm(conn, version_id: int, actor: str = "user") -> None:
    version = valuation_repo.get_version(conn, version_id)
    if not version:
        raise ValueError("估值版本不存在")
    if version["status"] not in ("draft", "pending_confirm", "pending_review"):
        raise ValuationError("当前状态不可确认")
    valuation_repo.set_status(conn, version_id, "confirmed", "用户确认")
    used = valuation_repo.parse_json(version["input_values"], []) or []
    metric_repo.lock_values(conn, [row["value_id"] for row in used if row.get("value_id")])
    audit.add(conn, object_type="valuation_version", object_id=version_id, action="confirm", actor=actor)


def mark_pending_after_upstream_change(conn, company_id: int, reason: str) -> int:
    count = valuation_repo.mark_pending_review(conn, company_id, reason)
    if count:
        for version in valuation_repo.list_versions(conn, company_id):
            if version["status"] == "pending_review":
                audit.add(conn, object_type="valuation_version", object_id=version["id"],
                          action="pending_review", reason=reason)
    return count


def valuation_view(conn, version_id: int) -> dict[str, Any]:
    version = valuation_repo.get_version(conn, version_id)
    if not version:
        raise ValueError("估值版本不存在")
    params = valuation_repo.parse_json(version["params"], {}) or {}
    return {
        "version": dict(version),
        "params": params,
        "params_json": params,
        "fcf_detail": valuation_repo.parse_json(version["fcf_detail"], []) or [],
        "projection": valuation_repo.parse_json(version["projection"], {}) or {},
        "net_cash": valuation_repo.parse_json(version["net_cash"], {}) or {},
        "sensitivity": valuation_repo.parse_json(version["sensitivity"], {}) or {},
        "input_values": valuation_repo.parse_json(version["input_values"], []) or [],
        "r_label": R_LEVEL_LABELS.get(params.get("r_source", ""), params.get("r_source", "")),
    }
