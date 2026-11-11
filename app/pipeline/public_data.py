"""免费公开数据源（无需 access token）。

当前接入：
- A 股：东方财富 F10「主要指标」与数据中心「现金流量表」公开接口；
- 港股/美股/非上市：暂不接入，明确报错，避免伪造数据。

定位：与年报/季报下载并行执行，先把网上公开的营收、利润、毛利率、经营现金流等
写进指标库（状态 pending，仍需人工确认），再与披露文件抽取互相校验。
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import requests

from app import config
from app.db.repositories import companies as company_repo
from app.db.repositories import metrics as metric_repo

EASTMONEY_KPI_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew"
EASTMONEY_CASH_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StockAnalyze/0.1"
EASTMONEY_REFERER = "https://emweb.securities.eastmoney.com/"

# 字段 → (指标 code, 单位, 换算系数)
KPI_FIELDS = {
    "TOTALOPERATEREVE": ("revenue", "元", 1.0),
    "PARENTNETPROFIT": ("net_profit_parent", "元", 1.0),
    "KCFJCXSYJLR": ("net_profit_deducted", "元", 1.0),
    "XSMLL": ("gross_margin", "ratio", 0.01),
}
CASH_FIELDS = {
    "NETCASH_OPERATE": ("ocf", "元", 1.0),
    "CONSTRUCT_LONG_ASSET": ("capex", "元", 1.0),
    "NETCASH_INVEST": ("investing_cf", "元", 1.0),
    "NETCASH_FINANCE": ("financing_cf", "元", 1.0),
    "CCE_ADD": ("net_cash_increase", "元", 1.0),
}
PERIOD_LABEL = {"annual": "年报", "h1": "中报", "q1": "一季报", "q3": "三季报"}


class PublicDataError(RuntimeError):
    pass


def _period_from_date(value: str | None) -> tuple[str, int] | None:
    if not value:
        return None
    try:
        year = int(str(value)[:4])
        month = int(str(value)[5:7])
    except (TypeError, ValueError):
        return None
    ptype = {3: "q1", 6: "h1", 9: "q3", 12: "annual"}.get(month)
    return (ptype, year) if ptype else None


def _period_from_report_type(report_type: str | None, date_value: str | None) -> tuple[str, int] | None:
    text = report_type or ""
    try:
        year = int(str(date_value)[:4])
    except (TypeError, ValueError):
        return None
    if "一季" in text:
        return ("q1", year)
    if "半年" in text or "中报" in text:
        return ("h1", year)
    if "三季" in text:
        return ("q3", year)
    if "年报" in text or "年度" in text:
        return ("annual", year)
    return _period_from_date(date_value)


def parse_rows(rows: list[dict[str, Any]], mapping: dict[str, tuple[str, str, float]],
               *, report_type_field: str | None = None) -> list[dict[str, Any]]:
    """把接口行解析成指标项；纯函数，便于用固定样本测试。"""
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for row in rows:
        period = (
            _period_from_report_type(row.get(report_type_field), row.get("REPORT_DATE"))
            if report_type_field else _period_from_date(row.get("REPORT_DATE"))
        )
        if not period:
            continue
        for field, (code, unit, scale) in mapping.items():
            raw = row.get(field)
            if raw is None:
                continue
            key = (code, period[0], period[1])
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "metric_code": code,
                "period_type": period[0],
                "fiscal_year": period[1],
                "value": float(raw) * scale,
                "unit": unit,
                "raw_value": float(raw),
                "raw_unit": "%" if unit == "ratio" else "元",
            })
    return items


def _request_json(conn, session: requests.Session, *, company_id: int, url: str,
                  params: dict[str, Any]) -> Any:
    interval = config.FETCH_INTERVAL.get("eastmoney", 1.0)
    last_error: Exception | None = None
    started = time.time()
    status_code = None
    for attempt in range(3):
        time.sleep(interval)
        try:
            response = session.get(url, params=params, timeout=30)
            status_code = response.status_code
            if response.status_code >= 400:
                raise PublicDataError(f"HTTP {response.status_code}")
            company_repo.log_fetch(
                conn, company_id=company_id, channel="eastmoney", url=url,
                status_code=status_code, retry_count=attempt,
                elapsed_ms=int((time.time() - started) * 1000), note="",
            )
            return response.json()
        except Exception as exc:  # noqa: BLE001 - 记录后重试
            last_error = exc
            time.sleep(1 + attempt)
    company_repo.log_fetch(
        conn, company_id=company_id, channel="eastmoney", url=url, status_code=status_code,
        retry_count=3, elapsed_ms=int((time.time() - started) * 1000), note=str(last_error),
    )
    raise PublicDataError(f"公开数据接口请求失败：{url}（{last_error}）")


def eastmoney_code(company) -> str:
    code = (company["code"] or "").strip()
    if not code:
        raise PublicDataError("企业缺少证券代码，无法访问公开数据")
    if code.startswith(("6", "9")):
        return f"SH{code}"
    if code.startswith(("0", "2", "3")):
        return f"SZ{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SH{code}"


def fetch_public_metrics(conn, company, *, years: int = 5) -> dict[str, Any]:
    """抓取公开指标并写入待确认队列；返回执行摘要。"""
    if company["market"] != "A":
        raise PublicDataError("公开数据源当前仅支持 A 股（港股/美股暂未接入免 token 渠道）")
    em_code = eastmoney_code(company)
    session = requests.Session()
    session.headers.update({"User-Agent": EASTMONEY_UA, "Referer": EASTMONEY_REFERER})

    annual_data = _request_json(
        conn, session, company_id=company["id"], url=EASTMONEY_KPI_URL,
        params={"type": 1, "code": em_code},
    )
    annual_rows: list[dict[str, Any]] = (annual_data or {}).get("data") or []

    quarterly_rows: list[dict[str, Any]] = []
    data = _request_json(
        conn, session, company_id=company["id"], url=EASTMONEY_KPI_URL,
        params={"type": 0, "code": em_code},
    )
    quarterly_rows.extend((data or {}).get("data") or [])

    cash_rows: list[dict[str, Any]] = []
    cash_data = _request_json(
        conn, session, company_id=company["id"], url=EASTMONEY_CASH_URL,
        params={
            "reportName": "RPT_DMSK_FN_CASHFLOW",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{company["code"]}")',
            "pageNumber": 1,
            "pageSize": 60,
            "sortColumns": "REPORT_DATE",
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        },
    )
    if isinstance(cash_data, dict) and cash_data.get("success"):
        cash_rows = (cash_data.get("result") or {}).get("data") or []

    items = parse_rows(annual_rows, KPI_FIELDS, report_type_field="REPORT_TYPE")
    items += parse_rows(quarterly_rows, KPI_FIELDS, report_type_field="REPORT_TYPE")
    items += parse_rows(cash_rows, CASH_FIELDS)

    cutoff = date.today().year - years + 1
    items = [item for item in items if item["fiscal_year"] >= cutoff]

    counters = {"inserted": 0, "updated": 0, "skipped": 0, "conflicts": 0}
    periods: set[str] = set()
    for item in items:
        ptype, year = item["period_type"], item["fiscal_year"]
        period_id, _ = company_repo.get_or_create_period(
            conn, company["id"], ptype, year,
            audited=True if ptype == "annual" else None, authority_rank=3,
        )
        metric = metric_repo.get_metric(conn, item["metric_code"])
        if not metric:
            continue
        _, action = metric_repo.upsert_value(
            conn,
            company_id=company["id"],
            period_id=period_id,
            metric_id=metric["id"],
            value=item["value"],
            unit=item["unit"],
            status="pending",
            confidence="high",
            raw_value=item["raw_value"],
            raw_unit=item["raw_unit"],
            raw_currency="CNY",
            caliber_note="公开数据源：东方财富公开接口（免 token），待与披露文件交叉校验",
        )
        if action == "inserted":
            counters["inserted"] += 1
        elif action == "superseded":
            counters["updated"] += 1
        elif action == "conflict":
            counters["conflicts"] += 1
        else:
            counters["skipped"] += 1
        periods.add(f"{year}{PERIOD_LABEL.get(ptype, ptype)}")

    return {
        "channel": "eastmoney",
        "source": "东方财富公开接口（免 token）",
        "items": len(items),
        "periods": sorted(periods),
        **counters,
    }
